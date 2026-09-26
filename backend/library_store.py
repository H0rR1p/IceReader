import json
import sqlite3
from .models import ChapterSnapshot, LibraryIndex, LibraryPatch, LibrarySnapshot, StudyDataSnapshot
from .paths import DATA_DIR


LIBRARY_PATH = DATA_DIR / "library.sqlite3"
LEGACY_PATH = DATA_DIR / "library.json"
TABLE_KEYS = {
    "books": "id", "chapters": "id", "sentences": "id", "tokens": "id",
    "annotations": "id", "contextSenses": "token_id", "lexemes": "key", "cards": "id",
}


def _connect() -> sqlite3.Connection:
    LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(LIBRARY_PATH)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("CREATE TABLE IF NOT EXISTS records (table_name TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (table_name, record_key))")
    return connection


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
        return LibraryIndex(
            books=_load_rows(connection, "books"),
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


def load_study_data() -> StudyDataSnapshot:
    _migrate_legacy()
    with _connect() as connection:
        return StudyDataSnapshot(
            lexemes=_load_rows(connection, "lexemes"),
            cards=_load_rows(connection, "cards"),
        )


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


def delete_book(book_id: str) -> dict[str, int]:
    with _connect() as connection:
        chapter_ids = [row[0] for row in connection.execute(
            "SELECT record_key FROM records WHERE table_name = 'chapters' AND json_extract(payload, '$.bookId') = ?", (book_id,),
        )]
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
        connection.execute("DELETE FROM records WHERE table_name = 'cards' AND json_extract(payload, '$.bookId') = ?", (book_id,))
        connection.execute("DELETE FROM records WHERE table_name = 'books' AND record_key = ?", (book_id,))
    return {"chapters": len(chapter_ids), "sentences": len(sentence_ids), "tokens": len(token_ids)}


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
