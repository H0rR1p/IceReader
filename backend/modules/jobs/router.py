import asyncio
from fastapi import APIRouter, Depends, HTTPException
from ...model_compat import BaseModel, Field

from ...core.request_context import RequestContext, current_request_context
from .service import get_job, list_jobs
from . import repository, runner


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class BudgetInput(BaseModel):
    max_calls: int | None = Field(default=None,ge=0,le=1000)
    max_tokens: int | None = Field(default=None,ge=0,le=1000000)
    max_estimated_tokens: int | None = Field(default=None,ge=0,le=100000)
    expected_updated_at: float


@router.post('/{job_id}/budget')
async def budget(job_id: str,request: BudgetInput,context: RequestContext=Depends(current_request_context)):
    try:
        values=request.model_dump(exclude_none=True)
        expected=values.pop('expected_updated_at')
        return await asyncio.to_thread(repository.update_task_budget,context.user_id,job_id,values,expected)
    except KeyError as exc:
        raise HTTPException(404,'任务不存在') from exc
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.get("")
async def read_jobs(
    limit: int = 50,
    book_id: str | None = None,
    context: RequestContext = Depends(current_request_context),
) -> list[dict]:
    return await asyncio.to_thread(repository.list_tasks, context.user_id, book_id, limit) if book_id else await asyncio.to_thread(list_jobs, context.user_id, limit)


@router.get("/{job_id}")
async def read_job(
    job_id: str,
    context: RequestContext = Depends(current_request_context),
) -> dict:
    try:
        return await asyncio.to_thread(get_job, context.user_id, job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc


@router.post('/{job_id}/cancel')
async def cancel(job_id: str, context: RequestContext = Depends(current_request_context)):
    try:
        return await asyncio.to_thread(repository.request_cancel, context.user_id, job_id)
    except KeyError as exc:
        raise HTTPException(404, '任务不存在') from exc


@router.post('/{job_id}/resume')
async def resume(job_id: str, context: RequestContext = Depends(current_request_context)):
    try:
        return await runner.resume(context.user_id, job_id)
    except KeyError as exc:
        raise HTTPException(404, '任务不存在') from exc
