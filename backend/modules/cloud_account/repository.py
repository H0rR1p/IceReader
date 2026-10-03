from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ...paths import DATA_DIR


CLIENT_PATH = DATA_DIR / "cloud-client.sqlite3"
KEY_PATH = DATA_DIR / "cloud-client.key"
DEFAULT_CLOUD_URL = "http://127.0.0.1:8010"
_lock = threading.Lock()
_initialized_path: Path | None = None


def _raw_connection() -> sqlite3.Connection:
    CLIENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(CLIENT_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def initialize_store() -> None:
    global _initialized_path
    resolved = CLIENT_PATH.resolve()
    if _initialized_path == resolved:
        return
    with _lock:
        if _initialized_path == resolved:
            return
        connection = _raw_connection()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cloud_settings(
                    id INTEGER PRIMARY KEY CHECK(id=1),base_url TEXT NOT NULL,updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cloud_accounts(
                    local_user_id TEXT PRIMARY KEY,cloud_user_id TEXT NOT NULL,email TEXT NOT NULL,
                    display_name TEXT NOT NULL,email_verified INTEGER NOT NULL DEFAULT 0,
                    role TEXT NOT NULL DEFAULT 'user',
                    access_token BLOB NOT NULL,refresh_token BLOB NOT NULL,access_expires_at REAL NOT NULL,
                    refresh_expires_at REAL NOT NULL,remote_cursor INTEGER NOT NULL DEFAULT 0,
                    local_cursor INTEGER NOT NULL DEFAULT 0,last_sync_at REAL,last_error TEXT,
                    created_at REAL NOT NULL,updated_at REAL NOT NULL
                );
                INSERT OR IGNORE INTO cloud_settings(id,base_url,updated_at) VALUES(1,'http://127.0.0.1:8010',0);
                """
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(cloud_accounts)")}
            if "role" not in columns:
                connection.execute("ALTER TABLE cloud_accounts ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
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


def _cipher() -> Fernet:
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
        try:
            KEY_PATH.chmod(0o600)
        except OSError:
            pass
    return Fernet(KEY_PATH.read_bytes().strip())


def _encrypt(value: str) -> bytes:
    return _cipher().encrypt(value.encode("utf-8"))


def _decrypt(value: bytes) -> str:
    try:
        return _cipher().decrypt(value).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("云端登录凭据无法解密，请重新登录") from exc


def get_base_url() -> str:
    with _connect() as connection:
        row = connection.execute("SELECT base_url FROM cloud_settings WHERE id=1").fetchone()
    return str(row[0]) if row else DEFAULT_CLOUD_URL


def set_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    with _connect() as connection:
        connection.execute(
            "INSERT INTO cloud_settings(id,base_url,updated_at) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET base_url=excluded.base_url,updated_at=excluded.updated_at",
            (normalized, time.time()),
        )
    return normalized


def save_account(local_user_id: str, payload: dict) -> dict:
    now = time.time()
    user = payload["user"]
    with _connect() as connection:
        previous = connection.execute(
            "SELECT cloud_user_id,remote_cursor,local_cursor,created_at FROM cloud_accounts WHERE local_user_id=?", (local_user_id,),
        ).fetchone()
        same_remote_account = bool(previous and str(previous["cloud_user_id"]) == str(user["id"]))
        connection.execute(
            """INSERT INTO cloud_accounts(
                local_user_id,cloud_user_id,email,display_name,email_verified,role,access_token,refresh_token,
                access_expires_at,refresh_expires_at,remote_cursor,local_cursor,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(local_user_id) DO UPDATE SET
                cloud_user_id=excluded.cloud_user_id,email=excluded.email,display_name=excluded.display_name,
                email_verified=excluded.email_verified,role=excluded.role,access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,access_expires_at=excluded.access_expires_at,
                refresh_expires_at=excluded.refresh_expires_at,last_error=NULL,updated_at=excluded.updated_at""",
            (local_user_id,str(user["id"]),str(user["email"]),str(user["display_name"]),
             1 if user.get("email_verified") else 0,str(user.get("role") or "user"),_encrypt(str(payload["access_token"])),_encrypt(str(payload["refresh_token"])),
             now + float(payload.get("expires_in") or 900),now + float(payload.get("refresh_expires_in") or 30 * 86400),
             int(previous["remote_cursor"]) if same_remote_account else 0,
             int(previous["local_cursor"]) if same_remote_account else 0,
             float(previous["created_at"]) if previous else now,now),
        )
    return account_status(local_user_id)


def account_credentials(local_user_id: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM cloud_accounts WHERE local_user_id=?", (local_user_id,)).fetchone()
    if not row:
        return None
    value = dict(row)
    value["access_token"] = _decrypt(value["access_token"])
    value["refresh_token"] = _decrypt(value["refresh_token"])
    return value


def account_status(local_user_id: str) -> dict:
    with _connect() as connection:
        row = connection.execute(
            """SELECT cloud_user_id,email,display_name,email_verified,role,access_expires_at,refresh_expires_at,
               remote_cursor,local_cursor,last_sync_at,last_error,created_at,updated_at
               FROM cloud_accounts WHERE local_user_id=?""", (local_user_id,),
        ).fetchone()
    if not row:
        return {"connected": False, "base_url": get_base_url()}
    value = dict(row)
    value["connected"] = True
    value["email_verified"] = bool(value["email_verified"])
    value["base_url"] = get_base_url()
    return value


def update_account_user(local_user_id: str, user: dict) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE cloud_accounts SET email=?,display_name=?,email_verified=?,role=?,updated_at=? WHERE local_user_id=?",
            (user["email"],user["display_name"],1 if user.get("email_verified") else 0,str(user.get("role") or "user"),time.time(),local_user_id),
        )


def update_tokens(local_user_id: str, payload: dict) -> None:
    now = time.time()
    with _connect() as connection:
        connection.execute(
            """UPDATE cloud_accounts SET access_token=?,refresh_token=?,access_expires_at=?,
               refresh_expires_at=?,email=?,display_name=?,email_verified=?,updated_at=? WHERE local_user_id=?""",
            (_encrypt(str(payload["access_token"])),_encrypt(str(payload["refresh_token"])),
             now+float(payload.get("expires_in") or 900),now+float(payload.get("refresh_expires_in") or 30*86400),
             payload["user"]["email"],payload["user"]["display_name"],1 if payload["user"].get("email_verified") else 0,
             now,local_user_id),
        )


def update_sync_state(local_user_id: str, *, local_cursor: int | None = None, remote_cursor: int | None = None, error: str | None = None, completed: bool = False) -> None:
    fields = ["last_error=?", "updated_at=?"]
    values: list[object] = [error, time.time()]
    if local_cursor is not None:
        fields.append("local_cursor=?")
        values.append(local_cursor)
    if remote_cursor is not None:
        fields.append("remote_cursor=?")
        values.append(remote_cursor)
    if completed:
        fields.append("last_sync_at=?")
        values.append(time.time())
    values.append(local_user_id)
    with _connect() as connection:
        connection.execute(f"UPDATE cloud_accounts SET {','.join(fields)} WHERE local_user_id=?", values)


def delete_account(local_user_id: str) -> None:
    with _connect() as connection:
        connection.execute("DELETE FROM cloud_accounts WHERE local_user_id=?", (local_user_id,))

