from __future__ import annotations

from ...models import LibraryPatch
from ..cards import repository as cards_repository
from ..learning import repository as learning_repository
from ..library.repository import apply_library_patch, apply_synced_progress


def apply_remote_changes(user_id: str, device_id: str, changes: list[dict]) -> dict[str, int]:
    applied = 0
    skipped = 0
    for change in sorted(changes, key=lambda item: int(item.get("cursor") or 0)):
        entity_type = str(change.get("entity_type") or "")
        payload = change.get("payload") or {}
        deleted = change.get("deleted_at") is not None or change.get("operation") == "delete"
        try:
            if entity_type == "knowledge_item" and not deleted:
                learning_repository.ensure_knowledge_item(payload)
            elif entity_type == "learning_event" and not deleted:
                learning_repository.append_events(user_id, device_id, [payload])
            elif entity_type == "card":
                if deleted:
                    changed = cards_repository.delete_synced_card(
                        user_id, str(change["entity_id"]), float(change.get("deleted_at") or change.get("updated_at") or 0),
                    )
                else:
                    changed = cards_repository.upsert_synced_card(user_id, payload)
                if not changed:
                    skipped += 1
                    continue
            elif entity_type == "review_log" and not deleted:
                cards_repository.review_card(
                    user_id, device_id, str(payload["card_id"]), str(payload["rating"]),
                    float(payload["reviewed_at"]), str(change["entity_id"]),
                )
            elif entity_type == "bookmark":
                if deleted:
                    apply_library_patch(user_id, LibraryPatch(deletes={"bookmarks": [str(change["entity_id"])]}))
                else:
                    apply_library_patch(user_id, LibraryPatch(upserts={"bookmarks": [payload]}))
            elif entity_type == "reading_progress" and not deleted:
                if not apply_synced_progress(user_id, payload, float(change.get("updated_at") or 0)):
                    skipped += 1
                    continue
            elif entity_type == "preference" and not deleted and payload.get("kind") == "card_limits":
                cards_repository.update_card_preferences(
                    user_id, int(payload["daily_new_limit"]), int(payload["daily_review_limit"]),
                )
            elif entity_type == "lexeme":
                if deleted:
                    apply_library_patch(user_id, LibraryPatch(deletes={"lexemes": [str(change["entity_id"])]}))
                else:
                    apply_library_patch(user_id, LibraryPatch(upserts={"lexemes": [payload]}))
            elif entity_type == "note" and not deleted:
                # Note mutations carry the joined card snapshot so a conflict
                # winner can be materialized without waiting for a card write.
                if not cards_repository.upsert_synced_card(user_id, payload):
                    skipped += 1
                    continue
            else:
                skipped += 1
                continue
            applied += 1
        except (KeyError, TypeError, ValueError):
            skipped += 1
    return {"applied": applied, "skipped": skipped}

