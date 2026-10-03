import asyncio
import json
import struct
import threading

import pytest

from . import voice_service
from .models import VoiceSettingsInput


USER_ID = "user-1"


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
    voice_service.save_voice_settings(USER_ID, VoiceSettingsInput(ymm_path=str(fake_ymm), playback_rate=125, volume=72))
    status = voice_service.install_template(USER_ID, _template())

    assert status.ready is True
    assert status.character_name == "琪露诺"
    assert status.character_names == ["琪露诺", "博丽灵梦"]
    assert status.playback_rate == 125
    assert status.volume == 72


def test_server_voice_defaults_are_available_to_every_user(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ymm_dir = tmp_path / "server-ymm"
    ymm_dir.mkdir()
    fake_ymm = ymm_dir / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    shared_template = tmp_path / "server-template.ymmp"
    shared_template.write_bytes(_template())
    monkeypatch.setenv("BINGDU_VOICE_YMM_PATH", str(fake_ymm))
    monkeypatch.setenv("BINGDU_VOICE_TEMPLATE_PATH", str(shared_template))
    monkeypatch.setenv("BINGDU_VOICE_CHARACTER", "博丽灵梦")

    status = voice_service.get_voice_settings("new-server-user")

    assert status.ready is True
    assert status.ymm_path == str(fake_ymm.resolve())
    assert status.template_found is True
    assert status.character_name == "博丽灵梦"
    assert status.character_names == ["琪露诺", "博丽灵梦"]
    assert voice_service._template_path_for_user("new-server-user") == shared_template.resolve()


def test_voice_character_can_override_template_character(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.install_template(USER_ID, _template())
    status = voice_service.save_voice_settings(USER_ID, VoiceSettingsInput(ymm_path=str(fake_ymm), character_name="灵梦", playback_rate=85))

    assert status.character_name == "灵梦"


def test_legacy_voice_settings_migrate_only_to_requested_user(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    voice_service.SETTINGS_PATH.write_text(json.dumps({"ymm_path": str(fake_ymm)}), encoding="utf-8")
    voice_service.TEMPLATE_PATH.write_bytes(_template())

    voice_service.migrate_legacy_voice_settings("migration-user")

    assert voice_service.get_voice_settings("migration-user").ready is True
    assert voice_service.get_voice_settings("other-user").ready is False


def test_voice_cache_key_changes_with_character(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.install_template(USER_ID, _template())
    cirno = voice_service.save_voice_settings(USER_ID, VoiceSettingsInput(
        ymm_path=str(fake_ymm), character_name="琪露诺", playback_rate=85, volume=50,
    ))
    reimu = voice_service.save_voice_settings(USER_ID, VoiceSettingsInput(
        ymm_path=str(fake_ymm), character_name="博丽灵梦", playback_rate=85, volume=50,
    ))

    template_path = voice_service._user_paths(USER_ID)[1]
    assert voice_service._cache_key("こんにちは。", cirno, template_path) != voice_service._cache_key("こんにちは。", reimu, template_path)


def test_cached_voice_does_not_start_ymm(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.save_voice_settings(USER_ID, VoiceSettingsInput(ymm_path=str(fake_ymm)))
    voice_service.install_template(USER_ID, _template())
    settings = voice_service.get_voice_settings(USER_ID)
    template_path, cache_dir = voice_service._user_paths(USER_ID)[1:]
    key = voice_service._cache_key("こんにちは。", settings, template_path)
    cache_dir.mkdir(parents=True)
    (cache_dir / f"{key}.wav").write_bytes(b"RIFF-cache")

    job = asyncio.run(voice_service.start_voice_job("user-1", "こんにちは。"))

    assert job.status == "complete"
    assert job.cached is True
    assert job.audio_url == f"/api/voice/jobs/{job.id}/audio"


def test_bridge_install_copies_soundtouch_next_to_plugin(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ymm_dir = tmp_path / "ymm"
    fake_ymm = ymm_dir / "YukkuriMovieMaker.exe"
    ymm_dir.mkdir()
    fake_ymm.write_bytes(b"exe")
    (ymm_dir / "SoundTouch.Net.dll").write_bytes(b"soundtouch")
    voice_service.save_voice_settings(USER_ID, VoiceSettingsInput(ymm_path=str(fake_ymm)))
    voice_service.install_template(USER_ID, _template())

    bridge_source = tmp_path / "bridge-source"
    bridge_source.mkdir()
    (bridge_source / "BingduYmmBridge.dll").write_bytes(b"bridge")
    (bridge_source / "BingduYmmBridge.deps.json").write_text("{}", encoding="utf-8")
    readiness = iter((False, True))
    monkeypatch.setattr(voice_service, "resource_path", lambda _: bridge_source)
    monkeypatch.setattr(voice_service, "_bridge_ready", lambda: next(readiness))
    monkeypatch.setattr(voice_service.subprocess, "Popen", lambda *args, **kwargs: object())

    voice_service._ensure_bridge(voice_service.get_voice_settings(USER_ID), voice_service._user_paths(USER_ID)[1])

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


def test_bridge_connection_path_can_be_shared_across_service_accounts(tmp_path, monkeypatch):
    shared = tmp_path / "bridge" / "connection.json"
    monkeypatch.setenv("BINGDU_VOICE_BRIDGE_CONNECTION_PATH", str(shared))
    assert voice_service._bridge_connection_path() == shared.resolve()


def test_bridge_request_tries_other_desktop_user_descriptor(tmp_path, monkeypatch):
    stale = tmp_path / "system" / "connection.json"
    active = tmp_path / "desktop" / "connection.json"
    stale.parent.mkdir()
    active.parent.mkdir()
    stale.write_text(json.dumps({"api_base": "http://127.0.0.1:1", "token": "stale"}), encoding="utf-8")
    active.write_text(json.dumps({"api_base": "http://127.0.0.1:2", "token": "active"}), encoding="utf-8")
    monkeypatch.setattr(voice_service, "_bridge_connection_paths", lambda: [stale, active])

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self): return b'{"success":true,"app":"bingdu-ymm-bridge","api_version":2}'

    def fake_urlopen(request, timeout):
        if request.full_url.endswith(":1/status"):
            raise OSError("stale descriptor")
        assert request.get_header("X-bingdu-token") == "active"
        assert timeout == 5.0
        return Response()

    monkeypatch.setattr(voice_service.urllib.request, "urlopen", fake_urlopen)
    assert voice_service._bridge_ready() is True


def test_bridge_install_does_not_replace_identical_loaded_files(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ymm_dir = tmp_path / "ymm"
    plugin_dir = ymm_dir / "user" / "plugin" / "BingduYmmBridge"
    plugin_dir.mkdir(parents=True)
    fake_ymm = ymm_dir / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    (ymm_dir / "SoundTouch.Net.dll").write_bytes(b"soundtouch")
    voice_service.save_voice_settings(USER_ID, VoiceSettingsInput(ymm_path=str(fake_ymm)))
    voice_service.install_template(USER_ID, _template())
    bridge_source = tmp_path / "bridge-source"
    bridge_source.mkdir()
    files = {"BingduYmmBridge.dll": b"bridge", "BingduYmmBridge.deps.json": b"{}", "SoundTouch.Net.dll": b"soundtouch"}
    for name, content in files.items():
        (plugin_dir / name).write_bytes(content)
        if name != "SoundTouch.Net.dll":
            (bridge_source / name).write_bytes(content)
    readiness = iter((False, True))
    monkeypatch.setattr(voice_service, "resource_path", lambda _: bridge_source)
    monkeypatch.setattr(voice_service, "_bridge_ready", lambda: next(readiness))
    monkeypatch.setattr(voice_service.shutil, "copy2", lambda *_args: pytest.fail("identical loaded file was replaced"))
    monkeypatch.setattr(voice_service.subprocess, "Popen", lambda *args, **kwargs: object())

    voice_service._ensure_bridge(voice_service.get_voice_settings(USER_ID), voice_service._user_paths(USER_ID)[1])


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
    voice_service.save_voice_settings(USER_ID, VoiceSettingsInput(ymm_path=str(fake_ymm)))
    voice_service.install_template(USER_ID, _template())

    def fake_synthesis(text, character, output, settings, job_id, _template_path):
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
        started = await voice_service.start_voice_job("user-1", "今日は晴れです。")
        await voice_service._jobs[started.id].task
        return voice_service.get_voice_job("user-1", started.id)

    result = asyncio.run(run_job())

    assert result.status == "complete"
    assert result.audio_url == f"/api/voice/jobs/{result.id}/audio"
    assert result.cached is False


def test_cancel_waits_for_worker_before_removing_job_directory(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.save_voice_settings(USER_ID, VoiceSettingsInput(ymm_path=str(fake_ymm)))
    voice_service.install_template(USER_ID, _template())
    entered = threading.Event()
    release = threading.Event()

    def slow_synthesis(_text, _character, output, _settings, _job_id, _template_path):
        entered.set()
        assert release.wait(timeout=3)
        output.write_bytes(b"unused")

    monkeypatch.setattr(voice_service, "_synthesize_with_bridge", slow_synthesis)
    monkeypatch.setattr(voice_service, "_cancel_bridge_job", lambda _job_id: True)
    monkeypatch.setattr(voice_service, "pronunciation_text", lambda value: value)

    async def run():
        started = await voice_service.start_voice_job("user-1", "今日は晴れです。", force=True)
        assert await asyncio.to_thread(entered.wait, 1)
        task = voice_service._jobs[started.id].task
        canceled = await voice_service.cancel_voice_job("user-1", started.id)
        assert canceled.status == "canceled"
        assert voice_service._jobs[started.id].finished_at is None
        assert (voice_service.JOBS_DIR / started.id).is_dir()
        assert task is not None and not task.done()
        release.set()
        await task
        return started.id

    job_id = asyncio.run(run())

    assert not (voice_service.JOBS_DIR / job_id).exists()
    assert voice_service.get_voice_job("user-1", job_id).status == "canceled"
    assert voice_service._jobs[job_id].finished_at is not None


def test_finished_voice_jobs_are_bounded(monkeypatch):
    voice_service._jobs.clear()
    monkeypatch.setattr(voice_service, "MAX_RETAINED_JOBS", 2)
    monkeypatch.setattr(voice_service, "JOB_TTL_SECONDS", 3600)
    for index in range(4):
        job = voice_service._VoiceJob(id=str(index), owner_user_id="user-1")
        voice_service._jobs[job.id] = job
        voice_service._finish_job(job, "complete", "done")
        job.finished_at = float(index + 1)

    voice_service._prune_jobs(now=4.0)

    assert set(voice_service._jobs) == {"2", "3"}


def test_voice_jobs_are_private_to_owner():
    voice_service._jobs.clear()
    job = voice_service._VoiceJob(id="private-job", owner_user_id="user-a")
    voice_service._finish_job(job, "complete", "done")
    voice_service._jobs[job.id] = job

    assert voice_service.get_voice_job("user-a", job.id).id == job.id
    with pytest.raises(KeyError):
        voice_service.get_voice_job("user-b", job.id)
