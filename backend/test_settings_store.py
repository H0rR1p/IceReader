from .models import LocalAiSettingsInput
from . import settings_store


def test_settings_are_saved_in_project_data_without_exposing_key(tmp_path, monkeypatch):
    path = tmp_path / "data" / "settings.json"
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", path)

    status = settings_store.save_settings(LocalAiSettingsInput(
        api_key="sk-local-test",
        base_url="https://api.deepseek.com/",
        model="deepseek-chat",
    ))

    assert status.has_api_key is True
    assert status.base_url == "https://api.deepseek.com"
    assert "sk-local-test" in path.read_text(encoding="utf-8")
    assert "api_key" not in status.model_dump()

    api_key, base_url, model = settings_store.resolve_settings(None, "https://unused.example", "unused")
    assert (api_key, base_url, model) == ("sk-local-test", "https://api.deepseek.com", "deepseek-chat")
