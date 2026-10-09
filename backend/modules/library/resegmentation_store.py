"""Versioned chapter replacement inside the owning library transaction.

Staged generations are private and never become reader results until the
complete chapter has validated and its expected source revision still wins.
Saved cards and learning events are immutable consumers of archived sources;
they are deliberately not deleted or rewritten in another database.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path

from ...nlp import stable_id, lexeme_key
from . import repository


class ResegmentationConflict(ValueError):
    pass


class ResegmentationNotFound(ValueError):
    pass


_initialization_lock = threading.Lock()
_initialized_paths: set[Path] = set()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def initialize_store(user_id: str) -> None:
    path = repository.LIBRARY_PATH.resolve()
    with _initialization_lock:
        if path in _initialized_paths and path.exists():
            return
        with repository._connect(user_id) as connection:
            # The schema is additive. Keep the first pre-migration DB backup
            # as well as per-chapter source snapshots used by undo/history.
            if connection.execute("SELECT 1 FROM records LIMIT 1").fetchone():
                backup = path.parent / "migration-backups" / "library.pre-resegmentation.sqlite3"
                if not backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    temporary = backup.with_suffix(".tmp")
                    target = repository.sqlite3.connect(temporary)
                    try:
                        connection.backup(target)
                    finally:
                        target.close()
                    temporary.replace(backup)
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS segmentation_generations (
                    owner_user_id TEXT NOT NULL, generation_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL, book_id TEXT NOT NULL,
                    job_id TEXT NOT NULL, parent_generation TEXT NOT NULL,
                    parent_revision INTEGER NOT NULL, target_revision INTEGER NOT NULL,
                    text_hash TEXT NOT NULL, rules_version TEXT NOT NULL,
                    status TEXT NOT NULL, stage_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                    map_json TEXT NOT NULL, created_at REAL NOT NULL, activated_at REAL,
                    PRIMARY KEY(owner_user_id,generation_id),
                    UNIQUE(owner_user_id,job_id,chapter_id)
                );
                CREATE INDEX IF NOT EXISTS idx_segmentation_chapter_generations
                    ON segmentation_generations(owner_user_id,chapter_id,created_at);
                CREATE TABLE IF NOT EXISTS segmentation_anchor_maps (
                    owner_user_id TEXT NOT NULL, chapter_id TEXT NOT NULL,
                    old_generation TEXT NOT NULL, new_generation TEXT NOT NULL,
                    entity_kind TEXT NOT NULL, old_id TEXT NOT NULL, new_id TEXT,
                    anchor_start INTEGER NOT NULL, anchor_end INTEGER NOT NULL,
                    status TEXT NOT NULL, payload_json TEXT NOT NULL,
                    PRIMARY KEY(owner_user_id,new_generation,entity_kind,old_id)
                );
            """)
        _initialized_paths.add(path)


def _owned_chapter(connection, user_id: str, chapter_id: str) -> dict:
    row = connection.execute("""SELECT c.payload FROM records c
        JOIN user_library_items b ON b.user_id=c.owner_user_id
          AND b.book_id=json_extract(c.payload,'$.bookId')
        WHERE c.owner_user_id=? AND c.table_name='chapters' AND c.record_key=?""",
        (user_id, chapter_id)).fetchone()
    if not row:
        raise ResegmentationNotFound("章节不存在或无权访问")
    return json.loads(row[0])


def owned_chapters(user_id: str, book_id: str, chapter_id: str | None = None) -> list[dict]:
    with repository._connect(user_id) as connection:
        if not connection.execute("SELECT 1 FROM user_library_items WHERE user_id=? AND book_id=?", (user_id, book_id)).fetchone():
            raise ResegmentationNotFound("书籍不存在或无权访问")
        values = [json.loads(row[0]) for row in connection.execute("""SELECT payload FROM records
            WHERE owner_user_id=? AND table_name='chapters' AND json_extract(payload,'$.bookId')=?
            ORDER BY CAST(json_extract(payload,'$.order') AS INTEGER),record_key""", (user_id, book_id))]
        if chapter_id:
            values = [item for item in values if item["id"] == chapter_id]
            if not values:
                raise ResegmentationNotFound("章节不属于所选书籍")
    return values


def preview(user_id: str,book_id: str,chapter_id: str | None=None) -> dict:
    chapters=owned_chapters(user_id,book_id,chapter_id)
    result={'chapters':len(chapters),'sentences':0,'translations':0,'bookmarks':0}
    with repository._connect(user_id) as connection:
        for chapter in chapters:
            count=connection.execute("SELECT COUNT(*),COALESCE(SUM(CASE WHEN COALESCE(json_extract(payload,'$.translation_zh'),'')!='' THEN 1 ELSE 0 END),0) FROM records WHERE owner_user_id=? AND table_name='sentences' AND json_extract(payload,'$.chapter_id')=?",(user_id,chapter['id'])).fetchone()
            result['sentences']+=int(count[0]); result['translations']+=int(count[1])
            result['bookmarks']+=connection.execute('SELECT COUNT(*) FROM user_bookmarks WHERE user_id=? AND chapter_id=?',(user_id,chapter['id'])).fetchone()[0]
    return result


def _record_rows(connection, user_id: str, table: str, field: str, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    # Bound batches avoid Android / older SQLite variable limits.
    output = []
    for offset in range(0, len(ids), 400):
        current = ids[offset:offset + 400]
        placeholders = ",".join("?" for _ in current)
        output.extend(json.loads(row[0]) for row in connection.execute(
            f"SELECT payload FROM records WHERE owner_user_id=? AND table_name=? AND json_extract(payload,'$.{field}') IN ({placeholders}) ORDER BY rowid",
            (user_id, table, *current)))
    return output


def _snapshot(connection, user_id: str, chapter: dict) -> dict:
    sentences = _record_rows(connection, user_id, "sentences", "chapter_id", [chapter["id"]])
    sentences.sort(key=lambda item: (int(item.get("start", 0)), item["id"]))
    ids = [item["id"] for item in sentences]
    tokens = _record_rows(connection, user_id, "tokens", "sentence_id", ids)
    return {"chapter": deepcopy(chapter), "sentences": sentences, "tokens": tokens,
            "annotations": _record_rows(connection, user_id, "annotations", "sentence_id", ids),
            "contextSenses": _record_rows(connection, user_id, "contextSenses", "token_id", [item["id"] for item in tokens])}


def _validate_analysis(text: str, chapter_id: str, sentences: list[dict], tokens: list[dict]) -> None:
    previous_end, identities = 0, set()
    by_id = {}
    for sentence in sorted(sentences, key=lambda item: int(item.get("start", -1))):
        start, end = sentence.get("start"), sentence.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
            raise ValueError("重切句子原文锚点不合法")
        if start < previous_end or text[previous_end:start].strip() or text[start:end] != sentence.get("original"):
            raise ValueError("重切句子遗漏、重叠或改变原文")
        if sentence.get("chapter_id") != chapter_id or not sentence.get("id") or sentence["id"] in identities:
            raise ValueError("重切句子引用不合法")
        previous_end = end
        identities.add(sentence["id"])
        by_id[sentence["id"]] = sentence
    if text[previous_end:].strip():
        raise ValueError("重切没有覆盖章节正文")
    identities = set()
    ends: dict[str, int] = {}
    for token in sorted(tokens, key=lambda item: (item.get("sentence_id", ""), int(item.get("start", -1)))):
        sentence = by_id.get(token.get("sentence_id"))
        start, end = token.get("start"), token.get("end")
        if not sentence or not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(sentence["original"]):
            raise ValueError("重切词素原文锚点不合法")
        previous = ends.get(sentence["id"], 0)
        if start < previous or sentence["original"][previous:start].strip() or sentence["original"][start:end] != token.get("surface"):
            raise ValueError("重切词素重叠或改变原文")
        if not token.get("id") or token["id"] in identities:
            raise ValueError("重切词素引用重复")
        identities.add(token["id"])
        ends[sentence["id"]] = end
    if any(sentence["original"].strip() and sentence["id"] not in ends for sentence in sentences):
        raise ValueError("重切缺少句子词素")
    if any(sentence["original"][ends.get(sentence["id"], 0):].strip() for sentence in sentences):
        raise ValueError("重切词素没有覆盖句子原文")


def _sentence_mapping(old: list[dict], new: list[dict]) -> list[dict]:
    result = []
    for prior in old:
        exact = next((item for item in new if (item["start"], item["end"], item["original"]) == (prior["start"], prior["end"], prior["original"])), None)
        target = exact or next((item for item in new if item["start"] <= prior["start"] < item["end"]), None)
        if target is None and new:
            target = min(new, key=lambda item: abs(item["start"] - prior["start"]))
        result.append({"old_id": prior["id"], "new_id": target["id"] if target else None,
                       "start": prior["start"], "end": prior["end"], "old_start": prior["start"], "old_end": prior["end"],
                       "new_start": target["start"] if target else None, "new_end": target["end"] if target else None,
                       "status": "exact" if exact else "original_anchor" if target else "unresolved"})
    return result


def _prepare_generation(snapshot: dict, sentences: list[dict], tokens: list[dict], generation_id: str, revision: int) -> tuple[dict, dict]:
    old_by_anchor = {(item["start"], item["end"], item["original"]): item for item in snapshot["sentences"]}
    sentence_ids = {}
    prepared_sentences = []
    for item in sorted(sentences, key=lambda row: row["start"]):
        row = deepcopy(item)
        prior = old_by_anchor.get((row["start"], row["end"], row["original"]))
        target_id = prior["id"] if prior else stable_id("sent", f"{generation_id}:{row['start']}:{row['end']}:{row['original']}")
        sentence_ids[row["id"]] = target_id
        row.update(id=target_id, translation_zh="", explanation_status="idle", explanation_detail=None,
                   status="complete", error=None, analysis_revision=revision, generation_id=generation_id)
        prepared_sentences.append(row)
    old_sentences = {item["id"]: item for item in snapshot["sentences"]}
    new_sentences = {item["id"]: item for item in prepared_sentences}
    old_tokens = {}
    for item in snapshot["tokens"]:
        parent = old_sentences.get(item.get("sentence_id"))
        if parent:
            key = (parent["start"] + item["start"], parent["start"] + item["end"], item["surface"])
            old_tokens[key] = item
    prepared_tokens = []
    token_map = []
    for item in tokens:
        row = deepcopy(item)
        target_parent = sentence_ids[row["sentence_id"]]
        parent = new_sentences[target_parent]
        key = (parent["start"] + row["start"], parent["start"] + row["end"], row["surface"])
        prior = old_tokens.get(key)
        target_id = prior["id"] if prior and prior["sentence_id"] == target_parent else stable_id("tok", f"{generation_id}:{target_parent}:{row['start']}:{row['end']}:{row['surface']}")
        row.update(id=target_id, sentence_id=target_parent, analysis_revision=revision, generation_id=generation_id)
        if row.get("lexeme_key"):
            row["lexemeKey"] = row["lexeme_key"]
        else:
            row['lexemeKey']=lexeme_key(str(row.get('lemma') or row['surface']),str(row.get('reading') or ''),str(row.get('part_of_speech') or 'unknown'))
        prepared_tokens.append(row)
        if prior:
            token_map.append({"old_id": prior["id"], "new_id": target_id, "start": key[0], "end": key[1], "status": "exact"})
    matched_old = {item["old_id"] for item in token_map}
    for prior in snapshot["tokens"]:
        if prior["id"] not in matched_old and prior.get("sentence_id") in old_sentences:
            parent = old_sentences[prior["sentence_id"]]
            token_map.append({"old_id": prior["id"], "new_id": None, "start": parent["start"] + prior["start"],
                              "end": parent["start"] + prior["end"], "status": "unresolved"})
    return {"sentences": prepared_sentences, "tokens": prepared_tokens, "annotations": [], "contextSenses": []}, {
        "sentences": _sentence_mapping(snapshot["sentences"], prepared_sentences), "tokens": token_map}


def _generation_row(connection, user_id: str, generation_id: str) -> dict:
    row = connection.execute("""SELECT generation_id,chapter_id,book_id,job_id,parent_generation,parent_revision,target_revision,
        text_hash,rules_version,status,stage_json,snapshot_json,map_json FROM segmentation_generations
        WHERE owner_user_id=? AND generation_id=?""", (user_id, generation_id)).fetchone()
    if not row:
        raise ResegmentationNotFound("重切版本不存在或无权访问")
    names = ["generation_id", "chapter_id", "book_id", "job_id", "parent_generation", "parent_revision", "target_revision",
             "text_hash", "rules_version", "status", "stage_json", "snapshot_json", "map_json"]
    return dict(zip(names, row))


def stage_generation(user_id: str, chapter_id: str, job_id: str, expected_revision: int, expected_text_hash: str,
                     sentences: list[dict], tokens: list[dict], rules_version: str) -> dict:
    initialize_store(user_id)
    with repository._connect(user_id) as connection:
        connection.execute("BEGIN IMMEDIATE")
        chapter = _owned_chapter(connection, user_id, chapter_id)
        revision = int(chapter.get("analysis_revision", 1))
        generation_id = stable_id("gen", f"{user_id}:{job_id}:{chapter_id}:{expected_text_hash}")
        existing = connection.execute("SELECT generation_id FROM segmentation_generations WHERE owner_user_id=? AND generation_id=?", (user_id, generation_id)).fetchone()
        if existing:
            prior = _generation_row(connection, user_id, generation_id)
            if prior["rules_version"] != rules_version:
                raise ResegmentationConflict("暂存结果规则版本已变化，请创建新任务")
            if prior["status"] == "active" and chapter.get("active_generation") == generation_id:
                return generation_summary(user_id, generation_id, connection=connection)
        if revision != expected_revision or text_hash(chapter.get("text", "")) != expected_text_hash:
            raise ResegmentationConflict("章节正文或切分版本已变化，请刷新后重新开始")
        if existing:
            return generation_summary(user_id, generation_id, connection=connection)
        _validate_analysis(chapter.get("text", ""), chapter_id, sentences, tokens)
        snapshot = _snapshot(connection, user_id, chapter)
        staged, mapping = _prepare_generation(snapshot, sentences, tokens, generation_id, revision + 1)
        parent = str(chapter.get("active_generation") or f"legacy:{chapter_id}:{revision}")
        connection.execute("""INSERT INTO segmentation_generations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id, generation_id, chapter_id, chapter["bookId"], job_id, parent, revision, revision + 1,
             expected_text_hash, rules_version, "staged", _json(staged), _json(snapshot), _json(mapping), time.time(), None))
        return generation_summary(user_id, generation_id, connection=connection)


def stage_payload(user_id: str,generation_id: str) -> dict:
    initialize_store(user_id)
    with repository._connect(user_id) as connection:
        row=_generation_row(connection,user_id,generation_id)
        _owned_chapter(connection,user_id,row['chapter_id'])
        return {'status':row['status'],'revision':row['target_revision'],**json.loads(row['stage_json'])}


def save_stage_structures(user_id: str,generation_id: str,structures: list[dict]):
    with repository._connect(user_id) as connection:
        connection.execute('BEGIN IMMEDIATE')
        row=_generation_row(connection,user_id,generation_id)
        if row['status']!='staged': return
        staged=json.loads(row['stage_json'])
        ids={value['id'] for value in staged['sentences']}
        if {value['sentence_id'] for value in structures}!=ids:
            raise ValueError('学习结构没有覆盖暂存句子')
        staged['structures']=structures
        connection.execute('UPDATE segmentation_generations SET stage_json=? WHERE owner_user_id=? AND generation_id=?',(_json(staged),user_id,generation_id))


def pending_events(user_id: str | None=None,limit=50):
    with repository._connect(user_id or '') as connection:
        condition=" AND owner_user_id=?" if user_id else ''
        values=([user_id] if user_id else [])+[limit]
        rows=connection.execute("SELECT id,owner_user_id,payload_json FROM outbox WHERE topic='segmentation.activated' AND delivered_at IS NULL"+condition+' ORDER BY created_at,id LIMIT ?',values).fetchall()
    return [{'id':row[0],'user_id':row[1],'payload':json.loads(row[2])} for row in rows]


def acknowledge_event(user_id: str,event_id: str):
    with repository._connect(user_id) as connection:
        connection.execute("UPDATE outbox SET delivered_at=? WHERE id=? AND owner_user_id=? AND topic='segmentation.activated'",(time.time(),event_id,user_id))


def generation_summary(user_id: str, generation_id: str, *, connection=None) -> dict:
    if connection is None:
        initialize_store(user_id)
        with repository._connect(user_id) as conn:
            return generation_summary(user_id, generation_id, connection=conn)
    row = _generation_row(connection, user_id, generation_id)
    _owned_chapter(connection, user_id, row["chapter_id"])
    stage, snapshot, mapping = json.loads(row["stage_json"]), json.loads(row["snapshot_json"]), json.loads(row["map_json"])
    return {key: row[key] for key in ("generation_id", "chapter_id", "book_id", "job_id", "parent_generation", "parent_revision", "target_revision", "text_hash", "rules_version", "status")} | {
        "old_sentence_count": len(snapshot["sentences"]), "new_sentence_count": len(stage["sentences"]),
        "translated_archived": sum(bool(item.get("translation_zh")) for item in snapshot["sentences"]),
        "unresolved_tokens": sum(item["status"] == "unresolved" for item in mapping["tokens"])}


def _delete_records(connection, user_id: str, table: str, key: str, ids: list[str]) -> None:
    for offset in range(0, len(ids), 400):
        part = ids[offset:offset + 400]
        placeholders = ",".join("?" for _ in part)
        connection.execute(f"DELETE FROM records WHERE owner_user_id=? AND table_name=? AND json_extract(payload,'$.{key}') IN ({placeholders})",
                           (user_id, table, *part))


def _upsert_records(connection, user_id: str, table: str, records: list[dict]) -> None:
    key = "token_id" if table == "contextSenses" else "id"
    connection.executemany("""INSERT INTO records(owner_user_id,table_name,record_key,payload) VALUES(?,?,?,?)
        ON CONFLICT(owner_user_id,table_name,record_key) DO UPDATE SET payload=excluded.payload""",
        [(user_id, table, item[key], _json(item)) for item in records])


def _remap_live_references(connection, user_id: str, chapter: dict, mapping: dict, staged: dict) -> None:
    by_old = {item["old_id"]: item for item in mapping["sentences"]}
    new_by_id = {item["id"]: item for item in staged["sentences"]}
    progress = connection.execute("SELECT sentence_id FROM user_book_progress WHERE user_id=? AND book_id=? AND chapter_id=?",
                                  (user_id, chapter["bookId"], chapter["id"])).fetchone()
    if progress and progress[0] in by_old:
        anchor = by_old[progress[0]]
        connection.execute("UPDATE user_book_progress SET sentence_id=?,updated_at=? WHERE user_id=? AND book_id=?",
                           (anchor["new_id"], time.time(), user_id, chapter["bookId"]))
        book = connection.execute("SELECT payload FROM records WHERE owner_user_id=? AND table_name='books' AND record_key=?",
                                  (user_id, chapter["bookId"])).fetchone()
        if book:
            payload = json.loads(book[0])
            payload.update(currentSentenceId=anchor["new_id"], readingSourceAnchor=anchor["start"],
                           currentAnalysisRevision=chapter["analysis_revision"], updatedAt=time.time() * 1000)
            _upsert_records(connection, user_id, "books", [payload])
            connection.execute("UPDATE user_library_items SET metadata_json=?,updated_at=? WHERE user_id=? AND book_id=?",
                               (_json(repository._book_metadata(payload)), time.time(), user_id, chapter["bookId"]))
    for bookmark_id, encoded in connection.execute("SELECT bookmark_id,payload_json FROM user_bookmarks WHERE user_id=? AND chapter_id=?",
                                                   (user_id, chapter["id"])).fetchall():
        bookmark = json.loads(encoded)
        anchor = by_old.get(bookmark.get("sentenceId"))
        if not anchor:
            continue
        # Preserve the user's quote and creation identity; only its live link
        # moves. The original anchor remains available for split/merge cases.
        bookmark.update(sentenceId=anchor["new_id"], originalSentenceId=bookmark.get("originalSentenceId", anchor["old_id"]),
                        sourceAnchor=bookmark.get("sourceAnchor", anchor["start"]), analysisRevision=chapter["analysis_revision"],
                        mappingStatus=anchor["status"], updatedAt=time.time() * 1000)
        if anchor["new_id"] in new_by_id:
            bookmark["sentenceStart"] = new_by_id[anchor["new_id"]]["start"]
        connection.execute("UPDATE user_bookmarks SET sentence_id=?,sentence_start=?,payload_json=?,updated_at=? WHERE user_id=? AND bookmark_id=?",
            (bookmark.get("sentenceId") or "", int(bookmark.get("sentenceStart", 0)), _json(bookmark), time.time(), user_id, bookmark_id))
        _upsert_records(connection, user_id, "bookmarks", [bookmark])


def activate_generation(user_id: str, generation_id: str) -> dict:
    initialize_store(user_id)
    with repository._connect(user_id) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _generation_row(connection, user_id, generation_id)
        chapter = _owned_chapter(connection, user_id, row["chapter_id"])
        if row["status"] == "active" and chapter.get("active_generation") == generation_id:
            return generation_summary(user_id, generation_id, connection=connection) | {"already_committed": True}
        parent = str(chapter.get("active_generation") or f"legacy:{chapter['id']}:{int(chapter.get('analysis_revision', 1))}")
        if row["status"] != "staged" or int(chapter.get("analysis_revision", 1)) != row["parent_revision"] or parent != row["parent_generation"] or text_hash(chapter.get("text", "")) != row["text_hash"]:
            raise ResegmentationConflict("重切结果已过期，拒绝覆盖更新后的章节")
        stage, snapshot, mapping = json.loads(row["stage_json"]), json.loads(row["snapshot_json"]), json.loads(row["map_json"])
        current = _snapshot(connection, user_id, chapter)
        # Capture translations/book annotations saved while tokenization ran;
        # a stage-time snapshot alone would silently lose these late results.
        row["snapshot_json"] = _json(current)
        connection.execute("UPDATE segmentation_generations SET snapshot_json=? WHERE owner_user_id=? AND generation_id=?",
                           (row["snapshot_json"], user_id, generation_id))
        old_ids, old_token_ids = [item["id"] for item in current["sentences"]], [item["id"] for item in current["tokens"]]
        for table in ("sentences", "tokens", "annotations"):
            _delete_records(connection, user_id, table, "id" if table == "sentences" else "sentence_id", old_ids)
        _delete_records(connection, user_id, "contextSenses", "token_id", old_token_ids)
        for table in ("sentences", "tokens", "annotations", "contextSenses"):
            _upsert_records(connection, user_id, table, stage.get(table, []))
        chapter.update(status="complete", error=None, analysis_revision=row["target_revision"], active_generation=generation_id,
                       segmentation_source="local-fallback", segmentation_rules_version=row["rules_version"], updatedAt=time.time() * 1000)
        _upsert_records(connection, user_id, "chapters", [chapter])
        _remap_live_references(connection, user_id, chapter, mapping, stage)
        for kind in ("sentences", "tokens"):
            for anchor in mapping[kind]:
                connection.execute("INSERT INTO segmentation_anchor_maps VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id, chapter["id"], row["parent_generation"], generation_id, kind[:-1], anchor["old_id"], anchor["new_id"],
                     anchor["start"], anchor["end"], anchor["status"], _json(anchor)))
        connection.execute("UPDATE segmentation_generations SET status='archived' WHERE owner_user_id=? AND chapter_id=? AND status='active'",
                           (user_id, chapter["id"]))
        connection.execute("UPDATE segmentation_generations SET status='active',activated_at=? WHERE owner_user_id=? AND generation_id=?",
                           (time.time(), user_id, generation_id))
        event = {"book_id": chapter["bookId"], "chapter_id": chapter["id"], "old_generation": row["parent_generation"],
                 "new_generation": generation_id, "old_revision": row["parent_revision"], "new_revision": row["target_revision"],
                 "text_hash": row["text_hash"], "sentence_anchors": mapping["sentences"]}
        connection.execute("INSERT INTO outbox(id,owner_user_id,topic,payload_json,created_at) VALUES(?,?,?,?,?)",
                           (str(uuid.uuid4()), user_id, "segmentation.activated", _json(event), time.time()))
        return generation_summary(user_id, generation_id, connection=connection) | {
            "sentence_map": mapping["sentences"], "token_map": mapping["tokens"], "already_committed": False}


def restore_generation(user_id: str, generation_id: str, expected_revision: int) -> dict:
    """Undo the active generation by activating a new monotonic revision."""
    initialize_store(user_id)
    restore_id = stable_id("gen", f"{user_id}:undo:{generation_id}:{expected_revision}")
    with repository._connect(user_id) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _generation_row(connection, user_id, generation_id)
        chapter = _owned_chapter(connection, user_id, row["chapter_id"])
        if chapter.get("active_generation") != generation_id or int(chapter.get("analysis_revision", 1)) != expected_revision:
            raise ResegmentationConflict("仅可恢复当前版本，章节已变化，请刷新")
        prior = json.loads(row["snapshot_json"])
        current = _snapshot(connection, user_id, chapter)
        stage = {key: deepcopy(prior[key]) for key in ("sentences", "tokens", "annotations", "contextSenses")}
        for sentence in stage["sentences"]:
            sentence.update(analysis_revision=expected_revision + 1, generation_id=restore_id,
                            translation_source_revision=int(prior["chapter"].get("analysis_revision", 1)))
        for token in stage["tokens"]:
            token.update(analysis_revision=expected_revision + 1, generation_id=restore_id)
        for row in [*stage['annotations'],*stage['contextSenses']]:
            row.update(analysis_revision=expected_revision+1)
        mapping = {"sentences": _sentence_mapping(current["sentences"], stage["sentences"]), "tokens": []}
        prior_tokens = {item["id"]: item for item in stage["tokens"]}
        current_sents = {item["id"]: item for item in current["sentences"]}
        for token in current["tokens"]:
            parent = current_sents[token["sentence_id"]]
            mapping["tokens"].append({"old_id": token["id"], "new_id": token["id"] if token["id"] in prior_tokens else None,
                "start": parent["start"] + token["start"], "end": parent["start"] + token["end"],
                "status": "exact" if token["id"] in prior_tokens else "unresolved"})
        connection.execute("INSERT INTO segmentation_generations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, restore_id, chapter["id"], chapter["bookId"], "undo:" + generation_id, generation_id,
             expected_revision, expected_revision + 1, text_hash(chapter["text"]), row["rules_version"], "staged",
             _json(stage), _json(current), _json(mapping), time.time(), None))
    return activate_generation(user_id, restore_id) | {"restored_from": generation_id}


def list_generations(user_id: str, chapter_id: str) -> list[dict]:
    initialize_store(user_id)
    with repository._connect(user_id) as connection:
        _owned_chapter(connection, user_id, chapter_id)
        ids = [row[0] for row in connection.execute("SELECT generation_id FROM segmentation_generations WHERE owner_user_id=? AND chapter_id=? ORDER BY created_at DESC", (user_id, chapter_id))]
        return [generation_summary(user_id, identity, connection=connection) for identity in ids]


def resolve_source(user_id: str, chapter_id: str, sentence_id: str) -> dict:
    """Locate a card's historical quote and a current original-text anchor."""
    initialize_store(user_id)
    with repository._connect(user_id) as connection:
        chapter = _owned_chapter(connection, user_id, chapter_id)
        current = _snapshot(connection, user_id, chapter)
        active = next((item for item in current["sentences"] if item["id"] == sentence_id), None)
        if active:
            return {"historical": False, "sentence": active, "current_sentence_id": sentence_id, "anchor_start": active["start"], "revision": int(chapter.get("analysis_revision", 1))}
        for encoded in connection.execute("SELECT snapshot_json FROM segmentation_generations WHERE owner_user_id=? AND chapter_id=? ORDER BY created_at DESC", (user_id, chapter_id)):
            snapshot = json.loads(encoded[0])
            prior = next((item for item in snapshot["sentences"] if item["id"] == sentence_id), None)
            if prior:
                target = _sentence_mapping([prior], current["sentences"])[0]
                return {"historical": True, "sentence": prior, "current_sentence_id": target["new_id"], "anchor_start": prior["start"],
                        "mapping_status": target["status"], "revision": int(chapter.get("analysis_revision", 1))}
        raise ResegmentationNotFound("历史句子来源不存在")
