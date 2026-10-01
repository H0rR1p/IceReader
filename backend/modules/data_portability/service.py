import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ...paths import DATA_DIR


BACKUP_SCHEMA_VERSION = 1
MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024
RESOURCE_PATTERN = re.compile(r"/api/assets/([0-9a-f]{20})(?:/|$)")

DATABASE_SPECS = {
    "library": {
        "path": DATA_DIR / "library.sqlite3",
        "tables": {
            "records": "owner_user_id", "user_library_items": "user_id",
            "user_book_progress": "user_id", "user_bookmarks": "user_id", "outbox": "owner_user_id",
        },
    },
    "learning": {
        "path": DATA_DIR / "learning.sqlite3",
        "tables": {
            "learning_events": "user_id", "user_knowledge_states": "user_id",
            "card_candidates": "user_id", "notes": "user_id", "cards": "user_id",
            "memory_states": "user_id", "review_logs": "user_id", "saved_card_views": "user_id",
            "card_undo_log": "user_id", "card_preferences": "user_id",
            "activity_windows": "user_id", "daily_learning_stats": "user_id",
        },
    },
    "ai": {
        "path": DATA_DIR / "ai.sqlite3",
        "tables": {"usage": "user_id"},
    },
}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (table,)).fetchone() is not None


def _rows(connection: sqlite3.Connection, table: str, user_column: str, user_id: str) -> list[dict]:
    if not _table_exists(connection, table):
        return []
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}" WHERE "{user_column}"=?', (user_id,))]


def _export_payload(user_id: str) -> dict[str, Any]:
    databases: dict[str, dict[str, list[dict]]] = {}
    knowledge_ids: set[str] = set()
    for name, spec in DATABASE_SPECS.items():
        path = Path(spec["path"])
        tables: dict[str, list[dict]] = {}
        if path.is_file():
            with sqlite3.connect(path) as connection:
                for table, user_column in spec["tables"].items():
                    table_rows = _rows(connection, table, user_column, user_id)
                    tables[table] = table_rows
                    for row in table_rows:
                        if row.get("knowledge_item_id"):
                            knowledge_ids.add(str(row["knowledge_item_id"]))
                if name == "learning" and knowledge_ids and _table_exists(connection, "knowledge_items"):
                    placeholders = ",".join("?" for _ in knowledge_ids)
                    connection.row_factory = sqlite3.Row
                    tables["knowledge_items"] = [dict(row) for row in connection.execute(
                        f"SELECT * FROM knowledge_items WHERE id IN ({placeholders})", tuple(knowledge_ids),
                    )]
        databases[name] = tables
    return {"source_user_id": user_id, "databases": databases}


def _safe_arcname(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("备份中包含不安全的文件路径")
    return str(path)


def _add_file(archive: zipfile.ZipFile, source: Path, arcname: str, checksums: dict[str, str]) -> None:
    safe_name = _safe_arcname(arcname)
    digest = hashlib.sha256()
    with source.open("rb") as reader, archive.open(safe_name, "w") as writer:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            writer.write(chunk)
    checksums[safe_name] = digest.hexdigest()


def create_backup(user_id: str, destination: Path | None = None) -> Path:
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if destination is None:
        destination = backup_dir / f".download-{uuid.uuid4().hex}.zip"
    payload = _export_payload(user_id)
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    checksums = {"data.json": hashlib.sha256(payload_bytes).hexdigest()}
    books_dir = DATA_DIR / "books"
    resource_keys = {
        match.group(1)
        for row in payload["databases"].get("library", {}).get("records", [])
        for match in RESOURCE_PATTERN.finditer(str(row.get("payload") or ""))
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("data.json", payload_bytes)
        for resource_key in sorted(resource_keys):
            resource_dir = books_dir / resource_key
            if resource_dir.is_dir():
                for source in resource_dir.rglob("*"):
                    if source.is_file():
                        _add_file(archive, source, f"files/books/{resource_key}/{source.relative_to(resource_dir).as_posix()}", checksums)
        custom_cover_dir = books_dir / "custom-covers" / user_id
        if custom_cover_dir.is_dir():
            for source in custom_cover_dir.iterdir():
                if source.is_file():
                    _add_file(archive, source, f"files/custom-covers/{source.name}", checksums)
        user_dir = DATA_DIR / "users" / user_id
        if user_dir.is_dir():
            for source in user_dir.rglob("*"):
                if source.is_file():
                    _add_file(archive, source, f"files/user/{source.relative_to(user_dir).as_posix()}", checksums)
        voice_dir = DATA_DIR / "voice" / "users" / user_id
        if voice_dir.is_dir():
            for source in voice_dir.rglob("*"):
                if source.is_file():
                    _add_file(archive, source, f"files/voice/{source.relative_to(voice_dir).as_posix()}", checksums)
        manifest = {
            "format": "bingdu-backup", "schema_version": BACKUP_SCHEMA_VERSION,
            "created_at": time.time(), "files": checksums,
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return destination


def _validate_archive(path: Path) -> tuple[dict, dict]:
    if path.stat().st_size > MAX_BACKUP_BYTES:
        raise ValueError("备份文件超过 2 GB 限制")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if sum(info.file_size for info in archive.infolist()) > MAX_BACKUP_BYTES:
            raise ValueError("备份解压后的内容超过 2 GB 限制")
        for name in names:
            _safe_arcname(name)
        if "manifest.json" not in names or "data.json" not in names:
            raise ValueError("这不是完整的冰读备份")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != "bingdu-backup" or int(manifest.get("schema_version", 0)) != BACKUP_SCHEMA_VERSION:
            raise ValueError("备份格式或版本不受支持")
        for name, expected in manifest.get("files", {}).items():
            if name not in names:
                raise ValueError(f"备份文件校验失败：{name}")
            digest = hashlib.sha256()
            with archive.open(name) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise ValueError(f"备份文件校验失败：{name}")
        payload = json.loads(archive.read("data.json"))
    if not isinstance(payload.get("databases"), dict):
        raise ValueError("备份数据结构不完整")
    return manifest, payload


def _restore_database(name: str, user_id: str, tables: dict[str, list[dict]]) -> int:
    spec = DATABASE_SPECS[name]
    path = Path(spec["path"])
    if not path.is_file():
        raise ValueError(f"本地数据库尚未初始化：{name}")
    restored = 0
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for table, user_column in reversed(tuple(spec["tables"].items())):
            if _table_exists(connection, table):
                connection.execute(f'DELETE FROM "{table}" WHERE "{user_column}"=?', (user_id,))
        ordered_tables = list(spec["tables"])
        if name == "learning":
            ordered_tables = ["knowledge_items", *ordered_tables]
        for table in ordered_tables:
            rows = tables.get(table, [])
            if not rows or not _table_exists(connection, table):
                continue
            actual_columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            for row in rows:
                clean = {key: value for key, value in row.items() if key in actual_columns}
                if name == "ai" and table == "usage":
                    clean.pop("id", None)
                user_column = spec["tables"].get(table)
                if user_column:
                    clean[user_column] = user_id
                columns = list(clean)
                placeholders = ",".join("?" for _ in columns)
                verb = "INSERT OR IGNORE" if table == "knowledge_items" else "INSERT"
                quoted_columns = ",".join(f'"{column}"' for column in columns)
                connection.execute(
                    f'{verb} INTO "{table}" ({quoted_columns}) VALUES ({placeholders})',
                    [clean[column] for column in columns],
                )
                restored += 1
    return restored


def restore_backup(user_id: str, archive_path: Path) -> dict[str, int | str]:
    _manifest, payload = _validate_archive(archive_path)
    safety_path = DATA_DIR / "backups" / f"before-restore-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    create_backup(user_id, safety_path)
    restored = 0
    for name in ("library", "learning", "ai"):
        if name in DATABASE_SPECS:
            restored += _restore_database(name, user_id, payload["databases"].get(name, {}))
    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            if not name.startswith("files/") or name.endswith("/"):
                continue
            relative = PurePosixPath(name).relative_to("files")
            if relative.parts[0] == "books":
                target = DATA_DIR / "books" / Path(*relative.parts[1:])
            elif relative.parts[0] == "custom-covers":
                target = DATA_DIR / "books" / "custom-covers" / user_id / Path(*relative.parts[1:])
            elif relative.parts[0] == "user":
                target = DATA_DIR / "users" / user_id / Path(*relative.parts[1:])
            elif relative.parts[0] == "voice":
                target = DATA_DIR / "voice" / "users" / user_id / Path(*relative.parts[1:])
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".restore")
            with archive.open(name) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            temporary.replace(target)
    return {"restored_rows": restored, "safety_backup": safety_path.name}


def save_upload(stream, filename: str) -> Path:
    suffix = ".zip" if filename.lower().endswith(".zip") else ".backup"
    handle = tempfile.NamedTemporaryFile(prefix="bingdu-restore-", suffix=suffix, delete=False)
    path = Path(handle.name)
    total = 0
    try:
        with handle:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BACKUP_BYTES:
                    raise ValueError("备份文件超过 2 GB 限制")
                handle.write(chunk)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise
