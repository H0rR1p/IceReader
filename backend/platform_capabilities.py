"""Runtime platform differences included in capability negotiation."""
import os


def platform_capabilities() -> dict:
    android = os.environ.get("BINGDU_ANDROID") == "1"
    online = bool(os.environ.get("BINGDU_PUBLIC_ORIGIN", "").strip())
    platform = "android" if android else "online" if online else "windows"
    return {"platform": platform,
            "tokenizer_backend": "sudachi-java-0.7.5" if android else "sudachipy",
            "voice": {"available": not android, "ymm4": not android},
            "identity": {"public": online, "guest": True, "local_profiles": not online}}
