"""Facts are durable user data; changes are reversible revisioned events."""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from fastapi import HTTPException
from ...paths import DATA_DIR

STORE_PATH = DATA_DIR / 'book_memory.sqlite3'
_lock = threading.Lock()
_initialized: set[Path] = set()


@contextmanager
def session():
    path = STORE_PATH.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        with _lock:
            if path not in _initialized:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.executescript('''
                    CREATE TABLE IF NOT EXISTS objects(user_id TEXT NOT NULL,book_id TEXT NOT NULL,
                    kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,
                    PRIMARY KEY(user_id,book_id,kind,id));
                    CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,
                    book_id TEXT NOT NULL,kind TEXT NOT NULL,object_id TEXT NOT NULL,
                    before_json TEXT,after_json TEXT NOT NULL,created_at REAL NOT NULL,undone INTEGER NOT NULL DEFAULT 0);
                    CREATE INDEX IF NOT EXISTS idx_memory_revisions ON revisions(user_id,book_id,created_at);
                    CREATE TABLE IF NOT EXISTS dependencies(user_id TEXT NOT NULL,book_id TEXT NOT NULL,
                    sentence_id TEXT NOT NULL,fact_id TEXT NOT NULL,revision INTEGER NOT NULL,
                    PRIMARY KEY(user_id,book_id,sentence_id,fact_id));
                    CREATE TABLE IF NOT EXISTS summaries(user_id TEXT NOT NULL,book_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(user_id,book_id,chunk_id));
                    CREATE TABLE IF NOT EXISTS preflight_responses(user_id TEXT NOT NULL,book_id TEXT NOT NULL,
                    cache_key TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(user_id,book_id,cache_key));
                    CREATE TABLE IF NOT EXISTS context_targets(user_id TEXT NOT NULL,book_id TEXT NOT NULL,
                    sentence_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(user_id,book_id,sentence_id));
                ''')
                connection.commit()
                _initialized.add(path)
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _get(connection, user: str, book: str, kind: str, object_id: str):
    row = connection.execute('SELECT payload FROM objects WHERE user_id=? AND book_id=? AND kind=? AND id=?', (user, book, kind, object_id)).fetchone()
    return json.loads(row[0]) if row else None


def get(user: str, book: str, kind: str, object_id: str) -> dict:
    with session() as connection:
        value = _get(connection, user, book, kind, object_id)
    if value is None:
        raise HTTPException(404, '人物或事实不存在')
    return value


def _put(connection, user, book, kind, payload, expected):
    object_id = payload['id']
    before = _get(connection, user, book, kind, object_id)
    if before and expected != before['revision']:
        raise HTTPException(409, '档案已更新，请刷新后重试')
    if not before and expected not in {None, 0}:
        raise HTTPException(409, '档案版本不匹配')
    value = {**payload, 'revision': (before['revision'] if before else 0) + 1}
    encoded = json.dumps(value, ensure_ascii=False)
    connection.execute('INSERT INTO objects VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,book_id,kind,id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload', (user, book, kind, object_id, value['revision'], encoded))
    connection.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,0)', (str(uuid.uuid4()), user, book, kind, object_id, json.dumps(before, ensure_ascii=False) if before else None, encoded, time.time()))
    return value


def put(user: str, book: str, kind: str, payload: dict, expected: int | None = None):
    with session() as connection:
        connection.execute('BEGIN IMMEDIATE')
        return _put(connection, user, book, kind, payload, expected)


def snapshot(user: str, book: str) -> dict:
    with session() as connection:
        rows = connection.execute('SELECT kind,payload FROM objects WHERE user_id=? AND book_id=?', (user, book)).fetchall()
        history = connection.execute('SELECT * FROM revisions WHERE user_id=? AND book_id=? ORDER BY created_at DESC LIMIT 200', (user, book)).fetchall()
    result = {'entities': [], 'facts': [], 'history': [dict(row) for row in history]}
    for row in rows:
        result['entities' if row['kind'] == 'entity' else 'facts'].append(json.loads(row['payload']))
    return result


def put_fact(user: str, book: str, payload: dict, expected=None):
    """Conflicting confirmed values remain explicit; never choose one silently."""
    with session() as connection:
        connection.execute('BEGIN IMMEDIATE')
        other = connection.execute("SELECT payload FROM objects WHERE user_id=? AND book_id=? AND kind='fact' AND id!=? AND json_extract(payload,'$.entity_id')=? AND json_extract(payload,'$.key')=? AND json_extract(payload,'$.status') IN('confirmed','conflict')", (user, book, payload['id'], payload['entity_id'], payload['key'])).fetchall()
        if payload['status'] == 'confirmed':
            for row in other:
                value = json.loads(row[0])
                if value['value'] != payload['value']:
                    payload = {**payload, 'status': 'conflict'}
                    if value['status'] != 'conflict':
                        _put(connection, user, book, 'fact', {**value, 'status': 'conflict'}, value['revision'])
        return _put(connection, user, book, 'fact', payload, expected)


def undo(user: str, book: str, revision_id: str):
    with session() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT * FROM revisions WHERE id=? AND user_id=? AND book_id=? AND undone=0', (revision_id, user, book)).fetchone()
        if row is None:
            raise HTTPException(404, '修订不存在或已撤销')
        after = json.loads(row['after_json'])
        current = _get(connection, user, book, row['kind'], row['object_id'])
        if current is None or current['revision'] != after['revision']:
            raise HTTPException(409, '后续修订已存在，请先处理最新修订')
        before = json.loads(row['before_json']) if row['before_json'] else {**after, 'status': 'rejected', 'deleted': True}
        result = _put(connection, user, book, row['kind'], before, current['revision'])
        connection.execute('UPDATE revisions SET undone=1 WHERE id=?', (revision_id,))
        return result


def record_dependencies(user: str, book: str, sentence_id: str, facts: list[dict]):
    with session() as connection:
        connection.execute('DELETE FROM dependencies WHERE user_id=? AND book_id=? AND sentence_id=?', (user, book, sentence_id))
        connection.executemany('INSERT INTO dependencies VALUES(?,?,?,?,?)', [(user, book, sentence_id, fact['id'], fact['revision']) for fact in facts])


def record_context_target(user,book,sentence_id,payload):
    with session() as connection:
        connection.execute('INSERT INTO context_targets VALUES(?,?,?,?) ON CONFLICT(user_id,book_id,sentence_id) DO UPDATE SET payload=excluded.payload',(user,book,sentence_id,json.dumps(payload,ensure_ascii=False)))


def context_targets(user,book):
    with session() as connection:
        return [(row['sentence_id'],json.loads(row['payload'])) for row in connection.execute('SELECT sentence_id,payload FROM context_targets WHERE user_id=? AND book_id=?',(user,book))]


def affected_sentences(user: str, book: str, fact_ids: list[str]) -> list[str]:
    if not fact_ids:
        return []
    with session() as connection:
        rows = connection.execute('SELECT DISTINCT sentence_id FROM dependencies WHERE user_id=? AND book_id=? AND fact_id IN(' + ','.join('?' for _ in fact_ids) + ')', [user, book, *fact_ids]).fetchall()
    return [row[0] for row in rows]


def save_summary(user,book,chunk_id,payload):
    with session() as connection:
        connection.execute('INSERT INTO summaries VALUES(?,?,?,?) ON CONFLICT(user_id,book_id,chunk_id) DO UPDATE SET payload=excluded.payload',
                           (user,book,chunk_id,json.dumps(payload,ensure_ascii=False)))


def read_summaries(user,book):
    with session() as connection:
        return [json.loads(row[0]) for row in connection.execute('SELECT payload FROM summaries WHERE user_id=? AND book_id=? ORDER BY chunk_id',(user,book))]


def cache_preflight_response(user,book,key,payload=None):
    with session() as connection:
        if payload is not None:
            connection.execute('INSERT INTO preflight_responses VALUES(?,?,?,?) ON CONFLICT(user_id,book_id,cache_key) DO UPDATE SET payload=excluded.payload',(user,book,key,json.dumps(payload,ensure_ascii=False)))
            return payload
        row=connection.execute('SELECT payload FROM preflight_responses WHERE user_id=? AND book_id=? AND cache_key=?',(user,book,key)).fetchone()
    return json.loads(row[0]) if row else None
