from .repository import append_events, ensure_knowledge_item


def resolve_knowledge_item(item: dict) -> str:
    return ensure_knowledge_item(item)


def record_learning_events(user_id: str, device_id: str, events: list[dict]) -> dict[str, int]:
    return append_events(user_id, device_id, events)
