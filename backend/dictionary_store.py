import io
import json
import sqlite3
import time
import unicodedata
import zipfile
from pathlib import Path

from .nlp import kata
from .paths import DATA_DIR


DICTIONARY_PATH = DATA_DIR / "dictionary.sqlite3"


def _connect() -> sqlite3.Connection:
    DICTIONARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DICTIONARY_PATH)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, lemma TEXT NOT NULL, reading TEXT NOT NULL, senses TEXT NOT NULL, source TEXT NOT NULL)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_entries_lemma_reading ON entries (lemma, reading)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_entries_source ON entries (source)")
    connection.execute("""CREATE TABLE IF NOT EXISTS dictionary_sources(
        source TEXT PRIMARY KEY, package_id TEXT, version TEXT, license TEXT,
        homepage TEXT, entries INTEGER NOT NULL, installed_at REAL NOT NULL
    )""")
    return connection


def _plain_gloss(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _plain_gloss(item)]
    if isinstance(value, dict):
        if "content" in value:
            return _plain_gloss(value["content"])
        return []
    return []


def _import_archive(archive: zipfile.ZipFile, filename: str, metadata_override: dict | None = None) -> dict:
    total_size = sum(item.file_size for item in archive.infolist())
    if total_size > 800 * 1024 * 1024:
        raise ValueError("词典解压后不能超过 800 MB")
    source = Path(filename).stem
    metadata: dict = {}
    if "index.json" in archive.namelist():
        try:
            metadata = json.loads(archive.read("index.json"))
            source = str(metadata.get("title") or source)
        except (ValueError, UnicodeDecodeError):
            metadata = {}
    metadata.update(metadata_override or {})
    source = str(metadata.get("source") or source)
    banks = sorted(name for name in archive.namelist() if Path(name).name.startswith("term_bank_") and name.endswith(".json"))
    if not banks:
        raise ValueError("没有找到 Yomitan term_bank_*.json")
    inserted = 0
    with _connect() as connection:
        connection.execute("DELETE FROM entries WHERE source = ?", (source,))
        for name in banks:
            entries = json.loads(archive.read(name))
            rows: list[tuple[str, str, str, str]] = []
            for entry in entries:
                if not isinstance(entry, list) or len(entry) < 6:
                    continue
                lemma = str(entry[0]).strip()
                reading = kata(str(entry[1] or entry[0]).strip())
                senses = list(dict.fromkeys(_plain_gloss(entry[5])))
                if lemma and senses:
                    rows.append((lemma, reading, json.dumps(senses, ensure_ascii=False), source))
            connection.executemany("INSERT INTO entries (lemma, reading, senses, source) VALUES (?, ?, ?, ?)", rows)
            inserted += len(rows)
        connection.execute(
            """INSERT INTO dictionary_sources(source,package_id,version,license,homepage,entries,installed_at)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET package_id=excluded.package_id,
               version=excluded.version,license=excluded.license,homepage=excluded.homepage,
               entries=excluded.entries,installed_at=excluded.installed_at""",
            (source, str(metadata.get("package_id") or ""), str(metadata.get("version") or metadata.get("revision") or ""),
             str(metadata.get("license") or ""), str(metadata.get("homepage") or ""), inserted, time.time()),
        )
    return {"source": source, "entries": inserted}


def import_yomitan(payload: bytes, filename: str, metadata: dict | None = None) -> dict:
    if len(payload) > 200 * 1024 * 1024:
        raise ValueError("词典 ZIP 不能超过 200 MB")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return _import_archive(archive, filename, metadata)


def import_yomitan_path(path: Path, metadata: dict | None = None) -> dict:
    if path.stat().st_size > 200 * 1024 * 1024:
        raise ValueError("词典 ZIP 不能超过 200 MB")
    with zipfile.ZipFile(path) as archive:
        return _import_archive(archive, path.name, metadata)


def dictionary_sources() -> list[dict]:
    if not DICTIONARY_PATH.exists():
        return []
    with _connect() as connection:
        return [{"source": row[0], "package_id": row[1], "version": row[2], "license": row[3],
                 "homepage": row[4], "entries": int(row[5]), "installed_at": float(row[6])}
                for row in connection.execute("SELECT source,package_id,version,license,homepage,entries,installed_at FROM dictionary_sources ORDER BY installed_at DESC")]


def _forms(lemma: str, surface: str = "") -> list[str]:
    values = [unicodedata.normalize("NFKC", value).strip() for value in (lemma, surface) if value.strip()]
    expanded: list[str] = []
    for value in values:
        expanded.extend((value, value.replace("着く", "付く"), value.replace("付く", "つく"), value.replace("つく", "付く")))
    return list(dict.fromkeys(value for value in expanded if value))


def lookup(lemma: str, reading: str, surface: str = "") -> dict | None:
    if not DICTIONARY_PATH.exists():
        return None
    normalized = kata(reading)
    forms = _forms(lemma, surface)
    placeholders = ",".join("?" for _ in forms)
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT lemma, reading, senses, source FROM entries WHERE lemma IN ({placeholders}) AND (reading = ? OR reading = lemma) LIMIT 30",
            (*forms, normalized),
        ).fetchall()
        if not rows:
            rows = connection.execute(
                f"SELECT lemma, reading, senses, source FROM entries WHERE lemma IN ({placeholders}) OR reading = ? LIMIT 30",
                (*forms, normalized),
            ).fetchall()
    if not rows:
        return None
    senses: list[str] = []
    sources: list[str] = []
    matched_lemma, matched_reading = rows[0][0], rows[0][1]
    for _, _, encoded, source in rows:
        senses.extend(json.loads(encoded))
        sources.append(source)
    return {"lemma": matched_lemma, "reading": matched_reading or normalized, "senses_zh": list(dict.fromkeys(senses)), "source": " / ".join(dict.fromkeys(sources))}

