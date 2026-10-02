import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from ...paths import DATA_DIR


SYNC_PATH=DATA_DIR / "sync.sqlite3"
APPEND_ONLY_TYPES={"learning_event","review_log"}
FORK_ON_CONFLICT_TYPES={"note"}
ALLOWED_TYPES={"knowledge_item","learning_event","note","card","review_log","bookmark","reading_progress","preference","lexeme"}
_lock=threading.Lock(); _initialized_path: Path | None=None


def _raw_connection():
    SYNC_PATH.parent.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(SYNC_PATH,timeout=15); connection.row_factory=sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL"); connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def initialize_store():
    global _initialized_path
    resolved=SYNC_PATH.resolve()
    if _initialized_path==resolved: return
    with _lock:
        if _initialized_path==resolved: return
        connection=_raw_connection()
        try:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sync_entities(
                    user_id TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,
                    version INTEGER NOT NULL,payload_json TEXT NOT NULL,source_device_id TEXT NOT NULL,
                    updated_at REAL NOT NULL,deleted_at REAL,conflict_group TEXT,
                    PRIMARY KEY(user_id,entity_type,entity_id)
                );
                CREATE TABLE IF NOT EXISTS sync_changes(
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,change_id TEXT NOT NULL UNIQUE,user_id TEXT NOT NULL,
                    source_device_id TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,
                    operation TEXT NOT NULL,base_version INTEGER,version INTEGER NOT NULL,payload_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,deleted_at REAL,conflict_group TEXT,created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sync_changes_user_cursor ON sync_changes(user_id,cursor);
                CREATE TABLE IF NOT EXISTS sync_bindings(
                    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,provider TEXT NOT NULL,provider_subject TEXT NOT NULL,
                    created_at REAL NOT NULL,updated_at REAL NOT NULL,UNIQUE(provider,provider_subject),UNIQUE(user_id,provider)
                );
                CREATE TABLE IF NOT EXISTS sync_device_cursors(
                    user_id TEXT NOT NULL,device_id TEXT NOT NULL,pull_cursor INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,PRIMARY KEY(user_id,device_id)
                );
            """)
            connection.commit(); _initialized_path=resolved
        finally: connection.close()


@contextmanager
def _connect():
    initialize_store(); connection=_raw_connection()
    try: yield connection; connection.commit()
    except Exception: connection.rollback(); raise
    finally: connection.close()


def _append_change(connection,user_id,device_id,mutation,entity_id,version,conflict_group=None):
    deleted_at=float(mutation.get("deleted_at")) if mutation.get("deleted_at") is not None else None
    operation="delete" if deleted_at is not None or mutation.get("operation")=="delete" else "upsert"
    payload=mutation.get("payload") or {}
    connection.execute("""INSERT INTO sync_changes(change_id,user_id,source_device_id,entity_type,entity_id,operation,base_version,version,payload_json,updated_at,deleted_at,conflict_group,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(mutation["change_id"],user_id,device_id,mutation["entity_type"],entity_id,operation,mutation.get("base_version"),version,json.dumps(payload,ensure_ascii=False,separators=(",",":")),float(mutation.get("updated_at") or time.time()),deleted_at,conflict_group,time.time()))
    connection.execute("""INSERT INTO sync_entities(user_id,entity_type,entity_id,version,payload_json,source_device_id,updated_at,deleted_at,conflict_group)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,entity_type,entity_id) DO UPDATE SET version=excluded.version,payload_json=excluded.payload_json,
        source_device_id=excluded.source_device_id,updated_at=excluded.updated_at,deleted_at=excluded.deleted_at,conflict_group=excluded.conflict_group""",
        (user_id,mutation["entity_type"],entity_id,version,json.dumps(payload,ensure_ascii=False,separators=(",",":")),device_id,float(mutation.get("updated_at") or time.time()),deleted_at,conflict_group))


def push_changes(user_id: str,device_id: str,mutations: list[dict]) -> dict:
    accepted=[]; skipped=[]; conflicts=[]
    with _connect() as connection:
        for mutation in mutations:
            entity_type=str(mutation["entity_type"]); entity_id=str(mutation["entity_id"])
            if entity_type not in ALLOWED_TYPES: raise ValueError(f"unsupported_entity:{entity_type}")
            duplicate=connection.execute("SELECT cursor FROM sync_changes WHERE change_id=?",(mutation["change_id"],)).fetchone()
            if duplicate: skipped.append({"change_id":mutation["change_id"],"reason":"duplicate","cursor":duplicate[0]}); continue
            current=connection.execute("SELECT * FROM sync_entities WHERE user_id=? AND entity_type=? AND entity_id=?",(user_id,entity_type,entity_id)).fetchone()
            incoming_payload=mutation.get("payload") or {}; current_payload=json.loads(current["payload_json"]) if current else None
            if current and entity_type in APPEND_ONLY_TYPES:
                if current_payload==incoming_payload: skipped.append({"change_id":mutation["change_id"],"reason":"same_append_only_entity"}); continue
                conflict_group=current["conflict_group"] or str(uuid.uuid4()); fork_id=f"{entity_id}@{device_id}@{str(mutation['change_id'])[:8]}"
                connection.execute("UPDATE sync_entities SET conflict_group=? WHERE user_id=? AND entity_type=? AND entity_id=?",(conflict_group,user_id,entity_type,entity_id))
                _append_change(connection,user_id,device_id,mutation,fork_id,1,conflict_group)
                conflicts.append({"entity_type":entity_type,"entity_id":entity_id,"fork_id":fork_id,"conflict_group":conflict_group}); continue
            base=mutation.get("base_version")
            if current and entity_type in FORK_ON_CONFLICT_TYPES and base is not None and int(base)!=int(current["version"]) and current_payload!=incoming_payload:
                conflict_group=current["conflict_group"] or str(uuid.uuid4()); fork_id=f"{entity_id}@{device_id}@{str(mutation['change_id'])[:8]}"
                connection.execute("UPDATE sync_entities SET conflict_group=? WHERE user_id=? AND entity_type=? AND entity_id=?",(conflict_group,user_id,entity_type,entity_id))
                _append_change(connection,user_id,device_id,mutation,fork_id,1,conflict_group)
                conflicts.append({"entity_type":entity_type,"entity_id":entity_id,"fork_id":fork_id,"conflict_group":conflict_group}); continue
            if current and float(mutation.get("updated_at") or 0)<float(current["updated_at"]):
                skipped.append({"change_id":mutation["change_id"],"reason":"older_than_current","current_version":current["version"]}); continue
            version=(int(current["version"])+1) if current else 1
            _append_change(connection,user_id,device_id,mutation,entity_id,version,current["conflict_group"] if current else None)
            accepted.append({"change_id":mutation["change_id"],"entity_id":entity_id,"version":version})
        cursor=connection.execute("SELECT COALESCE(MAX(cursor),0) FROM sync_changes WHERE user_id=?",(user_id,)).fetchone()[0]
    return {"accepted":accepted,"skipped":skipped,"conflicts":conflicts,"cursor":int(cursor)}


def pull_changes(user_id: str,device_id: str,after: int=0,limit: int=500) -> dict:
    with _connect() as connection:
        rows=connection.execute("SELECT * FROM sync_changes WHERE user_id=? AND cursor>? ORDER BY cursor LIMIT ?",(user_id,max(0,after),min(2000,max(1,limit)))).fetchall()
        cursor=int(rows[-1]["cursor"]) if rows else max(0,after)
        connection.execute("""INSERT INTO sync_device_cursors(user_id,device_id,pull_cursor,updated_at) VALUES(?,?,?,?)
            ON CONFLICT(user_id,device_id) DO UPDATE SET pull_cursor=MAX(pull_cursor,excluded.pull_cursor),updated_at=excluded.updated_at""",(user_id,device_id,cursor,time.time()))
    changes=[]
    for row in rows:
        value=dict(row); value["payload"]=json.loads(value.pop("payload_json")); changes.append(value)
    return {"changes":changes,"cursor":cursor,"has_more":len(rows)>=limit}


def list_changes_after(user_id: str, after: int = 0, limit: int = 500) -> dict:
    """Read the local outbox without advancing a device pull cursor."""
    safe_limit = min(1000, max(1, limit))
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM sync_changes WHERE user_id=? AND cursor>? ORDER BY cursor LIMIT ?",
            (user_id, max(0, after), safe_limit + 1),
        ).fetchall()
    has_more = len(rows) > safe_limit
    rows = rows[:safe_limit]
    changes = []
    for row in rows:
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        changes.append(value)
    return {
        "changes": changes,
        "cursor": int(rows[-1]["cursor"]) if rows else max(0, after),
        "has_more": has_more,
    }


def list_conflicts(user_id: str) -> list[dict]:
    with _connect() as connection:
        rows=connection.execute("SELECT * FROM sync_entities WHERE user_id=? AND conflict_group IS NOT NULL ORDER BY updated_at DESC",(user_id,)).fetchall()
    result=[]
    for row in rows:
        value=dict(row); value["payload"]=json.loads(value.pop("payload_json")); result.append(value)
    return result


def bind_identity(user_id: str,provider: str,subject: str) -> dict:
    now=time.time()
    with _connect() as connection:
        existing=connection.execute("SELECT user_id FROM sync_bindings WHERE provider=? AND provider_subject=?",(provider,subject)).fetchone()
        if existing and existing["user_id"]!=user_id: raise ValueError("identity_already_bound")
        connection.execute("""INSERT INTO sync_bindings(id,user_id,provider,provider_subject,created_at,updated_at) VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id,provider) DO UPDATE SET provider_subject=excluded.provider_subject,updated_at=excluded.updated_at""",(str(uuid.uuid4()),user_id,provider,subject,now,now))
        row=connection.execute("SELECT id,user_id,provider,provider_subject,created_at,updated_at FROM sync_bindings WHERE user_id=? AND provider=?",(user_id,provider)).fetchone()
    return dict(row)


def list_bindings(user_id: str) -> list[dict]:
    with _connect() as connection:
        return [dict(row) for row in connection.execute("SELECT id,provider,provider_subject,created_at,updated_at FROM sync_bindings WHERE user_id=?",(user_id,)).fetchall()]


def sync_status(user_id: str) -> dict:
    with _connect() as connection:
        row=connection.execute("""SELECT COUNT(*) AS entities,
            SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS tombstones,
            COUNT(DISTINCT conflict_group) AS conflicts FROM sync_entities WHERE user_id=?""",(user_id,)).fetchone()
        cursor=connection.execute("SELECT COALESCE(MAX(cursor),0) FROM sync_changes WHERE user_id=?",(user_id,)).fetchone()[0]
        devices=connection.execute("SELECT COUNT(*) FROM sync_device_cursors WHERE user_id=?",(user_id,)).fetchone()[0]
        bindings=connection.execute("SELECT COUNT(*) FROM sync_bindings WHERE user_id=?",(user_id,)).fetchone()[0]
    return {"cursor":int(cursor),"entities":int(row["entities"] or 0),"tombstones":int(row["tombstones"] or 0),"conflicts":int(row["conflicts"] or 0),"devices":int(devices),"bindings":int(bindings),"content_scope":"learning-only"}


def record_local_change(user_id: str,device_id: str,entity_type: str,entity_id: str,payload: dict,updated_at: float | None=None,deleted_at: float | None=None):
    return push_changes(user_id,device_id,[{"change_id":str(uuid.uuid4()),"entity_type":entity_type,"entity_id":entity_id,"payload":payload,"updated_at":updated_at or time.time(),"deleted_at":deleted_at}])

