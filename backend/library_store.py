import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from .models import ChapterSnapshot, LibraryIndex, LibraryPatch, LibrarySnapshot, StudyDataSnapshot
from .paths import DATA_DIR


LIBRARY_PATH = DATA_DIR / "library.sqlite3"
LEGACY_PATH = DATA_DIR / "library.json"
LIBRARY_SCHEMA_VERSION = 2
_initialization_lock = threading.Lock()
_initialized_paths: set[Path] = set()
_RESOURCE_KEY_PATTERN = re.compile(r"/api/assets/([0-9a-f]{20})(?:/|$)")
TABLE_KEYS = {
    "books": "id", "chapters": "id", "sentences": "id", "tokens": "id",
    "annotations": "id", "contextSenses": "token_id", "lexemes": "key",
    "bookmarks": "id",
}
OBSOLETE_INDEXES = {
    "idx_chapters_book_order", "idx_sentences_chapter_start", "idx_tokens_sentence",
    "idx_annotations_sentence", "idx_cards_book", "idx_cards_book_v2", "idx_lexemes_reading_lemma",
    "idx_tokens_sentence_v2", "idx_annotations_sentence_v2",
}
INDEX_DEFINITIONS = {
    "idx_chapters_book_order_v2": "CREATE INDEX idx_chapters_book_order_v2 ON records(table_name, json_extract(payload, '$.bookId'), CAST(json_extract(payload, '$.order') AS INTEGER))",
    "idx_sentences_chapter_start_v2": "CREATE INDEX idx_sentences_chapter_start_v2 ON records(table_name, json_extract(payload, '$.chapter_id'), CAST(json_extract(payload, '$.start') AS INTEGER))",
    "idx_records_sentence_v2": "CREATE INDEX idx_records_sentence_v2 ON records(table_name, json_extract(payload, '$.sentence_id'))",
    "idx_lexemes_reading_lemma_v2": "CREATE INDEX idx_lexemes_reading_lemma_v2 ON records(table_name, json_extract(payload, '$.reading'), json_extract(payload, '$.lemma'))",
}


def _initialize_connection(connection: sqlite3.Connection, path: Path) -> None:
    with _initialization_lock:
        if path in _initialized_paths:
            return
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS records (table_name TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (table_name, record_key))")
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if schema_version < LIBRARY_SCHEMA_VERSION:
            # Cards were removed from the product. Purge them once as a schema
            # migration instead of turning every read connection into a write.
            connection.execute("DELETE FROM records WHERE table_name = 'cards'")
            existing_indexes = {
                row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
            }
            for obsolete_index in OBSOLETE_INDEXES & existing_indexes:
                connection.execute(f"DROP INDEX {obsolete_index}")
                existing_indexes.remove(obsolete_index)
            for index_name, statement in INDEX_DEFINITIONS.items():
                if index_name not in existing_indexes:
                    connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {LIBRARY_SCHEMA_VERSION}")
        connection.commit()
        _initialized_paths.add(path)


def initialize_store() -> None:
    with _connect():
        pass


@contextmanager
def _connect():
    path = LIBRARY_PATH.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    if not existed:
        with _initialization_lock:
            _initialized_paths.discard(path)
    connection = sqlite3.connect(path)
    _initialize_connection(connection, path)
    connection.execute("PRAGMA synchronous=NORMAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _resource_keys(payloads: list[str]) -> set[str]:
    return {
        match.group(1)
        for payload in payloads
        for match in _RESOURCE_KEY_PATTERN.finditer(payload)
    }


def _migrate_legacy() -> None:
    if LIBRARY_PATH.exists() or not LEGACY_PATH.exists():
        return
    try:
        snapshot = LibrarySnapshot.model_validate_json(LEGACY_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return
    save_library(snapshot)
    LEGACY_PATH.replace(LEGACY_PATH.with_suffix(".json.migrated"))


def load_library() -> LibrarySnapshot | None:
    _migrate_legacy()
    if not LIBRARY_PATH.exists():
        return None
    values: dict[str, list[dict]] = {name: [] for name in TABLE_KEYS}
    with _connect() as connection:
        for table_name, payload in connection.execute("SELECT table_name, payload FROM records ORDER BY rowid"):
            if table_name in values:
                values[table_name].append(json.loads(payload))
    return LibrarySnapshot(**values)


def _load_rows(connection: sqlite3.Connection, table_name: str) -> list[dict]:
    return [json.loads(row[0]) for row in connection.execute(
        "SELECT payload FROM records WHERE table_name = ? ORDER BY rowid", (table_name,),
    )]


def load_library_index() -> LibraryIndex:
    _migrate_legacy()
    with _connect() as connection:
        chapters = _load_rows(connection, "chapters")
        translation_rows = connection.execute(
            """
            SELECT json_extract(payload, '$.chapter_id') AS chapter_id,
                   COUNT(*) AS sentence_count,
                   SUM(CASE
                         WHEN TRIM(COALESCE(json_extract(payload, '$.translation_zh'), '')) != ''
                          AND COALESCE(json_extract(payload, '$.explanation_status'), 'idle') = 'complete'
                         THEN 1 ELSE 0 END) AS translated_count
            FROM records
            WHERE table_name = 'sentences'
            GROUP BY chapter_id
            """
        ).fetchall()
        progress_by_chapter = {
            str(chapter_id): (int(sentence_count), int(translated_count or 0))
            for chapter_id, sentence_count, translated_count in translation_rows
        }
        chapters_by_book: dict[str, list[dict]] = {}
        for chapter in chapters:
            if str(chapter.get("text", "")).strip():
                chapters_by_book.setdefault(str(chapter.get("bookId", "")), []).append(chapter)
        books = _load_rows(connection, "books")
        for book in books:
            text_chapters = chapters_by_book.get(str(book.get("id", "")), [])
            book["translationComplete"] = bool(text_chapters) and all(
                progress_by_chapter.get(str(chapter.get("id", "")), (0, 0))[0] > 0
                and progress_by_chapter.get(str(chapter.get("id", "")), (0, 0))[0]
                == progress_by_chapter.get(str(chapter.get("id", "")), (0, 0))[1]
                for chapter in text_chapters
            )
        return LibraryIndex(
            books=books,
            chapters=[{
                key: row[key] for key in ("id", "bookId", "title", "order", "status", "error") if key in row
            } for row in chapters],
        )


def load_chapter(chapter_id: str) -> ChapterSnapshot:
    _migrate_legacy()
    with _connect() as connection:
        chapter_row = connection.execute(
            "SELECT payload FROM records WHERE table_name = 'chapters' AND record_key = ?", (chapter_id,),
        ).fetchone()
        chapter = json.loads(chapter_row[0]) if chapter_row else None
        sentences = [json.loads(row[0]) for row in connection.execute(
            "SELECT payload FROM records WHERE table_name = 'sentences' AND json_extract(payload, '$.chapter_id') = ? ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER)",
            (chapter_id,),
        )]
        sentence_ids = [str(row.get("id", "")) for row in sentences if row.get("id")]
        tokens: list[dict] = []
        annotations: list[dict] = []
        if sentence_ids:
            placeholders = ",".join("?" for _ in sentence_ids)
            tokens = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE table_name = 'tokens' AND json_extract(payload, '$.sentence_id') IN ({placeholders}) ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER)",
                sentence_ids,
            )]
            annotations = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE table_name = 'annotations' AND json_extract(payload, '$.sentence_id') IN ({placeholders}) ORDER BY rowid",
                sentence_ids,
            )]
        token_ids = [str(row.get("id", "")) for row in tokens if row.get("id")]
        context_senses: list[dict] = []
        if token_ids:
            placeholders = ",".join("?" for _ in token_ids)
            context_senses = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE table_name = 'contextSenses' AND record_key IN ({placeholders}) ORDER BY rowid",
                token_ids,
            )]
        lexeme_keys = list({str(row.get("lexemeKey", "")) for row in tokens if row.get("lexemeKey")})
        lexemes: list[dict] = []
        if lexeme_keys:
            placeholders = ",".join("?" for _ in lexeme_keys)
            lexemes = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE table_name = 'lexemes' AND record_key IN ({placeholders}) ORDER BY rowid",
                lexeme_keys,
            )]
    return ChapterSnapshot(
        chapter=chapter, sentences=sentences, tokens=tokens, annotations=annotations,
        contextSenses=context_senses, lexemes=lexemes,
    )


def load_chapter_view(chapter_id: str) -> ChapterSnapshot:
    """Load chapter text and sentence rows without the much larger token graph."""
    _migrate_legacy()
    with _connect() as connection:
        chapter_row = connection.execute(
            "SELECT payload FROM records WHERE table_name = 'chapters' AND record_key = ?", (chapter_id,),
        ).fetchone()
        chapter = json.loads(chapter_row[0]) if chapter_row else None
        sentences = [json.loads(row[0]) for row in connection.execute(
            "SELECT payload FROM records WHERE table_name = 'sentences' AND json_extract(payload, '$.chapter_id') = ? ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER)",
            (chapter_id,),
        )]
    return ChapterSnapshot(chapter=chapter, sentences=sentences)


def load_chapter_details(chapter_id: str, offset: int, limit: int) -> dict:
    """Load token and annotation data only for one visible sentence window."""
    _migrate_legacy()
    with _connect() as connection:
        sentence_ids = [row[0] for row in connection.execute(
            """
            SELECT record_key FROM records
            WHERE table_name = 'sentences' AND json_extract(payload, '$.chapter_id') = ?
            ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER)
            LIMIT ? OFFSET ?
            """,
            (chapter_id, limit, offset),
        )]
        tokens: list[dict] = []
        annotations: list[dict] = []
        if sentence_ids:
            placeholders = ",".join("?" for _ in sentence_ids)
            tokens = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE table_name = 'tokens' AND json_extract(payload, '$.sentence_id') IN ({placeholders}) ORDER BY rowid",
                sentence_ids,
            )]
            annotations = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE table_name = 'annotations' AND json_extract(payload, '$.sentence_id') IN ({placeholders}) ORDER BY rowid",
                sentence_ids,
            )]
        token_ids = [str(row.get("id", "")) for row in tokens if row.get("id")]
        context_senses: list[dict] = []
        if token_ids:
            placeholders = ",".join("?" for _ in token_ids)
            context_senses = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE table_name = 'contextSenses' AND record_key IN ({placeholders}) ORDER BY rowid",
                token_ids,
            )]
        lexeme_keys = list({str(row.get("lexemeKey", "")) for row in tokens if row.get("lexemeKey")})
        lexemes: list[dict] = []
        if lexeme_keys:
            placeholders = ",".join("?" for _ in lexeme_keys)
            lexemes = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE table_name = 'lexemes' AND record_key IN ({placeholders}) ORDER BY rowid",
                lexeme_keys,
            )]
    return {
        "offset": offset, "limit": limit, "sentence_ids": sentence_ids,
        "tokens": tokens, "annotations": annotations,
        "contextSenses": context_senses, "lexemes": lexemes,
    }


def load_translation_queue(
    book_id: str,
    chapter_id: str | None = None,
    cursor: str = "",
    limit: int = 160,
    detail_mode: str = "meaning",
    include_tokens: bool = False,
) -> dict:
    """Return one stable page of pending sentences directly from SQLite.

    The cursor is based on chapter order, sentence offset and sentence id, so
    updating a completed sentence while workers are running cannot shift later
    pages and cause skipped work.
    """
    _migrate_legacy()
    after_order, after_start, after_id = -1, -1, ""
    if cursor:
        try:
            order_value, start_value, after_id = cursor.split(":", 2)
            after_order, after_start = int(order_value), int(start_value)
        except (ValueError, TypeError):
            raise ValueError("无效的翻译队列游标") from None
    with _connect() as connection:
        parameters: list[object] = [book_id]
        chapter_filter = ""
        if chapter_id:
            chapter_filter = " AND c.record_key = ?"
            parameters.append(chapter_id)
        parameters.extend([detail_mode, after_order, after_order, after_start, after_order, after_start, after_id, limit + 1])
        rows = connection.execute(
            f"""
            SELECT s.payload, c.payload,
                   CAST(json_extract(c.payload, '$.order') AS INTEGER) AS chapter_order,
                   CAST(json_extract(s.payload, '$.start') AS INTEGER) AS sentence_start,
                   s.record_key
            FROM records AS s
            JOIN records AS c
              ON c.table_name = 'chapters'
             AND c.record_key = json_extract(s.payload, '$.chapter_id')
            WHERE s.table_name = 'sentences'
              AND json_extract(c.payload, '$.bookId') = ?
              {chapter_filter}
              AND (
                    COALESCE(json_extract(s.payload, '$.translation_zh'), '') = ''
                 OR COALESCE(json_extract(s.payload, '$.explanation_status'), 'idle') != 'complete'
                 OR (? = 'full' AND json_extract(s.payload, '$.explanation_detail') = 'meaning')
              )
              AND (
                    chapter_order > ?
                 OR (chapter_order = ? AND sentence_start > ?)
                 OR (chapter_order = ? AND sentence_start = ? AND s.record_key > ?)
              )
            ORDER BY chapter_order, sentence_start, s.record_key
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        sentence_ids = [str(row[4]) for row in rows]
        tokens_by_sentence: dict[str, list[dict]] = {sentence_id: [] for sentence_id in sentence_ids}
        if include_tokens and sentence_ids:
            placeholders = ",".join("?" for _ in sentence_ids)
            for token_row in connection.execute(
                f"""SELECT payload FROM records
                    WHERE table_name = 'tokens'
                      AND json_extract(payload, '$.sentence_id') IN ({placeholders})
                    ORDER BY json_extract(payload, '$.sentence_id'),
                             CAST(json_extract(payload, '$.start') AS INTEGER)""",
                sentence_ids,
            ):
                token = json.loads(token_row[0])
                tokens_by_sentence.setdefault(str(token.get("sentence_id", "")), []).append(token)
        items = []
        for row in rows:
            sentence = json.loads(row[0])
            chapter = json.loads(row[1])
            items.append({
                "sentence": sentence,
                "tokens": tokens_by_sentence.get(str(sentence.get("id", "")), []),
                "chapterTitle": str(chapter.get("title", "")),
                "chapterOrder": int(row[2]),
            })
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = f"{int(last[2])}:{int(last[3])}:{last[4]}"
        context_before: list[str] = []
        if rows:
            first_sentence = json.loads(rows[0][0])
            previous = connection.execute(
                """SELECT json_extract(payload, '$.original') FROM records
                   WHERE table_name = 'sentences'
                     AND json_extract(payload, '$.chapter_id') = ?
                     AND CAST(json_extract(payload, '$.start') AS INTEGER) < ?
                   ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER) DESC
                   LIMIT 2""",
                (first_sentence.get("chapter_id"), int(rows[0][3])),
            ).fetchall()
            context_before = [str(value[0]) for value in reversed(previous) if value[0]]
    return {"items": items, "nextCursor": next_cursor, "contextBefore": context_before}


def load_study_data() -> StudyDataSnapshot:
    _migrate_legacy()
    with _connect() as connection:
        return StudyDataSnapshot(
            lexemes=_load_rows(connection, "lexemes"),
        )


def load_bookmarks(book_id: str) -> list[dict]:
    _migrate_legacy()
    with _connect() as connection:
        return [json.loads(row[0]) for row in connection.execute(
            """SELECT payload FROM records
               WHERE table_name = 'bookmarks' AND json_extract(payload, '$.bookId') = ?
               ORDER BY CAST(json_extract(payload, '$.chapterOrder') AS INTEGER),
                        CAST(json_extract(payload, '$.sentenceStart') AS INTEGER)""",
            (book_id,),
        )]


def find_personal_lexeme(exact_key: str, lemma: str, reading: str, surface: str = "") -> dict | None:
    _migrate_legacy()
    with _connect() as connection:
        exact = connection.execute(
            "SELECT payload FROM records WHERE table_name = 'lexemes' AND record_key = ?", (exact_key,),
        ).fetchone()
        if exact:
            return json.loads(exact[0])
        fallback = connection.execute(
            "SELECT payload FROM records WHERE table_name = 'lexemes' AND json_extract(payload, '$.reading') = ? AND (json_extract(payload, '$.lemma') = ? OR json_extract(payload, '$.lemma') = ?) LIMIT 1",
            (reading, lemma, surface),
        ).fetchone()
        return json.loads(fallback[0]) if fallback else None


def apply_library_patch(patch: LibraryPatch) -> dict[str, int]:
    changed = 0
    with _connect() as connection:
        for table_name, keys in patch.deletes.items():
            if table_name not in TABLE_KEYS:
                raise ValueError(f"未知数据表：{table_name}")
            connection.executemany(
                "DELETE FROM records WHERE table_name = ? AND record_key = ?",
                ((table_name, str(key)) for key in keys),
            )
            changed += len(keys)
        for table_name, rows in patch.upserts.items():
            key_name = TABLE_KEYS.get(table_name)
            if not key_name:
                raise ValueError(f"未知数据表：{table_name}")
            values = []
            for row in rows:
                if key_name not in row:
                    raise ValueError(f"{table_name} 记录缺少主键 {key_name}")
                values.append((table_name, str(row[key_name]), json.dumps(row, ensure_ascii=False, separators=(",", ":"))))
            connection.executemany(
                "INSERT INTO records (table_name, record_key, payload) VALUES (?, ?, ?) ON CONFLICT(table_name, record_key) DO UPDATE SET payload = excluded.payload",
                values,
            )
            changed += len(values)
    return {"changed": changed}


def delete_book(book_id: str) -> dict[str, Any]:
    with _connect() as connection:
        chapter_rows = connection.execute(
            "SELECT record_key, payload FROM records WHERE table_name = 'chapters' AND json_extract(payload, '$.bookId') = ?", (book_id,),
        ).fetchall()
        chapter_ids = [row[0] for row in chapter_rows]
        target_payloads = [row[1] for row in chapter_rows]
        book_row = connection.execute(
            "SELECT payload FROM records WHERE table_name = 'books' AND record_key = ?", (book_id,),
        ).fetchone()
        if book_row:
            target_payloads.append(book_row[0])
        target_resource_keys = _resource_keys(target_payloads)
        sentence_ids: list[str] = []
        if chapter_ids:
            placeholders = ",".join("?" for _ in chapter_ids)
            sentence_ids = [row[0] for row in connection.execute(
                f"SELECT record_key FROM records WHERE table_name = 'sentences' AND json_extract(payload, '$.chapter_id') IN ({placeholders})",
                chapter_ids,
            )]
        token_ids: list[str] = []
        if sentence_ids:
            placeholders = ",".join("?" for _ in sentence_ids)
            token_ids = [row[0] for row in connection.execute(
                f"SELECT record_key FROM records WHERE table_name = 'tokens' AND json_extract(payload, '$.sentence_id') IN ({placeholders})",
                sentence_ids,
            )]
            for table_name in ("tokens", "annotations", "sentences"):
                source_field = "sentence_id" if table_name != "sentences" else "id"
                connection.execute(
                    f"DELETE FROM records WHERE table_name = ? AND json_extract(payload, '$.{source_field}') IN ({placeholders})",
                    (table_name, *sentence_ids),
                )
        if token_ids:
            placeholders = ",".join("?" for _ in token_ids)
            connection.execute(
                f"DELETE FROM records WHERE table_name = 'contextSenses' AND record_key IN ({placeholders})", token_ids,
            )
        if chapter_ids:
            placeholders = ",".join("?" for _ in chapter_ids)
            connection.execute(
                f"DELETE FROM records WHERE table_name = 'chapters' AND record_key IN ({placeholders})", chapter_ids,
            )
        connection.execute("DELETE FROM records WHERE table_name = 'bookmarks' AND json_extract(payload, '$.bookId') = ?", (book_id,))
        connection.execute("DELETE FROM records WHERE table_name = 'books' AND record_key = ?", (book_id,))
        remaining_resource_keys = _resource_keys([
            row[0] for row in connection.execute(
                "SELECT payload FROM records WHERE table_name IN ('books', 'chapters')"
            )
        ])
    return {
        "chapters": len(chapter_ids), "sentences": len(sentence_ids), "tokens": len(token_ids),
        "resource_keys": sorted(target_resource_keys - remaining_resource_keys),
    }


def save_library(snapshot: LibrarySnapshot) -> LibrarySnapshot:
    values = snapshot.model_dump()
    with _connect() as connection:
        for table_name, key_name in TABLE_KEYS.items():
            rows = values.get(table_name, [])
            incoming_keys = {str(row[key_name]) for row in rows}
            existing_keys = {row[0] for row in connection.execute("SELECT record_key FROM records WHERE table_name = ?", (table_name,))}
            removed = existing_keys - incoming_keys
            if removed:
                connection.executemany("DELETE FROM records WHERE table_name = ? AND record_key = ?", ((table_name, key) for key in removed))
            connection.executemany(
                "INSERT INTO records (table_name, record_key, payload) VALUES (?, ?, ?) ON CONFLICT(table_name, record_key) DO UPDATE SET payload = excluded.payload",
                ((table_name, str(row[key_name]), json.dumps(row, ensure_ascii=False, separators=(",", ":"))) for row in rows),
            )
    return snapshot
