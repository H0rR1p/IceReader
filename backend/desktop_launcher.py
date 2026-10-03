"""Private desktop sidecar. Never opens a browser or reuses another service."""
import json
from contextlib import closing
import multiprocessing
import os
from pathlib import Path
import socket
import sqlite3
import shutil
import sys
import threading

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def migrate_data(destination: Path, source: Path | None) -> None:
    marker = destination / ".desktop-initialized"
    if marker.exists():
        return
    destination.mkdir(parents=True, exist_ok=True)
    # Never overwrite an existing desktop database with an older copy.
    if source and source.is_dir() and source.resolve() != destination.resolve():
        for item in source.iterdir():
            target = destination / item.name
            if item.name.endswith(("-wal", "-shm")):
                continue
            if item.is_dir():
                # An interrupted copy can leave an incomplete resource directory.
                for file in item.rglob("*"):
                    copied = target / file.relative_to(item)
                    if file.is_file() and not copied.exists():
                        copied.parent.mkdir(parents=True, exist_ok=True)
                        temporary = copied.with_name(copied.name + ".migration")
                        shutil.copy2(file, temporary)
                        temporary.replace(copied)
                continue
            if target.exists():
                continue
            if item.suffix == ".sqlite3":
                temporary = target.with_suffix(".migration")
                with closing(sqlite3.connect(f"{item.as_uri()}?mode=ro", uri=True)) as reader:
                    with closing(sqlite3.connect(temporary)) as writer:
                        reader.backup(writer)
                temporary.replace(target)
            elif item.is_file():
                shutil.copy2(item, target)
    marker.write_text(json.dumps({"source": str(source or ""), "version": 1}), encoding="utf-8")


def main() -> None:
    import uvicorn
    destination = Path(os.environ["BINGDU_DATA_DIR"])
    source = os.environ.get("BINGDU_LEGACY_DATA_DIR")
    migrate_data(destination, Path(source) if source else None)
    # Import only after migration; module paths and database stores are immutable.
    from backend.app import app
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_config=None))

    def watch_parent() -> None:
        # Pipe closure also stops the sidecar after a parent crash.
        if sys.stdin:
            sys.stdin.readline()
        server.should_exit = True

    threading.Thread(target=watch_parent, daemon=True).start()
    print(json.dumps({"port": listener.getsockname()[1]}), flush=True)
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
