import asyncio
import hashlib
from typing import Literal

from fastapi import APIRouter, Depends
from ...android_compat import BaseModel, Field

from ...core.request_context import RequestContext, current_request_context
from .repository import append_events, knowledge_states, list_blindspots, replay_user
from ..sync.repository import push_changes


class KnowledgeItemInput(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    type: Literal["vocabulary", "sense", "expression", "grammar"]
    canonical_key: str = Field(min_length=1, max_length=300)
    lemma: str = ""
    reading: str = ""
    grammar_pattern: str = ""


class LearningEventInput(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    item: KnowledgeItemInput
    event_type: Literal[
        "lookup", "repeated_lookup", "translation_reveal", "grammar_reveal",
        "mark_unknown", "mark_mastered", "natural_exposure",
        "srs_again", "srs_hard", "srs_good", "srs_easy",
    ]
    evidence_weight: float | None = Field(default=None, ge=-1, le=1)
    occurred_at: float
    context: dict = Field(default_factory=dict)


class LearningEventBatch(BaseModel):
    events: list[LearningEventInput] = Field(min_items=1, max_items=100)


class KnowledgeStateQuery(BaseModel):
    items: list[KnowledgeItemInput] = Field(min_items=1,max_items=2000)


router = APIRouter(prefix="/api/learning", tags=["learning"])


@router.post("/events")
async def create_learning_events(
    request: LearningEventBatch,
    context: RequestContext = Depends(current_request_context),
) -> dict[str, int]:
    events=[event.model_dump(exclude_none=True) for event in request.events]
    result=await asyncio.to_thread(
        append_events, context.user_id, context.device_id,
        events,
    )
    mutations=[]
    for event in events:
        item_key=f"{event['item']['type']}:{event['item']['canonical_key']}"
        item_id=hashlib.sha256(item_key.encode("utf-8")).hexdigest()
        mutations.append({"change_id":f"knowledge:{item_id}","entity_type":"knowledge_item","entity_id":item_id,"payload":event["item"],"updated_at":event["occurred_at"]})
        mutations.append({"change_id":f"learning:{event['id']}","entity_type":"learning_event","entity_id":event["id"],"payload":event,"updated_at":event["occurred_at"]})
    await asyncio.to_thread(push_changes,context.user_id,context.device_id,mutations)
    return result


@router.post("/replay")
async def replay_learning_state(
    context: RequestContext = Depends(current_request_context),
) -> dict[str, int]:
    return {"projected": await asyncio.to_thread(replay_user, context.user_id)}


@router.get("/blindspots")
async def read_blindspots(
    limit: int = 50,
    context: RequestContext = Depends(current_request_context),
) -> list[dict]:
    return await asyncio.to_thread(list_blindspots, context.user_id, limit)


@router.post("/states")
async def read_knowledge_states(
    request: KnowledgeStateQuery,
    context: RequestContext = Depends(current_request_context),
) -> list[dict]:
    return await asyncio.to_thread(knowledge_states,context.user_id,[item.model_dump() for item in request.items])
