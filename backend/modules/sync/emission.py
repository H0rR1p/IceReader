"""Build idempotent transport records from durable user revisions.

Collection runs before cloud sync as a recovery path for a process stopping
between a user-store commit and an immediate emission call.
"""
import hashlib
import json
import logging
import time

from ..book_memory import repository as memory
from ..linguistics import repository as structures
from ..library import repository as library
from ..library.sources import get_sentence_source, get_book_source
from . import repository

logger = logging.getLogger(__name__)


def after_commit(kind: str, user: str, device: str, *identities) -> bool:
    """Best-effort emission after a domain commit; collection recovers failure."""
    handlers = {'span_override': emit_span_override, 'book_memory': emit_book_memory,
                'source_generation': emit_source_generation}
    try:
        handlers[kind](user, device, *identities)
        return True
    except Exception:
        logger.exception('Sync emission deferred for %s; collection will retry', kind)
        return False


def _mutation(kind, identity, payload, timestamp=None):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{kind}:{identity}:{encoded}".encode()).hexdigest()
    return {"change_id": f"revision:{digest}", "entity_type": kind, "entity_id": identity,
            "payload": payload, "updated_at": timestamp or time.time()}


def _source(chapter, user):
    result = {"chapter_id": chapter["id"], "generation_id": chapter.get("active_generation") or
            f"legacy:{chapter['id']}:{int(chapter.get('analysis_revision', 1))}",
            "source_revision": int(chapter.get("analysis_revision", 1))}
    if chapter.get('active_generation'):
        with library._connect(user) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='segmentation_generations'").fetchone():
                row = connection.execute("SELECT parent_generation FROM segmentation_generations WHERE owner_user_id=? AND generation_id=?", (user, chapter['active_generation'])).fetchone()
                if row:
                    result['parent_generation'] = row[0]
    return result


def _emit(user, device, changes):
    if not changes:
        return {"accepted": [], "skipped": [], "conflicts": []}
    return repository.push_changes(user, device, changes)


def emit_span_override(user: str, device: str, sentence_id: str, span_id: str) -> dict:
    source = get_sentence_source(user, sentence_id)
    with structures.session() as connection:
        row = connection.execute("SELECT * FROM span_overrides WHERE user_id=? AND sentence_id=? AND span_id=?",
                                 (user, sentence_id, span_id)).fetchone()
        history = connection.execute("SELECT * FROM span_override_history WHERE user_id=? AND sentence_id=? AND span_id=? ORDER BY revision",
                                     (user, sentence_id, span_id)).fetchall()
    identity = f"{sentence_id}:{span_id}"
    changes = []
    if row is not None and source is not None and hashlib.sha256(source[0]['original'].encode()).hexdigest() == row['text_hash']:
        _, chapter = source
        payload = {key: row[key] for key in ("sentence_id", "span_id", "text_hash", "revision", "updated_at")}
        payload.update(book_id=chapter["bookId"], chapter_id=chapter["id"], source=_source(chapter, user),
                       payload=json.loads(row["payload"]))
        original_revision = payload['payload'].get('analysis_revision')
        if original_revision is not None and int(original_revision) != payload['source']['source_revision']:
            # Placement can survive a recut with the same text. Preserve the exact
            # confirmation payload and distinguish its original source explicitly.
            payload['origin_source'] = {'source_revision': int(original_revision), 'text_hash': row['text_hash']}
            payload['source_binding'] = 'same_text_preserved'
        changes.append(_mutation("span_override", identity, payload, row["updated_at"]))
    for item in history:
        value = {key: item[key] for key in ("sentence_id", "span_id", "revision", "created_at")}
        # History remains transportable if its old sentence is archived; it
        # must not claim to be a new override on a different current sentence.
        value['payload'] = json.loads(item['payload'])
        changes.append(_mutation("span_override_revision", f"{identity}:{item['revision']}", value, item["created_at"]))
    return _emit(user, device, changes)


def emit_book_memory(user: str, device: str, book_id: str) -> dict:
    _, chapters = get_book_source(user, book_id)
    sources = [_source(chapter, user) for chapter in chapters]
    with memory.session() as connection:
        objects = connection.execute("SELECT * FROM objects WHERE user_id=? AND book_id=? ORDER BY kind,id", (user, book_id)).fetchall()
        history = connection.execute("SELECT * FROM revisions WHERE user_id=? AND book_id=? ORDER BY created_at,id", (user, book_id)).fetchall()
    changes = []
    for item in objects:
        value = json.loads(item["payload"])
        revisions = [dict(row) for row in history if row["kind"] == item["kind"] and row["object_id"] == item["id"]]
        for row in revisions:
            row.pop("user_id", None)
        payload = {"book_id": book_id, "object": value, "sources": sources, "history": revisions}
        stamp = max((row["created_at"] for row in revisions), default=None)
        changes.append(_mutation("book_entity" if item["kind"] == "entity" else "book_fact",
                                 f"{book_id}:{item['id']}", payload, stamp))
    for item in history:
        row = dict(item)
        row.pop("user_id", None)
        changes.append(_mutation("book_memory_revision", row["id"], row, row["created_at"]))
    return _emit(user, device, changes)


def emit_source_generation(user: str, device: str, generation_id: str) -> dict:
    # Read only the committed manifest and maps. Body/stage/snapshot are never
    # selected, so they cannot accidentally enter a cloud request.
    with library._connect(user) as connection:
        connection.row_factory = library.sqlite3.Row
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='segmentation_generations'").fetchone()
        if not exists:
            return _emit(user, device, [])
        row = connection.execute("SELECT book_id,chapter_id,generation_id,parent_generation,parent_revision,target_revision,text_hash,rules_version,status,activated_at FROM segmentation_generations WHERE owner_user_id=? AND generation_id=? AND status IN ('active','committed')", (user, generation_id)).fetchone()
        if row is None:
            return _emit(user, device, [])
        mappings = connection.execute("SELECT entity_kind,old_id,new_id,anchor_start,anchor_end,status FROM segmentation_anchor_maps WHERE owner_user_id=? AND new_generation=? ORDER BY entity_kind,old_id", (user, generation_id)).fetchall()
    get_book_source(user, row["book_id"])
    payload = {key: row[key] for key in ("book_id", "chapter_id", "generation_id", "parent_generation", "parent_revision", "target_revision", "text_hash", "rules_version")}
    # Activation state is local. The transport manifest stays immutable when
    # a later generation archives this one.
    payload.update(source_revision=row["target_revision"], status="committed", maps=[
        {"entity_kind": item["entity_kind"], "old_id": item["old_id"], "new_id": item["new_id"],
         "start": item["anchor_start"], "end": item["anchor_end"], "status": item["status"]} for item in mappings])
    return _emit(user, device, [_mutation("source_generation", row["chapter_id"], payload, row["activated_at"])])


def collect_user_revisions(user: str, device: str) -> dict:
    """Recover committed revisions missing from the outbox; never read assets."""
    totals = {"accepted": 0, "skipped": 0, "conflicts": 0}
    with library._connect(user) as connection:
        books = [row[0] for row in connection.execute("SELECT record_key FROM records WHERE owner_user_id=? AND table_name='books'", (user,))]
        has_generations = connection.execute("SELECT 1 FROM sqlite_master WHERE name='segmentation_generations'").fetchone()
        generations = [row[0] for row in connection.execute("SELECT generation_id FROM segmentation_generations WHERE owner_user_id=? AND status IN ('active','committed') ORDER BY target_revision,created_at", (user,))] if has_generations else []
    with structures.session() as connection:
        spans = connection.execute("SELECT sentence_id,span_id FROM span_overrides WHERE user_id=? ORDER BY updated_at", (user,)).fetchall()
    for result in [*(emit_source_generation(user, device, item) for item in generations),
                   *(emit_book_memory(user, device, book) for book in books),
                   *(emit_span_override(user, device, item[0], item[1]) for item in spans)]:
        for key in totals:
            totals[key] += len(result[key])
    return totals
