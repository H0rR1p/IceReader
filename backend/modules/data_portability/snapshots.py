"""Durable user state omitted by legacy transfer packages."""
import json

EXTENSION_TABLES = {
    "library": {"segmentation_generations": "owner_user_id", "segmentation_anchor_maps": "owner_user_id"},
    "linguistics": {"preferences": "user_id", "span_overrides": "user_id",
                    "span_override_history": "user_id", "span_senses": "user_id"},
    "book_memory": {"objects": "user_id", "revisions": "user_id", "dependencies": "user_id", "summaries": "user_id"},
}


def validate_snapshots(databases):
    if not isinstance(databases, dict) or any(name not in EXTENSION_TABLES for name in databases):
        raise ValueError("迁移包版本快照包含不支持的数据库")
    for name, tables in databases.items():
        if not isinstance(tables, dict) or any(table not in EXTENSION_TABLES[name] for table in tables):
            raise ValueError("迁移包版本快照包含不支持的数据表")
        for rows in tables.values():
            if not isinstance(rows, list) or len(rows) > 2_000_000 or any(not isinstance(row, dict) for row in rows):
                raise ValueError("迁移包版本快照记录格式错误")


def collect_snapshot_ids(ids, databases):
    for name, tables in databases.items():
        for table, rows in tables.items():
            for row in rows:
                if table == "objects":
                    ids.add("entities" if row["kind"] == "entity" else "facts", row["id"])
                elif table == "revisions":
                    ids.add("memory_revisions", row["id"])
                    ids.add("entities" if row["kind"] == "entity" else "facts", row["object_id"])
                elif table == "summaries":
                    ids.add("chunks", row["chunk_id"])
                elif table == "segmentation_generations":
                    ids.add("generations", row["generation_id"])
                    ids.add("jobs", row["job_id"])
                    for field in ("snapshot_json", "stage_json"):
                        snapshot = json.loads(row[field])
                        for nested_table in ("sentences", "tokens", "annotations"):
                            for nested in snapshot.get(nested_table, []):
                                # Historic IDs remain stable unless a book graph had to
                                # be mapped because an unrelated target book collided.
                                if ids.get("chapters", row["chapter_id"]) != row["chapter_id"]:
                                    ids.add(nested_table, nested["id"])
                elif table in {"span_overrides", "span_override_history"} and ids.get("sentences", row["sentence_id"]) != row["sentence_id"]:
                    ids.add("spans", row["span_id"])


def merge_snapshots(connection, aliases, databases, ids) -> int:
    imported = 0
    for name, tables in databases.items():
        alias = aliases[name]
        for table, rows in tables.items():
            info = connection.execute(f'PRAGMA "{alias}".table_info("{table}")').fetchall()
            if not info:
                if rows:
                    raise ValueError(f"目标服务尚未支持版本数据表：{name}.{table}")
                continue
            columns = {row[1] for row in info}
            primary = [row[1] for row in sorted(info, key=lambda item: item[5]) if row[5]]
            for original in rows:
                if set(original) - columns:
                    raise ValueError(f"目标服务缺少版本数据列：{name}.{table}")
                domain = "memory_revisions" if table == "revisions" else None
                if table == "objects":
                    domain = "entities" if original.get("kind") == "entity" else "facts"
                clean = ids.rewrite(original, domain)
                clean[EXTENSION_TABLES[name][table]] = ids.target
                where = " AND ".join(f'"{key}"=?' for key in primary)
                current = connection.execute(f'SELECT * FROM "{alias}"."{table}" WHERE {where}', [clean[key] for key in primary]).fetchone()
                if current:
                    if all(current[key] == value for key, value in clean.items()):
                        continue
                    if table in {"revisions", "span_override_history", "segmentation_generations", "segmentation_anchor_maps"}:
                        raise ValueError(f"版本历史冲突，已保留现有数据：{name}.{table}")
                    if "revision" in clean and int(clean["revision"]) > int(current["revision"]):
                        names = [key for key in clean if key not in primary]
                        connection.execute(f'UPDATE "{alias}"."{table}" SET ' + ",".join(f'"{key}"=?' for key in names) + f' WHERE {where}', [*[clean[key] for key in names], *[clean[key] for key in primary]])
                        imported += 1
                    continue
                names = list(clean)
                connection.execute(f'INSERT INTO "{alias}"."{table}" (' + ",".join(f'"{key}"' for key in names) + ') VALUES (' + ",".join("?" for _ in names) + ')', list(clean.values()))
                imported += 1
    return imported
