import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ...core.errors import AuthenticationError, ConflictError
from ...paths import DATA_DIR


IDENTITY_PATH = DATA_DIR / "identity.sqlite3"
IDENTITY_SCHEMA_VERSION = 1
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_ACTIVE_SESSIONS_PER_USER = 12
MIGRATION_AUTH_KEY = "local:migrated"


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    user_id: str
    session_id: str
    device_id: str
    auth_provider: str
    token: str | None = None


def _now() -> float:
    return time.time()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _password_hash(password: str, salt: bytes | None = None) -> str:
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=actual_salt, n=2**14, r=8, p=1)
    return f"scrypt${actual_salt.hex()}${digest.hex()}"


def _password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_hex, digest_hex = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        actual = _password_hash(password, bytes.fromhex(salt_hex)).split("$", 2)[2]
        return hmac.compare_digest(actual, digest_hex)
    except (TypeError, ValueError):
        return False


@contextmanager
def _connect():
    IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(IDENTITY_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_store() -> str:
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                avatar_filename TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                disabled_at REAL
            );
            CREATE TABLE IF NOT EXISTS auth_identities (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                provider_subject TEXT NOT NULL,
                password_hash TEXT,
                created_at REAL NOT NULL,
                UNIQUE(provider, provider_subject)
            );
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_seen_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                auth_provider TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                revoked_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, expires_at);
            PRAGMA user_version = 1;
            """
        )
        user_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(users)")}
        if "avatar_filename" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN avatar_filename TEXT")
        row = connection.execute(
            """SELECT u.id FROM users AS u
               JOIN auth_identities AS a ON a.user_id = u.id
               WHERE a.provider = 'local' AND a.provider_subject = ?""",
            (MIGRATION_AUTH_KEY,),
        ).fetchone()
        if row:
            return str(row["id"])
        user_id = str(uuid.uuid4())
        now = _now()
        connection.execute(
            "INSERT INTO users(id, display_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, "本机用户", now, now),
        )
        connection.execute(
            """INSERT INTO auth_identities(
                   id, user_id, provider, provider_subject, password_hash, created_at
               ) VALUES (?, ?, 'local', ?, NULL, ?)""",
            (str(uuid.uuid4()), user_id, MIGRATION_AUTH_KEY, now),
        )
        return user_id


def migration_user_id() -> str:
    return initialize_store()


def _create_session(connection: sqlite3.Connection, user_id: str, device_id: str | None) -> SessionIdentity:
    now = _now()
    actual_device_id = device_id or str(uuid.uuid4())
    existing_device = connection.execute(
        "SELECT user_id FROM devices WHERE id = ?", (actual_device_id,),
    ).fetchone()
    if existing_device and str(existing_device["user_id"]) != user_id:
        actual_device_id = str(uuid.uuid4())
        existing_device = None
    if existing_device:
        connection.execute(
            "UPDATE devices SET last_seen_at = ? WHERE id = ?", (now, actual_device_id),
        )
    else:
        connection.execute(
            "INSERT INTO devices(id, user_id, label, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
            (actual_device_id, user_id, "本机浏览器", now, now),
        )
    session_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    connection.execute(
        """INSERT INTO sessions(
               id, user_id, device_id, token_hash, auth_provider,
               created_at, last_seen_at, expires_at
           ) VALUES (?, ?, ?, ?, 'local', ?, ?, ?)""",
        (session_id, user_id, actual_device_id, _token_hash(token), now, now, now + SESSION_TTL_SECONDS),
    )
    connection.execute(
        """UPDATE sessions SET revoked_at=? WHERE id IN (
               SELECT id FROM sessions WHERE user_id=? AND revoked_at IS NULL
               ORDER BY last_seen_at DESC LIMIT -1 OFFSET ?
           )""",
        (now, user_id, MAX_ACTIVE_SESSIONS_PER_USER),
    )
    return SessionIdentity(user_id, session_id, actual_device_id, "local", token)


def resolve_or_bootstrap_session(token: str | None, device_id: str | None) -> SessionIdentity:
    default_user_id = initialize_store()
    now = _now()
    with _connect() as connection:
        if token:
            row = connection.execute(
                """SELECT id, user_id, device_id, auth_provider FROM sessions
                   WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?""",
                (_token_hash(token), now),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now, row["id"]),
                )
                connection.execute(
                    "UPDATE devices SET last_seen_at = ? WHERE id = ?", (now, row["device_id"]),
                )
                return SessionIdentity(
                    str(row["user_id"]), str(row["id"]), str(row["device_id"]),
                    str(row["auth_provider"]), None,
                )
        return _create_session(connection, default_user_id, device_id)


def create_local_user(display_name: str, username: str, password: str) -> SessionIdentity:
    initialize_store()
    normalized = username.strip().casefold()
    if len(normalized) < 2:
        raise ValueError("用户名至少需要 2 个字符")
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    now = _now()
    user_id = str(uuid.uuid4())
    with _connect() as connection:
        if connection.execute(
            "SELECT 1 FROM auth_identities WHERE provider = 'local' AND provider_subject = ?",
            (normalized,),
        ).fetchone():
            raise ConflictError("用户名已存在")
        connection.execute(
            "INSERT INTO users(id, display_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, display_name.strip() or username.strip(), now, now),
        )
        connection.execute(
            """INSERT INTO auth_identities(
                   id, user_id, provider, provider_subject, password_hash, created_at
               ) VALUES (?, ?, 'local', ?, ?, ?)""",
            (str(uuid.uuid4()), user_id, normalized, _password_hash(password), now),
        )
        return _create_session(connection, user_id, None)


def login_local_user(username: str, password: str, device_id: str | None) -> SessionIdentity:
    initialize_store()
    normalized = username.strip().casefold()
    with _connect() as connection:
        row = connection.execute(
            """SELECT a.user_id, a.password_hash FROM auth_identities AS a
               JOIN users AS u ON u.id = a.user_id
               WHERE a.provider = 'local' AND a.provider_subject = ? AND u.disabled_at IS NULL""",
            (normalized,),
        ).fetchone()
        if not row or not row["password_hash"] or not _password_matches(password, str(row["password_hash"])):
            raise AuthenticationError("用户名或密码不正确")
        return _create_session(connection, str(row["user_id"]), device_id)


def revoke_session(session_id: str, user_id: str) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE sessions SET revoked_at = ? WHERE id = ? AND user_id = ?",
            (_now(), session_id, user_id),
        )


def list_user_sessions(user_id: str) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            """SELECT s.id,s.device_id,d.label,s.auth_provider,s.created_at,s.last_seen_at,s.expires_at
               FROM sessions s JOIN devices d ON d.id=s.device_id
               WHERE s.user_id=? AND s.revoked_at IS NULL AND s.expires_at>?
               ORDER BY s.last_seen_at DESC LIMIT ?""",
            (user_id, _now(), MAX_ACTIVE_SESSIONS_PER_USER),
        ).fetchall()
    return [dict(row) for row in rows]


def revoke_other_session(user_id: str, session_id: str, current_session_id: str) -> bool:
    if session_id == current_session_id:
        raise ValueError("不能在这里注销当前会话")
    with _connect() as connection:
        return bool(connection.execute(
            "UPDATE sessions SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
            (_now(), session_id, user_id),
        ).rowcount)


def revoke_other_sessions(user_id: str, current_session_id: str) -> int:
    with _connect() as connection:
        return int(connection.execute(
            "UPDATE sessions SET revoked_at=? WHERE user_id=? AND id<>? AND revoked_at IS NULL",
            (_now(), user_id, current_session_id),
        ).rowcount)


def change_local_password(user_id: str, current_password: str, new_password: str) -> None:
    if len(new_password) < 8:
        raise ValueError("新密码至少需要 8 个字符")
    with _connect() as connection:
        row = connection.execute(
            """SELECT id,password_hash,provider_subject FROM auth_identities
               WHERE user_id=? AND provider='local' ORDER BY created_at LIMIT 1""",
            (user_id,),
        ).fetchone()
        if not row or row["provider_subject"] == MIGRATION_AUTH_KEY or not row["password_hash"]:
            raise ValueError("本机访客模式没有可修改的密码")
        if not _password_matches(current_password, str(row["password_hash"])):
            raise AuthenticationError("当前密码不正确")
        connection.execute(
            "UPDATE auth_identities SET password_hash=? WHERE id=? AND user_id=?",
            (_password_hash(new_password), row["id"], user_id),
        )


def user_profile(user_id: str) -> dict:
    with _connect() as connection:
        row = connection.execute(
            """SELECT u.id, u.display_name, u.avatar_filename, u.created_at, u.updated_at,
                      a.provider_subject AS username
               FROM users AS u
               LEFT JOIN auth_identities AS a
                 ON a.user_id = u.id AND a.provider = 'local'
               WHERE u.id = ? AND u.disabled_at IS NULL
               ORDER BY CASE WHEN a.provider_subject = ? THEN 1 ELSE 0 END
               LIMIT 1""",
            (user_id, MIGRATION_AUTH_KEY),
        ).fetchone()
        if not row:
            raise AuthenticationError()
        return {
            "user_id": str(row["id"]),
            "display_name": str(row["display_name"]),
            "avatar_url": f"/api/me/avatar?v={int(float(row['updated_at']) * 1000)}" if row["avatar_filename"] else None,
            "created_at": float(row["created_at"]),
            "username": None if row["username"] == MIGRATION_AUTH_KEY else row["username"],
            "is_guest": row["username"] == MIGRATION_AUTH_KEY,
        }


def update_user_profile(user_id: str, display_name: str | None = None, avatar_filename: str | None = None) -> dict:
    normalized_name = display_name.strip() if display_name is not None else None
    if normalized_name is not None and not normalized_name:
        raise ValueError("昵称不能为空")
    if normalized_name is not None and len(normalized_name) > 40:
        raise ValueError("昵称不能超过 40 个字符")
    with _connect() as connection:
        if not connection.execute("SELECT 1 FROM users WHERE id = ? AND disabled_at IS NULL", (user_id,)).fetchone():
            raise AuthenticationError()
        fields: list[str] = []
        values: list[object] = []
        if normalized_name is not None:
            fields.append("display_name = ?")
            values.append(normalized_name)
        if avatar_filename is not None:
            fields.append("avatar_filename = ?")
            values.append(avatar_filename)
        if fields:
            fields.append("updated_at = ?")
            values.append(_now())
            values.append(user_id)
            connection.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
    return user_profile(user_id)


def avatar_filename(user_id: str) -> str | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT avatar_filename FROM users WHERE id = ? AND disabled_at IS NULL", (user_id,),
        ).fetchone()
        return str(row["avatar_filename"]) if row and row["avatar_filename"] else None
