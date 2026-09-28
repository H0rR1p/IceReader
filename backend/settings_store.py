import json
from .models import LocalAiSettingsInput, LocalAiSettingsStatus
from .paths import DATA_DIR


SETTINGS_PATH = DATA_DIR / "settings.json"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


def _read() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def get_settings_status() -> LocalAiSettingsStatus:
    data = _read()
    return LocalAiSettingsStatus(
        base_url=str(data.get("base_url") or DEFAULT_BASE_URL),
        model=str(data.get("model") or DEFAULT_MODEL),
        has_api_key=bool(data.get("api_key")),
        cache_hit_usd_per_million=float(data.get("cache_hit_usd_per_million") or 0),
        cache_miss_usd_per_million=float(data.get("cache_miss_usd_per_million") or 0),
        output_usd_per_million=float(data.get("output_usd_per_million") or 0),
    )


def save_settings(incoming: LocalAiSettingsInput) -> LocalAiSettingsStatus:
    current = _read()
    api_key = (incoming.api_key or "").strip() or str(current.get("api_key") or "")
    payload = {
        "api_key": api_key,
        "base_url": str(incoming.base_url).rstrip("/"),
        "model": incoming.model.strip(),
        "cache_hit_usd_per_million": incoming.cache_hit_usd_per_million,
        "cache_miss_usd_per_million": incoming.cache_miss_usd_per_million,
        "output_usd_per_million": incoming.output_usd_per_million,
    }
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(SETTINGS_PATH)
    return get_settings_status()


def resolve_settings(header_api_key: str | None, base_url: str, model: str) -> tuple[str, str, str]:
    stored = _read()
    api_key = (header_api_key or "").strip() or str(stored.get("api_key") or "")
    return (
        api_key,
        str(stored.get("base_url") or base_url).rstrip("/"),
        str(stored.get("model") or model),
    )


def get_pricing() -> dict[str, float]:
    data = _read()
    return {
        "cache_hit": float(data.get("cache_hit_usd_per_million") or 0),
        "cache_miss": float(data.get("cache_miss_usd_per_million") or 0),
        "output": float(data.get("output_usd_per_million") or 0),
    }
