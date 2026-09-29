import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .paths import DATA_DIR


AI_DATA_PATH = DATA_DIR / "ai.sqlite3"
_connection: sqlite3.Connection | None = None
_connection_path: Path | None = None
_connection_lock = threading.RLock()


def _open_connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            operation TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
            cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            item_count INTEGER NOT NULL DEFAULT 1,
            success INTEGER NOT NULL DEFAULT 1,
            error TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS response_cache (
            cache_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at REAL NOT NULL,
            last_used_at REAL NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    return connection


def _get_connection() -> sqlite3.Connection:
    """Return one process-local connection, rebuilding it only when the data path changes."""
    global _connection, _connection_path
    path = AI_DATA_PATH.resolve()
    with _connection_lock:
        if _connection is None or _connection_path != path:
            if _connection is not None:
                _connection.close()
            _connection = _open_connection(path)
            _connection_path = path
        return _connection


@contextmanager
def _session():
    """Serialize access to the shared SQLite connection and commit atomically."""
    with _connection_lock:
        connection = _get_connection()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def close_store() -> None:
    global _connection, _connection_path
    with _connection_lock:
        if _connection is not None:
            _connection.close()
        _connection = None
        _connection_path = None


def record_usage(
    operation: str,
    model: str,
    usage: dict[str, Any] | None,
    duration_ms: int,
    item_count: int = 1,
    success: bool = True,
    error: str = "",
) -> None:
    usage = usage or {}
    details = usage.get("prompt_tokens_details") or {}
    cache_hit = int(usage.get("prompt_cache_hit_tokens") or details.get("cached_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    cache_miss = int(usage.get("prompt_cache_miss_tokens") or max(0, prompt - cache_hit))
    completion = int(usage.get("completion_tokens") or 0)
    with _session() as connection:
        connection.execute(
            """
            INSERT INTO usage (
                created_at, operation, model, prompt_tokens, cache_hit_tokens,
                cache_miss_tokens, completion_tokens, duration_ms, item_count, success, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(), operation, model, prompt, cache_hit, cache_miss,
                completion, max(0, int(duration_ms)), max(1, int(item_count)),
                1 if success else 0, error[:500],
            ),
        )


def usage_summary(pricing: dict[str, float] | None = None) -> dict[str, Any]:
    pricing = pricing or {}
    with _session() as connection:
        total = connection.execute(
            """
            SELECT COUNT(*) AS requests, COALESCE(SUM(item_count), 0) AS items,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,
                   COALESCE(SUM(cache_miss_tokens), 0) AS cache_miss_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(duration_ms), 0) AS duration_ms,
                   COALESCE(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END), 0) AS failures
            FROM usage
            """
        ).fetchone()
        operations = [dict(row) for row in connection.execute(
            """
            SELECT operation, COUNT(*) AS requests, COALESCE(SUM(item_count), 0) AS items,
                   COALESCE(SUM(cache_hit_tokens), 0) AS cache_hit_tokens,
                   COALESCE(SUM(cache_miss_tokens), 0) AS cache_miss_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(duration_ms), 0) AS duration_ms
            FROM usage GROUP BY operation ORDER BY operation
            """
        )]
        cache = connection.execute(
            "SELECT COUNT(*) AS entries, COALESCE(SUM(hit_count), 0) AS hits FROM response_cache"
        ).fetchone()
    data = dict(total)
    data["operations"] = operations
    data["response_cache_entries"] = int(cache["entries"])
    data["response_cache_hits"] = int(cache["hits"])
    data["estimated_cost_usd"] = round((
        int(data["cache_hit_tokens"]) * float(pricing.get("cache_hit", 0))
        + int(data["cache_miss_tokens"]) * float(pricing.get("cache_miss", 0))
        + int(data["completion_tokens"]) * float(pricing.get("output", 0))
    ) / 1_000_000, 6)
    data["pricing_configured"] = any(float(value or 0) > 0 for value in pricing.values())
    return data


def make_cache_key(kind: str, model: str, prompt_version: str, value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{kind}\0{model}\0{prompt_version}\0{serialized}".encode("utf-8")).hexdigest()


def get_cached_response(cache_key: str) -> dict[str, Any] | None:
    with _session() as connection:
        row = connection.execute(
            "SELECT payload FROM response_cache WHERE cache_key = ?", (cache_key,),
        ).fetchone()
        if not row:
            return None
        connection.execute(
            "UPDATE response_cache SET last_used_at = ?, hit_count = hit_count + 1 WHERE cache_key = ?",
            (time.time(), cache_key),
        )
    try:
        value = json.loads(row["payload"])
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def set_cached_response(
    cache_key: str, kind: str, model: str, prompt_version: str, payload: dict[str, Any],
) -> None:
    now = time.time()
    with _session() as connection:
        connection.execute(
            """
            INSERT INTO response_cache (
                cache_key, kind, model, prompt_version, payload, created_at, last_used_at, hit_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload = excluded.payload, last_used_at = excluded.last_used_at
            """,
            (
                cache_key, kind, model, prompt_version,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")), now, now,
            ),
        )
