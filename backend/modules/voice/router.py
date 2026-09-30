from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ...core.request_context import RequestContext, current_request_context
from ...models import VoiceJobStatus, VoiceSettingsInput, VoiceSettingsStatus, VoiceSynthesisRequest
from ...voice_service import (
    cancel_voice_job,
    get_voice_audio_path,
    get_voice_job,
    get_voice_settings,
    install_template,
    save_voice_settings,
    start_voice_job,
)
from ..jobs.service import create_job as create_persistent_job
from ..jobs.service import update_job as update_persistent_job


router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.get("/settings", response_model=VoiceSettingsStatus)
async def read_voice_settings() -> VoiceSettingsStatus:
    return get_voice_settings()


@router.put("/settings", response_model=VoiceSettingsStatus)
async def update_voice_settings(settings: VoiceSettingsInput) -> VoiceSettingsStatus:
    try:
        return save_voice_settings(settings)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/template", response_model=VoiceSettingsStatus)
async def upload_voice_template(file: UploadFile = File(...)) -> VoiceSettingsStatus:
    if not (file.filename or "").lower().endswith(".ymmp"):
        raise HTTPException(400, "请选择 .ymmp 配音模板")
    try:
        return install_template(await file.read())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/jobs", response_model=VoiceJobStatus)
async def create_voice_job(
    request: VoiceSynthesisRequest,
    context: RequestContext = Depends(current_request_context),
) -> VoiceJobStatus:
    try:
        job = await start_voice_job(context.user_id, request.text, request.force)
        create_persistent_job(
            context.user_id,
            "voice",
            job_id=job.id,
            request_id=context.request_id,
            message=job.message,
        )
        update_persistent_job(
            context.user_id, job.id, status=job.status, message=job.message,
            result={"audio_url": job.audio_url, "cached": job.cached},
        )
        return job
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/jobs/{job_id}", response_model=VoiceJobStatus)
async def read_voice_job(
    job_id: str,
    context: RequestContext = Depends(current_request_context),
) -> VoiceJobStatus:
    try:
        job = get_voice_job(context.user_id, job_id)
        update_persistent_job(
            context.user_id, job.id, status=job.status, message=job.message,
            result={"audio_url": job.audio_url, "cached": job.cached},
        )
        return job
    except KeyError as exc:
        raise HTTPException(404, "配音任务不存在") from exc


@router.get("/jobs/{job_id}/audio")
async def read_voice_audio(
    job_id: str,
    context: RequestContext = Depends(current_request_context),
) -> FileResponse:
    try:
        return FileResponse(get_voice_audio_path(context.user_id, job_id), media_type="audio/wav")
    except KeyError as exc:
        raise HTTPException(404, "配音文件不存在") from exc


@router.delete("/jobs/{job_id}", response_model=VoiceJobStatus)
async def delete_voice_job(
    job_id: str,
    context: RequestContext = Depends(current_request_context),
) -> VoiceJobStatus:
    try:
        job = await cancel_voice_job(context.user_id, job_id)
        update_persistent_job(context.user_id, job.id, status=job.status, message=job.message)
        return job
    except KeyError as exc:
        raise HTTPException(404, "配音任务不存在") from exc
