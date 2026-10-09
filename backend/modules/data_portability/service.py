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
from contextlib import closing

from ...paths import DATA_DIR
from .learning_transfer import LEARNING_TABLES, merge_learning, validate_learning
from .identity import TransferIds
from .protocol import compatibility, validate_compatibility
from .snapshots import EXTENSION_TABLES, collect_snapshot_ids, merge_snapshots, validate_snapshots
from .transaction import StagedFiles, attached_transaction


BACKUP_SCHEMA_VERSION = 3
BOOK_TRANSFER_SCHEMA_VERSION = 3
MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024
RESOURCE_PATTERN = re.compile(r"/api/assets/([0-9a-f]{20})(?:/|$)")
BOOK_RECORD_TABLES = {"books", "chapters", "sentences", "tokens", "annotations", "contextSenses", "lexemes"}

DATABASE_SPECS = {
    "library": {
        "path": DATA_DIR / "library.sqlite3",
        "tables": {
            "records": "owner_user_id", "user_library_items": "user_id",
            "user_book_progress": "user_id", "user_bookmarks": "user_id", "outbox": "owner_user_id",
            **EXTENSION_TABLES["library"],
            "portability_book_origins": "target_user_id",
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
            "transfer_daily_credits": "target_user_id",
        },
    },
    "ai": {
        "path": DATA_DIR / "ai.sqlite3",
        "tables": {"usage": "user_id"},
    },
    "linguistics": {"path": DATA_DIR / "linguistics.sqlite3", "tables": EXTENSION_TABLES["linguistics"]},
    "book_memory": {"path": DATA_DIR / "book_memory.sqlite3", "tables": EXTENSION_TABLES["book_memory"]},
}

PUBLIC_BOOK_FIELDS = {
    "books": {"id", "title", "author", "coverUrl", "customCover", "createdAt", "updatedAt", "translationComplete", "showImages"},
    "chapters": {"id", "bookId", "title", "order", "text", "blocks", "originalHtmlUrl", "original_html_url", "status", "segmentation_revision", "analysis_revision", "active_generation", "segmentation_source"},
    "sentences": {"id", "chapter_id", "chapterId", "start", "end", "original", "text", "translation_zh", "status", "explanation_status", "explanation_detail", "segmentation_revision", "analysis_revision"},
    "tokens": {"id", "sentence_id", "sentenceId", "start", "end", "surface", "lemma", "reading", "part_of_speech", "is_content", "lexemeKey", "pos_full", "conjugation_type", "conjugation_form", "normalized_form", "surface_reading", "lemma_reading", "role", "analysis_revision"},
    "annotations": {"id", "sentence_id", "sentenceId", "type", "anchor_start", "anchor_end", "quote", "structure", "explanation_zh", "analysis_revision"},
    "contextSenses": {"token_id", "tokenId", "gloss_zh", "analysis_revision"},
    "lexemes": {"key", "lemma", "reading", "firstKana", "part_of_speech", "senses_zh", "source", "updatedAt"},
}


def _public_book_payload(table: str, payload: dict) -> dict:
    result = {key: value for key, value in payload.items() if key in PUBLIC_BOOK_FIELDS[table]}
    if "blocks" in result:
        fields = {"id", "type", "start", "end", "text", "level", "asset_url", "alt", "placement", "width", "height"}
        result["blocks"] = [{key: value for key, value in block.items() if key in fields} for block in result["blocks"]]
    # A share carries public book content only. Account IDs are unnecessary even
    # for custom covers, whose files are already addressed by book ID.
    for key in ("coverUrl", "originalHtmlUrl", "original_html_url"):
        if isinstance(result.get(key), str):
            result[key] = re.sub(r"/api/assets/custom-covers/[^/]+/", "/api/assets/custom-covers/shared/", result[key])
    return result


def _validate_database_payload(databases):
    if any(name not in DATABASE_SPECS for name in databases):
        raise ValueError("备份包含不支持的数据库")
    for name, tables in databases.items():
        allowed = set(DATABASE_SPECS[name]["tables"])
        if name == "learning":
            allowed |= {"knowledge_items", "knowledge_aliases", "canonical_aliases"}
        if not isinstance(tables, dict) or set(tables) - allowed:
            raise ValueError("备份包含不支持的数据表")
        for rows in tables.values():
            if not isinstance(rows, list) or len(rows) > 2_000_000 or any(not isinstance(row, dict) for row in rows):
                raise ValueError("备份数据记录格式错误")


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
            with closing(sqlite3.connect(path)) as connection:
                for table, user_column in spec["tables"].items():
                    table_rows = _rows(connection, table, user_column, user_id)
                    tables[table] = table_rows
                    for row in table_rows:
                        if row.get("knowledge_item_id"):
                            knowledge_ids.add(str(row["knowledge_item_id"]))
                if name == "learning" and knowledge_ids and _table_exists(connection, "knowledge_items"):
                    # Only aliases reachable from this user's learning evidence
                    # are portable; global aliases belonging to others are excluded.
                    if _table_exists(connection, "knowledge_aliases"):
                        aliases = [dict(row) for row in connection.execute("SELECT * FROM knowledge_aliases")]
                        while True:
                            reachable = [row for row in aliases if row["alias_id"] in knowledge_ids or row["target_id"] in knowledge_ids]
                            expanded = knowledge_ids | {str(row[key]) for row in reachable for key in ("alias_id", "target_id")}
                            if expanded == knowledge_ids:
                                break
                            knowledge_ids = expanded
                        tables["knowledge_aliases"] = reachable
                    if _table_exists(connection, "canonical_aliases"):
                        tables["canonical_aliases"] = [dict(row) for row in connection.execute("SELECT * FROM canonical_aliases") if row["target_id"] in knowledge_ids]
                    placeholders = ",".join("?" for _ in knowledge_ids)
                    connection.row_factory = sqlite3.Row
                    tables["knowledge_items"] = [dict(row) for row in connection.execute(
                        f"SELECT * FROM knowledge_items WHERE id IN ({placeholders})", tuple(knowledge_ids),
                    )]
        databases[name] = tables
    return {"source_user_id": user_id, "databases": databases}


def _safe_arcname(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ValueError("备份中包含不安全的文件路径")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} or ":" in part for part in path.parts):
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


def create_backup(user_id: str, destination: Path | None = None, schema_version: int = BACKUP_SCHEMA_VERSION) -> Path:
    if schema_version not in {1, 3}:
        raise ValueError("备份导出版本不受支持")
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if destination is None:
        destination = backup_dir / f".download-{uuid.uuid4().hex}.zip"
    payload = _export_payload(user_id)
    if schema_version < 3:
        payload["databases"].pop("linguistics", None)
        payload["databases"].pop("book_memory", None)
        for name, table_names in (("library", [*EXTENSION_TABLES["library"], "portability_book_origins"]), ("learning", ("knowledge_aliases", "canonical_aliases", "transfer_daily_credits"))):
            for table in table_names:
                payload["databases"].get(name, {}).pop(table, None)
        for table in ("notes", "card_candidates"):
            for row in payload["databases"].get("learning", {}).get(table, []):
                row.pop("source_json", None)
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
            "format": "bingdu-backup", "schema_version": schema_version,
            "created_at": time.time(), "files": checksums,
            **compatibility(schema_version, "backup"),
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return destination


def _book_records(user_id: str, allowed_book_ids: set[str] | None = None) -> list[dict]:
    path = Path(DATABASE_SPECS["library"]["path"])
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(
            """SELECT table_name,record_key,payload FROM records
               WHERE owner_user_id=? AND table_name IN ('books','chapters','sentences','tokens','annotations','contextSenses','lexemes')
               ORDER BY rowid""",
            (user_id,),
        )]
    decoded = [{**row, "payload": json.loads(row["payload"])} for row in rows]
    books = [row for row in decoded if row["table_name"] == "books"]
    book_ids = {str(row["record_key"]) for row in books}
    if allowed_book_ids is not None:
        book_ids &= allowed_book_ids
    chapters = [row for row in decoded if row["table_name"] == "chapters" and str(row["payload"].get("bookId") or "") in book_ids]
    chapter_ids = {str(row["record_key"]) for row in chapters}
    sentences = [row for row in decoded if row["table_name"] == "sentences" and str(row["payload"].get("chapter_id") or row["payload"].get("chapterId") or "") in chapter_ids]
    sentence_ids = {str(row["record_key"]) for row in sentences}
    tokens = [row for row in decoded if row["table_name"] == "tokens" and str(row["payload"].get("sentence_id") or row["payload"].get("sentenceId") or "") in sentence_ids]
    token_ids = {str(row["record_key"]) for row in tokens}
    lexeme_keys = {str(row["payload"].get("lexemeKey") or "") for row in tokens}
    annotations = [row for row in decoded if row["table_name"] == "annotations" and str(row["payload"].get("sentence_id") or row["payload"].get("sentenceId") or "") in sentence_ids]
    context_senses = [row for row in decoded if row["table_name"] == "contextSenses" and (str(row["record_key"]) in token_ids or str(row["payload"].get("token_id") or row["payload"].get("tokenId") or "") in token_ids)]
    lexemes = [row for row in decoded if row["table_name"] == "lexemes" and str(row["record_key"]) in lexeme_keys]
    return [row for row in books if str(row["record_key"]) in book_ids] + chapters + sentences + tokens + annotations + context_senses + lexemes


def create_book_transfer(user_id: str, destination: Path | None = None, book_ids: list[str] | None = None, schema_version: int = BOOK_TRANSFER_SCHEMA_VERSION) -> Path:
    metadata = compatibility(schema_version, "book-share" if book_ids is not None else "account-migration")
    sharing = book_ids is not None
    records = _book_records(user_id, set(book_ids) if sharing else None)
    selected_book_ids = {str(row["record_key"]) for row in records if row["table_name"] == "books"}
    if sharing and (not selected_book_ids or selected_book_ids != set(book_ids)):
        raise ValueError("所选书籍不存在或不属于当前账号，请刷新书架后重试")
    transfer_dir = DATA_DIR / "backups"
    transfer_dir.mkdir(parents=True, exist_ok=True)
    if destination is None:
        destination = transfer_dir / f".book-transfer-{uuid.uuid4().hex}.zip"
    book_ids = selected_book_ids
    exported = {} if sharing else _export_payload(user_id)["databases"]
    if sharing:
        for row in records:
            row["payload"] = _public_book_payload(row["table_name"], row["payload"])
            if row["table_name"] == "books":
                row["payload"] = {key: value for key, value in row["payload"].items()
                                  if key not in {"currentChapterId", "currentSentenceId", "lastOpenedAt", "collectionId", "collectionName"}}
                row["payload"]["showImages"] = False
            elif row["table_name"] == "lexemes":
                row["payload"].pop("groups", None)
    # Include personal vocabulary even if it is no longer referenced by a book.
    known = {(row["table_name"], row["record_key"]) for row in records}
    for row in exported.get("library", {}).get("records", []):
        if row["table_name"] == "lexemes" and ("lexemes", row["record_key"]) not in known:
            records.append({"table_name": "lexemes", "record_key": row["record_key"], "payload": json.loads(row["payload"])})
    snapshots = {name: {table: exported.get(name, {}).get(table, []) for table in tables}
                 for name, tables in EXTENSION_TABLES.items() if name in DATABASE_SPECS} if schema_version == 3 and not sharing else {}
    if snapshots.get('library'):
        committed = [row for row in snapshots['library'].get('segmentation_generations', [])
                     if row.get('status') in {'active', 'archived', 'committed'}]
        committed_ids = {row['generation_id'] for row in committed}
        snapshots['library']['segmentation_generations'] = committed
        snapshots['library']['segmentation_anchor_maps'] = [row for row in snapshots['library'].get('segmentation_anchor_maps', [])
                                                           if row.get('new_generation') in committed_ids]
    payload = {"source_user_id": "" if sharing else user_id, "records": records,
               "library_data": {name: exported.get("library", {}).get(name, []) for name in ("user_book_progress", "user_bookmarks")},
               "learning": {name: rows for name, rows in exported.get("learning", {}).items() if name in LEARNING_TABLES and (schema_version == 3 or name not in {"knowledge_aliases", "canonical_aliases", "card_undo_log", "transfer_daily_credits"})},
               "snapshots": snapshots}
    if schema_version < 3:
        for table in ("notes", "card_candidates"):
            for row in payload["learning"].get(table, []):
                row.pop("source_json", None)
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    checksums = {"data.json": hashlib.sha256(payload_bytes).hexdigest()}
    encoded_payloads = [json.dumps(row["payload"], ensure_ascii=False) for row in records]
    resource_keys = {match.group(1) for value in encoded_payloads for match in RESOURCE_PATTERN.finditer(value)}
    books_dir = DATA_DIR / "books"
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
                if source.is_file() and source.stem in book_ids:
                    _add_file(archive, source, f"files/custom-covers/{source.name}", checksums)
        manifest = {
            "format": "bingdu-book-transfer", "schema_version": schema_version,
            "purpose": "book-share" if sharing else "account-migration",
            "created_at": time.time(), "book_count": len(book_ids), "files": checksums,
            **metadata,
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return destination


def _validate_archive(path: Path) -> tuple[dict, dict]:
    if path.stat().st_size > MAX_BACKUP_BYTES:
        raise ValueError("备份文件超过 2 GB 限制")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("备份中包含重复文件")
        if sum(info.file_size for info in archive.infolist()) > MAX_BACKUP_BYTES:
            raise ValueError("备份解压后的内容超过 2 GB 限制")
        for name in names:
            _safe_arcname(name)
        if "manifest.json" not in names or "data.json" not in names:
            raise ValueError("这不是完整的冰读备份")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != "bingdu-backup" or int(manifest.get("schema_version", 0)) not in {1, BACKUP_SCHEMA_VERSION}:
            raise ValueError("备份格式或版本不受支持")
        validate_compatibility(manifest)
        checksums = manifest.get("files", {})
        if not isinstance(checksums, dict) or any(name != "manifest.json" and name not in checksums for name in names):
            raise ValueError("备份文件清单不完整")
        for name, expected in checksums.items():
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
    _validate_database_payload(payload["databases"])
    return manifest, payload


def _validate_book_transfer(path: Path) -> tuple[dict, dict]:
    if path.stat().st_size > MAX_BACKUP_BYTES:
        raise ValueError("迁移包超过 2 GB 限制")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("迁移包中包含重复文件")
        if sum(info.file_size for info in archive.infolist()) > MAX_BACKUP_BYTES:
            raise ValueError("迁移包解压后的内容超过 2 GB 限制")
        for name in names:
            _safe_arcname(name)
        if "manifest.json" not in names or "data.json" not in names:
            raise ValueError("这不是冰读书籍迁移包")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != "bingdu-book-transfer" or int(manifest.get("schema_version", 0)) not in {1, 2, BOOK_TRANSFER_SCHEMA_VERSION}:
            raise ValueError("书籍迁移包格式或版本不受支持")
        # Legacy packages produced before v3 never had capability declarations.
        if int(manifest["schema_version"]) < 3:
            manifest.pop("min_reader_schema_version", None)
            manifest.pop("required_capabilities", None)
        validate_compatibility(manifest)
        checksums = manifest.get("files", {})
        if not isinstance(checksums, dict) or any(name != "manifest.json" and name not in checksums for name in names):
            raise ValueError("迁移包文件清单不完整")
        for name, expected in checksums.items():
            if name not in names:
                raise ValueError(f"迁移包文件校验失败：{name}")
            digest = hashlib.sha256()
            with archive.open(name) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise ValueError(f"迁移包文件校验失败：{name}")
        payload = json.loads(archive.read("data.json"))
    if not isinstance(payload.get("records"), list) or len(payload["records"]) > 2_000_000:
        raise ValueError("迁移包数据结构不完整或记录过多")
    for row in payload["records"]:
        if not isinstance(row, dict) or row.get("table_name") not in BOOK_RECORD_TABLES or not isinstance(row.get("record_key"), str) or not row["record_key"] or not isinstance(row.get("payload"), dict):
            raise ValueError("迁移包包含不支持的数据记录")
    validate_learning(payload.get("learning", {}))
    validate_snapshots(payload.get("snapshots", {}))
    if manifest.get("purpose") == "book-share" and (payload.get("learning") or any(rows for tables in payload.get("snapshots", {}).values() for rows in tables.values()) or any(payload.get("library_data", {}).values())):
        raise ValueError("书籍分享包不得包含个人学习或档案数据")
    library_data = payload.get("library_data", {})
    if not isinstance(library_data, dict) or any(name not in {"user_book_progress", "user_bookmarks"} for name in library_data):
        raise ValueError("迁移包书库数据格式错误")
    if any(not isinstance(rows, list) or len(rows) > 2_000_000 or any(not isinstance(row, dict) for row in rows) for rows in library_data.values()):
        raise ValueError("迁移包书库记录格式错误")
    return manifest, payload


def _rewrite_transfer_payload(value: dict, source_user_id: str, target_user_id: str) -> dict:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    encoded = encoded.replace(
        f"/api/assets/custom-covers/{source_user_id}/",
        f"/api/assets/custom-covers/{target_user_id}/",
    )
    return json.loads(encoded)


def _transfer_paths(payload: dict) -> dict[str, Path]:
    paths = {"library": Path(DATABASE_SPECS["library"]["path"])}
    if any(payload.get("learning", {}).values()):
        paths["learning"] = Path(DATABASE_SPECS["learning"]["path"])
    for name, tables in payload.get("snapshots", {}).items():
        if any(tables.values()) and name != "library":
            paths[name] = Path(DATABASE_SPECS[name]["path"])
    return paths


def _share_source_fingerprint(records) -> str:
    """An anonymous source namespace depends on original content, not progress.

    Account IDs stay absent from sharing. Different anonymous books with a
    colliding legacy ID must not reuse each other's imported-origin mapping.
    Translation changes do not create a new book identity.
    """
    originals = []
    for row in records:
        payload = row['payload']
        if row['table_name'] == 'books':
            originals.append(['book', str(row['record_key']), payload.get('title', ''), payload.get('author', '')])
        elif row['table_name'] == 'chapters':
            originals.append(['chapter', str(payload.get('bookId') or payload.get('book_id') or ''),
                              int(payload.get('order') or 0), str(payload.get('text') or '')])
    encoded = json.dumps(sorted(originals, key=lambda row: json.dumps(row, ensure_ascii=False)), ensure_ascii=False, separators=(',', ':'))
    return 'shared:' + hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _same_shared_book(connection, records, source_book: str, target_user: str, target_book: str) -> bool:
    incoming = next((row['payload'] for row in records if row['table_name'] == 'books' and row['record_key'] == source_book), None)
    saved = connection.execute("SELECT payload FROM records WHERE owner_user_id=? AND table_name='books' AND record_key=?", (target_user, target_book)).fetchone()
    if incoming is None or saved is None:
        return False
    existing = json.loads(saved[0])
    if any(str(incoming.get(key) or '') != str(existing.get(key) or '') for key in ('title', 'author')):
        return False
    incoming_chapters = [row['payload'] for row in records if row['table_name'] == 'chapters'
                         and str(row['payload'].get('bookId') or row['payload'].get('book_id') or '') == source_book]
    existing_chapters = [json.loads(row[0]) for row in connection.execute(
        "SELECT payload FROM records WHERE owner_user_id=? AND table_name='chapters' AND COALESCE(json_extract(payload,'$.bookId'),json_extract(payload,'$.book_id'))=?", (target_user, target_book))]
    def originals(chapters):
        return sorted((int(row.get('order') or 0), str(row.get('text') or '')) for row in chapters)
    # A title or an ID alone is not proof of an identical book. Empty legacy
    # book records conservatively keep the collision/remapping path.
    return bool(incoming_chapters) and any(row.get('text') for row in incoming_chapters) and originals(incoming_chapters) == originals(existing_chapters)


def _transfer_ids(connection, records, source: str, target: str, *, sharing: bool = False):
    ids = TransferIds(source, target)
    connection.execute("CREATE TABLE IF NOT EXISTS portability_book_origins(target_user_id TEXT NOT NULL,source_user_id TEXT NOT NULL,source_book_id TEXT NOT NULL,target_book_id TEXT NOT NULL,PRIMARY KEY(target_user_id,source_user_id,source_book_id))")
    source_books = {row["record_key"] for row in records if row["table_name"] == "books"}
    collision_books = set()
    imported, skipped = set(), set()
    for book in sorted(source_books):
        found = connection.execute("SELECT target_book_id FROM portability_book_origins WHERE target_user_id=? AND source_user_id=? AND source_book_id=?", (target, source, book)).fetchone()
        target_book = found[0] if found else book
        existing = connection.execute("SELECT 1 FROM user_library_items WHERE user_id=? AND book_id=?", (target, target_book)).fetchone()
        same_share = sharing and existing and _same_shared_book(connection, records, book, target, target_book)
        if sharing and not found and not same_share:
            # The same book can arrive in another selection/package after a
            # prior ID collision. Check exact owned originals before creating
            # a second remapped copy; no foreign user's records are queried.
            incoming = next(row['payload'] for row in records if row['table_name'] == 'books' and row['record_key'] == book)
            candidates = connection.execute("SELECT r.record_key FROM records r JOIN user_library_items u ON u.user_id=r.owner_user_id AND u.book_id=r.record_key WHERE r.owner_user_id=? AND r.table_name='books' AND COALESCE(json_extract(r.payload,'$.title'),'')=? AND COALESCE(json_extract(r.payload,'$.author'),'')=? ORDER BY r.record_key", (target, str(incoming.get('title') or ''), str(incoming.get('author') or ''))).fetchall()
            match = next((row[0] for row in candidates if _same_shared_book(connection, records, book, target, row[0])), None)
            if match is not None:
                target_book, existing, same_share = match, True, True
        if existing and not found and source != target and not same_share:
            ids.add("books", book)
            target_book = ids.get("books", book)
            collision_books.add(book)
            existing = connection.execute("SELECT 1 FROM user_library_items WHERE user_id=? AND book_id=?", (target, target_book)).fetchone()
        else:
            ids.add("books", book, target_book)
            if target_book != book:
                collision_books.add(book)
        (skipped if existing else imported).add(book)
        connection.execute("INSERT OR IGNORE INTO portability_book_origins VALUES(?,?,?,?)", (target, source, book, target_book))
    if collision_books:
        graph = _select_transfer_records(records, collision_books)
        for row in graph:
            if row["table_name"] not in {"books", "lexemes", "contextSenses"}:
                ids.add(row["table_name"], row["record_key"])
    return ids, imported, skipped


def import_book_transfer(user_id: str, archive_path: Path) -> dict[str, int]:
    manifest, payload = _validate_book_transfer(archive_path)
    records = payload["records"]
    sharing = manifest.get("purpose") == "book-share"
    source_user_id = _share_source_fingerprint(records) if sharing else str(payload.get("source_user_id") or "shared")
    package_book_ids = {row["record_key"] for row in records if row["table_name"] == "books"}
    snapshots = payload.get("snapshots", {})
    files = StagedFiles()
    success = False
    try:
        with attached_transaction(_transfer_paths(payload)) as (connection, aliases):
            ids, imported_book_ids, skipped = _transfer_ids(connection, records, source_user_id, user_id, sharing=sharing)
            collect_snapshot_ids(ids, snapshots)
            selected = _select_transfer_records(records, imported_book_ids)
            selected_keys = {(row["table_name"], row["record_key"]) for row in selected}
            selected.extend(row for row in records if row["table_name"] == "lexemes" and ("lexemes", row["record_key"]) not in selected_keys)
            selected_strings = [json.dumps(row["payload"], ensure_ascii=False) for row in selected]
            resource_keys = {match.group(1) for value in selected_strings for match in RESOURCE_PATTERN.finditer(value)}
            with zipfile.ZipFile(archive_path) as archive:
                for name in archive.namelist():
                    if name.endswith("/") or not name.startswith("files/"):
                        continue
                    relative = PurePosixPath(name).relative_to("files")
                    target = None
                    if len(relative.parts) >= 3 and relative.parts[0] == "books" and relative.parts[1] in resource_keys:
                        target = DATA_DIR / "books" / Path(*relative.parts[1:])
                    elif len(relative.parts) == 2 and relative.parts[0] == "custom-covers" and Path(relative.parts[1]).stem in imported_book_ids:
                        original = Path(relative.parts[1])
                        target = DATA_DIR / "books" / "custom-covers" / user_id / (ids.get("books", original.stem) + original.suffix)
                    if target is not None:
                        files.add(archive, name, target)
            learning_counts = {"imported_cards": 0, "imported_learning_records": 0}
            if any(payload.get("learning", {}).values()):
                learning_counts = merge_learning(connection, payload["learning"], source_user_id, user_id, ids=ids)
            imported_records = 0
            now = time.time()
            for row in selected:
                domain = "tokens" if row["table_name"] == "contextSenses" else row["table_name"]
                clean_payload = ids.rewrite(row["payload"], domain)
                clean_payload = _rewrite_transfer_payload(clean_payload, source_user_id, user_id)
                # Custom-cover filenames follow the remapped book identity.
                if row["table_name"] == "books" and isinstance(clean_payload.get("coverUrl"), str):
                    clean_payload["coverUrl"] = clean_payload["coverUrl"].replace('/' + row["record_key"] + '.', '/' + ids.get("books", row["record_key"]) + '.')
                record_key = ids.get(domain, row["record_key"])
                encoded = json.dumps(clean_payload, ensure_ascii=False, separators=(",", ":"))
                cursor = connection.execute("INSERT OR IGNORE INTO records(owner_user_id,table_name,record_key,payload) VALUES(?,?,?,?)", (user_id, row["table_name"], record_key, encoded))
                imported_records += cursor.rowcount
                if row["table_name"] == "books":
                    created_at = float(clean_payload.get("createdAt") or now)
                    updated_at = float(clean_payload.get("updatedAt") or created_at)
                    metadata = {key: value for key, value in clean_payload.items() if key not in {"currentChapterId", "currentSentenceId", "lastOpenedAt"}}
                    connection.execute("INSERT OR IGNORE INTO user_library_items(user_id,book_id,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?)", (user_id, record_key, json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), created_at, updated_at))
            for table, rows in payload.get("library_data", {}).items():
                if not _table_exists(connection, table):
                    if rows:
                        raise ValueError(f"目标服务尚未支持书库数据表：{table}")
                    continue
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                for original in rows:
                    if original.get("book_id") not in package_book_ids:
                        continue
                    if set(original) - columns:
                        raise ValueError(f"目标服务缺少书库数据列：{table}")
                    clean = ids.rewrite(original)
                    clean["user_id"] = user_id
                    names = list(clean)
                    connection.execute(f'INSERT OR IGNORE INTO "{table}" ({",".join(names)}) VALUES ({",".join("?" for _ in names)})', list(clean.values()))
            active_snapshots = {name: tables for name, tables in snapshots.items() if name in aliases}
            merge_snapshots(connection, aliases, active_snapshots, ids)
            files.publish()
        success = True
    finally:
        files.finish(success)
    return {"imported_books": len(imported_book_ids), "skipped_books": len(skipped),
            "imported_records": imported_records, **learning_counts}


def _select_transfer_records(records: list[dict], allowed_book_ids: set[str]) -> list[dict]:
    books = [row for row in records if row["table_name"] == "books" and str(row["record_key"]) in allowed_book_ids]
    chapter_ids = {str(row["record_key"]) for row in records if row["table_name"] == "chapters" and str(row["payload"].get("bookId") or "") in allowed_book_ids}
    chapters = [row for row in records if row["table_name"] == "chapters" and str(row["record_key"]) in chapter_ids]
    sentence_ids = {str(row["record_key"]) for row in records if row["table_name"] == "sentences" and str(row["payload"].get("chapter_id") or row["payload"].get("chapterId") or "") in chapter_ids}
    sentences = [row for row in records if row["table_name"] == "sentences" and str(row["record_key"]) in sentence_ids]
    tokens = [row for row in records if row["table_name"] == "tokens" and str(row["payload"].get("sentence_id") or row["payload"].get("sentenceId") or "") in sentence_ids]
    token_ids = {str(row["record_key"]) for row in tokens}
    lexeme_keys = {str(row["payload"].get("lexemeKey") or "") for row in tokens}
    annotations = [row for row in records if row["table_name"] == "annotations" and str(row["payload"].get("sentence_id") or row["payload"].get("sentenceId") or "") in sentence_ids]
    context_senses = [row for row in records if row["table_name"] == "contextSenses" and (str(row["record_key"]) in token_ids or str(row["payload"].get("token_id") or row["payload"].get("tokenId") or "") in token_ids)]
    lexemes = [row for row in records if row["table_name"] == "lexemes" and str(row["record_key"]) in lexeme_keys]
    return books + chapters + sentences + tokens + annotations + context_senses + lexemes


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
    manifest, payload = _validate_archive(archive_path)
    source_user_id = str(payload.get("source_user_id") or user_id)
    safety_path = DATA_DIR / "backups" / f"before-restore-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.zip"
    create_backup(user_id, safety_path)
    databases = payload["databases"]
    paths = {name: Path(DATABASE_SPECS[name]["path"]) for name, tables in databases.items()
             if Path(DATABASE_SPECS[name]["path"]).is_file() or any(tables.values())}
    if "library" not in paths:
        raise ValueError("本地书库尚未初始化")
    paths = {"library": paths.pop("library"), **paths}
    ids = TransferIds(source_user_id, user_id)
    snapshots = {name: {table: rows for table, rows in tables.items() if table in EXTENSION_TABLES.get(name, {})}
                 for name, tables in databases.items() if name in EXTENSION_TABLES}
    collect_snapshot_ids(ids, snapshots)
    for row in databases.get("library", {}).get("outbox", []):
        ids.add("outbox", row["id"])
    files = StagedFiles()
    success = False
    restored = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                if not name.startswith("files/") or name.endswith("/"):
                    continue
                relative = PurePosixPath(name).relative_to("files")
                category = relative.parts[0]
                if len(relative.parts) < 2:
                    raise ValueError("备份文件路径不完整")
                if category == "books":
                    target = DATA_DIR / "books" / Path(*relative.parts[1:])
                elif category == "custom-covers":
                    target = DATA_DIR / "books" / "custom-covers" / user_id / Path(*relative.parts[1:])
                elif category == "user":
                    target = DATA_DIR / "users" / user_id / Path(*relative.parts[1:])
                elif category == "voice":
                    target = DATA_DIR / "voice" / "users" / user_id / Path(*relative.parts[1:])
                else:
                    raise ValueError("备份包含未知文件类别")
                files.add(archive, name, target)
        with attached_transaction(paths) as (connection, aliases):
            # Validate every target table and column before deleting a single row.
            for name, tables in databases.items():
                if name not in aliases:
                    continue
                alias = aliases[name]
                for table, rows in tables.items():
                    info = connection.execute(f'PRAGMA "{alias}".table_info("{table}")').fetchall()
                    if rows and not info:
                        raise ValueError(f"目标服务尚未支持备份数据表：{name}.{table}")
                    columns = {row[1] for row in info}
                    if any(set(row) - columns for row in rows):
                        raise ValueError(f"目标服务缺少备份数据列：{name}.{table}")
            for name in reversed(list(paths)):
                alias = aliases[name]
                for table, user_column in reversed(list(DATABASE_SPECS[name]["tables"].items())):
                    # A legacy backup cannot erase v3 state that it never contained.
                    if table not in databases[name]:
                        continue
                    if connection.execute(f'SELECT 1 FROM "{alias}".sqlite_master WHERE type=? AND name=?', ("table", table)).fetchone():
                        connection.execute(f'DELETE FROM "{alias}"."{table}" WHERE "{user_column}"=?', (user_id,))
            for name, tables in databases.items():
                if name not in aliases:
                    continue
                if name == "learning":
                    counts = merge_learning(connection, tables, source_user_id, user_id, ids=ids, replace=True)
                    restored += counts["imported_cards"] + counts["imported_learning_records"]
                    restored += len(tables.get("knowledge_items", []))
                    continue
                alias = aliases[name]
                for table, rows in tables.items():
                    for original in rows:
                        domain = "outbox" if table == "outbox" else None
                        if table == "records":
                            clean = dict(original)
                            value = json.loads(original["payload"])
                            clean["payload"] = json.dumps(_rewrite_transfer_payload(ids.rewrite(value, original["table_name"]), source_user_id, user_id), ensure_ascii=False)
                        else:
                            if table == "objects":
                                domain = "entities" if original["kind"] == "entity" else "facts"
                            elif table == "revisions":
                                domain = "memory_revisions"
                            clean = ids.rewrite(original, domain)
                        user_column = DATABASE_SPECS[name]["tables"].get(table)
                        if user_column:
                            clean[user_column] = user_id
                        if name == "ai" and table == "usage":
                            clean.pop("id", None)
                        names = list(clean)
                        connection.execute(f'INSERT INTO "{alias}"."{table}" (' + ",".join(f'"{key}"' for key in names) + ') VALUES (' + ",".join("?" for _ in names) + ')', list(clean.values()))
                        restored += 1
            files.publish()
        success = True
    finally:
        files.finish(success)
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
