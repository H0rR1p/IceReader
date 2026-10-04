"""Account-independent, additive learning transfer; no credentials or bindings."""
import json
import sqlite3
import uuid

LEARNING_TABLES = (
    "knowledge_items", "learning_events", "user_knowledge_states", "card_candidates",
    "notes", "cards", "memory_states", "review_logs", "saved_card_views",
    "card_preferences", "activity_windows", "daily_learning_stats",
)


def validate_learning(tables: dict) -> None:
    if not isinstance(tables, dict) or any(name not in LEARNING_TABLES for name in tables):
        raise ValueError("迁移包学习数据包含不支持的表")
    for rows in tables.values():
        if not isinstance(rows, list) or len(rows) > 2_000_000 or any(not isinstance(row, dict) for row in rows):
            raise ValueError("迁移包学习记录格式错误或数量过多")
    for row in tables.get("knowledge_items", []):
        if any(not isinstance(row.get(name), str) or not row[name] for name in ("id", "type", "canonical_key")):
            raise ValueError("迁移包知识条目格式错误")
    for row in tables.get("daily_learning_stats", []):
        if not isinstance(row.get("local_date"), str) or not isinstance(row.get("timezone"), str):
            raise ValueError("迁移包学习日期格式错误")
        for name, value in row.items():
            if name.endswith("_seconds") or name in {"cards_reviewed", "sentences_read", "lookup_count"}:
                if not isinstance(value, (int, float)) or value < 0:
                    raise ValueError("迁移包学习统计格式错误")


def merge_learning(connection: sqlite3.Connection, tables: dict, source: str, target: str) -> dict:
    """Called inside the library transaction with the learning database attached."""
    validate_learning(tables)
    connection.row_factory = sqlite3.Row
    existing = {row[0] for row in connection.execute("SELECT name FROM learning.sqlite_master WHERE type='table'")}
    id_map = {}
    knowledge_map = {}
    for table in LEARNING_TABLES:
        if table == "knowledge_items":
            continue
        for row in tables.get(table, []):
            if row.get("id"):
                id_map[str(row["id"])] = str(row["id"]) if source == target else str(uuid.uuid5(uuid.NAMESPACE_URL, f"bingdu-transfer:{target}:{source}:{table}:{row['id']}"))
    for row in tables.get("knowledge_items", []):
        found = connection.execute("SELECT id FROM learning.knowledge_items WHERE type=? AND canonical_key=?", (row["type"], row["canonical_key"])).fetchone()
        item_id = found[0] if found else str(uuid.uuid5(uuid.NAMESPACE_URL, f"bingdu-knowledge:{row['type']}:{row['canonical_key']}"))
        knowledge_map[str(row["id"])] = item_id
        # Review evidence can use the card ID as its knowledge-item ID.
        # Keep card references distinct from knowledge references in that case.
        id_map.setdefault(str(row["id"]), item_id)

    def rewrite(value):
        if isinstance(value, str):
            return id_map.get(value, value)
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: knowledge_map.get(item, item) if key == 'knowledge_item_id' and isinstance(item, str) else rewrite(item) for key, item in value.items()}
        return value

    counts = {"imported_cards": 0, "imported_learning_records": 0}
    for table in LEARNING_TABLES:
        if table not in existing:
            if tables.get(table):
                raise ValueError(f"目标服务尚未支持学习数据表：{table}")
            continue
        columns = {row[1] for row in connection.execute(f'PRAGMA learning.table_info("{table}")')}
        for original in tables.get(table, []):
            row = {key: knowledge_map.get(value, value) if key == 'knowledge_item_id' or table == 'knowledge_items' and key == 'id' else rewrite(value) for key, value in original.items() if key in columns}
            if "user_id" in columns:
                row["user_id"] = target
            for key, value in list(row.items()):
                if key.endswith("_json") and isinstance(value, str):
                    row[key] = json.dumps(rewrite(json.loads(value)), ensure_ascii=False)
            if table == "daily_learning_stats":
                if source == target:
                    continue
                # Source snapshots are credited only once per source/day. Later exports
                # add the positive delta, retaining activity already present at target.
                connection.execute("CREATE TABLE IF NOT EXISTS learning.transfer_daily_credits(target_user_id TEXT,source_user_id TEXT,local_date TEXT,timezone TEXT,payload TEXT,PRIMARY KEY(target_user_id,source_user_id,local_date,timezone))")
                key = (target, source, row["local_date"], row["timezone"])
                previous = connection.execute("SELECT payload FROM learning.transfer_daily_credits WHERE target_user_id=? AND source_user_id=? AND local_date=? AND timezone=?", key).fetchone()
                old = json.loads(previous[0]) if previous else {}
                metrics = [name for name in row if name.endswith("_seconds") or name in {"cards_reviewed", "sentences_read", "lookup_count"}]
                for name in metrics:
                    row[name] = max(0, float(row[name]) - float(old.get(name, 0)))
                names = list(row)
                updates = ','.join(f'"{name}"="{name}"+excluded."{name}"' for name in metrics)
                connection.execute(f'INSERT INTO learning.daily_learning_stats ({",".join(names)}) VALUES ({",".join("?" for _ in names)}) ON CONFLICT(user_id,local_date,timezone) DO UPDATE SET {updates}', list(row.values()))
                credited = {**original, **{name: max(float(original[name]), float(old.get(name, 0))) for name in metrics}}
                connection.execute("INSERT OR REPLACE INTO learning.transfer_daily_credits VALUES(?,?,?,?,?)", (*key, json.dumps(credited)))
                continue
            names = list(row)
            if not names:
                raise ValueError("迁移包包含空学习记录")
            quoted = ','.join(f'"{name}"' for name in names)
            cursor = connection.execute(f'INSERT OR IGNORE INTO learning."{table}" ({quoted}) VALUES ({",".join("?" for _ in names)})', list(row.values()))
            if table == "cards":
                counts["imported_cards"] += cursor.rowcount
            elif table != "knowledge_items":
                counts["imported_learning_records"] += cursor.rowcount
    if "card_search" in existing:
        connection.execute("DELETE FROM learning.card_search WHERE user_id=?", (target,))
        for row in connection.execute("SELECT c.id,n.* FROM learning.cards c JOIN learning.notes n ON n.id=c.note_id WHERE c.user_id=? AND c.deleted_at IS NULL", (target,)).fetchall():
            connection.execute("INSERT INTO learning.card_search(card_id,user_id,lemma,reading,gloss,sentence,book_title,tags) VALUES(?,?,?,?,?,?,?,?)", (row["id"], target, row["lemma"], row["reading"], row["gloss"], row["sentence"], row["book_title"], " ".join(json.loads(row["tags_json"]))))
    return counts
