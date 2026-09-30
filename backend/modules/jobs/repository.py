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
    return _deserialize_job(row)


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
