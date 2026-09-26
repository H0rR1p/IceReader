import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    override = os.environ.get("BINGDU_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return PROJECT_ROOT / "data"


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return root / name


DATA_DIR = _data_dir()
DIST_DIR = resource_path("dist")
