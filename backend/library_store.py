import json
import sqlite3
from pathlib import Path

from .models import LibrarySnapshot


LIBRARY_PATH = Path(__file__).resolve().parent.parent / "data" / "library.sqlite3"
LEGACY_PATH = Path(__file__).resolve().parent.parent / "data" / "library.json"
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
