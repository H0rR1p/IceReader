import json
import shutil
from pathlib import Path
from .models import LocalAiSettingsInput, LocalAiSettingsStatus
from .paths import DATA_DIR


SETTINGS_PATH = DATA_DIR / "settings.json"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


def _path_for_user(user_id: str) -> Path:
    return SETTINGS_PATH.parent / "users" / user_id / SETTINGS_PATH.name


def migrate_legacy_settings(user_id: str) -> None:
    target = _path_for_user(user_id)
    if not target.exists() and SETTINGS_PATH.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SETTINGS_PATH, target)


def _read(user_id: str) -> dict:
    try:
        data = json.loads(_path_for_user(user_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def get_settings_status(user_id: str) -> LocalAiSettingsStatus:
    data = _read(user_id)
    return LocalAiSettingsStatus(
        base_url=str(data.get("base_url") or DEFAULT_BASE_URL),
        model=str(data.get("model") or DEFAULT_MODEL),
        has_api_key=bool(data.get("api_key")),
        cache_hit_usd_per_million=float(data.get("cache_hit_usd_per_million") or 0),
        cache_miss_usd_per_million=float(data.get("cache_miss_usd_per_million") or 0),
        output_usd_per_million=float(data.get("output_usd_per_million") or 0),
    )


def save_settings(user_id: str, incoming: LocalAiSettingsInput) -> LocalAiSettingsStatus:
    current = _read(user_id)
    api_key = (incoming.api_key or "").strip() or str(current.get("api_key") or "")
    payload = {
        "api_key": api_key,
        "base_url": str(incoming.base_url).rstrip("/"),
        "model": incoming.model.strip(),
        "cache_hit_usd_per_million": incoming.cache_hit_usd_per_million,
        "cache_miss_usd_per_million": incoming.cache_miss_usd_per_million,
        "output_usd_per_million": incoming.output_usd_per_million,
    }
    target = _path_for_user(user_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return get_settings_status(user_id)


def resolve_settings(user_id: str, header_api_key: str | None, base_url: str, model: str) -> tuple[str, str, str]:
    stored = _read(user_id)
    api_key = (header_api_key or "").strip() or str(stored.get("api_key") or "")
    return (
        api_key,
        str(stored.get("base_url") or base_url).rstrip("/"),
        str(stored.get("model") or model),
    )


def get_pricing(user_id: str) -> dict[str, float]:
    data = _read(user_id)
    return {
        "cache_hit": float(data.get("cache_hit_usd_per_million") or 0),
        "cache_miss": float(data.get("cache_miss_usd_per_million") or 0),
        "output": float(data.get("output_usd_per_million") or 0),
    }
