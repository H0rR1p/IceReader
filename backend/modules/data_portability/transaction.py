"""One rollback journal transaction for all databases, with reversible file publication."""
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager, closing
from pathlib import Path


@contextmanager
def attached_transaction(paths: dict[str, Path]):
    # SQLite's super-journal provides crash atomicity for ATTACH only when every
    # database uses rollback journals (WAL has one independent commit per file).
    modes = {}
    connection = None
    try:
        for name, path in paths.items():
            if not path.is_file():
                raise ValueError(f"目标服务数据库尚未初始化：{name}")
            with closing(sqlite3.connect(path, timeout=15)) as setup:
                modes[name] = setup.execute("PRAGMA journal_mode").fetchone()[0]
                setup.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                try:
                    mode = setup.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                except sqlite3.OperationalError as exc:
                    raise ValueError("数据库正忙，请关闭其他数据库连接后重试迁移") from exc
                if mode.lower() != "delete":
                    raise ValueError("数据库正忙，请稍后重试迁移")
        first = next(iter(paths))
        connection = sqlite3.connect(paths[first], timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        aliases = {first: "main"}
        for name, path in paths.items():
            if name == first:
                continue
            connection.execute(f'ATTACH DATABASE ? AS "{name}"', (str(path),))
            connection.execute(f'PRAGMA "{name}".synchronous=FULL')
            aliases[name] = name
        connection.execute("BEGIN IMMEDIATE")
        yield connection, aliases
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()
        for name, mode in modes.items():
            if mode.lower() == "wal":
                with closing(sqlite3.connect(paths[name], timeout=15)) as setup:
                    setup.execute("PRAGMA journal_mode=WAL")


class StagedFiles:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="bingdu-import-")
        self.pending = []
        self.published = []

    def add(self, archive, name: str, target: Path):
        staged = Path(self.directory.name) / f"file-{len(self.pending)}"
        with archive.open(name) as source, staged.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        self.pending.append((staged, target))

    def publish(self):
        for staged, target in self.pending:
            target.parent.mkdir(parents=True, exist_ok=True)
            old = Path(self.directory.name) / f"old-{len(self.published)}"
            existed = target.exists()
            if existed:
                shutil.copy2(target, old)
            self.published.append((target, old if existed else None))
            shutil.copy2(staged, target)

    def finish(self, success: bool):
        if not success:
            for target, old in reversed(self.published):
                if old is None:
                    target.unlink(missing_ok=True)
                else:
                    shutil.copy2(old, target)
        self.directory.cleanup()
