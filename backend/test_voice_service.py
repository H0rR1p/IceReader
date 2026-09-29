import asyncio
import json
import struct
import threading

from . import voice_service
from .models import VoiceSettingsInput


def _template() -> bytes:
    return json.dumps({
        "Characters": [
            {"Name": "琪露诺", "GroupName": "幻想乡口音"},
            {"Name": "博丽灵梦", "GroupName": "幻想乡口音"},
        ],
        "Timelines": [{
            "Items": [{
                "$type": "YukkuriMovieMaker.Project.Items.VoiceItem, YukkuriMovieMaker",
                "CharacterName": "琪露诺",
                "Serif": "旧文本",
                "Hatsuon": "旧文本",
                "VoiceCache": "old.wav",
                "PlaybackRate2": {"Values": [{"Value": 100.0}]},
                "Volume": {"Values": [{"Value": 50.0}]},
            }],
            "CurrentFrame": 80,
            "Length": 100,
        }],
    }, ensure_ascii=False).encode("utf-8")


def _paths(tmp_path, monkeypatch):
    voice_dir = tmp_path / "voice"
    monkeypatch.setattr(voice_service, "VOICE_DIR", voice_dir)
    monkeypatch.setattr(voice_service, "CACHE_DIR", voice_dir / "cache")
    monkeypatch.setattr(voice_service, "JOBS_DIR", voice_dir / "jobs")
    monkeypatch.setattr(voice_service, "SETTINGS_PATH", voice_dir / "settings.json")
    monkeypatch.setattr(voice_service, "TEMPLATE_PATH", voice_dir / "template.ymmp")
    voice_service._jobs.clear()
    voice_service._render_lock = None


def test_template_install_reads_character_and_settings(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.save_voice_settings(VoiceSettingsInput(ymm_path=str(fake_ymm), playback_rate=125, volume=72))
    status = voice_service.install_template(_template())

    assert status.ready is True
    assert status.character_name == "琪露诺"
    assert status.character_names == ["琪露诺", "博丽灵梦"]
    assert status.playback_rate == 125
    assert status.volume == 72


def test_voice_character_can_override_template_character(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.install_template(_template())
    status = voice_service.save_voice_settings(VoiceSettingsInput(ymm_path=str(fake_ymm), character_name="灵梦", playback_rate=85))

    assert status.character_name == "灵梦"


def test_voice_cache_key_changes_with_character(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.install_template(_template())
    cirno = voice_service.save_voice_settings(VoiceSettingsInput(
        ymm_path=str(fake_ymm), character_name="琪露诺", playback_rate=85, volume=50,
    ))
    reimu = voice_service.save_voice_settings(VoiceSettingsInput(
        ymm_path=str(fake_ymm), character_name="博丽灵梦", playback_rate=85, volume=50,
    ))

    assert voice_service._cache_key("こんにちは。", cirno) != voice_service._cache_key("こんにちは。", reimu)


def test_cached_voice_does_not_start_ymm(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.save_voice_settings(VoiceSettingsInput(ymm_path=str(fake_ymm)))
    voice_service.install_template(_template())
    settings = voice_service.get_voice_settings()
    key = voice_service._cache_key("こんにちは。", settings)
    voice_service.CACHE_DIR.mkdir(parents=True)
    (voice_service.CACHE_DIR / f"{key}.wav").write_bytes(b"RIFF-cache")

    job = asyncio.run(voice_service.start_voice_job("こんにちは。"))

    assert job.status == "complete"
    assert job.cached is True
    assert job.audio_url == f"/api/voice/audio/{key}.wav"


def test_bridge_install_copies_soundtouch_next_to_plugin(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ymm_dir = tmp_path / "ymm"
    fake_ymm = ymm_dir / "YukkuriMovieMaker.exe"
    ymm_dir.mkdir()
    fake_ymm.write_bytes(b"exe")
    (ymm_dir / "SoundTouch.Net.dll").write_bytes(b"soundtouch")
    voice_service.save_voice_settings(VoiceSettingsInput(ymm_path=str(fake_ymm)))
    voice_service.install_template(_template())

    bridge_source = tmp_path / "bridge-source"
    bridge_source.mkdir()
    (bridge_source / "BingduYmmBridge.dll").write_bytes(b"bridge")
    (bridge_source / "BingduYmmBridge.deps.json").write_text("{}", encoding="utf-8")
    readiness = iter((False, True))
    monkeypatch.setattr(voice_service, "resource_path", lambda _: bridge_source)
    monkeypatch.setattr(voice_service, "_bridge_ready", lambda: next(readiness))
    monkeypatch.setattr(voice_service.subprocess, "Popen", lambda *args, **kwargs: object())

    voice_service._ensure_bridge(voice_service.get_voice_settings())

    plugin_dir = ymm_dir / "user" / "plugin" / "BingduYmmBridge"
    assert (plugin_dir / "BingduYmmBridge.dll").read_bytes() == b"bridge"
    assert (plugin_dir / "SoundTouch.Net.dll").read_bytes() == b"soundtouch"


def test_bridge_readiness_requires_cancel_capable_api(monkeypatch):
    monkeypatch.setattr(voice_service, "_bridge_request", lambda *_args, **_kwargs: {
        "success": True, "app": "bingdu-ymm-bridge", "api_version": 1,
    })
    assert voice_service._bridge_ready() is False
    monkeypatch.setattr(voice_service, "_bridge_request", lambda *_args, **_kwargs: {
        "success": True, "app": "bingdu-ymm-bridge", "api_version": 2,
    })
    assert voice_service._bridge_ready() is True


def test_trim_float_wave_removes_trailing_silence(tmp_path):
    sample_rate = 1000
    active = [0.1] * 1000
    silence = [0.0] * 2000
    payload = struct.pack("<" + "f" * len(active + silence), *(active + silence))
    fmt = struct.pack("<HHIIHH", 3, 1, sample_rate, sample_rate * 4, 4, 32)
    raw = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(payload)) + payload
    path = tmp_path / "voice.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(raw)) + raw)

    duration = voice_service._trim_wave(path, tail_seconds=0.25)

    assert 1.24 <= duration <= 1.26
    assert len(path.read_bytes()) < len(raw) + 8


def test_bridge_voice_job_completes_and_caches_real_audio(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.save_voice_settings(VoiceSettingsInput(ymm_path=str(fake_ymm)))
    voice_service.install_template(_template())

    def fake_synthesis(text, character, output, settings, job_id):
        assert text == "きょうははれです。"
        assert settings.playback_rate == 85
        assert character == "琪露诺"
        assert job_id
        sample_rate = 1000
        samples = [1200] * 700 + [0] * 300
        payload = struct.pack("<" + "h" * len(samples), *samples)
        fmt = struct.pack("<HHIIHH", 1, 1, sample_rate, sample_rate * 2, 2, 16)
        raw = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(payload)) + payload
        output.write_bytes(b"RIFF" + struct.pack("<I", len(raw)) + raw)

    monkeypatch.setattr(voice_service, "_synthesize_with_bridge", fake_synthesis)

    async def run_job():
        started = await voice_service.start_voice_job("今日は晴れです。")
        await voice_service._jobs[started.id].task
        return voice_service.get_voice_job(started.id)

    result = asyncio.run(run_job())

    assert result.status == "complete"
    assert result.audio_url and result.audio_url.endswith(".wav")
    assert result.cached is False


def test_cancel_waits_for_worker_before_removing_job_directory(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.save_voice_settings(VoiceSettingsInput(ymm_path=str(fake_ymm)))
    voice_service.install_template(_template())
    entered = threading.Event()
    release = threading.Event()

    def slow_synthesis(_text, _character, output, _settings, _job_id):
        entered.set()
        assert release.wait(timeout=3)
        output.write_bytes(b"unused")

    monkeypatch.setattr(voice_service, "_synthesize_with_bridge", slow_synthesis)
    monkeypatch.setattr(voice_service, "_cancel_bridge_job", lambda _job_id: True)
    monkeypatch.setattr(voice_service, "pronunciation_text", lambda value: value)

    async def run():
        started = await voice_service.start_voice_job("今日は晴れです。", force=True)
        assert await asyncio.to_thread(entered.wait, 1)
        task = voice_service._jobs[started.id].task
        canceled = await voice_service.cancel_voice_job(started.id)
        assert canceled.status == "canceled"
        assert voice_service._jobs[started.id].finished_at is None
        assert (voice_service.JOBS_DIR / started.id).is_dir()
        assert task is not None and not task.done()
        release.set()
        await task
        return started.id

    job_id = asyncio.run(run())

    assert not (voice_service.JOBS_DIR / job_id).exists()
    assert voice_service.get_voice_job(job_id).status == "canceled"
    assert voice_service._jobs[job_id].finished_at is not None


def test_finished_voice_jobs_are_bounded(monkeypatch):
    voice_service._jobs.clear()
    monkeypatch.setattr(voice_service, "MAX_RETAINED_JOBS", 2)
    monkeypatch.setattr(voice_service, "JOB_TTL_SECONDS", 3600)
    for index in range(4):
        job = voice_service._VoiceJob(id=str(index))
        voice_service._jobs[job.id] = job
        voice_service._finish_job(job, "complete", "done")
        job.finished_at = float(index + 1)

    voice_service._prune_jobs(now=4.0)

    assert set(voice_service._jobs) == {"2", "3"}
