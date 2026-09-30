from fastapi import APIRouter, Depends, HTTPException

from ...core.request_context import RequestContext, current_request_context
from .service import get_job, list_jobs


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def read_jobs(
    limit: int = 50,
    context: RequestContext = Depends(current_request_context),
) -> list[dict]:
    return list_jobs(context.user_id, limit)


@router.get("/{job_id}")
async def read_job(
    job_id: str,
    context: RequestContext = Depends(current_request_context),
) -> dict:
    try:
        return get_job(context.user_id, job_id)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
