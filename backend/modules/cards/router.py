import asyncio
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from ...android_compat import BaseModel, Field

from ...core.request_context import RequestContext, current_request_context
from ..learning.service import record_learning_events
from . import repository, service
from ..sync.repository import push_changes


router = APIRouter(prefix="/api/cards",tags=["cards"])


async def _sync_cards(context: RequestContext, card_ids: list[str]) -> None:
    rows = await asyncio.to_thread(repository.cards_by_ids, context.user_id, card_ids)
    mutations = []
    for card in rows:
        updated = float(card.get("updated_at") or time.time())
        deleted = card.get("deleted_at")
        mutations.extend([
            {"change_id": str(uuid.uuid4()), "entity_type": "note", "entity_id": str(card["note_id"]), "payload": card, "updated_at": updated,
             "base_version": max(0, int(card.get("note_version") or 1) - 1)},
            {"change_id": str(uuid.uuid4()), "entity_type": "card", "entity_id": str(card["id"]), "payload": card, "updated_at": updated,
             "base_version": max(0, int(card.get("version") or 1) - 1),
             **({"deleted_at": float(deleted)} if deleted is not None else {})},
        ])
    if mutations:
        await asyncio.to_thread(push_changes, context.user_id, context.device_id, mutations)


class CandidateInput(BaseModel):
    id: str | None = None
    item: dict
    lemma: str = Field(min_length=1,max_length=300)
    reading: str = ""
    gloss: str = ""
    sentence: str = ""
    book_id: str = ""
    book_title: str = ""
    chapter_id: str = ""
    sentence_id: str = ""
    card_template: str = "context-recognition"


class BulkInput(BaseModel):
    card_ids: list[str] = Field(min_items=1,max_items=500)
    status: Literal["active","suspended","archived"]


class BulkTagInput(BaseModel):
    card_ids: list[str] = Field(min_items=1,max_items=500)
    tag: str = Field(min_length=1,max_length=100)
    remove: bool = False


class ReviewInput(BaseModel):
    id: str = Field(min_length=1,max_length=200)
    rating: Literal["again","hard","good","easy"]
    reviewed_at: float = Field(default_factory=time.time)


class CardNoteInput(BaseModel):
    lemma: str = Field(min_length=1, max_length=300)
    reading: str = Field(default="", max_length=300)
    gloss: str = Field(default="", max_length=2000)
    sentence: str = Field(default="", max_length=5000)


class CardPreferencesInput(BaseModel):
    daily_new_limit: int = Field(ge=0, le=200)
    daily_review_limit: int = Field(ge=1, le=1000)


class MergeCardsInput(BaseModel):
    target_card_id: str = Field(min_length=1, max_length=200)
    source_card_ids: list[str] = Field(min_items=1, max_items=100)


@router.post("/candidates")
async def create_candidate(payload: CandidateInput,context: RequestContext=Depends(current_request_context)) -> dict:
    return await asyncio.to_thread(service.add_candidate,context.user_id,payload.model_dump())


@router.get("/candidates")
async def read_candidates(status: str="candidate",limit: int=100,context: RequestContext=Depends(current_request_context)) -> list[dict]:
    return await asyncio.to_thread(repository.list_candidates,context.user_id,status,limit)


@router.post("/candidates/{candidate_id}/accept")
async def accept_candidate(candidate_id: str,context: RequestContext=Depends(current_request_context)) -> dict:
    try: card=await asyncio.to_thread(service.accept,context.user_id,candidate_id)
    except KeyError: raise HTTPException(404,"候选卡不存在") from None
    except repository.DailyNewLimitError as exc: raise HTTPException(409,str(exc)) from None
    now=float(card.get("updated_at") or time.time())
    await asyncio.to_thread(push_changes,context.user_id,context.device_id,[
        {"change_id":f"note:{card['note_id']}:1","entity_type":"note","entity_id":card["note_id"],"payload":card,"updated_at":now,"base_version":0},
        {"change_id":f"card:{card['id']}:1","entity_type":"card","entity_id":card["id"],"payload":card,"updated_at":now,"base_version":0},
    ])
    return card


@router.post("/candidates/{candidate_id}/reject")
async def reject_candidate(candidate_id: str,context: RequestContext=Depends(current_request_context)) -> dict:
    try: await asyncio.to_thread(repository.reject_candidate,context.user_id,candidate_id)
    except KeyError: raise HTTPException(404,"候选卡不存在") from None
    return {"rejected":True}


@router.get("")
async def read_cards(q: str="",status: str="",due: str="",limit: int=100,offset: int=0,context: RequestContext=Depends(current_request_context)) -> list[dict]:
    return await asyncio.to_thread(repository.search_cards,context.user_id,q,status,due,limit,offset)


@router.get("/page")
async def read_cards_page(q: str="",status: str="",due: str="",limit: int=50,offset: int=0,context: RequestContext=Depends(current_request_context)) -> dict:
    safe_limit=min(100,max(1,limit)); safe_offset=max(0,offset)
    items,total=await asyncio.gather(
        asyncio.to_thread(repository.search_cards,context.user_id,q,status,due,safe_limit,safe_offset),
        asyncio.to_thread(repository.count_cards,context.user_id,q,status,due),
    )
    return {"items":items,"total":total,"limit":safe_limit,"offset":safe_offset}


@router.get("/summary")
async def read_card_summary(context: RequestContext=Depends(current_request_context)) -> dict:
    return await asyncio.to_thread(repository.card_summary,context.user_id)


@router.post("/bulk-status")
async def bulk_status(payload: BulkInput,context: RequestContext=Depends(current_request_context)) -> dict:
    result=await asyncio.to_thread(repository.update_card_statuses,context.user_id,payload.card_ids,payload.status)
    await _sync_cards(context,payload.card_ids)
    return result


@router.post("/bulk-tags")
async def bulk_tags(payload: BulkTagInput,context: RequestContext=Depends(current_request_context)) -> dict:
    result=await asyncio.to_thread(repository.update_card_tags,context.user_id,payload.card_ids,payload.tag.strip(),payload.remove)
    await _sync_cards(context,payload.card_ids)
    return result


@router.patch("/{card_id}")
async def edit_card(card_id: str,payload: CardNoteInput,context: RequestContext=Depends(current_request_context)) -> dict:
    try:
        result=await asyncio.to_thread(repository.update_card_note,context.user_id,card_id,payload.model_dump())
        await _sync_cards(context,[card_id])
        return result
    except KeyError:
        raise HTTPException(404,"卡片不存在") from None
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None


@router.post("/undo/{undo_id}")
async def undo_card_change(undo_id: str,context: RequestContext=Depends(current_request_context)) -> dict:
    try: return await asyncio.to_thread(repository.undo_card_action,context.user_id,undo_id)
    except KeyError: raise HTTPException(404,"撤销记录不存在或已经使用") from None


@router.get("/tags")
async def read_card_tags(context: RequestContext=Depends(current_request_context)) -> list[dict]:
    return await asyncio.to_thread(repository.list_tags,context.user_id)


@router.get("/preferences")
async def read_card_preferences(context: RequestContext=Depends(current_request_context)) -> dict:
    return await asyncio.to_thread(repository.get_card_preferences,context.user_id)


@router.put("/preferences")
async def write_card_preferences(payload: CardPreferencesInput,context: RequestContext=Depends(current_request_context)) -> dict:
    result=await asyncio.to_thread(repository.update_card_preferences,context.user_id,payload.daily_new_limit,payload.daily_review_limit)
    await asyncio.to_thread(push_changes,context.user_id,context.device_id,[{
        "change_id":str(uuid.uuid4()),"entity_type":"preference","entity_id":"card-limits",
        "payload":{"kind":"card_limits",**result},"updated_at":time.time(),
    }])
    return result


@router.post("/merge")
async def merge_duplicate_cards(payload: MergeCardsInput,context: RequestContext=Depends(current_request_context)) -> dict:
    try:
        result=await asyncio.to_thread(repository.merge_cards,context.user_id,payload.target_card_id,payload.source_card_ids)
        await _sync_cards(context,[payload.target_card_id,*payload.source_card_ids])
        return result
    except KeyError:
        raise HTTPException(404,"待合并卡片不存在") from None
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None


@router.get("/due")
async def read_due_cards(limit: int=100,context: RequestContext=Depends(current_request_context)) -> list[dict]:
    return await asyncio.to_thread(repository.due_cards,context.user_id,limit)


@router.post("/{card_id}/review")
async def submit_review(card_id: str,payload: ReviewInput,context: RequestContext=Depends(current_request_context)) -> dict:
    try:
        state=await asyncio.to_thread(repository.review_card,context.user_id,context.device_id,card_id,payload.rating,payload.reviewed_at,payload.id)
    except KeyError: raise HTTPException(404,"卡片不存在") from None
    await asyncio.to_thread(record_learning_events,context.user_id,context.device_id,[{
        "id":f"learning:{payload.id}","item":{"id":card_id,"type":"vocabulary","canonical_key":f"card:{card_id}"},
        "event_type":f"srs_{payload.rating}","occurred_at":payload.reviewed_at,"context":{"card_id":card_id},
    }])
    await asyncio.to_thread(push_changes,context.user_id,context.device_id,[{
        "change_id":f"review:{payload.id}","entity_type":"review_log","entity_id":payload.id,
        "payload":{"card_id":card_id,"rating":payload.rating,"reviewed_at":payload.reviewed_at,"state":state},
        "updated_at":payload.reviewed_at,
    }])
    return state


@router.get("/views/saved")
async def read_views(context: RequestContext=Depends(current_request_context)) -> list[dict]:
    return await asyncio.to_thread(repository.list_views,context.user_id)


@router.post("/views/saved")
async def write_view(payload: dict,context: RequestContext=Depends(current_request_context)) -> dict:
    name=str(payload.get("name") or "").strip()
    if not name: raise HTTPException(422,"视图名称不能为空")
    return await asyncio.to_thread(repository.save_view,context.user_id,name,payload.get("query") or {})


@router.delete("/views/saved/{view_id}")
async def remove_view(view_id: str,context: RequestContext=Depends(current_request_context)) -> dict:
    if not await asyncio.to_thread(repository.delete_view,context.user_id,view_id):
        raise HTTPException(404,"筛选视图不存在")
    return {"deleted":True}

