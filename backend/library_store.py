import json
from pathlib import Path

from .models import LibrarySnapshot


LIBRARY_PATH = Path(__file__).resolve().parent.parent / "data" / "library.json"


def load_library() -> LibrarySnapshot | None:
    try:
        return LibrarySnapshot.model_validate_json(LIBRARY_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None


def save_library(snapshot: LibrarySnapshot) -> LibrarySnapshot:
    LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LIBRARY_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(snapshot.model_dump(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(LIBRARY_PATH)
    return snapshot
