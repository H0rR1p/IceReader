import asyncio
import json
import struct

from . import voice_service
from .models import VoiceSettingsInput


def _template() -> bytes:
    return json.dumps({
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


def test_template_install_reads_character_and_settings(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.save_voice_settings(VoiceSettingsInput(ymm_path=str(fake_ymm), playback_rate=125, volume=72))
    status = voice_service.install_template(_template())

    assert status.ready is True
    assert status.character_name == "琪露诺"
    assert status.playback_rate == 125
    assert status.volume == 72


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

    def fake_synthesis(text, character, output, settings):
        assert text == "きょうははれです。"
        assert settings.playback_rate == 90
        assert character == "琪露诺"
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
