from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import CloudConfig


ACCESS_TTL = 15 * 60
REFRESH_TTL = 30 * 24 * 60 * 60
EMAIL_VERIFY_TTL = 24 * 60 * 60
PASSWORD_RESET_TTL = 60 * 60
HANDOFF_TTL = 5 * 60
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
APPEND_ONLY_TYPES = {"learning_event", "review_log"}
FORK_ON_CONFLICT_TYPES = {"note"}
ALLOWED_TYPES = {
    "knowledge_item", "learning_event", "note", "card", "review_log",
    "bookmark", "reading_progress", "preference", "lexeme",
}


class CloudAuthError(ValueError):
    pass


class CloudConflictError(ValueError):
    pass


class CloudRepository:
    def __init__(self, config: CloudConfig):
        self.config = config
        self.path = config.database_path
        self._lock = threading.Lock()
        self._initialized = False

    def _raw_connection(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            connection = self._raw_connection()
            try:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users(
                        id TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE,display_name TEXT NOT NULL,
                        password_hash TEXT,email_verified_at REAL,created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,disabled_at REAL,role TEXT NOT NULL DEFAULT 'user'
                    );
                    CREATE TABLE IF NOT EXISTS oauth_identities(
                        id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        provider TEXT NOT NULL,subject TEXT NOT NULL,email TEXT NOT NULL,
                        created_at REAL NOT NULL,UNIQUE(provider,subject)
                    );
                    CREATE TABLE IF NOT EXISTS refresh_sessions(
                        id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        family_id TEXT NOT NULL,device_id TEXT NOT NULL,device_name TEXT NOT NULL,
                        token_hash TEXT NOT NULL UNIQUE,created_at REAL NOT NULL,last_seen_at REAL NOT NULL,
                        expires_at REAL NOT NULL,rotated_at REAL,revoked_at REAL
                    );
                    CREATE INDEX IF NOT EXISTS idx_cloud_sessions_user ON refresh_sessions(user_id,last_seen_at DESC);
                    CREATE TABLE IF NOT EXISTS access_tokens(
                        id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        session_id TEXT NOT NULL REFERENCES refresh_sessions(id) ON DELETE CASCADE,
                        token_hash TEXT NOT NULL UNIQUE,created_at REAL NOT NULL,expires_at REAL NOT NULL,
                        revoked_at REAL
                    );
                    CREATE INDEX IF NOT EXISTS idx_cloud_access_hash ON access_tokens(token_hash,expires_at);
                    CREATE TABLE IF NOT EXISTS action_tokens(
                        id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        kind TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,used_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS oauth_states(
                        state_hash TEXT PRIMARY KEY,provider TEXT NOT NULL,code_verifier TEXT NOT NULL,
                        nonce TEXT NOT NULL,return_url TEXT NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS handoff_codes(
                        code_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        provider TEXT NOT NULL,device_id TEXT NOT NULL,device_name TEXT NOT NULL,
                        created_at REAL NOT NULL,expires_at REAL NOT NULL,used_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS sync_entities(
                        user_id TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,
                        version INTEGER NOT NULL,payload_json TEXT NOT NULL,source_device_id TEXT NOT NULL,
                        updated_at REAL NOT NULL,deleted_at REAL,conflict_group TEXT,
                        PRIMARY KEY(user_id,entity_type,entity_id)
                    );
                    CREATE TABLE IF NOT EXISTS sync_changes(
                        cursor INTEGER PRIMARY KEY AUTOINCREMENT,change_id TEXT NOT NULL,user_id TEXT NOT NULL,
                        source_device_id TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,
                        operation TEXT NOT NULL,base_version INTEGER,version INTEGER NOT NULL,
                        payload_json TEXT NOT NULL,updated_at REAL NOT NULL,deleted_at REAL,
                        conflict_group TEXT,created_at REAL NOT NULL,UNIQUE(user_id,change_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_cloud_changes_user_cursor ON sync_changes(user_id,cursor);
                    CREATE TABLE IF NOT EXISTS sync_device_cursors(
                        user_id TEXT NOT NULL,device_id TEXT NOT NULL,pull_cursor INTEGER NOT NULL DEFAULT 0,
                        updated_at REAL NOT NULL,PRIMARY KEY(user_id,device_id)
                    );
                    """
                )
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(users)")}
                if "role" not in columns:
                    connection.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
                connection.commit()
                self._initialized = True
            finally:
                connection.close()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        connection = self._raw_connection()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def normalize_email(value: str) -> str:
        email = value.strip().casefold()
        if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
            raise ValueError("邮箱地址格式不正确")
        return email

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _password_hash(password: str, salt: bytes | None = None) -> str:
        if len(password) < 10:
            raise ValueError("密码至少需要 10 个字符")
        actual_salt = salt or secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=actual_salt, n=2**15, r=8, p=1,
            maxmem=64 * 1024 * 1024,
        )
        return f"scrypt${actual_salt.hex()}${digest.hex()}"

    @classmethod
    def _password_matches(cls, password: str, encoded: str | None) -> bool:
        if not encoded:
            return False
        try:
            algorithm, salt_hex, digest_hex = encoded.split("$", 2)
            if algorithm != "scrypt":
                return False
            actual = cls._password_hash(password, bytes.fromhex(salt_hex)).split("$", 2)[2]
            return hmac.compare_digest(actual, digest_hex)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _public_user(row: sqlite3.Row | dict) -> dict:
        return {
            "id": str(row["id"]), "email": str(row["email"]),
            "display_name": str(row["display_name"]),
            "email_verified": row["email_verified_at"] is not None,
            "created_at": float(row["created_at"]),
            "role": str(row["role"] or "user"),
        }

    def ensure_admin(self) -> dict | None:
        email = self.config.admin_email.strip()
        password = self.config.admin_password
        if not email and not password:
            return None
        if not email or not password:
            raise RuntimeError("BINGDU_CLOUD_ADMIN_EMAIL 和 BINGDU_CLOUD_ADMIN_PASSWORD 必须同时设置")
        normalized = self.normalize_email(email)
        if len(password) < 12:
            raise RuntimeError("管理员密码至少需要 12 个字符")
        now = time.time()
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE email=?", (normalized,)).fetchone()
            if row:
                connection.execute(
                    """UPDATE users SET role='admin',disabled_at=NULL,password_hash=?,
                       email_verified_at=COALESCE(email_verified_at,?),updated_at=? WHERE id=?""",
                    (self._password_hash(password), now, now, row["id"]),
                )
            else:
                user_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO users(
                        id,email,display_name,password_hash,email_verified_at,created_at,updated_at,role
                    ) VALUES(?,?,?,?,?,?,?,'admin')""",
                    (user_id, normalized, self.config.admin_display_name[:40], self._password_hash(password), now, now, now),
                )
            row = connection.execute("SELECT * FROM users WHERE email=?", (normalized,)).fetchone()
        return self._public_user(row)

    def update_display_name(self, user_id: str, display_name: str) -> dict:
        normalized = display_name.strip()
        if not normalized:
            raise ValueError("昵称不能为空")
        if len(normalized) > 40:
            raise ValueError("昵称不能超过 40 个字符")
        with self.connect() as connection:
            connection.execute(
                "UPDATE users SET display_name=?,updated_at=? WHERE id=?",
                (normalized, time.time(), user_id),
            )
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("账号不存在")
        return self._public_user(row)

    def list_users(self, query: str = "", limit: int = 50, offset: int = 0) -> dict:
        needle = query.strip().casefold()
        where = "WHERE lower(email) LIKE ? OR lower(display_name) LIKE ?" if needle else ""
        params: tuple[object, ...] = (f"%{needle}%", f"%{needle}%") if needle else ()
        with self.connect() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) FROM users {where}", params).fetchone()[0])
            rows = connection.execute(
                f"""SELECT u.*,
                    (SELECT COUNT(*) FROM refresh_sessions s WHERE s.user_id=u.id AND s.revoked_at IS NULL AND s.rotated_at IS NULL AND s.expires_at>?) AS active_sessions,
                    (SELECT COUNT(*) FROM sync_entities e WHERE e.user_id=u.id AND e.deleted_at IS NULL) AS sync_entities
                    FROM users u {where} ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",
                (time.time(), *params, max(1, min(limit, 200)), max(0, offset)),
            ).fetchall()
        return {"items": [{**self._public_user(row), "disabled": row["disabled_at"] is not None,
                            "active_sessions": int(row["active_sessions"]), "sync_entities": int(row["sync_entities"])} for row in rows],
                "total": total, "limit": limit, "offset": offset}

    def set_user_disabled(self, actor_user_id: str, user_id: str, disabled: bool) -> dict:
        if actor_user_id == user_id and disabled:
            raise ValueError("不能禁用当前管理员账号")
        now = time.time()
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise ValueError("账号不存在")
            connection.execute("UPDATE users SET disabled_at=?,updated_at=? WHERE id=?", (now if disabled else None, now, user_id))
            if disabled:
                connection.execute("UPDATE refresh_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, user_id))
                connection.execute("UPDATE access_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, user_id))
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return {**self._public_user(row), "disabled": row["disabled_at"] is not None}

    def register(self, email: str, password: str, display_name: str, device_id: str, device_name: str) -> dict:
        normalized = self.normalize_email(email)
        name = display_name.strip()
        if not name or len(name) > 40:
            raise ValueError("昵称需要 1 到 40 个字符")
        now = time.time()
        user_id = str(uuid.uuid4())
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM users WHERE email=?", (normalized,)).fetchone():
                raise CloudConflictError("该邮箱已经注册")
            connection.execute(
                "INSERT INTO users(id,email,display_name,password_hash,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (user_id, normalized, name, self._password_hash(password), now, now),
            )
            tokens = self._issue_tokens(connection, user_id, device_id, device_name)
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return {"user": self._public_user(row), **tokens}

    def login(self, email: str, password: str, device_id: str, device_name: str) -> dict:
        normalized = self.normalize_email(email)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE email=? AND disabled_at IS NULL", (normalized,),
            ).fetchone()
            if not row or not self._password_matches(password, row["password_hash"]):
                raise CloudAuthError("邮箱或密码不正确")
            tokens = self._issue_tokens(connection, str(row["id"]), device_id, device_name)
        return {"user": self._public_user(row), **tokens}

    def _issue_tokens(
        self, connection: sqlite3.Connection, user_id: str, device_id: str,
        device_name: str, family_id: str | None = None,
    ) -> dict:
        now = time.time()
        refresh = secrets.token_urlsafe(48)
        access = secrets.token_urlsafe(40)
        session_id = str(uuid.uuid4())
        family = family_id or str(uuid.uuid4())
        connection.execute(
            """INSERT INTO refresh_sessions(
                id,user_id,family_id,device_id,device_name,token_hash,created_at,last_seen_at,expires_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (session_id, user_id, family, device_id[:200], (device_name or "冰读设备")[:200],
             self._token_hash(refresh), now, now, now + REFRESH_TTL),
        )
        connection.execute(
            "INSERT INTO access_tokens(id,user_id,session_id,token_hash,created_at,expires_at) VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), user_id, session_id, self._token_hash(access), now, now + ACCESS_TTL),
        )
        return {
            "access_token": access, "refresh_token": refresh, "token_type": "bearer",
            "expires_in": ACCESS_TTL, "refresh_expires_in": REFRESH_TTL,
        }

    def refresh(self, refresh_token: str) -> dict:
        now = time.time()
        token_hash = self._token_hash(refresh_token)
        replay_detected = False
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM refresh_sessions WHERE token_hash=?", (token_hash,),
            ).fetchone()
            if not row:
                raise CloudAuthError("刷新凭据无效")
            if row["rotated_at"] is not None:
                connection.execute(
                    "UPDATE refresh_sessions SET revoked_at=? WHERE family_id=? AND revoked_at IS NULL",
                    (now, row["family_id"]),
                )
                replay_detected = True
            elif row["revoked_at"] is not None or float(row["expires_at"]) <= now:
                raise CloudAuthError("刷新凭据已过期或撤销")
            if not replay_detected:
                connection.execute("UPDATE refresh_sessions SET rotated_at=?,last_seen_at=? WHERE id=?", (now, now, row["id"]))
                connection.execute("UPDATE access_tokens SET revoked_at=? WHERE session_id=? AND revoked_at IS NULL", (now, row["id"]))
                tokens = self._issue_tokens(
                    connection, str(row["user_id"]), str(row["device_id"]),
                    str(row["device_name"]), str(row["family_id"]),
                )
                user = connection.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        if replay_detected:
            raise CloudAuthError("检测到刷新凭据重复使用，相关设备会话已撤销")
        return {"user": self._public_user(user), **tokens}

    def authenticate(self, access_token: str) -> tuple[dict, str]:
        now = time.time()
        with self.connect() as connection:
            row = connection.execute(
                """SELECT u.*,a.session_id,s.device_id FROM access_tokens a
                   JOIN users u ON u.id=a.user_id JOIN refresh_sessions s ON s.id=a.session_id
                   WHERE a.token_hash=? AND a.revoked_at IS NULL AND a.expires_at>?
                     AND s.revoked_at IS NULL AND s.expires_at>? AND u.disabled_at IS NULL""",
                (self._token_hash(access_token), now, now),
            ).fetchone()
            if not row:
                raise CloudAuthError("访问凭据无效或已过期")
            connection.execute("UPDATE refresh_sessions SET last_seen_at=? WHERE id=?", (now, row["session_id"]))
        return self._public_user(row), str(row["device_id"])

    def logout(self, access_token: str) -> None:
        now = time.time()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM access_tokens WHERE token_hash=?", (self._token_hash(access_token),),
            ).fetchone()
            if row:
                connection.execute("UPDATE access_tokens SET revoked_at=? WHERE session_id=? AND revoked_at IS NULL", (now, row["session_id"]))
                connection.execute("UPDATE refresh_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (now, row["session_id"]))

    def sessions(self, user_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT id,device_id,device_name,created_at,last_seen_at,expires_at
                   FROM refresh_sessions WHERE user_id=? AND revoked_at IS NULL AND rotated_at IS NULL
                     AND expires_at>? ORDER BY last_seen_at DESC""", (user_id, time.time()),
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke_session(self, user_id: str, session_id: str) -> bool:
        now = time.time()
        with self.connect() as connection:
            changed = connection.execute(
                "UPDATE refresh_sessions SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
                (now, session_id, user_id),
            ).rowcount
            connection.execute("UPDATE access_tokens SET revoked_at=? WHERE session_id=? AND revoked_at IS NULL", (now, session_id))
        return bool(changed)

    def create_action_token(self, user_id: str, kind: str, ttl: int) -> str:
        token = secrets.token_urlsafe(40)
        now = time.time()
        with self.connect() as connection:
            connection.execute("DELETE FROM action_tokens WHERE expires_at<=? OR used_at IS NOT NULL", (now,))
            connection.execute(
                "INSERT INTO action_tokens(id,user_id,kind,token_hash,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), user_id, kind, self._token_hash(token), now, now + ttl),
            )
        return token

    def request_action_token(self, email: str, kind: str) -> tuple[dict, str] | None:
        normalized = self.normalize_email(email)
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE email=? AND disabled_at IS NULL", (normalized,)).fetchone()
        if not row:
            return None
        ttl = EMAIL_VERIFY_TTL if kind == "verify_email" else PASSWORD_RESET_TTL
        return self._public_user(row), self.create_action_token(str(row["id"]), kind, ttl)

    def verify_email(self, token: str) -> dict:
        user_id = self._consume_action_token(token, "verify_email")
        now = time.time()
        with self.connect() as connection:
            connection.execute("UPDATE users SET email_verified_at=COALESCE(email_verified_at,?),updated_at=? WHERE id=?", (now, now, user_id))
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._public_user(row)

    def reset_password(self, token: str, new_password: str) -> dict:
        password_hash = self._password_hash(new_password)
        user_id = self._consume_action_token(token, "reset_password")
        now = time.time()
        with self.connect() as connection:
            connection.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (password_hash, now, user_id))
            connection.execute("UPDATE refresh_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, user_id))
            connection.execute("UPDATE access_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, user_id))
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._public_user(row)

    def _consume_action_token(self, token: str, kind: str) -> str:
        now = time.time()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id,user_id FROM action_tokens WHERE token_hash=? AND kind=? AND used_at IS NULL AND expires_at>?",
                (self._token_hash(token), kind, now),
            ).fetchone()
            if not row:
                raise CloudAuthError("链接无效或已经过期")
            connection.execute("UPDATE action_tokens SET used_at=? WHERE id=?", (now, row["id"]))
            return str(row["user_id"])

    def upsert_oauth_user(self, provider: str, subject: str, email: str, display_name: str) -> dict:
        normalized = self.normalize_email(email)
        now = time.time()
        with self.connect() as connection:
            identity = connection.execute(
                "SELECT user_id FROM oauth_identities WHERE provider=? AND subject=?", (provider, subject),
            ).fetchone()
            if identity:
                user_id = str(identity["user_id"])
            else:
                user = connection.execute("SELECT id FROM users WHERE email=?", (normalized,)).fetchone()
                user_id = str(user["id"]) if user else str(uuid.uuid4())
                if not user:
                    connection.execute(
                        "INSERT INTO users(id,email,display_name,email_verified_at,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                        (user_id, normalized, display_name[:40] or normalized.split("@", 1)[0], now, now, now),
                    )
                connection.execute(
                    "INSERT INTO oauth_identities(id,user_id,provider,subject,email,created_at) VALUES(?,?,?,?,?,?)",
                    (str(uuid.uuid4()), user_id, provider, subject, normalized, now),
                )
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._public_user(row)

    def create_oauth_state(self, provider: str, verifier: str, nonce: str, return_url: str) -> str:
        state = secrets.token_urlsafe(32)
        now = time.time()
        with self.connect() as connection:
            connection.execute("DELETE FROM oauth_states WHERE expires_at<=?", (now,))
            connection.execute(
                "INSERT INTO oauth_states(state_hash,provider,code_verifier,nonce,return_url,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                (self._token_hash(state), provider, verifier, nonce, return_url, now, now + 10 * 60),
            )
        return state

    def consume_oauth_state(self, state: str, provider: str) -> dict:
        now = time.time()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM oauth_states WHERE state_hash=? AND provider=? AND expires_at>?",
                (self._token_hash(state), provider, now),
            ).fetchone()
            if not row:
                raise CloudAuthError("第三方登录状态无效或已经过期")
            connection.execute("DELETE FROM oauth_states WHERE state_hash=?", (row["state_hash"],))
        return dict(row)

    def create_handoff(self, user_id: str, provider: str, device_id: str, device_name: str) -> str:
        code = secrets.token_urlsafe(40)
        now = time.time()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO handoff_codes(code_hash,user_id,provider,device_id,device_name,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                (self._token_hash(code), user_id, provider, device_id[:200], device_name[:200], now, now + HANDOFF_TTL),
            )
        return code

    def exchange_handoff(self, code: str) -> dict:
        now = time.time()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM handoff_codes WHERE code_hash=? AND used_at IS NULL AND expires_at>?",
                (self._token_hash(code), now),
            ).fetchone()
            if not row:
                raise CloudAuthError("登录交接码无效或已经过期")
            connection.execute("UPDATE handoff_codes SET used_at=? WHERE code_hash=?", (now, row["code_hash"]))
            user = connection.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
            tokens = self._issue_tokens(connection, str(row["user_id"]), str(row["device_id"]), str(row["device_name"]))
        return {"user": self._public_user(user), **tokens}

    def _append_change(self, connection: sqlite3.Connection, user_id: str, device_id: str, mutation: dict, entity_id: str, version: int, conflict_group: str | None = None) -> None:
        deleted_at = float(mutation["deleted_at"]) if mutation.get("deleted_at") is not None else None
        operation = "delete" if deleted_at is not None or mutation.get("operation") == "delete" else "upsert"
        payload = mutation.get("payload") or {}
        updated_at = float(mutation.get("updated_at") or time.time())
        connection.execute(
            """INSERT INTO sync_changes(change_id,user_id,source_device_id,entity_type,entity_id,operation,
                base_version,version,payload_json,updated_at,deleted_at,conflict_group,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mutation["change_id"], user_id, device_id, mutation["entity_type"], entity_id, operation,
             mutation.get("base_version"), version, json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
             updated_at, deleted_at, conflict_group, time.time()),
        )
        connection.execute(
            """INSERT INTO sync_entities(user_id,entity_type,entity_id,version,payload_json,source_device_id,updated_at,deleted_at,conflict_group)
               VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,entity_type,entity_id) DO UPDATE SET
               version=excluded.version,payload_json=excluded.payload_json,source_device_id=excluded.source_device_id,
               updated_at=excluded.updated_at,deleted_at=excluded.deleted_at,conflict_group=excluded.conflict_group""",
            (user_id, mutation["entity_type"], entity_id, version,
             json.dumps(payload, ensure_ascii=False, separators=(",", ":")), device_id,
             updated_at, deleted_at, conflict_group),
        )

    def push_changes(self, user_id: str, device_id: str, mutations: list[dict]) -> dict:
        accepted, skipped, conflicts = [], [], []
        with self.connect() as connection:
            for mutation in mutations:
                entity_type = str(mutation["entity_type"])
                entity_id = str(mutation["entity_id"])
                if entity_type not in ALLOWED_TYPES:
                    raise ValueError(f"unsupported_entity:{entity_type}")
                duplicate = connection.execute(
                    "SELECT cursor FROM sync_changes WHERE user_id=? AND change_id=?", (user_id, mutation["change_id"]),
                ).fetchone()
                if duplicate:
                    skipped.append({"change_id": mutation["change_id"], "reason": "duplicate", "cursor": duplicate[0]})
                    continue
                current = connection.execute(
                    "SELECT * FROM sync_entities WHERE user_id=? AND entity_type=? AND entity_id=?",
                    (user_id, entity_type, entity_id),
                ).fetchone()
                incoming = mutation.get("payload") or {}
                current_payload = json.loads(current["payload_json"]) if current else None
                if current and entity_type in APPEND_ONLY_TYPES:
                    if current_payload == incoming:
                        skipped.append({"change_id": mutation["change_id"], "reason": "same_append_only_entity"})
                        continue
                    group = current["conflict_group"] or str(uuid.uuid4())
                    fork_id = f"{entity_id}@{device_id}@{str(mutation['change_id'])[:8]}"
                    connection.execute(
                        "UPDATE sync_entities SET conflict_group=? WHERE user_id=? AND entity_type=? AND entity_id=?",
                        (group, user_id, entity_type, entity_id),
                    )
                    self._append_change(connection, user_id, device_id, mutation, fork_id, 1, group)
                    conflicts.append({"entity_type": entity_type, "entity_id": entity_id, "fork_id": fork_id, "conflict_group": group})
                    continue
                base = mutation.get("base_version")
                if current and entity_type in FORK_ON_CONFLICT_TYPES and base is not None and int(base) != int(current["version"]) and current_payload != incoming:
                    group = current["conflict_group"] or str(uuid.uuid4())
                    fork_id = f"{entity_id}@{device_id}@{str(mutation['change_id'])[:8]}"
                    connection.execute(
                        "UPDATE sync_entities SET conflict_group=? WHERE user_id=? AND entity_type=? AND entity_id=?",
                        (group, user_id, entity_type, entity_id),
                    )
                    self._append_change(connection, user_id, device_id, mutation, fork_id, 1, group)
                    conflicts.append({"entity_type": entity_type, "entity_id": entity_id, "fork_id": fork_id, "conflict_group": group})
                    continue
                if current and float(mutation.get("updated_at") or 0) < float(current["updated_at"]):
                    skipped.append({"change_id": mutation["change_id"], "reason": "older_than_current", "current_version": current["version"]})
                    continue
                version = int(current["version"]) + 1 if current else 1
                self._append_change(connection, user_id, device_id, mutation, entity_id, version, current["conflict_group"] if current else None)
                accepted.append({"change_id": mutation["change_id"], "entity_id": entity_id, "version": version})
            cursor = int(connection.execute("SELECT COALESCE(MAX(cursor),0) FROM sync_changes WHERE user_id=?", (user_id,)).fetchone()[0])
        return {"accepted": accepted, "skipped": skipped, "conflicts": conflicts, "cursor": cursor}

    def pull_changes(self, user_id: str, device_id: str, after: int, limit: int) -> dict:
        safe_limit = min(1000, max(1, limit))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sync_changes WHERE user_id=? AND cursor>? ORDER BY cursor LIMIT ?",
                (user_id, max(0, after), safe_limit + 1),
            ).fetchall()
            has_more = len(rows) > safe_limit
            rows = rows[:safe_limit]
            cursor = int(rows[-1]["cursor"]) if rows else max(0, after)
            connection.execute(
                """INSERT INTO sync_device_cursors(user_id,device_id,pull_cursor,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(user_id,device_id) DO UPDATE SET pull_cursor=MAX(pull_cursor,excluded.pull_cursor),updated_at=excluded.updated_at""",
                (user_id, device_id, cursor, time.time()),
            )
        changes = []
        for row in rows:
            value = dict(row)
            value["payload"] = json.loads(value.pop("payload_json"))
            changes.append(value)
        return {"changes": changes, "cursor": cursor, "has_more": has_more}

    def conflicts(self, user_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sync_entities WHERE user_id=? AND conflict_group IS NOT NULL ORDER BY conflict_group,updated_at DESC",
                (user_id,),
            ).fetchall()
        groups: dict[str, dict] = {}
        for row in rows:
            group_id = str(row["conflict_group"])
            group = groups.setdefault(group_id, {"id": group_id, "entity_type": row["entity_type"], "versions": []})
            value = dict(row)
            value["payload"] = json.loads(value.pop("payload_json"))
            group["versions"].append(value)
        return list(groups.values())

    def resolve_conflict(self, user_id: str, device_id: str, group_id: str, winner_id: str) -> dict:
        now = time.time()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sync_entities WHERE user_id=? AND conflict_group=?", (user_id, group_id),
            ).fetchall()
            winner = next((row for row in rows if row["entity_id"] == winner_id), None)
            if not winner:
                raise KeyError("conflict_not_found")
            base_id = min((str(row["entity_id"]).split("@", 1)[0] for row in rows), key=len)
            payload = json.loads(winner["payload_json"])
            mutation = {
                "change_id": f"resolve:{group_id}:{uuid.uuid4()}", "entity_type": winner["entity_type"],
                "entity_id": base_id, "payload": payload, "updated_at": now,
            }
            current = connection.execute(
                "SELECT version FROM sync_entities WHERE user_id=? AND entity_type=? AND entity_id=?",
                (user_id, winner["entity_type"], base_id),
            ).fetchone()
            self._append_change(connection, user_id, device_id, mutation, base_id, int(current["version"]) + 1 if current else 1, None)
            loser_ids = [str(row["entity_id"]) for row in rows if str(row["entity_id"]) != base_id]
            if loser_ids:
                placeholders = ",".join("?" for _ in loser_ids)
                connection.execute(
                    f"DELETE FROM sync_entities WHERE user_id=? AND entity_type=? AND entity_id IN ({placeholders})",
                    (user_id, winner["entity_type"], *loser_ids),
                )
            connection.execute(
                "UPDATE sync_entities SET conflict_group=NULL WHERE user_id=? AND entity_type=? AND entity_id=?",
                (user_id, winner["entity_type"], base_id),
            )
        return {"resolved": True, "entity_type": winner["entity_type"], "entity_id": base_id, "payload": payload}

    def sync_status(self, user_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS entities,SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS tombstones,
                   COUNT(DISTINCT conflict_group) AS conflicts FROM sync_entities WHERE user_id=?""", (user_id,),
            ).fetchone()
            cursor = int(connection.execute("SELECT COALESCE(MAX(cursor),0) FROM sync_changes WHERE user_id=?", (user_id,)).fetchone()[0])
            devices = int(connection.execute("SELECT COUNT(*) FROM sync_device_cursors WHERE user_id=?", (user_id,)).fetchone()[0])
        return {
            "cursor": cursor, "entities": int(row["entities"] or 0),
            "tombstones": int(row["tombstones"] or 0), "conflicts": int(row["conflicts"] or 0),
            "devices": devices, "content_scope": "learning-only",
        }

