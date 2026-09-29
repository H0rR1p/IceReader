import asyncio
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from .models import VoiceJobStatus, VoiceSettingsInput, VoiceSettingsStatus
from .nlp import pronunciation_text
from .paths import DATA_DIR, PROJECT_ROOT, resource_path


VOICE_DIR = DATA_DIR / "voice"
CACHE_DIR = VOICE_DIR / "cache"
JOBS_DIR = VOICE_DIR / "jobs"
SETTINGS_PATH = VOICE_DIR / "settings.json"
TEMPLATE_PATH = VOICE_DIR / "template.ymmp"
YMM_DIRECTORY_NAME = "幻想乡口音剪辑器"
CACHE_VERSION = b"voice-v5-character-hiragana"
BRIDGE_API_VERSION = 2
BRIDGE_CONNECTION_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BingduYmmBridge" / "connection.json"
JOB_TTL_SECONDS = 60 * 60
MAX_RETAINED_JOBS = 256


@dataclass
class _VoiceJob:
    id: str
    status: str = "queued"
    message: str = "等待生成"
    audio_url: str | None = None
    cached: bool = False
    task: asyncio.Task | None = None
    cancel_requested: bool = False
    finished_at: float | None = None


_jobs: dict[str, _VoiceJob] = {}
_render_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _render_lock
    if _render_lock is None:
        _render_lock = asyncio.Lock()
    return _render_lock


def _finish_job(job: _VoiceJob, status: str, message: str) -> None:
    job.status = status
    job.message = message
    job.finished_at = time.monotonic()


def _prune_jobs(now: float | None = None) -> None:
    """Retain active jobs and only a bounded, recent set of terminal results."""
    current = time.monotonic() if now is None else now
    expired = [
        job_id for job_id, job in _jobs.items()
        if job.finished_at is not None and current - job.finished_at >= JOB_TTL_SECONDS
    ]
    for job_id in expired:
        _jobs.pop(job_id, None)
    overflow = max(0, len(_jobs) - MAX_RETAINED_JOBS)
    if overflow:
        terminal = sorted(
            ((job.finished_at, job_id) for job_id, job in _jobs.items() if job.finished_at is not None),
            key=lambda value: value[0],
        )
        for _, job_id in terminal[:overflow]:
            _jobs.pop(job_id, None)


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


def _template_characters(project: dict) -> list[str]:
    names: list[str] = []
    for character in project.get("Characters", []):
        if isinstance(character, dict):
            name = str(character.get("Name") or "").strip()
            if name and name not in names:
                names.append(name)
    for timeline in project.get("Timelines", []):
        for item in timeline.get("Items", []):
            if "VoiceItem" not in str(item.get("$type", "")):
                continue
            name = str(item.get("CharacterName") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


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
    characters: list[str] = []
    template_found = TEMPLATE_PATH.is_file()
    if template_found:
        try:
            project = _read_template()
            characters = _template_characters(project)
            character = str(data.get("character_name") or "").strip() or _template_character(project)
            if character and character not in characters:
                characters.insert(0, character)
        except ValueError:
            template_found = False
    return VoiceSettingsStatus(
        ymm_path=str(ymm or data.get("ymm_path") or ""),
        ymm_found=bool(ymm),
        template_found=template_found,
        character_name=character,
        character_names=characters,
        playback_rate=int(data.get("playback_rate") or 85),
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
        "character_name": incoming.character_name.strip(),
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


def _cache_key(text: str, settings: VoiceSettingsStatus) -> str:
    digest = hashlib.sha256()
    digest.update(CACHE_VERSION)
    digest.update(text.strip().encode("utf-8"))
    digest.update(TEMPLATE_PATH.read_bytes())
    digest.update(b"\0character\0")
    digest.update(settings.character_name.strip().encode("utf-8"))
    digest.update(f"{settings.playback_rate}:{settings.volume}".encode("ascii"))
    return digest.hexdigest()


def _trim_wave(path: Path, tail_seconds: float = 0.25) -> float:
    """Trim trailing digital silence from PCM/float WAV and return duration."""
    raw = path.read_bytes()
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise RuntimeError("YMM4 输出的 WAV 格式不正确")
    offset = 12
    fmt: tuple[int, int, int, int, int] | None = None
    data_chunk: tuple[int, int, int] | None = None
    while offset + 8 <= len(raw):
        chunk_id = raw[offset:offset + 4]
        size = struct.unpack_from("<I", raw, offset + 4)[0]
        start = offset + 8
        end = min(start + size, len(raw))
        if chunk_id == b"fmt " and size >= 16:
            audio_format, channels, sample_rate, _, block_align, bits = struct.unpack_from("<HHIIHH", raw, start)
            fmt = (audio_format, channels, sample_rate, block_align, bits)
        elif chunk_id == b"data":
            data_chunk = (offset, start, end)
            break
        offset = start + size + (size & 1)
    if not fmt or not data_chunk:
        raise RuntimeError("YMM4 输出的 WAV 缺少音频数据")
    audio_format, channels, sample_rate, block_align, bits = fmt
    chunk_offset, data_start, data_end = data_chunk
    if channels <= 0 or sample_rate <= 0 or block_align <= 0:
        raise RuntimeError("YMM4 输出的 WAV 参数不正确")
    payload = raw[data_start:data_end]
    frame_count = len(payload) // block_align
    if frame_count <= 0:
        raise RuntimeError("YMM4 输出了空 WAV")

    last_active = -1
    if audio_format == 3 and bits == 32:
        for frame in range(frame_count - 1, -1, -1):
            values = struct.unpack_from("<" + "f" * channels, payload, frame * block_align)
            if any(abs(value) > 0.0005 for value in values):
                last_active = frame
                break
    elif audio_format == 1 and bits == 16:
        for frame in range(frame_count - 1, -1, -1):
            values = struct.unpack_from("<" + "h" * channels, payload, frame * block_align)
            if any(abs(value) > 24 for value in values):
                last_active = frame
                break
    else:
        return frame_count / sample_rate
    if last_active < 0:
        raise RuntimeError("YMM4 输出的 WAV 中没有可播放声音")

    keep_frames = min(frame_count, last_active + 1 + int(sample_rate * tail_seconds))
    keep_size = keep_frames * block_align
    suffix_start = data_end + ((data_end - data_start) & 1)
    trimmed_payload = payload[:keep_size]
    padded_payload = trimmed_payload + (b"\0" if keep_size & 1 else b"")
    rebuilt = bytearray(raw[:chunk_offset])
    rebuilt.extend(b"data")
    rebuilt.extend(struct.pack("<I", keep_size))
    rebuilt.extend(padded_payload)
    rebuilt.extend(raw[suffix_start:])
    struct.pack_into("<I", rebuilt, 4, len(rebuilt) - 8)
    temporary = path.with_suffix(".trim.tmp")
    temporary.write_bytes(rebuilt)
    temporary.replace(path)
    return keep_frames / sample_rate


def _bridge_request(method: str, path: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
    connection = json.loads(BRIDGE_CONNECTION_PATH.read_text(encoding="utf-8"))
    base = str(connection["api_base"]).rstrip("/")
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=body,
        method=method,
        headers={
            "X-Bingdu-Token": str(connection["token"]),
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error")
        except (ValueError, AttributeError):
            detail = None
        raise RuntimeError(str(detail or f"YMM4 配音桥返回 {exc.code}")) from exc


def _bridge_ready() -> bool:
    try:
        status = _bridge_request("GET", "/status")
        return (
            status.get("success") is True
            and status.get("app") == "bingdu-ymm-bridge"
            and int(status.get("api_version") or 0) >= BRIDGE_API_VERSION
        )
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError, urllib.error.URLError):
        return False


def _cancel_bridge_job(job_id: str) -> bool:
    try:
        result = _bridge_request("POST", "/cancel", {"job_id": job_id}, timeout=5.0)
        return result.get("success") is True and result.get("canceled") is True
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError, urllib.error.URLError):
        return False


def _ensure_bridge(settings: VoiceSettingsStatus) -> None:
    if _bridge_ready():
        return
    source = resource_path("ymm4-bridge")
    if not (source / "BingduYmmBridge.dll").is_file():
        source = PROJECT_ROOT / "assets" / "ymm4-bridge"
    target = Path(settings.ymm_path).parent / "user" / "plugin" / "BingduYmmBridge"
    if not source.is_dir():
        raise RuntimeError("冰读安装包缺少 YMM4 配音桥")
    target.mkdir(parents=True, exist_ok=True)
    try:
        for name in ("BingduYmmBridge.dll", "BingduYmmBridge.deps.json"):
            shutil.copy2(source / name, target / name)
        soundtouch = Path(settings.ymm_path).parent / "SoundTouch.Net.dll"
        if not soundtouch.is_file():
            raise RuntimeError("当前 YMM4 目录缺少 SoundTouch.Net.dll，无法进行恒定音高的语速调整")
        shutil.copy2(soundtouch, target / soundtouch.name)
    except PermissionError as exc:
        raise RuntimeError("配音桥需要更新，请先保存项目并完全退出 YMM4 后重试") from exc
    subprocess.Popen(
        [settings.ymm_path, str(TEMPLATE_PATH)],
        cwd=str(Path(settings.ymm_path).parent),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _bridge_ready():
            return
        time.sleep(0.5)
    raise RuntimeError("无法连接冰读 YMM4 配音桥，请保存并重启 YMM4 后重试")


def _synthesize_with_bridge(
    text: str, character: str, output: Path, settings: VoiceSettingsStatus, job_id: str,
) -> None:
    _ensure_bridge(settings)
    result = _bridge_request("POST", "/synthesize", {
        "text": text,
        "character": character,
        "output": str(output.resolve()),
        "playback_rate": settings.playback_rate,
        "volume": settings.volume,
        "job_id": job_id,
    }, timeout=140)
    if result.get("success") is not True:
        raise RuntimeError(str(result.get("error") or "YMM4 配音桥合成失败"))
    if not output.is_file():
        raise RuntimeError("YMM4 配音桥未输出 WAV 文件")


def _status(job: _VoiceJob) -> VoiceJobStatus:
    return VoiceJobStatus(
        id=job.id,
        status=job.status,
        message=job.message,
        audio_url=job.audio_url,
        cached=job.cached,
    )


async def start_voice_job(text: str, force: bool = False) -> VoiceJobStatus:
    _prune_jobs()
    settings = get_voice_settings()
    if not settings.ready:
        raise ValueError("配音尚未配置，请先设置 YMM4 路径并导入配音模板")
    normalized = text.strip()
    cache_key = _cache_key(normalized, settings)
    cache_file = CACHE_DIR / f"{cache_key}.wav"
    job = _VoiceJob(id=uuid.uuid4().hex)
    _jobs[job.id] = job
    if cache_file.is_file() and not force:
        _finish_job(job, "complete", "已从本地语音缓存读取")
        job.audio_url = f"/api/voice/audio/{cache_file.name}"
        job.cached = True
        _prune_jobs()
        return _status(job)
    job.task = asyncio.create_task(_run_job(job, normalized, cache_key, settings))
    return _status(job)


async def _run_job(job: _VoiceJob, text: str, cache_key: str, settings: VoiceSettingsStatus) -> None:
    job_dir = JOBS_DIR / job.id
    try:
        async with _lock():
            if job.cancel_requested:
                _finish_job(job, "canceled", "配音任务已取消")
                return
            job.status, job.message = "running", "YMM4 正在生成语音"
            job_dir.mkdir(parents=True, exist_ok=True)
            output_file = job_dir / "voice.wav"
            character = settings.character_name or ""
            if not character:
                raise RuntimeError("配音模板缺少角色名称，请重新导入有效的 YMM4 项目")
            if job.cancel_requested:
                _finish_job(job, "canceled", "配音任务已取消")
                return
            worker = asyncio.create_task(asyncio.to_thread(
                _synthesize_with_bridge, pronunciation_text(text), character, output_file, settings, job.id,
            ))
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                job.cancel_requested = True
                await asyncio.to_thread(_cancel_bridge_job, job.id)
                try:
                    await asyncio.shield(worker)
                except Exception:
                    pass
                raise
            if job.cancel_requested:
                _finish_job(job, "canceled", "配音任务已取消")
                return
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file = CACHE_DIR / f"{cache_key}.wav"
            temporary = cache_file.with_suffix(".tmp")
            shutil.copyfile(output_file, temporary)
            duration = _trim_wave(temporary)
            if duration < 0.2:
                raise RuntimeError("YMM4 配音桥生成的语音过短，请检查系统输出设备和 YMM4 预览音量")
            temporary.replace(cache_file)
            _finish_job(job, "complete", "配音已生成并保存到本地缓存")
            job.audio_url = f"/api/voice/audio/{cache_file.name}"
    except asyncio.CancelledError:
        _finish_job(job, "canceled", "配音任务已取消")
    except Exception as exc:
        if job.cancel_requested:
            _finish_job(job, "canceled", "配音任务已取消")
        else:
            _finish_job(job, "failed", str(exc))
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        _prune_jobs()


def get_voice_job(job_id: str) -> VoiceJobStatus:
    _prune_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise KeyError(job_id)
    return _status(job)


async def cancel_voice_job(job_id: str) -> VoiceJobStatus:
    _prune_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise KeyError(job_id)
    if job.status in {"complete", "failed", "canceled"}:
        return _status(job)
    job.cancel_requested = True
    await asyncio.to_thread(_cancel_bridge_job, job.id)
    # Keep the record non-terminal for pruning purposes until the worker has
    # actually stopped and released its files. The public status is canceled
    # immediately, while _run_job sets finished_at during safe cleanup.
    job.status, job.message = "canceled", "配音任务已取消"
    return _status(job)
