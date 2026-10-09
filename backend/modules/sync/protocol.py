"""Capability checks and source ancestry guards shared by local and cloud sync."""
import json
from ..data_portability.protocol import CAPABILITIES, PROTOCOL_VERSION

LEGACY_TYPES = {"knowledge_item", "learning_event", "note", "card", "review_log",
                "bookmark", "reading_progress", "preference", "lexeme"}
REVISION_TYPES = {"span_override", "span_override_revision", "book_entity", "book_fact",
                  "book_memory_revision", "source_generation"}
APPEND_ONLY_TYPES = {"learning_event", "review_log", "span_override_revision", "book_memory_revision"}
ALLOWED_TYPES = LEGACY_TYPES | REVISION_TYPES
FORK_ON_CONFLICT_TYPES = {"note", "span_override", "book_entity", "book_fact", "source_generation"}


def capabilities() -> dict:
    return {"schema_version": PROTOCOL_VERSION, "capabilities": sorted(CAPABILITIES),
            "accepted_schema_versions": [1, 2, 3], "entity_types": sorted(ALLOWED_TYPES),
            "content_scope": "learning-and-user-revisions", "book_source_upload": False}


def required_capabilities(change: dict) -> set[str]:
    kind = change.get("entity_type", "")
    required = set()
    if kind in REVISION_TYPES:
        required.add("user-revisions-v1")
    if kind.startswith("book_"):
        required.add("book-memory-v1")
    payload = change.get("payload") or {}
    if kind == "source_generation" or source_metadata(payload).get("generation") or payload.get("sources") or isinstance(payload.get("source"), dict) and payload["source"]:
        required.add("source-generations-v1")
    return required


def validate_exchange(changes: list[dict], schema_version: int = PROTOCOL_VERSION, peer_capabilities=None) -> None:
    if schema_version not in {1, 2, 3}:
        raise ValueError("unsupported_sync_schema")
    available = CAPABILITIES if peer_capabilities is None else set(peer_capabilities)
    for change in changes:
        kind = change.get("entity_type", "")
        if kind not in ALLOWED_TYPES:
            raise ValueError(f"unsupported_entity:{kind}")
        required = required_capabilities(change)
        if required and (schema_version < 3 or required - available):
            raise ValueError("sync_capability_required:" + ",".join(sorted(required)))
        if kind == "source_generation":
            validate_source_generation(change.get("payload") or {})


def validate_source_generation(payload: dict) -> None:
    allowed = {"book_id", "chapter_id", "generation_id", "parent_generation", "parent_revision",
               "source_revision", "target_revision", "text_hash", "rules_version", "status", "maps"}
    mapping_keys = {"entity_kind", "old_id", "new_id", "start", "end", "status"}
    if set(payload) - allowed:
        raise ValueError("book_source_upload_forbidden")
    if not all(isinstance(payload.get(key), str) and payload[key] for key in
               ("book_id", "chapter_id", "generation_id", "text_hash")):
        raise ValueError("invalid_source_generation")
    revision = payload.get("source_revision", payload.get("target_revision"))
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("invalid_source_generation")
    if payload.get("status") not in {None, "active", "archived", "committed"}:
        raise ValueError("uncommitted_source_generation")
    mappings = payload.get("maps", [])
    if not isinstance(mappings, list):
        raise ValueError("invalid_source_mapping")
    for mapping in mappings:
        if not isinstance(mapping, dict) or set(mapping) - mapping_keys:
            raise ValueError("book_source_upload_forbidden")
        if not all(isinstance(mapping.get(key), str) for key in ("entity_kind", "old_id", "status")):
            raise ValueError("invalid_source_mapping")
        if mapping.get("new_id") is not None and not isinstance(mapping["new_id"], str):
            raise ValueError("invalid_source_mapping")
        if any(not isinstance(mapping.get(key), int) or isinstance(mapping[key], bool) for key in ("start", "end")) or not 0 <= mapping["start"] <= mapping["end"]:
            raise ValueError("invalid_source_mapping")


def source_metadata(payload: dict) -> dict:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else payload
    return {"generation": source.get("generation_id") or source.get("active_generation") or source.get("generation"),
            "revision": source.get("source_revision") or source.get("analysis_revision") or source.get("target_revision"),
            "parent": source.get("parent_generation") or source.get("base_generation"),
            "object_revision": payload.get("revision") or payload.get("override_revision") or (payload.get("object") or {}).get("revision")}


def revision_conflict(entity_type: str, incoming: dict, current: dict | None) -> str | None:
    if not current or current == incoming:
        return None
    if entity_type in APPEND_ONLY_TYPES:
        return None
    incoming_sources = {item.get('chapter_id'): item for item in incoming.get('sources', []) if isinstance(item, dict)}
    for source in current.get('sources', []):
        if not isinstance(source, dict):
            continue
        reason = revision_conflict('source_reference', incoming_sources.get(source.get('chapter_id'), {}), source)
        if reason:
            return reason
    new, old = source_metadata(incoming), source_metadata(current)
    if old["generation"] and not new["generation"]:
        return "missing_source_generation"
    if old["generation"] and new["generation"] != old["generation"]:
        if new["parent"] != old["generation"]:
            return "source_generation_conflict"
    if old["revision"] is not None:
        if new["revision"] is None or int(new["revision"]) < int(old["revision"]):
            return "stale_source_revision"
    if entity_type in REVISION_TYPES - APPEND_ONLY_TYPES and old["object_revision"] is not None:
        old_object = current.get('object', current.get('payload', current))
        new_object = incoming.get('object', incoming.get('payload', incoming))
        if new["object_revision"] is None or int(new["object_revision"]) < int(old["object_revision"]) or old_object != new_object and int(new["object_revision"]) == int(old["object_revision"]):
            return "stale_object_revision"
    return None


def generation_conflict(connection, user_id: str, mutation: dict) -> str | None:
    """Protect a chapter even when an older device writes a new object ID."""
    kind = mutation['entity_type']
    # Saved cards/notes can intentionally cite archived examples. Their own
    # source metadata still guards replacement of an existing card identity.
    if kind in {'source_generation', 'card', 'note'} or kind in APPEND_ONLY_TYPES:
        return None
    payload = mutation.get('payload') or {}
    references = list(payload.get('sources') or [])
    chapter_id = payload.get('chapter_id') or payload.get('chapterId')
    source = payload.get('source') if isinstance(payload.get('source'), dict) else payload
    chapter_id = chapter_id or source.get('chapter_id')
    if chapter_id:
        references.append({**source, 'chapter_id': chapter_id})
    for reference in references:
        if not isinstance(reference, dict):
            raise ValueError('invalid_source_reference')
        row = connection.execute("SELECT payload_json FROM sync_entities WHERE user_id=? AND entity_type='source_generation' AND entity_id=?", (user_id, reference.get('chapter_id'))).fetchone()
        if row:
            reason = revision_conflict('source_reference', reference, json.loads(row[0]))
            if reason:
                return reason
    return None


def history_flag_advance(entity_type: str, incoming: dict, current: dict) -> bool:
    """Undo marks may advance without rewriting the immutable revision body."""
    if entity_type != 'book_memory_revision':
        return False
    if {key: value for key, value in incoming.items() if key != 'undone'} != {key: value for key, value in current.items() if key != 'undone'}:
        return False
    return int(current.get('undone', 0)) == 0 and int(incoming.get('undone', 0)) == 1
