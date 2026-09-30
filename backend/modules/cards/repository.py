import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from ...paths import DATA_DIR
from ..learning.repository import initialize_store as initialize_learning_store
from .scheduler import MODEL_VERSION, retrievability, schedule_review


CARDS_PATH = DATA_DIR / "learning.sqlite3"
_lock = threading.Lock()
_initialized_path: Path | None = None


def _raw_connection() -> sqlite3.Connection:
    CARDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(CARDS_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_store() -> None:
    global _initialized_path
    initialize_learning_store()
    resolved = CARDS_PATH.resolve()
    if _initialized_path == resolved:
        return
    with _lock:
        if _initialized_path == resolved:
            return
        connection = _raw_connection()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS card_candidates (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, knowledge_item_id TEXT NOT NULL,
                    lemma TEXT NOT NULL, reading TEXT NOT NULL DEFAULT '', gloss TEXT NOT NULL DEFAULT '',
                    sentence TEXT NOT NULL DEFAULT '', book_id TEXT NOT NULL DEFAULT '',
                    book_title TEXT NOT NULL DEFAULT '', chapter_id TEXT NOT NULL DEFAULT '',
                    sentence_id TEXT NOT NULL DEFAULT '', card_template TEXT NOT NULL DEFAULT 'context-recognition',
                    status TEXT NOT NULL DEFAULT 'candidate', created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1, deleted_at REAL,
                    UNIQUE(user_id, knowledge_item_id, card_template, sentence_id)
                );
                CREATE INDEX IF NOT EXISTS idx_candidates_user_status ON card_candidates(user_id, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, knowledge_item_id TEXT NOT NULL,
                    lemma TEXT NOT NULL, reading TEXT NOT NULL DEFAULT '', gloss TEXT NOT NULL DEFAULT '',
                    sentence TEXT NOT NULL DEFAULT '', book_id TEXT NOT NULL DEFAULT '', book_title TEXT NOT NULL DEFAULT '',
                    chapter_id TEXT NOT NULL DEFAULT '', sentence_id TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, version INTEGER NOT NULL DEFAULT 1, deleted_at REAL
                );
                CREATE TABLE IF NOT EXISTS cards (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, note_id TEXT NOT NULL REFERENCES notes(id),
                    knowledge_item_id TEXT NOT NULL, card_template TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                    priority INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1, deleted_at REAL,
                    UNIQUE(user_id, knowledge_item_id, card_template, note_id)
                );
                CREATE INDEX IF NOT EXISTS idx_cards_user_status ON cards(user_id, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS memory_states (
                    card_id TEXT PRIMARY KEY REFERENCES cards(id), user_id TEXT NOT NULL,
                    difficulty REAL NOT NULL DEFAULT 5, stability REAL NOT NULL DEFAULT 0,
                    retrievability REAL NOT NULL DEFAULT 0, due_at REAL NOT NULL,
                    last_review_at REAL, reps INTEGER NOT NULL DEFAULT 0, lapses INTEGER NOT NULL DEFAULT 0,
                    model_version TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_due ON memory_states(user_id, due_at);
                CREATE TABLE IF NOT EXISTS review_logs (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, card_id TEXT NOT NULL REFERENCES cards(id),
                    rating TEXT NOT NULL, reviewed_at REAL NOT NULL, elapsed_days REAL NOT NULL,
                    scheduled_days REAL NOT NULL, state_before_json TEXT NOT NULL,
                    state_after_json TEXT NOT NULL, model_version TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reviews_card ON review_logs(user_id, card_id, reviewed_at DESC);
                CREATE TABLE IF NOT EXISTS saved_card_views (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, query_json TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(user_id, name)
                );
                CREATE TABLE IF NOT EXISTS card_undo_log (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, action TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS card_search USING fts5(
                    card_id UNINDEXED, user_id UNINDEXED, lemma, reading, gloss, sentence, book_title, tags,
                    tokenize='unicode61'
                );
                """
            )
            connection.commit()
            _initialized_path = resolved
        finally:
            connection.close()


@contextmanager
def _connect():
    initialize_store()
    connection = _raw_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _row(value: sqlite3.Row | None) -> dict | None:
    if value is None:
        return None
    result = dict(value)
    if "tags_json" in result:
        result["tags"] = json.loads(result.pop("tags_json") or "[]")
    return result


def create_candidate(user_id: str, knowledge_item_id: str, payload: dict) -> dict:
    now = time.time()
    candidate_id = str(payload.get("id") or uuid.uuid4())
    with _connect() as connection:
        connection.execute(
            """INSERT INTO card_candidates(
                id,user_id,knowledge_item_id,lemma,reading,gloss,sentence,book_id,book_title,
                chapter_id,sentence_id,card_template,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id,knowledge_item_id,card_template,sentence_id) DO UPDATE SET
                gloss=CASE WHEN excluded.gloss<>'' THEN excluded.gloss ELSE card_candidates.gloss END,
                sentence=excluded.sentence,book_title=excluded.book_title,updated_at=excluded.updated_at,
                version=card_candidates.version+1,deleted_at=NULL""",
            (candidate_id,user_id,knowledge_item_id,payload["lemma"],payload.get("reading",""),payload.get("gloss",""),
             payload.get("sentence",""),payload.get("book_id",""),payload.get("book_title",""),
             payload.get("chapter_id",""),payload.get("sentence_id",""),payload.get("card_template","context-recognition"),
             "candidate",now,now),
        )
        row = connection.execute(
            "SELECT * FROM card_candidates WHERE user_id=? AND knowledge_item_id=? AND card_template=? AND sentence_id=?",
            (user_id,knowledge_item_id,payload.get("card_template","context-recognition"),payload.get("sentence_id","")),
        ).fetchone()
    return _row(row) or {}


def list_candidates(user_id: str, status: str = "candidate", limit: int = 100) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM card_candidates WHERE user_id=? AND status=? AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?",
            (user_id,status,min(500,max(1,limit))),
        ).fetchall()
    return [_row(row) or {} for row in rows]


def accept_candidate(user_id: str, candidate_id: str) -> dict:
    now = time.time()
    with _connect() as connection:
        candidate = connection.execute(
            "SELECT * FROM card_candidates WHERE id=? AND user_id=? AND deleted_at IS NULL",
            (candidate_id,user_id),
        ).fetchone()
        if candidate is None:
            raise KeyError("candidate_not_found")
        note_id, card_id = str(uuid.uuid4()), str(uuid.uuid4())
        connection.execute(
            """INSERT INTO notes(id,user_id,knowledge_item_id,lemma,reading,gloss,sentence,book_id,book_title,chapter_id,sentence_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (note_id,user_id,candidate["knowledge_item_id"],candidate["lemma"],candidate["reading"],candidate["gloss"],
             candidate["sentence"],candidate["book_id"],candidate["book_title"],candidate["chapter_id"],candidate["sentence_id"],now,now),
        )
        connection.execute(
            "INSERT INTO cards(id,user_id,note_id,knowledge_item_id,card_template,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (card_id,user_id,note_id,candidate["knowledge_item_id"],candidate["card_template"],"active",now,now),
        )
        connection.execute(
            "INSERT INTO memory_states(card_id,user_id,due_at,model_version,updated_at) VALUES(?,?,?,?,?)",
            (card_id,user_id,now,MODEL_VERSION,now),
        )
        connection.execute("UPDATE card_candidates SET status='accepted',updated_at=?,version=version+1 WHERE id=?",(now,candidate_id))
        _index_card(connection,user_id,card_id)
        row = connection.execute("SELECT c.*,n.lemma,n.reading,n.gloss,n.sentence,n.book_title,n.tags_json,m.difficulty,m.stability,m.retrievability,m.due_at,m.reps,m.lapses FROM cards c JOIN notes n ON n.id=c.note_id JOIN memory_states m ON m.card_id=c.id WHERE c.id=?",(card_id,)).fetchone()
    return _row(row) or {}


def reject_candidate(user_id: str, candidate_id: str) -> None:
    with _connect() as connection:
        cursor = connection.execute("UPDATE card_candidates SET status='rejected',updated_at=?,version=version+1 WHERE id=? AND user_id=?",(time.time(),candidate_id,user_id))
        if not cursor.rowcount:
            raise KeyError("candidate_not_found")


def _index_card(connection: sqlite3.Connection, user_id: str, card_id: str) -> None:
    connection.execute("DELETE FROM card_search WHERE card_id=? AND user_id=?",(card_id,user_id))
    row = connection.execute("SELECT c.id,n.lemma,n.reading,n.gloss,n.sentence,n.book_title,n.tags_json FROM cards c JOIN notes n ON n.id=c.note_id WHERE c.id=? AND c.user_id=?",(card_id,user_id)).fetchone()
    if row:
        connection.execute("INSERT INTO card_search(card_id,user_id,lemma,reading,gloss,sentence,book_title,tags) VALUES(?,?,?,?,?,?,?,?)",(row["id"],user_id,row["lemma"],row["reading"],row["gloss"],row["sentence"],row["book_title"]," ".join(json.loads(row["tags_json"] or "[]"))))


def search_cards(user_id: str, query: str = "", status: str = "", due: str = "", limit: int = 100, offset: int = 0) -> list[dict]:
    clauses = ["c.user_id=?", "c.deleted_at IS NULL", "n.deleted_at IS NULL"]
    values: list[object] = [user_id]
    if status:
        clauses.append("c.status=?")
        values.append(status)
    if due == "today":
        clauses.append("m.due_at<=?")
        values.append(time.time())
    if query.strip():
        clauses.append("c.id IN (SELECT card_id FROM card_search WHERE user_id=? AND card_search MATCH ?)")
        values.extend([user_id, " AND ".join(f'\"{part.replace(chr(34), chr(34)*2)}\"*' for part in query.split())])
    values.extend([min(500,max(1,limit)),max(0,offset)])
    sql = f"""SELECT c.*,n.lemma,n.reading,n.gloss,n.sentence,n.book_id,n.book_title,n.chapter_id,n.sentence_id,n.tags_json,
              m.difficulty,m.stability,m.due_at,m.last_review_at,m.reps,m.lapses,
              CASE WHEN m.stability>0 THEN pow(1.0+MAX(0,?-COALESCE(m.last_review_at,?))/(86400.0*9.0*m.stability),-1.0) ELSE 0 END AS retrievability
              FROM cards c JOIN notes n ON n.id=c.note_id JOIN memory_states m ON m.card_id=c.id
              WHERE {' AND '.join(clauses)} ORDER BY m.due_at,c.updated_at DESC LIMIT ? OFFSET ?"""
    values = [time.time(),time.time(),*values]
    with _connect() as connection:
        rows = connection.execute(sql,values).fetchall()
    return [_row(row) or {} for row in rows]


def update_card_statuses(user_id: str, card_ids: list[str], status: str) -> dict:
    now = time.time()
    with _connect() as connection:
        placeholders = ",".join("?" for _ in card_ids)
        rows = connection.execute(f"SELECT id,status FROM cards WHERE user_id=? AND id IN ({placeholders})",[user_id,*card_ids]).fetchall()
        undo_id = str(uuid.uuid4())
        connection.execute("INSERT INTO card_undo_log(id,user_id,action,snapshot_json,created_at) VALUES(?,?,?,?,?)",(undo_id,user_id,"status",json.dumps([dict(row) for row in rows]),now))
        cursor = connection.execute(f"UPDATE cards SET status=?,updated_at=?,version=version+1 WHERE user_id=? AND id IN ({placeholders})",[status,now,user_id,*card_ids])
    return {"updated":cursor.rowcount,"undo_id":undo_id}


def update_card_tags(user_id: str, card_ids: list[str], tag: str, remove: bool = False) -> dict:
    now = time.time()
    updated = 0
    with _connect() as connection:
        placeholders = ",".join("?" for _ in card_ids)
        rows = connection.execute(
            f"""SELECT c.id,n.id AS note_id,n.tags_json FROM cards c JOIN notes n ON n.id=c.note_id
                WHERE c.user_id=? AND c.id IN ({placeholders})""",
            [user_id,*card_ids],
        ).fetchall()
        snapshots = []
        for row in rows:
            tags = json.loads(row["tags_json"] or "[]")
            snapshots.append({"card_id":row["id"],"note_id":row["note_id"],"tags":tags})
            if remove:
                tags = [value for value in tags if value != tag]
            elif tag not in tags:
                tags.append(tag)
            connection.execute(
                "UPDATE notes SET tags_json=?,updated_at=?,version=version+1 WHERE id=? AND user_id=?",
                (json.dumps(tags,ensure_ascii=False),now,row["note_id"],user_id),
            )
            _index_card(connection,user_id,row["id"])
            updated += 1
        undo_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO card_undo_log(id,user_id,action,snapshot_json,created_at) VALUES(?,?,?,?,?)",
            (undo_id,user_id,"tags",json.dumps(snapshots,ensure_ascii=False),now),
        )
    return {"updated":updated,"undo_id":undo_id}


def card_summary(user_id: str) -> dict[str,int]:
    now = time.time()
    with _connect() as connection:
        candidate = connection.execute("SELECT COUNT(*) FROM card_candidates WHERE user_id=? AND status='candidate' AND deleted_at IS NULL",(user_id,)).fetchone()[0]
        row = connection.execute(
            """SELECT COUNT(*) AS active,
               SUM(CASE WHEN m.due_at<=? AND c.status='active' THEN 1 ELSE 0 END) AS due_now,
               SUM(CASE WHEN m.due_at<=? AND c.status='active' THEN 1 ELSE 0 END) AS due_7_days,
               SUM(CASE WHEN m.due_at<=? AND c.status='active' THEN 1 ELSE 0 END) AS due_30_days
               FROM cards c JOIN memory_states m ON m.card_id=c.id
               WHERE c.user_id=? AND c.deleted_at IS NULL""",
            (now,now+7*86400,now+30*86400,user_id),
        ).fetchone()
    return {"candidates":int(candidate),"active":int(row["active"] or 0),"due_now":int(row["due_now"] or 0),"due_7_days":int(row["due_7_days"] or 0),"due_30_days":int(row["due_30_days"] or 0),"daily_new_limit":20}


def save_view(user_id: str, name: str, query: dict) -> dict:
    now = time.time()
    with _connect() as connection:
        connection.execute("""INSERT INTO saved_card_views(id,user_id,name,query_json,created_at,updated_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id,name) DO UPDATE SET query_json=excluded.query_json,updated_at=excluded.updated_at,version=saved_card_views.version+1""",(str(uuid.uuid4()),user_id,name,json.dumps(query,ensure_ascii=False),now,now))
        row = connection.execute("SELECT * FROM saved_card_views WHERE user_id=? AND name=?",(user_id,name)).fetchone()
    result = dict(row)
    result["query"] = json.loads(result.pop("query_json"))
    return result


def list_views(user_id: str) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute("SELECT * FROM saved_card_views WHERE user_id=? ORDER BY updated_at DESC",(user_id,)).fetchall()
    result=[]
    for row in rows:
        value=dict(row); value["query"]=json.loads(value.pop("query_json")); result.append(value)
    return result


def review_card(user_id: str, device_id: str, card_id: str, rating: str, reviewed_at: float, review_id: str) -> dict:
    with _connect() as connection:
        existing = connection.execute("SELECT state_after_json FROM review_logs WHERE id=? AND user_id=?",(review_id,user_id)).fetchone()
        if existing:
            return json.loads(existing[0])
        row = connection.execute("SELECT m.*,c.status FROM memory_states m JOIN cards c ON c.id=m.card_id WHERE m.card_id=? AND m.user_id=? AND c.deleted_at IS NULL",(card_id,user_id)).fetchone()
        if row is None:
            raise KeyError("card_not_found")
        elapsed = max(0.0,(reviewed_at-(row["last_review_at"] or reviewed_at))/86400.0)
        scheduled = schedule_review(rating,difficulty=row["difficulty"],stability=row["stability"],elapsed_days=elapsed,lapses=row["lapses"])
        due_at = reviewed_at + scheduled.interval_days*86400
        before={key:row[key] for key in ("difficulty","stability","retrievability","due_at","last_review_at","reps","lapses","model_version")}
        after={"card_id":card_id,"difficulty":scheduled.difficulty,"stability":scheduled.stability,"retrievability":scheduled.retrievability,"due_at":due_at,"last_review_at":reviewed_at,"reps":row["reps"]+1,"lapses":scheduled.lapses,"model_version":MODEL_VERSION}
        connection.execute("""UPDATE memory_states SET difficulty=?,stability=?,retrievability=?,due_at=?,last_review_at=?,reps=?,lapses=?,model_version=?,updated_at=? WHERE card_id=? AND user_id=?""",(after["difficulty"],after["stability"],after["retrievability"],after["due_at"],reviewed_at,after["reps"],after["lapses"],MODEL_VERSION,time.time(),card_id,user_id))
        if scheduled.lapses>=5:
            connection.execute("UPDATE cards SET status='leech',updated_at=?,version=version+1 WHERE id=? AND user_id=?",(time.time(),card_id,user_id))
        connection.execute("INSERT INTO review_logs(id,user_id,card_id,rating,reviewed_at,elapsed_days,scheduled_days,state_before_json,state_after_json,model_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(review_id,user_id,card_id,rating,reviewed_at,elapsed,scheduled.interval_days,json.dumps(before),json.dumps(after),MODEL_VERSION,time.time()))
    return after


def due_cards(user_id: str, limit: int = 100) -> list[dict]:
    return search_cards(user_id,status="active",due="today",limit=limit)
