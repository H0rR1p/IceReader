import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from ...paths import DATA_DIR


JOBS_PATH = DATA_DIR / "jobs.sqlite3"
_schema_lock = threading.Lock()
_initialized_path: Path | None = None


def _connect_raw() -> sqlite3.Connection:
    JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(JOBS_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_store() -> None:
    global _initialized_path
    resolved = JOBS_PATH.resolve()
    if _initialized_path == resolved:
        return
    with _schema_lock:
        if _initialized_path == resolved:
            return
        connection = _connect_raw()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    request_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_owner_updated
                    ON jobs(owner_user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS task_specs(
                    job_id TEXT PRIMARY KEY REFERENCES jobs(id), request_json TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',lock_key TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_active_task_scope ON task_specs(lock_key) WHERE active=1;
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    delivered_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_pending
                    ON outbox(delivered_at, created_at);
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id TEXT,
                    request_id TEXT,
                    category TEXT NOT NULL,
                    name TEXT NOT NULL,
                    duration_ms REAL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_observations_created
                    ON observations(created_at DESC);
                """
            )
            connection.commit()
            _initialized_path = resolved
        finally:
            connection.close()


@contextmanager
def _connect():
    initialize_store()
    connection = _connect_raw()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_job(
    owner_user_id: str,
    kind: str,
    *,
    job_id: str | None = None,
    request_id: str | None = None,
    message: str = "",
) -> str:
    actual_id = job_id or str(uuid.uuid4())
    now = time.time()
    with _connect() as connection:
        cursor = connection.execute(
            """INSERT INTO jobs(
                id, owner_user_id, kind, status, message, request_id, created_at, updated_at
            ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                kind=excluded.kind,
                message=excluded.message,
                request_id=excluded.request_id,
                updated_at=excluded.updated_at
            WHERE jobs.owner_user_id=excluded.owner_user_id""",
            (actual_id, owner_user_id, kind, message, request_id, now, now),
        )
        if cursor.rowcount != 1:
            raise KeyError(actual_id)
    return actual_id


def update_job(
    owner_user_id: str,
    job_id: str,
    *,
    status: str,
    message: str = "",
    current: int = 0,
    total: int = 0,
    retry_count: int = 0,
    result: dict | None = None,
) -> None:
    terminal = status in {"complete", "failed", "canceled"}
    now = time.time()
    with _connect() as connection:
        cursor = connection.execute(
            """UPDATE jobs SET status=?, message=?, progress_current=?, progress_total=?,
                retry_count=?, result_json=?, updated_at=?, finished_at=?
                WHERE id=? AND owner_user_id=?""",
            (
                status, message, current, total, retry_count,
                json.dumps(result or {}, ensure_ascii=False), now, now if terminal else None,
                job_id, owner_user_id,
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError(job_id)


def get_job(owner_user_id: str, job_id: str) -> dict:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id=? AND owner_user_id=?",
            (job_id, owner_user_id),
        ).fetchone()
    if row is None:
        raise KeyError(job_id)
    value = _deserialize_job(row)
    with _connect() as connection:
        spec = connection.execute('SELECT request_json FROM task_specs WHERE job_id=?', (job_id,)).fetchone()
    if spec:
        request = json.loads(spec[0])
        value['scope'] = {key: request.get(key) for key in ('book_id','chapter_id','scope','mode','sentence_ids','max_calls','max_tokens','max_estimated_tokens','provider_base_url','provider_model') if key in request}
    return value


def list_tasks(owner: str, book_id: str, limit: int = 50) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute("SELECT j.id FROM jobs j JOIN task_specs t ON t.job_id=j.id WHERE j.owner_user_id=? AND json_extract(t.request_json,'$.book_id')=? ORDER BY t.active DESC,j.updated_at DESC LIMIT ?", (owner, book_id, min(200, max(1, limit)))).fetchall()
    return [get_job(owner, row[0]) for row in rows]


def _deserialize_job(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["cancel_requested"] = bool(value["cancel_requested"])
    value["result"] = json.loads(value.pop("result_json") or "{}")
    return value


def list_jobs(owner_user_id: str, limit: int = 50) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM jobs WHERE owner_user_id=? ORDER BY updated_at DESC LIMIT ?",
            (owner_user_id, min(200, max(1, limit))),
        ).fetchall()
    return [_deserialize_job(row) for row in rows]


def record_observation(
    category: str,
    name: str,
    *,
    owner_user_id: str | None = None,
    request_id: str | None = None,
    duration_ms: float | None = None,
    detail: dict | None = None,
) -> None:
    with _connect() as connection:
        connection.execute(
            """INSERT INTO observations(
                owner_user_id, request_id, category, name, duration_ms, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                owner_user_id, request_id, category, name, duration_ms,
                json.dumps(detail or {}, ensure_ascii=False), time.time(),
            ),
        )


def enqueue_outbox(owner_user_id: str, topic: str, payload: dict) -> str:
    event_id = str(uuid.uuid4())
    with _connect() as connection:
        connection.execute(
            "INSERT INTO outbox(id, owner_user_id, topic, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (event_id, owner_user_id, topic, json.dumps(payload, ensure_ascii=False), time.time()),
        )
    return event_id


def create_task(owner: str, kind: str, request: dict, lock_key: str, request_id: str | None = None) -> dict:
    job_id, now = str(uuid.uuid4()), time.time()
    with _connect() as connection:
        connection.execute("INSERT INTO jobs(id,owner_user_id,kind,status,request_id,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?)",
                           (job_id, owner, kind, request_id, now, now))
        connection.execute("INSERT INTO task_specs(job_id,request_json,lock_key) VALUES(?,?,?)",
                           (job_id, json.dumps(request, ensure_ascii=False), owner + ':' + lock_key))
    return get_job(owner, job_id)


def task_spec(owner: str, job_id: str) -> dict:
    with _connect() as connection:
        row = connection.execute("SELECT t.* FROM task_specs t JOIN jobs j ON j.id=t.job_id WHERE j.id=? AND j.owner_user_id=?", (job_id, owner)).fetchone()
    if row is None:
        raise KeyError(job_id)
    return {"request": json.loads(row["request_json"]), "checkpoint": json.loads(row["checkpoint_json"]), "lock_key": row["lock_key"]}


def checkpoint_task(owner: str, job_id: str, checkpoint: dict, current: int, total: int,
                    result: dict | None = None) -> None:
    with _connect() as connection:
        cursor = connection.execute("UPDATE task_specs SET checkpoint_json=? WHERE job_id=? AND EXISTS(SELECT 1 FROM jobs WHERE id=? AND owner_user_id=?)",
                                   (json.dumps(checkpoint, ensure_ascii=False), job_id, job_id, owner))
        if cursor.rowcount != 1:
            raise KeyError(job_id)
        connection.execute("UPDATE jobs SET progress_current=?,progress_total=?,result_json=?,updated_at=? WHERE id=? AND owner_user_id=?",
                           (current, total, json.dumps(result or {}, ensure_ascii=False), time.time(), job_id, owner))


def finish_task(owner: str, job_id: str, status: str, message: str, result: dict | None = None) -> None:
    if status not in {'complete','failed','canceled','paused'}:
        raise ValueError(status)
    with _connect() as connection:
        cursor = connection.execute("UPDATE jobs SET status=?,message=?,result_json=?,updated_at=?,finished_at=? WHERE id=? AND owner_user_id=?",
            (status, message, json.dumps(result or {}, ensure_ascii=False), time.time(), time.time() if status != 'paused' else None, job_id, owner))
        if cursor.rowcount != 1:
            raise KeyError(job_id)
        if status != 'paused':
            connection.execute("UPDATE task_specs SET active=0 WHERE job_id=?", (job_id,))


def request_cancel(owner: str, job_id: str) -> dict:
    with _connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute("SELECT j.status FROM jobs j JOIN task_specs t ON t.job_id=j.id WHERE j.id=? AND j.owner_user_id=?", (job_id, owner)).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row['status'] in {'queued','paused','failed'}:
            connection.execute("UPDATE jobs SET cancel_requested=1,status='canceled',message='已取消，已提交的章节保留',updated_at=?,finished_at=? WHERE id=?", (time.time(), time.time(), job_id))
            connection.execute("UPDATE task_specs SET active=0 WHERE job_id=?", (job_id,))
        elif row['status'] == 'running':
            connection.execute("UPDATE jobs SET cancel_requested=1,message='正在安全停止当前步骤',updated_at=? WHERE id=?", (time.time(), job_id))
    return get_job(owner, job_id)


def retire_task_kind(kind: str, message: str) -> None:
    """Run before workers start; retain checkpoints and committed results."""
    with _connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute("UPDATE jobs SET status='canceled',cancel_requested=1,message=?,updated_at=?,finished_at=? WHERE kind=? AND status IN ('queued','running','paused','failed') AND id IN (SELECT job_id FROM task_specs)",
                           (message, time.time(), time.time(), kind))
        connection.execute("UPDATE task_specs SET active=0 WHERE job_id IN (SELECT id FROM jobs WHERE kind=?)", (kind,))


def resume_task(owner: str, job_id: str) -> dict:
    with _connect() as connection:
        row = connection.execute("SELECT j.status FROM jobs j JOIN task_specs t ON t.job_id=j.id WHERE j.id=? AND j.owner_user_id=?", (job_id, owner)).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row['status'] not in {'paused','failed','canceled'}:
            raise ValueError('此任务不可恢复')
        connection.execute("UPDATE task_specs SET active=1 WHERE job_id=?", (job_id,))
        connection.execute("UPDATE jobs SET status='queued',cancel_requested=0,finished_at=NULL,message='等待恢复',updated_at=? WHERE id=?", (time.time(), job_id))
    return get_job(owner, job_id)


def update_task_budget(owner: str, job_id: str, changes: dict, expected_updated_at: float) -> dict:
    with _connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT j.kind,j.status,j.updated_at,t.request_json FROM jobs j JOIN task_specs t ON t.job_id=j.id WHERE j.id=? AND j.owner_user_id=?',(job_id,owner)).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row['status'] not in {'paused','failed','canceled'} or row['updated_at']!=expected_updated_at:
            raise ValueError('任务状态已变化，请刷新后调整暂停任务的预算')
        if row['kind'] != 'book-preflight-v1':
            raise ValueError('此任务没有AI预算')
        limits={'max_calls':1000,'max_tokens':1000000}
        if not changes or any(key not in limits or not isinstance(value,int) or not 0<=value<=limits[key] for key,value in changes.items()):
            raise ValueError('预算字段或范围不合法')
        request={**json.loads(row['request_json']),**changes}
        connection.execute('UPDATE task_specs SET request_json=? WHERE job_id=?',(json.dumps(request,ensure_ascii=False),job_id))
        connection.execute('UPDATE jobs SET updated_at=? WHERE id=?',(time.time(),job_id))
    return get_job(owner,job_id)


def recover_tasks() -> None:
    with _connect() as connection:
        connection.execute("UPDATE jobs SET status='paused',message='服务重启，保留断点，可继续',updated_at=? WHERE status='running' AND id IN(SELECT job_id FROM task_specs)", (time.time(),))


def claim_task(kinds: list[str]) -> dict | None:
    if not kinds:
        return None
    with _connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute("SELECT j.* FROM jobs j JOIN task_specs t ON t.job_id=j.id WHERE j.status='queued' AND t.active=1 AND j.kind IN(" + ','.join('?' for _ in kinds) + ") ORDER BY j.created_at LIMIT 1", kinds).fetchone()
        if row is None:
            return None
        connection.execute("UPDATE jobs SET status='running',message='正在处理',updated_at=? WHERE id=?", (time.time(), row['id']))
        result = _deserialize_job(row)
        result['status'] = 'running'
        return result
