import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from ...models import ChapterSnapshot, LibraryIndex, LibraryPatch, LibrarySnapshot, StudyDataSnapshot
from ...paths import DATA_DIR
from ..jobs.service import record_observation


LIBRARY_PATH = DATA_DIR / "library.sqlite3"
LEGACY_PATH = DATA_DIR / "library.json"
LIBRARY_SCHEMA_VERSION = 5
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
    "idx_chapters_book_order_v2", "idx_sentences_chapter_start_v2",
    "idx_records_sentence_v2", "idx_lexemes_reading_lemma_v2",
}
INDEX_DEFINITIONS = {
    "idx_chapters_book_order_v3": "CREATE INDEX idx_chapters_book_order_v3 ON records(owner_user_id, table_name, json_extract(payload, '$.bookId'), CAST(json_extract(payload, '$.order') AS INTEGER))",
    "idx_sentences_chapter_start_v3": "CREATE INDEX idx_sentences_chapter_start_v3 ON records(owner_user_id, table_name, json_extract(payload, '$.chapter_id'), CAST(json_extract(payload, '$.start') AS INTEGER))",
    "idx_records_sentence_v3": "CREATE INDEX idx_records_sentence_v3 ON records(owner_user_id, table_name, json_extract(payload, '$.sentence_id'))",
    "idx_lexemes_reading_lemma_v3": "CREATE INDEX idx_lexemes_reading_lemma_v3 ON records(owner_user_id, table_name, json_extract(payload, '$.reading'), json_extract(payload, '$.lemma'))",
}


def _initialize_connection(connection: sqlite3.Connection, path: Path, migration_user_id: str) -> None:
    with _initialization_lock:
        if path in _initialized_paths:
            return
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS records (owner_user_id TEXT NOT NULL, table_name TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (owner_user_id, table_name, record_key))")
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(records)")}
        if "owner_user_id" not in columns:
            backup_path = path.parent / "migration-backups" / "library.pre-user-boundary.sqlite3"
            if not backup_path.exists():
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(backup_path) as backup_connection:
                    connection.backup(backup_connection)
            connection.execute(
                "CREATE TABLE records_v3 (owner_user_id TEXT NOT NULL, table_name TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (owner_user_id, table_name, record_key))"
            )
            connection.execute(
                "INSERT INTO records_v3(owner_user_id, table_name, record_key, payload) SELECT ?, table_name, record_key, payload FROM records",
                (migration_user_id,),
            )
            connection.execute("DROP TABLE records")
            connection.execute("ALTER TABLE records_v3 RENAME TO records")
            schema_version = 2
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_library_items (
                user_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (user_id, book_id)
            );
            CREATE TABLE IF NOT EXISTS user_book_progress (
                user_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                chapter_id TEXT,
                sentence_id TEXT,
                last_opened_at REAL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (user_id, book_id)
            );
            CREATE TABLE IF NOT EXISTS user_bookmarks (
                user_id TEXT NOT NULL,
                bookmark_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                sentence_id TEXT NOT NULL,
                chapter_order INTEGER NOT NULL DEFAULT 0,
                sentence_start INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (user_id, bookmark_id)
            );
            CREATE INDEX IF NOT EXISTS idx_user_bookmarks_book_order
                ON user_bookmarks(user_id, book_id, chapter_order, sentence_start);
            CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                delivered_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_library_outbox_pending
                ON outbox(delivered_at, created_at);
            """
        )
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
            connection.execute(
                """INSERT OR IGNORE INTO user_library_items(user_id, book_id, metadata_json, created_at, updated_at)
                   SELECT owner_user_id, record_key, payload,
                          COALESCE(json_extract(payload, '$.createdAt'), 0),
                          COALESCE(json_extract(payload, '$.updatedAt'), 0)
                   FROM records WHERE table_name = 'books'"""
            )
            connection.execute(
                """INSERT OR IGNORE INTO user_book_progress(user_id, book_id, chapter_id, sentence_id, last_opened_at, updated_at)
                   SELECT owner_user_id, record_key,
                          json_extract(payload, '$.currentChapterId'),
                          json_extract(payload, '$.currentSentenceId'),
                          json_extract(payload, '$.lastOpenedAt'),
                          COALESCE(json_extract(payload, '$.updatedAt'), 0)
                   FROM records WHERE table_name = 'books'"""
            )
            connection.execute(
                """INSERT OR IGNORE INTO user_bookmarks(
                       user_id, bookmark_id, book_id, chapter_id, sentence_id,
                       chapter_order, sentence_start, payload_json, created_at, updated_at
                   )
                   SELECT owner_user_id, record_key,
                          COALESCE(json_extract(payload, '$.bookId'), ''),
                          COALESCE(json_extract(payload, '$.chapterId'), ''),
                          COALESCE(json_extract(payload, '$.sentenceId'), record_key),
                          COALESCE(json_extract(payload, '$.chapterOrder'), 0),
                          COALESCE(json_extract(payload, '$.sentenceStart'), 0),
                          payload,
                          COALESCE(json_extract(payload, '$.createdAt'), 0),
                          COALESCE(json_extract(payload, '$.updatedAt'), json_extract(payload, '$.createdAt'), 0)
                   FROM records WHERE table_name = 'bookmarks'"""
            )
            connection.execute(f"PRAGMA user_version = {LIBRARY_SCHEMA_VERSION}")
        connection.commit()
        _initialized_paths.add(path)


def initialize_store(migration_user_id: str) -> None:
    with _connect(migration_user_id):
        pass


@contextmanager
def _connect(migration_user_id: str):
    started_at = time.perf_counter()
    path = LIBRARY_PATH.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    if not existed:
        with _initialization_lock:
            _initialized_paths.discard(path)
    connection = sqlite3.connect(path)
    _initialize_connection(connection, path, migration_user_id)
    connection.execute("PRAGMA synchronous=NORMAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        duration_ms = (time.perf_counter() - started_at) * 1000
        if duration_ms >= 100:
            record_observation(
                "slow_query", "library.transaction",
                owner_user_id=migration_user_id, duration_ms=duration_ms,
            )


def _resource_keys(payloads: list[str]) -> set[str]:
    return {
        match.group(1)
        for payload in payloads
        for match in _RESOURCE_KEY_PATTERN.finditer(payload)
    }


def _migrate_legacy(user_id: str) -> None:
    if LIBRARY_PATH.exists() or not LEGACY_PATH.exists():
        return
    try:
        snapshot = LibrarySnapshot.model_validate_json(LEGACY_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return
    save_library(user_id, snapshot)
    LEGACY_PATH.replace(LEGACY_PATH.with_suffix(".json.migrated"))


_PROGRESS_FIELDS = {"currentChapterId", "currentSentenceId", "lastOpenedAt"}


def _book_metadata(book: dict) -> dict:
    """Keep reusable book metadata separate from per-user reading progress."""
    return {key: value for key, value in book.items() if key not in _PROGRESS_FIELDS}


def _load_user_books(connection: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = connection.execute(
        """SELECT item.metadata_json, progress.chapter_id, progress.sentence_id,
                  progress.last_opened_at
           FROM user_library_items AS item
           LEFT JOIN user_book_progress AS progress
             ON progress.user_id = item.user_id AND progress.book_id = item.book_id
           WHERE item.user_id = ?
           ORDER BY item.created_at, item.rowid""",
        (user_id,),
    ).fetchall()
    books: list[dict] = []
    for metadata_json, chapter_id, sentence_id, last_opened_at in rows:
        book = json.loads(metadata_json)
        for field in _PROGRESS_FIELDS:
            book.pop(field, None)
        if chapter_id:
            book["currentChapterId"] = chapter_id
        if sentence_id:
            book["currentSentenceId"] = sentence_id
        if last_opened_at is not None:
            book["lastOpenedAt"] = last_opened_at
        books.append(book)
    return books


def load_library(user_id: str) -> LibrarySnapshot | None:
    _migrate_legacy(user_id)
    if not LIBRARY_PATH.exists():
        return None
    values: dict[str, list[dict]] = {name: [] for name in TABLE_KEYS}
    with _connect(user_id) as connection:
        for table_name, payload in connection.execute(
            "SELECT table_name, payload FROM records WHERE owner_user_id = ? ORDER BY rowid", (user_id,),
        ):
            if table_name in values:
                values[table_name].append(json.loads(payload))
        values["books"] = _load_user_books(connection, user_id)
        values["bookmarks"] = [json.loads(row[0]) for row in connection.execute(
            "SELECT payload_json FROM user_bookmarks WHERE user_id = ? ORDER BY chapter_order, sentence_start",
            (user_id,),
        )]
    return LibrarySnapshot(**values)


def _load_rows(connection: sqlite3.Connection, user_id: str, table_name: str) -> list[dict]:
    return [json.loads(row[0]) for row in connection.execute(
        "SELECT payload FROM records WHERE owner_user_id = ? AND table_name = ? ORDER BY rowid",
        (user_id, table_name),
    )]


def load_library_index(user_id: str) -> LibraryIndex:
    _migrate_legacy(user_id)
    with _connect(user_id) as connection:
        chapters = _load_rows(connection, user_id, "chapters")
        translation_rows = connection.execute(
            """
            SELECT json_extract(payload, '$.chapter_id') AS chapter_id,
                   COUNT(*) AS sentence_count,
                   SUM(CASE
                         WHEN TRIM(COALESCE(json_extract(payload, '$.translation_zh'), '')) != ''
                          AND COALESCE(json_extract(payload, '$.explanation_status'), 'idle') = 'complete'
                         THEN 1 ELSE 0 END) AS translated_count
            FROM records
            WHERE owner_user_id = ? AND table_name = 'sentences'
            GROUP BY chapter_id
            """,
            (user_id,),
        ).fetchall()
        progress_by_chapter = {
            str(chapter_id): (int(sentence_count), int(translated_count or 0))
            for chapter_id, sentence_count, translated_count in translation_rows
        }
        chapters_by_book: dict[str, list[dict]] = {}
        for chapter in chapters:
            if str(chapter.get("text", "")).strip():
                chapters_by_book.setdefault(str(chapter.get("bookId", "")), []).append(chapter)
        books = _load_user_books(connection, user_id)
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


def load_chapter(user_id: str, chapter_id: str) -> ChapterSnapshot:
    _migrate_legacy(user_id)
    with _connect(user_id) as connection:
        chapter_row = connection.execute(
            "SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'chapters' AND record_key = ?",
            (user_id, chapter_id),
        ).fetchone()
        chapter = json.loads(chapter_row[0]) if chapter_row else None
        sentences = [json.loads(row[0]) for row in connection.execute(
            "SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'sentences' AND json_extract(payload, '$.chapter_id') = ? ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER)",
            (user_id, chapter_id),
        )]
        sentence_ids = [str(row.get("id", "")) for row in sentences if row.get("id")]
        tokens: list[dict] = []
        annotations: list[dict] = []
        if sentence_ids:
            placeholders = ",".join("?" for _ in sentence_ids)
            tokens = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'tokens' AND json_extract(payload, '$.sentence_id') IN ({placeholders}) ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER)",
                (user_id, *sentence_ids),
            )]
            annotations = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'annotations' AND json_extract(payload, '$.sentence_id') IN ({placeholders}) ORDER BY rowid",
                (user_id, *sentence_ids),
            )]
        token_ids = [str(row.get("id", "")) for row in tokens if row.get("id")]
        context_senses: list[dict] = []
        if token_ids:
            placeholders = ",".join("?" for _ in token_ids)
            context_senses = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'contextSenses' AND record_key IN ({placeholders}) ORDER BY rowid",
                (user_id, *token_ids),
            )]
        lexeme_keys = list({str(row.get("lexemeKey", "")) for row in tokens if row.get("lexemeKey")})
        lexemes: list[dict] = []
        if lexeme_keys:
            placeholders = ",".join("?" for _ in lexeme_keys)
            lexemes = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'lexemes' AND record_key IN ({placeholders}) ORDER BY rowid",
                (user_id, *lexeme_keys),
            )]
    return ChapterSnapshot(
        chapter=chapter, sentences=sentences, tokens=tokens, annotations=annotations,
        contextSenses=context_senses, lexemes=lexemes,
    )


def load_chapter_view(user_id: str, chapter_id: str) -> ChapterSnapshot:
    """Load chapter text and sentence rows without the much larger token graph."""
    _migrate_legacy(user_id)
    with _connect(user_id) as connection:
        chapter_row = connection.execute(
            "SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'chapters' AND record_key = ?",
            (user_id, chapter_id),
        ).fetchone()
        chapter = json.loads(chapter_row[0]) if chapter_row else None
        sentences = [json.loads(row[0]) for row in connection.execute(
            "SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'sentences' AND json_extract(payload, '$.chapter_id') = ? ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER)",
            (user_id, chapter_id),
        )]
    return ChapterSnapshot(chapter=chapter, sentences=sentences)


def load_chapter_details(user_id: str, chapter_id: str, offset: int, limit: int) -> dict:
    """Load token and annotation data only for one visible sentence window."""
    _migrate_legacy(user_id)
    with _connect(user_id) as connection:
        sentence_ids = [row[0] for row in connection.execute(
            """
            SELECT record_key FROM records
            WHERE owner_user_id = ? AND table_name = 'sentences' AND json_extract(payload, '$.chapter_id') = ?
            ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER)
            LIMIT ? OFFSET ?
            """,
            (user_id, chapter_id, limit, offset),
        )]
        tokens: list[dict] = []
        annotations: list[dict] = []
        if sentence_ids:
            placeholders = ",".join("?" for _ in sentence_ids)
            tokens = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'tokens' AND json_extract(payload, '$.sentence_id') IN ({placeholders}) ORDER BY rowid",
                (user_id, *sentence_ids),
            )]
            annotations = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'annotations' AND json_extract(payload, '$.sentence_id') IN ({placeholders}) ORDER BY rowid",
                (user_id, *sentence_ids),
            )]
        token_ids = [str(row.get("id", "")) for row in tokens if row.get("id")]
        context_senses: list[dict] = []
        if token_ids:
            placeholders = ",".join("?" for _ in token_ids)
            context_senses = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'contextSenses' AND record_key IN ({placeholders}) ORDER BY rowid",
                (user_id, *token_ids),
            )]
        lexeme_keys = list({str(row.get("lexemeKey", "")) for row in tokens if row.get("lexemeKey")})
        lexemes: list[dict] = []
        if lexeme_keys:
            placeholders = ",".join("?" for _ in lexeme_keys)
            lexemes = [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'lexemes' AND record_key IN ({placeholders}) ORDER BY rowid",
                (user_id, *lexeme_keys),
            )]
    return {
        "offset": offset, "limit": limit, "sentence_ids": sentence_ids,
        "tokens": tokens, "annotations": annotations,
        "contextSenses": context_senses, "lexemes": lexemes,
    }


def load_translation_queue(
    user_id: str,
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
    _migrate_legacy(user_id)
    after_order, after_start, after_id = -1, -1, ""
    if cursor:
        try:
            order_value, start_value, after_id = cursor.split(":", 2)
            after_order, after_start = int(order_value), int(start_value)
        except (ValueError, TypeError):
            raise ValueError("无效的翻译队列游标") from None
    with _connect(user_id) as connection:
        parameters: list[object] = [user_id, user_id, book_id]
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
              ON c.owner_user_id = s.owner_user_id
             AND c.table_name = 'chapters'
             AND c.record_key = json_extract(s.payload, '$.chapter_id')
            WHERE s.owner_user_id = ?
              AND c.owner_user_id = ?
              AND s.table_name = 'sentences'
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
                    WHERE owner_user_id = ? AND table_name = 'tokens'
                      AND json_extract(payload, '$.sentence_id') IN ({placeholders})
                    ORDER BY json_extract(payload, '$.sentence_id'),
                             CAST(json_extract(payload, '$.start') AS INTEGER)""",
                (user_id, *sentence_ids),
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
                   WHERE owner_user_id = ? AND table_name = 'sentences'
                     AND json_extract(payload, '$.chapter_id') = ?
                     AND CAST(json_extract(payload, '$.start') AS INTEGER) < ?
                   ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER) DESC
                   LIMIT 2""",
                (user_id, first_sentence.get("chapter_id"), int(rows[0][3])),
            ).fetchall()
            context_before = [str(value[0]) for value in reversed(previous) if value[0]]
    return {"items": items, "nextCursor": next_cursor, "contextBefore": context_before}


def load_study_data(user_id: str) -> StudyDataSnapshot:
    _migrate_legacy(user_id)
    with _connect(user_id) as connection:
        return StudyDataSnapshot(
            lexemes=_load_rows(connection, user_id, "lexemes"),
        )


def load_bookmarks(user_id: str, book_id: str) -> list[dict]:
    _migrate_legacy(user_id)
    with _connect(user_id) as connection:
        return [json.loads(row[0]) for row in connection.execute(
            """SELECT payload_json FROM user_bookmarks
               WHERE user_id = ? AND book_id = ?
               ORDER BY chapter_order, sentence_start""",
            (user_id, book_id),
        )]


def find_personal_lexeme(user_id: str, exact_key: str, lemma: str, reading: str, surface: str = "") -> dict | None:
    _migrate_legacy(user_id)
    with _connect(user_id) as connection:
        exact = connection.execute(
            "SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'lexemes' AND record_key = ?",
            (user_id, exact_key),
        ).fetchone()
        if exact:
            return json.loads(exact[0])
        fallback = connection.execute(
            "SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'lexemes' AND json_extract(payload, '$.reading') = ? AND (json_extract(payload, '$.lemma') = ? OR json_extract(payload, '$.lemma') = ?) LIMIT 1",
            (user_id, reading, lemma, surface),
        ).fetchone()
        return json.loads(fallback[0]) if fallback else None


def owns_book(user_id: str, book_id: str) -> bool:
    with _connect(user_id) as connection:
        return connection.execute(
            "SELECT 1 FROM user_library_items WHERE user_id = ? AND book_id = ?",
            (user_id, book_id),
        ).fetchone() is not None


def owns_resource_key(user_id: str, resource_key: str) -> bool:
    marker = f"/api/assets/{resource_key}/"
    with _connect(user_id) as connection:
        return connection.execute(
            """SELECT 1 FROM records
               WHERE owner_user_id = ? AND table_name IN ('books', 'chapters')
                 AND instr(payload, ?) > 0 LIMIT 1""",
            (user_id, marker),
        ).fetchone() is not None


def apply_library_patch(user_id: str, patch: LibraryPatch) -> dict[str, int]:
    changed = 0
    with _connect(user_id) as connection:
        for table_name, keys in patch.deletes.items():
            if table_name not in TABLE_KEYS:
                raise ValueError(f"未知数据表：{table_name}")
            connection.executemany(
                "DELETE FROM records WHERE owner_user_id = ? AND table_name = ? AND record_key = ?",
                ((user_id, table_name, str(key)) for key in keys),
            )
            if table_name == "books":
                connection.executemany(
                    "DELETE FROM user_library_items WHERE user_id = ? AND book_id = ?",
                    ((user_id, str(key)) for key in keys),
                )
                connection.executemany(
                    "DELETE FROM user_book_progress WHERE user_id = ? AND book_id = ?",
                    ((user_id, str(key)) for key in keys),
                )
            elif table_name == "bookmarks":
                connection.executemany(
                    "DELETE FROM user_bookmarks WHERE user_id = ? AND bookmark_id = ?",
                    ((user_id, str(key)) for key in keys),
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
                values.append((user_id, table_name, str(row[key_name]), json.dumps(row, ensure_ascii=False, separators=(",", ":"))))
            connection.executemany(
                "INSERT INTO records (owner_user_id, table_name, record_key, payload) VALUES (?, ?, ?, ?) ON CONFLICT(owner_user_id, table_name, record_key) DO UPDATE SET payload = excluded.payload",
                values,
            )
            if table_name == "books":
                connection.executemany(
                    """INSERT INTO user_library_items(user_id, book_id, metadata_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, book_id) DO UPDATE SET
                           metadata_json = excluded.metadata_json, updated_at = excluded.updated_at""",
                    (
                        (
                            user_id, str(row[key_name]),
                            json.dumps(_book_metadata(row), ensure_ascii=False, separators=(",", ":")),
                            float(row.get("createdAt") or 0), float(row.get("updatedAt") or 0),
                        )
                        for row in rows
                    ),
                )
                connection.executemany(
                    """INSERT INTO user_book_progress(user_id, book_id, chapter_id, sentence_id, last_opened_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, book_id) DO UPDATE SET
                           chapter_id = excluded.chapter_id, sentence_id = excluded.sentence_id,
                           last_opened_at = excluded.last_opened_at, updated_at = excluded.updated_at""",
                    (
                        (
                            user_id, str(row[key_name]), row.get("currentChapterId"),
                            row.get("currentSentenceId"), row.get("lastOpenedAt"),
                            float(row.get("updatedAt") or 0),
                        )
                        for row in rows
                    ),
                )
            elif table_name == "bookmarks":
                connection.executemany(
                    """INSERT INTO user_bookmarks(
                           user_id, bookmark_id, book_id, chapter_id, sentence_id,
                           chapter_order, sentence_start, payload_json, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, bookmark_id) DO UPDATE SET
                           book_id = excluded.book_id, chapter_id = excluded.chapter_id,
                           sentence_id = excluded.sentence_id, chapter_order = excluded.chapter_order,
                           sentence_start = excluded.sentence_start, payload_json = excluded.payload_json,
                           updated_at = excluded.updated_at""",
                    (
                        (
                            user_id, str(row[key_name]), str(row.get("bookId") or ""),
                            str(row.get("chapterId") or ""), str(row.get("sentenceId") or row[key_name]),
                            int(row.get("chapterOrder") or 0), int(row.get("sentenceStart") or 0),
                            json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                            float(row.get("createdAt") or 0),
                            float(row.get("updatedAt") or row.get("createdAt") or 0),
                        )
                        for row in rows
                    ),
                )
            changed += len(values)
        if changed:
            connection.execute(
                "INSERT INTO outbox(id, owner_user_id, topic, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()), user_id, "library.changed",
                    json.dumps({
                        "upsert_tables": sorted(patch.upserts),
                        "delete_tables": sorted(patch.deletes),
                        "changed": changed,
                    }, ensure_ascii=False, separators=(",", ":")),
                    time.time(),
                ),
            )
    return {"changed": changed}


def delete_book(user_id: str, book_id: str) -> dict[str, Any]:
    with _connect(user_id) as connection:
        chapter_rows = connection.execute(
            "SELECT record_key, payload FROM records WHERE owner_user_id = ? AND table_name = 'chapters' AND json_extract(payload, '$.bookId') = ?",
            (user_id, book_id),
        ).fetchall()
        chapter_ids = [row[0] for row in chapter_rows]
        target_payloads = [row[1] for row in chapter_rows]
        book_row = connection.execute(
            "SELECT payload FROM records WHERE owner_user_id = ? AND table_name = 'books' AND record_key = ?",
            (user_id, book_id),
        ).fetchone()
        if book_row:
            target_payloads.append(book_row[0])
        target_resource_keys = _resource_keys(target_payloads)
        sentence_ids: list[str] = []
        if chapter_ids:
            placeholders = ",".join("?" for _ in chapter_ids)
            sentence_ids = [row[0] for row in connection.execute(
                f"SELECT record_key FROM records WHERE owner_user_id = ? AND table_name = 'sentences' AND json_extract(payload, '$.chapter_id') IN ({placeholders})",
                (user_id, *chapter_ids),
            )]
        token_ids: list[str] = []
        if sentence_ids:
            placeholders = ",".join("?" for _ in sentence_ids)
            token_ids = [row[0] for row in connection.execute(
                f"SELECT record_key FROM records WHERE owner_user_id = ? AND table_name = 'tokens' AND json_extract(payload, '$.sentence_id') IN ({placeholders})",
                (user_id, *sentence_ids),
            )]
            for table_name in ("tokens", "annotations", "sentences"):
                source_field = "sentence_id" if table_name != "sentences" else "id"
                connection.execute(
                    f"DELETE FROM records WHERE owner_user_id = ? AND table_name = ? AND json_extract(payload, '$.{source_field}') IN ({placeholders})",
                    (user_id, table_name, *sentence_ids),
                )
        if token_ids:
            placeholders = ",".join("?" for _ in token_ids)
            connection.execute(
                f"DELETE FROM records WHERE owner_user_id = ? AND table_name = 'contextSenses' AND record_key IN ({placeholders})",
                (user_id, *token_ids),
            )
        if chapter_ids:
            placeholders = ",".join("?" for _ in chapter_ids)
            connection.execute(
                f"DELETE FROM records WHERE owner_user_id = ? AND table_name = 'chapters' AND record_key IN ({placeholders})",
                (user_id, *chapter_ids),
            )
        connection.execute("DELETE FROM records WHERE owner_user_id = ? AND table_name = 'bookmarks' AND json_extract(payload, '$.bookId') = ?", (user_id, book_id))
        connection.execute("DELETE FROM records WHERE owner_user_id = ? AND table_name = 'books' AND record_key = ?", (user_id, book_id))
        connection.execute("DELETE FROM user_library_items WHERE user_id = ? AND book_id = ?", (user_id, book_id))
        connection.execute("DELETE FROM user_book_progress WHERE user_id = ? AND book_id = ?", (user_id, book_id))
        connection.execute("DELETE FROM user_bookmarks WHERE user_id = ? AND book_id = ?", (user_id, book_id))
        connection.execute(
            "INSERT INTO outbox(id, owner_user_id, topic, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), user_id, "library.book_deleted",
                json.dumps({"book_id": book_id}, ensure_ascii=False, separators=(",", ":")),
                time.time(),
            ),
        )
        remaining_resource_keys = _resource_keys([
            row[0] for row in connection.execute(
                "SELECT payload FROM records WHERE table_name IN ('books', 'chapters')",
            )
        ])
    return {
        "chapters": len(chapter_ids), "sentences": len(sentence_ids), "tokens": len(token_ids),
        "resource_keys": sorted(target_resource_keys - remaining_resource_keys),
    }


def save_library(user_id: str, snapshot: LibrarySnapshot) -> LibrarySnapshot:
    values = snapshot.model_dump()
    with _connect(user_id) as connection:
        for table_name, key_name in TABLE_KEYS.items():
            rows = values.get(table_name, [])
            incoming_keys = {str(row[key_name]) for row in rows}
            existing_keys = {row[0] for row in connection.execute("SELECT record_key FROM records WHERE owner_user_id = ? AND table_name = ?", (user_id, table_name))}
            removed = existing_keys - incoming_keys
            if removed:
                connection.executemany("DELETE FROM records WHERE owner_user_id = ? AND table_name = ? AND record_key = ?", ((user_id, table_name, key) for key in removed))
            connection.executemany(
                "INSERT INTO records (owner_user_id, table_name, record_key, payload) VALUES (?, ?, ?, ?) ON CONFLICT(owner_user_id, table_name, record_key) DO UPDATE SET payload = excluded.payload",
                ((user_id, table_name, str(row[key_name]), json.dumps(row, ensure_ascii=False, separators=(",", ":"))) for row in rows),
            )
    with _connect(user_id) as connection:
        book_ids = {str(row["id"]) for row in values.get("books", [])}
        bookmark_ids = {str(row["id"]) for row in values.get("bookmarks", [])}
        if book_ids:
            placeholders = ",".join("?" for _ in book_ids)
            connection.execute(
                f"DELETE FROM user_library_items WHERE user_id = ? AND book_id NOT IN ({placeholders})",
                (user_id, *book_ids),
            )
            connection.execute(
                f"DELETE FROM user_book_progress WHERE user_id = ? AND book_id NOT IN ({placeholders})",
                (user_id, *book_ids),
            )
        else:
            connection.execute("DELETE FROM user_library_items WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM user_book_progress WHERE user_id = ?", (user_id,))
        if bookmark_ids:
            placeholders = ",".join("?" for _ in bookmark_ids)
            connection.execute(
                f"DELETE FROM user_bookmarks WHERE user_id = ? AND bookmark_id NOT IN ({placeholders})",
                (user_id, *bookmark_ids),
            )
        else:
            connection.execute("DELETE FROM user_bookmarks WHERE user_id = ?", (user_id,))
    apply_library_patch(user_id, LibraryPatch(upserts={
        "books": values.get("books", []), "bookmarks": values.get("bookmarks", []),
    }))
    return snapshot
