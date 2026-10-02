from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import httpx

from .dictionary_store import dictionary_sources, import_yomitan_path
from .paths import DATA_DIR


PACKAGE = {
    "package_id": "greyindex/jitendex-yomitan-zh",
    "title": "Jitendex 日中简体词典",
    "version": "v2026.08.11-zh.4",
    "license": "CC BY-SA 4.0",
    "homepage": "https://github.com/greyindex/jitendex-yomitan-zh",
    "catalog": "https://github.com/MarvNC/yomitan-dictionaries",
    "url": "https://github.com/greyindex/jitendex-yomitan-zh/releases/download/v2026.08.11-zh.4/jitendex-yomitan-zh-full-review-dedup-20260927.zip",
    "sha256": "9872fc32d05e3f7ea9b5b70a253610e49d99b6eef7db519b96faad1bbec2b678",
}
PACKAGE_DIR = DATA_DIR / "dictionaries"
PACKAGE_PATH = PACKAGE_DIR / "jitendex-yomitan-zh-v2026.08.11-zh.4.zip"
_lock = threading.Lock()
_job = {"status": "idle", "message": "", "downloaded": 0, "total": 0}


def _installed_source() -> dict | None:
    return next((item for item in dictionary_sources() if item.get("package_id") == PACKAGE["package_id"]), None)


def status() -> dict:
    with _lock:
        job = dict(_job)
    installed = _installed_source()
    return {"package": PACKAGE, "installed": installed, "job": job,
            "update_available": bool(installed and installed.get("version") != PACKAGE["version"])}


def install() -> dict:
    with _lock:
        if _job["status"] in {"downloading", "importing"}:
            return status()
        _job.update(status="downloading", message="正在下载内置词典", downloaded=0, total=0)
    try:
        PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
        valid_cached = PACKAGE_PATH.exists() and _sha256(PACKAGE_PATH) == PACKAGE["sha256"]
        if not valid_cached:
            temporary = PACKAGE_PATH.with_suffix(".zip.part")
            temporary.unlink(missing_ok=True)
            with httpx.stream("GET", PACKAGE["url"], follow_redirects=True, timeout=httpx.Timeout(180.0, connect=20.0)) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or 0)
                with _lock:
                    _job["total"] = total
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        output.write(chunk)
                        with _lock:
                            _job["downloaded"] += len(chunk)
            if _sha256(temporary) != PACKAGE["sha256"]:
                temporary.unlink(missing_ok=True)
                raise ValueError("词典校验失败，下载文件与固定版本不一致")
            temporary.replace(PACKAGE_PATH)
        with _lock:
            _job.update(status="importing", message="正在建立词典索引")
        result = import_yomitan_path(PACKAGE_PATH, {
            "source": PACKAGE["title"], "package_id": PACKAGE["package_id"],
            "version": PACKAGE["version"], "license": PACKAGE["license"],
            "homepage": PACKAGE["homepage"],
        })
        with _lock:
            _job.update(status="complete", message=f"已安装 {result['entries']:,} 个词条")
        return status()
    except Exception as exc:
        with _lock:
            _job.update(status="failed", message=str(exc))
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

