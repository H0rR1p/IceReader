import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ...paths import DATA_DIR


LEARNING_PATH = DATA_DIR / "learning.sqlite3"
MODEL_VERSION = 1
_lock = threading.Lock()
_initialized_path: Path | None = None


EVENT_WEIGHTS = {
    "lookup": -0.12,
    "repeated_lookup": -0.18,
    "translation_reveal": -0.06,
    "grammar_reveal": -0.08,
    "mark_unknown": -0.30,
    "mark_mastered": 0.25,
    "natural_exposure": 0.02,
    "srs_again": -0.35,
    "srs_hard": 0.04,
    "srs_good": 0.16,
    "srs_easy": 0.24,
}


def _raw_connection() -> sqlite3.Connection:
    LEARNING_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(LEARNING_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_store() -> None:
    global _initialized_path
    resolved = LEARNING_PATH.resolve()
    if _initialized_path == resolved:
        return
    with _lock:
        if _initialized_path == resolved:
            return
        connection = _raw_connection()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    lemma TEXT NOT NULL DEFAULT '',
                    reading TEXT NOT NULL DEFAULT '',
                    grammar_pattern TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    UNIQUE(type, canonical_key)
                );
                CREATE TABLE IF NOT EXISTS learning_events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    knowledge_item_id TEXT NOT NULL REFERENCES knowledge_items(id),
                    event_type TEXT NOT NULL,
                    evidence_weight REAL NOT NULL,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    occurred_at REAL NOT NULL,
                    received_at REAL NOT NULL,
                    model_version INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_events_user_item
                    ON learning_events(user_id, knowledge_item_id, occurred_at, id);
                CREATE TABLE IF NOT EXISTS user_knowledge_states (
                    user_id TEXT NOT NULL,
                    knowledge_item_id TEXT NOT NULL REFERENCES knowledge_items(id),
                    mastery REAL NOT NULL,
                    confidence REAL NOT NULL,
                    exposure_count INTEGER NOT NULL DEFAULT 0,
                    lookup_count INTEGER NOT NULL DEFAULT 0,
                    translation_reveal_count INTEGER NOT NULL DEFAULT 0,
                    grammar_reveal_count INTEGER NOT NULL DEFAULT 0,
                    assisted_success_count INTEGER NOT NULL DEFAULT 0,
                    unassisted_success_count INTEGER NOT NULL DEFAULT 0,
                    last_seen_at REAL,
                    last_lookup_at REAL,
                    model_version INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id, knowledge_item_id)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_blindspots
                    ON user_knowledge_states(user_id, mastery, confidence, updated_at DESC);
                CREATE TABLE IF NOT EXISTS knowledge_aliases(alias_id TEXT PRIMARY KEY,target_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS canonical_aliases(type TEXT NOT NULL,alias_key TEXT NOT NULL,target_id TEXT NOT NULL,PRIMARY KEY(type,alias_key));
                """
            )
            _upgrade_grammar_keys(connection)
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


def _upgrade_grammar_keys(connection):
    import re
    from ..linguistics.rules import get_rule_catalog
    normalize = lambda value: re.sub(r'[「」『』\s]', '', value).replace('〜','~').replace('～','~').lstrip('~').lower()
    catalog = {}
    for rule in get_rule_catalog(include_disabled=True):
        key = normalize(rule['label'])
        catalog.setdefault(key, []).append(rule['id'])
    rows = connection.execute("SELECT id,canonical_key FROM knowledge_items WHERE type='grammar'").fetchall()
    for row in rows:
        matches = catalog.get(normalize(row['canonical_key']), [])
        if len(matches) != 1 or row['canonical_key'] == matches[0]:
            continue
        found = connection.execute("SELECT id FROM knowledge_items WHERE type='grammar' AND canonical_key=?", (matches[0],)).fetchone()
        target = str(found[0]) if found else str(row['id'])
        if not found:
            connection.execute('UPDATE knowledge_items SET canonical_key=? WHERE id=?', (matches[0],row['id']))
        elif target != row['id']:
            connection.execute('INSERT OR IGNORE INTO knowledge_aliases VALUES(?,?)', (row['id'],target))
        connection.execute("INSERT OR IGNORE INTO canonical_aliases VALUES('grammar',?,?)", (row['canonical_key'],target))
        users = [value[0] for value in connection.execute('SELECT DISTINCT user_id FROM learning_events WHERE knowledge_item_id IN(?,?)', (row['id'],target))]
        for user in users:
            _project_item(connection,user,target)


def ensure_item(item: dict, connection: sqlite3.Connection) -> str:
    alias = connection.execute('SELECT target_id FROM canonical_aliases WHERE type=? AND alias_key=?', (item['type'], item['canonical_key'])).fetchone()
    if alias:
        return str(alias[0])
    item_id = str(item["id"])
    connection.execute(
        """INSERT INTO knowledge_items(id, type, canonical_key, lemma, reading, grammar_pattern, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(type, canonical_key) DO NOTHING""",
        (
            item_id, item["type"], item["canonical_key"], item.get("lemma", ""),
            item.get("reading", ""), item.get("grammar_pattern", ""), time.time(),
        ),
    )
    row = connection.execute(
        "SELECT id FROM knowledge_items WHERE type=? AND canonical_key=?",
        (item["type"], item["canonical_key"]),
    ).fetchone()
    return str(row[0])


def ensure_knowledge_item(item: dict) -> str:
    with _connect() as connection:
        return ensure_item(item, connection)


def get_knowledge_item(item_id: str) -> dict:
    with _connect() as connection:
        row = connection.execute('SELECT * FROM knowledge_items WHERE id=?', (item_id,)).fetchone()
    if row is None:
        raise KeyError(item_id)
    return dict(row)


def append_events(user_id: str, device_id: str, events: list[dict]) -> dict[str, int]:
    inserted = 0
    affected: set[str] = set()
    with _connect() as connection:
        for event in events:
            item_id = ensure_item(event["item"], connection)
            event_type = str(event["event_type"])
            weight = float(event.get("evidence_weight", EVENT_WEIGHTS.get(event_type, 0)))
            cursor = connection.execute(
                """INSERT OR IGNORE INTO learning_events(
                    id, user_id, device_id, knowledge_item_id, event_type, evidence_weight,
                    context_json, occurred_at, received_at, model_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["id"], user_id, device_id, item_id, event_type, weight,
                    json.dumps(event.get("context", {}), ensure_ascii=False, separators=(",", ":")),
                    float(event["occurred_at"]), time.time(), MODEL_VERSION,
                ),
            )
            if cursor.rowcount:
                inserted += 1
                affected.add(item_id)
        for item_id in affected:
            _project_item(connection, user_id, item_id)
    return {"inserted": inserted, "projected": len(affected)}


def _project_item(connection: sqlite3.Connection, user_id: str, item_id: str) -> None:
    alias = connection.execute('SELECT target_id FROM knowledge_aliases WHERE alias_id=?', (item_id,)).fetchone()
    item_id = str(alias[0]) if alias else item_id
    rows = connection.execute(
        """SELECT event_type, evidence_weight, occurred_at FROM learning_events
           WHERE user_id=? AND (knowledge_item_id=? OR knowledge_item_id IN(SELECT alias_id FROM knowledge_aliases WHERE target_id=?)) ORDER BY occurred_at, id""",
        (user_id, item_id, item_id),
    ).fetchall()
    mastery = 0.5
    confidence = 0.0
    counts = {
        "exposure_count": 0, "lookup_count": 0, "translation_reveal_count": 0,
        "grammar_reveal_count": 0, "assisted_success_count": 0,
        "unassisted_success_count": 0,
    }
    last_seen_at = None
    last_lookup_at = None
    for row in rows:
        event_type, weight, occurred_at = str(row[0]), float(row[1]), float(row[2])
        mastery = min(1.0, max(0.0, mastery + weight))
        confidence = min(1.0, confidence + (0.12 if abs(weight) >= 0.2 else 0.05))
        if event_type == "natural_exposure":
            counts["exposure_count"] += 1
            counts["unassisted_success_count"] += 1
        elif event_type in {"lookup", "repeated_lookup"}:
            counts["lookup_count"] += 1
            last_lookup_at = occurred_at
        elif event_type == "translation_reveal":
            counts["translation_reveal_count"] += 1
        elif event_type == "grammar_reveal":
            counts["grammar_reveal_count"] += 1
        elif event_type in {"srs_good", "srs_easy"}:
            counts["unassisted_success_count"] += 1
        elif event_type == "srs_hard":
            counts["assisted_success_count"] += 1
        last_seen_at = max(last_seen_at or occurred_at, occurred_at)
    connection.execute(
        """INSERT INTO user_knowledge_states(
            user_id, knowledge_item_id, mastery, confidence, exposure_count, lookup_count,
            translation_reveal_count, grammar_reveal_count, assisted_success_count,
            unassisted_success_count, last_seen_at, last_lookup_at, model_version, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, knowledge_item_id) DO UPDATE SET
            mastery=excluded.mastery, confidence=excluded.confidence,
            exposure_count=excluded.exposure_count, lookup_count=excluded.lookup_count,
            translation_reveal_count=excluded.translation_reveal_count,
            grammar_reveal_count=excluded.grammar_reveal_count,
            assisted_success_count=excluded.assisted_success_count,
            unassisted_success_count=excluded.unassisted_success_count,
            last_seen_at=excluded.last_seen_at, last_lookup_at=excluded.last_lookup_at,
            model_version=excluded.model_version, updated_at=excluded.updated_at""",
        (
            user_id, item_id, mastery, confidence, counts["exposure_count"], counts["lookup_count"],
            counts["translation_reveal_count"], counts["grammar_reveal_count"],
            counts["assisted_success_count"], counts["unassisted_success_count"],
            last_seen_at, last_lookup_at, MODEL_VERSION, time.time(),
        ),
    )


def replay_user(user_id: str) -> int:
    with _connect() as connection:
        item_ids = [str(row[0]) for row in connection.execute(
            "SELECT DISTINCT knowledge_item_id FROM learning_events WHERE user_id=?",
            (user_id,),
        )]
        connection.execute("DELETE FROM user_knowledge_states WHERE user_id=?", (user_id,))
        for item_id in item_ids:
            _project_item(connection, user_id, item_id)
    return len(item_ids)


def list_blindspots(user_id: str, limit: int = 50) -> list[dict]:
    with _connect() as connection:
        rows = connection.execute(
            """SELECT s.*, i.type, i.canonical_key, i.lemma, i.reading, i.grammar_pattern
               FROM user_knowledge_states s JOIN knowledge_items i ON i.id=s.knowledge_item_id
               WHERE s.user_id=? AND s.knowledge_item_id NOT IN(SELECT alias_id FROM knowledge_aliases) AND (s.mastery < 0.5 OR s.lookup_count > 0)
               ORDER BY s.mastery ASC, s.lookup_count DESC, s.updated_at DESC LIMIT ?""",
            (user_id, min(200, max(1, limit))),
        ).fetchall()
    result = []
    for row in rows:
        value = dict(row)
        reasons = []
        if value["lookup_count"]:
            reasons.append(f"查词 {value['lookup_count']} 次")
        if value["translation_reveal_count"]:
            reasons.append(f"查看句意 {value['translation_reveal_count']} 次")
        if value["grammar_reveal_count"]:
            reasons.append(f"查看语法 {value['grammar_reveal_count']} 次")
        if value["confidence"] < 0.2:
            reasons.append("证据仍少")
        value["reasons"] = reasons
        result.append(value)
    return result


def knowledge_states(user_id: str, items: list[dict]) -> list[dict]:
    if not items:
        return []
    result=[]
    with _connect() as connection:
        for item in items:
            alias=connection.execute('SELECT target_id FROM canonical_aliases WHERE type=? AND alias_key=?', (item['type'],item['canonical_key'])).fetchone()
            if alias:
                canonical=connection.execute('SELECT canonical_key FROM knowledge_items WHERE id=?', (alias[0],)).fetchone()
                query_key=canonical[0] if canonical else item['canonical_key']
            else:
                query_key=item['canonical_key']
            row=connection.execute(
                """SELECT i.type,i.canonical_key,i.lemma,i.reading,i.grammar_pattern,
                   s.mastery,s.confidence,s.exposure_count,s.lookup_count,s.last_seen_at
                   FROM knowledge_items i LEFT JOIN user_knowledge_states s
                     ON s.knowledge_item_id=i.id AND s.user_id=?
                   WHERE i.type=? AND i.canonical_key=?""",
                (user_id,item["type"],query_key),
            ).fetchone()
            value=dict(row) if row else {
                "type":item["type"],"canonical_key":item["canonical_key"],
                "lemma":item.get("lemma",""),"reading":item.get("reading",""),"grammar_pattern":item.get("grammar_pattern",""),
                "mastery":0.5,"confidence":0.0,"exposure_count":0,"lookup_count":0,"last_seen_at":None,
            }
            if value["mastery"] is None:
                value.update({"mastery":0.5,"confidence":0.0,"exposure_count":0,"lookup_count":0,"last_seen_at":None})
            value['canonical_key']=item['canonical_key']
            result.append(value)
    return result
