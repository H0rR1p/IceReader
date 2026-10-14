"""User-scoped derived analyses and persisted reading preferences."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ...paths import DATA_DIR
from .models import ContextPolicy

STORE_PATH = DATA_DIR / "linguistics.sqlite3"
_lock = threading.RLock()
_initialized: set[Path] = set()


@contextmanager
def session():
    path = STORE_PATH.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        with _lock:
            if not existed or path not in _initialized:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS analyses(
                      user_id TEXT NOT NULL, sentence_id TEXT NOT NULL,
                      text_hash TEXT NOT NULL, version TEXT NOT NULL, revision INTEGER NOT NULL,
                      payload TEXT NOT NULL, updated_at REAL NOT NULL,
                      PRIMARY KEY(user_id,sentence_id));
                    CREATE TABLE IF NOT EXISTS preferences(
                      user_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS span_overrides(
                      user_id TEXT NOT NULL,sentence_id TEXT NOT NULL,span_id TEXT NOT NULL,
                      text_hash TEXT NOT NULL,payload TEXT NOT NULL,revision INTEGER NOT NULL,
                      updated_at REAL NOT NULL,PRIMARY KEY(user_id,sentence_id,span_id));
                    CREATE TABLE IF NOT EXISTS span_senses(
                      user_id TEXT NOT NULL,sentence_id TEXT NOT NULL,
                      text_hash TEXT NOT NULL,version TEXT NOT NULL,revision INTEGER NOT NULL,
                      context_hash TEXT NOT NULL,payload TEXT NOT NULL,updated_at REAL NOT NULL,
                      PRIMARY KEY(user_id,sentence_id));
                    CREATE TABLE IF NOT EXISTS usage_budgets(user_id TEXT NOT NULL,day TEXT NOT NULL,
                      calls INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(user_id,day));
                    CREATE TABLE IF NOT EXISTS span_override_history(user_id TEXT NOT NULL,sentence_id TEXT NOT NULL,
                      span_id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL,
                      PRIMARY KEY(user_id,sentence_id,span_id,revision));
                    CREATE TABLE IF NOT EXISTS ambiguity_responses(user_id TEXT NOT NULL,cache_key TEXT NOT NULL,
                      payload TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(user_id,cache_key));
                """)
                connection.commit()
                _initialized.add(path)
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def load_preferences(user_id: str) -> dict:
    default = {"context_policy": ContextPolicy().model_dump(),
               "dependency_enhancement": False, "ambiguity_resolution": False}
    with session() as connection:
        row = connection.execute("SELECT payload FROM preferences WHERE user_id=?", (user_id,)).fetchone()
    return {**default, **json.loads(row[0]), "ambiguity_resolution": False} if row else default


def save_preferences(user_id: str, payload: dict) -> dict:
    context = ContextPolicy.model_validate(payload.get("context_policy", {})).model_dump()
    result = {"context_policy": context,
              "dependency_enhancement": bool(payload.get("dependency_enhancement", False)),
              "ambiguity_resolution": False}
    with session() as connection:
        connection.execute("INSERT INTO preferences VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                           (user_id, json.dumps(result, ensure_ascii=False), time.time()))
    return result


def get_analysis(user_id: str, sentence_id: str, text_hash: str, version: str, revision: int) -> dict | None:
    with session() as connection:
        row = connection.execute("SELECT payload FROM analyses WHERE user_id=? AND sentence_id=? AND text_hash=? AND version=? AND revision=?",
                                 (user_id, sentence_id, text_hash, version, revision)).fetchone()
    return json.loads(row[0]) if row else None


def put_analysis(user_id: str, sentence_id: str, result: dict) -> None:
    manifest = result["analysis_manifest"]
    with session() as connection:
        connection.execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,sentence_id) DO UPDATE SET text_hash=excluded.text_hash,version=excluded.version,revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at",
                           (user_id, sentence_id, manifest["text_hash"], manifest["version"],
                            manifest["revision"], json.dumps(result, ensure_ascii=False), time.time()))


def invalidate_sentences(user_id: str, sentence_ids: list[str]) -> None:
    with session() as connection:
        connection.executemany("DELETE FROM analyses WHERE user_id=? AND sentence_id=?", [(user_id, item) for item in sentence_ids])
        connection.executemany("DELETE FROM span_senses WHERE user_id=? AND sentence_id=?", [(user_id, item) for item in sentence_ids])


def save_span_senses(user_id: str, sentence_id: str, senses: list[dict], manifest: dict,
                     context_hash: str) -> None:
    """Persist generated context meanings independently from rebuildable spans."""
    with session() as connection:
        connection.execute("""INSERT INTO span_senses VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id,sentence_id) DO UPDATE SET text_hash=excluded.text_hash,
            version=excluded.version,revision=excluded.revision,context_hash=excluded.context_hash,
            payload=excluded.payload,updated_at=excluded.updated_at""",
            (user_id, sentence_id, manifest["text_hash"], manifest["version"], manifest["revision"],
             context_hash, json.dumps(senses, ensure_ascii=False), time.time()))


def load_span_senses(user_id: str, sentence_id: str, text_hash: str, version: str, revision: int,
                     context_hash: str | None = None) -> list[dict]:
    with session() as connection:
        row = connection.execute("""SELECT payload,context_hash FROM span_senses WHERE user_id=?
            AND sentence_id=? AND text_hash=? AND version=? AND revision=?""",
            (user_id, sentence_id, text_hash, version, revision)).fetchone()
    if row is None or (context_hash is not None and row["context_hash"] != context_hash):
        return []
    return json.loads(row["payload"])


def merge_span_sense(user_id: str, sentence_id: str, sense: dict, manifest: dict,
                     context_hash: str) -> None:
    """Merge an individual meaning transactionally without erasing other nodes."""
    with session() as connection:
        row = connection.execute("SELECT * FROM span_senses WHERE user_id=? AND sentence_id=?",
                                 (user_id, sentence_id)).fetchone()
        valid = row is not None and (row["text_hash"], row["version"], row["revision"], row["context_hash"]) == (
            manifest["text_hash"], manifest["version"], manifest["revision"], context_hash)
        previous = json.loads(row["payload"]) if valid else []
        senses = {value["span_id"]: value for value in previous}
        senses[sense["span_id"]] = sense
        connection.execute("""INSERT INTO span_senses VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id,sentence_id) DO UPDATE SET text_hash=excluded.text_hash,
            version=excluded.version,revision=excluded.revision,context_hash=excluded.context_hash,
            payload=excluded.payload,updated_at=excluded.updated_at""",
            (user_id, sentence_id, manifest["text_hash"], manifest["version"], manifest["revision"],
             context_hash, json.dumps(list(senses.values()), ensure_ascii=False), time.time()))


def load_span_override(user_id: str, sentence_id: str, span_id: str, text_hash: str, version: str,
                       *, include_retired: bool = False) -> dict | None:
    with session() as connection:
        row = connection.execute('SELECT payload,revision,text_hash FROM span_overrides WHERE user_id=? AND sentence_id=? AND span_id=?',
                                 (user_id, sentence_id, span_id)).fetchone()
    if not row:
        return None
    value = json.loads(row['payload'])
    same_text = row['text_hash'] == text_hash
    if same_text and value.get('version') == version:
        return {**value, 'revision': row['revision']}
    if include_retired:
        return {**value, 'revision': row['revision'], '_retired': True, '_same_text': same_text}
    return None


def save_span_override(user_id: str, sentence_id: str, span_id: str, text_hash: str,
                       version: str, expected_revision: int | None, payload: dict) -> dict:
    from fastapi import HTTPException
    with session() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT revision FROM span_overrides WHERE user_id=? AND sentence_id=? AND span_id=?', (user_id, sentence_id, span_id)).fetchone()
        current = int(row[0]) if row else 0
        if expected_revision not in ({None, 0} if current == 0 else {current}):
            raise HTTPException(409, '结构修订已变化，请刷新后重试')
        value = {**payload, 'version': version, 'revision': current + 1}
        connection.execute('INSERT INTO span_overrides VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,sentence_id,span_id) DO UPDATE SET text_hash=excluded.text_hash,payload=excluded.payload,revision=excluded.revision,updated_at=excluded.updated_at',
                           (user_id, sentence_id, span_id, text_hash, json.dumps(value, ensure_ascii=False), current + 1, time.time()))
        connection.execute('INSERT INTO span_override_history VALUES(?,?,?,?,?,?)',
                           (user_id,sentence_id,span_id,current+1,json.dumps(value,ensure_ascii=False),time.time()))
        # Meanings generated before a changed interpretation may now be stale.
        connection.execute('DELETE FROM span_senses WHERE user_id=? AND sentence_id=?', (user_id, sentence_id))
    return value
