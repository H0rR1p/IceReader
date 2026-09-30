import uuid

from ..learning.service import resolve_knowledge_item
from . import repository


def add_candidate(user_id: str, payload: dict) -> dict:
    item = payload["item"]
    knowledge_item_id = resolve_knowledge_item({**item,"id":item.get("id") or str(uuid.uuid4())})
    return repository.create_candidate(user_id,knowledge_item_id,payload)


def accept(user_id: str, candidate_id: str) -> dict:
    return repository.accept_candidate(user_id,candidate_id)
