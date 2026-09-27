import asyncio
import json

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


def test_template_install_and_sentence_replacement(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    fake_ymm = tmp_path / "YukkuriMovieMaker.exe"
    fake_ymm.write_bytes(b"exe")
    voice_service.save_voice_settings(VoiceSettingsInput(ymm_path=str(fake_ymm), playback_rate=125, volume=72))
    status = voice_service.install_template(_template())

    assert status.ready is True
    assert status.character_name == "琪露诺"
    project = voice_service.render_project("今日は晴れです。", 125, 72)
    item = project["Timelines"][0]["Items"][0]
    assert item["Serif"] == "今日は晴れです。"
    assert item["Hatsuon"] == "今日は晴れです。"
    assert item["VoiceCache"] == ""
    assert item["PlaybackRate2"]["Values"][0]["Value"] == 125.0
    assert item["Volume"]["Values"][0]["Value"] == 72.0


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
