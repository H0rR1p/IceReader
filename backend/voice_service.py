import asyncio
import hashlib
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from .models import VoiceJobStatus, VoiceSettingsInput, VoiceSettingsStatus
from .paths import DATA_DIR, PROJECT_ROOT


VOICE_DIR = DATA_DIR / "voice"
CACHE_DIR = VOICE_DIR / "cache"
JOBS_DIR = VOICE_DIR / "jobs"
SETTINGS_PATH = VOICE_DIR / "settings.json"
TEMPLATE_PATH = VOICE_DIR / "template.ymmp"
YMM_DIRECTORY_NAME = "幻想乡口音剪辑器"


@dataclass
class _VoiceJob:
    id: str
    status: str = "queued"
    message: str = "等待生成"
    audio_url: str | None = None
    cached: bool = False
    task: asyncio.Task | None = None
    process: asyncio.subprocess.Process | None = None
    cancel_requested: bool = False


_jobs: dict[str, _VoiceJob] = {}
_render_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _render_lock
    if _render_lock is None:
        _render_lock = asyncio.Lock()
    return _render_lock


def _read_settings() -> dict:
    try:
        value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _candidate_roots() -> list[Path]:
    roots = [PROJECT_ROOT, Path.cwd()]
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        roots.extend(executable.parents[:4])
    unique: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _detect_ymm() -> Path | None:
    configured = str(_read_settings().get("ymm_path") or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate.resolve()
    for root in _candidate_roots():
        candidate = root / YMM_DIRECTORY_NAME / "YukkuriMovieMaker.exe"
        if candidate.is_file():
            return candidate.resolve()
    return None


def _voice_item(project: dict) -> dict:
    for timeline in project.get("Timelines", []):
        for item in timeline.get("Items", []):
            if "VoiceItem" in str(item.get("$type", "")):
                return item
    raise ValueError("配音模板中没有找到语音项目")


def _template_character(project: dict) -> str:
    item = _voice_item(project)
    return str(item.get("CharacterName") or "")


def _read_template() -> dict:
    try:
        value = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError("尚未导入 YMM4 配音模板") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"无法读取配音模板：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("配音模板格式不正确")
    _voice_item(value)
    return value


def get_voice_settings() -> VoiceSettingsStatus:
    data = _read_settings()
    ymm = _detect_ymm()
    character = ""
    template_found = TEMPLATE_PATH.is_file()
    if template_found:
        try:
            character = _template_character(_read_template())
        except ValueError:
            template_found = False
    return VoiceSettingsStatus(
        ymm_path=str(ymm or data.get("ymm_path") or ""),
        ymm_found=bool(ymm),
        template_found=template_found,
        character_name=character,
        playback_rate=int(data.get("playback_rate") or 100),
        volume=int(data.get("volume") if data.get("volume") is not None else 50),
        ready=bool(ymm and template_found),
    )


def save_voice_settings(incoming: VoiceSettingsInput) -> VoiceSettingsStatus:
    requested = incoming.ymm_path.strip()
    if requested and not Path(requested).expanduser().is_file():
        raise ValueError("找不到指定的 YukkuriMovieMaker.exe")
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "ymm_path": str(Path(requested).expanduser().resolve()) if requested else "",
        "playback_rate": incoming.playback_rate,
        "volume": incoming.volume,
    }
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(SETTINGS_PATH)
    return get_voice_settings()


def install_template(payload: bytes) -> VoiceSettingsStatus:
    if len(payload) > 5 * 1024 * 1024:
        raise ValueError("YMM4 配音模板不能超过 5 MB")
    try:
        project = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("无法解析 YMM4 配音模板") from exc
    if not isinstance(project, dict):
        raise ValueError("YMM4 配音模板格式不正确")
    _voice_item(project)
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = TEMPLATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(project, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(TEMPLATE_PATH)
    return get_voice_settings()


def _set_animated_value(container: dict | None, value: float) -> None:
    if not isinstance(container, dict):
        return
    values = container.get("Values")
    if isinstance(values, list) and values and isinstance(values[0], dict):
        values[0]["Value"] = value


def render_project(text: str, playback_rate: int, volume: int) -> dict:
    project = _read_template()
    item = _voice_item(project)
    item["Serif"] = text
    item["Hatsuon"] = text
    item["VoiceCache"] = ""
    item["VoiceLength"] = "00:00:00"
    item["Length"] = 1
    _set_animated_value(item.get("PlaybackRate2"), float(playback_rate))
    _set_animated_value(item.get("Volume"), float(volume))
    for timeline in project.get("Timelines", []):
        timeline["CurrentFrame"] = 0
        timeline["Length"] = 1
    return project


def _cache_key(text: str, settings: VoiceSettingsStatus) -> str:
    digest = hashlib.sha256()
    digest.update(text.strip().encode("utf-8"))
    digest.update(TEMPLATE_PATH.read_bytes())
    digest.update(f"{settings.playback_rate}:{settings.volume}".encode("ascii"))
    return digest.hexdigest()


def _status(job: _VoiceJob) -> VoiceJobStatus:
    return VoiceJobStatus(
        id=job.id,
        status=job.status,
        message=job.message,
        audio_url=job.audio_url,
        cached=job.cached,
    )


async def start_voice_job(text: str, force: bool = False) -> VoiceJobStatus:
    settings = get_voice_settings()
    if not settings.ready:
        raise ValueError("配音尚未配置，请先设置 YMM4 路径并导入配音模板")
    normalized = text.strip()
    cache_key = _cache_key(normalized, settings)
    cache_file = CACHE_DIR / f"{cache_key}.wav"
    job = _VoiceJob(id=uuid.uuid4().hex)
    _jobs[job.id] = job
    if cache_file.is_file() and not force:
        job.status = "complete"
        job.message = "已从本地语音缓存读取"
        job.audio_url = f"/api/voice/audio/{cache_file.name}"
        job.cached = True
        return _status(job)
    job.task = asyncio.create_task(_run_job(job, normalized, cache_key, settings))
    return _status(job)


async def _run_job(job: _VoiceJob, text: str, cache_key: str, settings: VoiceSettingsStatus) -> None:
    job_dir = JOBS_DIR / job.id
    try:
        async with _lock():
            if job.cancel_requested:
                job.status, job.message = "canceled", "配音任务已取消"
                return
            job.status, job.message = "running", "YMM4 正在生成语音"
            job_dir.mkdir(parents=True, exist_ok=True)
            project_path = job_dir / "sentence.ymmp"
            output_dir = job_dir / "output"
            output_dir.mkdir()
            project = render_project(text, settings.playback_rate, settings.volume)
            project_path.write_text(json.dumps(project, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            creation_flags = 0x08000000 if os.name == "nt" else 0
            job.process = await asyncio.create_subprocess_exec(
                settings.ymm_path,
                "--encode", str(project_path),
                "--output", str(output_dir),
                cwd=str(Path(settings.ymm_path).parent),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=creation_flags,
            )
            try:
                output, _ = await asyncio.wait_for(job.process.communicate(), timeout=120)
            except asyncio.TimeoutError as exc:
                job.process.kill()
                await job.process.wait()
                raise RuntimeError("YMM4 配音超过 120 秒，任务已停止") from exc
            if job.cancel_requested:
                job.status, job.message = "canceled", "配音任务已取消"
                return
            if job.process.returncode != 0:
                detail = output.decode("utf-8", errors="replace").strip()[-600:]
                raise RuntimeError(detail or f"YMM4 返回错误代码 {job.process.returncode}")
            wav_files = sorted(output_dir.rglob("*.wav"))
            if not wav_files:
                raise RuntimeError("YMM4 已结束，但输出目录中没有 WAV 文件")
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file = CACHE_DIR / f"{cache_key}.wav"
            temporary = cache_file.with_suffix(".tmp")
            shutil.copyfile(wav_files[0], temporary)
            temporary.replace(cache_file)
            job.status = "complete"
            job.message = "配音已生成并保存到本地缓存"
            job.audio_url = f"/api/voice/audio/{cache_file.name}"
    except asyncio.CancelledError:
        job.status, job.message = "canceled", "配音任务已取消"
    except Exception as exc:
        job.status, job.message = "failed", str(exc)
    finally:
        job.process = None
        shutil.rmtree(job_dir, ignore_errors=True)


def get_voice_job(job_id: str) -> VoiceJobStatus:
    job = _jobs.get(job_id)
    if not job:
        raise KeyError(job_id)
    return _status(job)


async def cancel_voice_job(job_id: str) -> VoiceJobStatus:
    job = _jobs.get(job_id)
    if not job:
        raise KeyError(job_id)
    if job.status in {"complete", "failed", "canceled"}:
        return _status(job)
    job.cancel_requested = True
    if job.process and job.process.returncode is None:
        job.process.terminate()
        try:
            await asyncio.wait_for(job.process.wait(), timeout=3)
        except asyncio.TimeoutError:
            job.process.kill()
            await job.process.wait()
    if job.task and not job.task.done():
        job.task.cancel()
    job.status, job.message = "canceled", "配音任务已取消"
    return _status(job)
