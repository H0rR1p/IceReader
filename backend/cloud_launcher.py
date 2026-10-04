"""Windows entry point for the standalone IceReader cloud service."""

from __future__ import annotations

import logging
import multiprocessing
import os
import sys

import uvicorn

if __package__ in {None, ""}:
    sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) )

from backend.cloud_app import app  # noqa: E402
from backend.paths import DATA_DIR  # noqa: E402


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=DATA_DIR / "cloud-service.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        encoding="utf-8",
    )
    uvicorn.run(
        app,
        host=os.environ.get("BINGDU_CLOUD_HOST", "127.0.0.1"),
        port=int(os.environ.get("BINGDU_CLOUD_PORT", "8010")),
        log_level="info",
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

