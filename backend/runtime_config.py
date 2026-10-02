from __future__ import annotations

import os
from urllib.parse import urlsplit


PUBLIC_ORIGIN = os.environ.get("BINGDU_PUBLIC_ORIGIN", "").strip().rstrip("/")
PUBLIC_MODE = bool(PUBLIC_ORIGIN)

_parsed_origin = urlsplit(PUBLIC_ORIGIN) if PUBLIC_ORIGIN else None
PUBLIC_HOST = (_parsed_origin.hostname or "").casefold() if _parsed_origin else ""
COOKIE_SECURE = bool(_parsed_origin and _parsed_origin.scheme.casefold() == "https")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
ALLOWED_HOSTS = LOCAL_HOSTS | ({PUBLIC_HOST} if PUBLIC_HOST else set())
ALLOWED_ORIGIN_HOSTS = ALLOWED_HOSTS
CORS_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]
if PUBLIC_ORIGIN:
    CORS_ORIGINS.append(PUBLIC_ORIGIN)

