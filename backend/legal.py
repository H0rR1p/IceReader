"""Public, version-specific source and license downloads (AGPL section 13)."""
import json
import os
from pathlib import Path
import sys

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from .paths import PROJECT_ROOT

router = APIRouter(prefix="/api/legal", tags=["source and licenses"])


def legal_directory() -> Path:
    configured = os.environ.get("BINGDU_LEGAL_DIR")
    if configured:
        return Path(configured).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "legal"
    return PROJECT_ROOT / "build" / "legal"


def _file(name: str) -> Path:
    path = legal_directory() / name
    if not path.is_file():
        raise HTTPException(503, "当前构建尚未生成许可与对应源码包，请运行 scripts/prepare_legal.py")
    return path


@router.get("")
def release_info() -> dict:
    result = json.loads(_file("release.json").read_text(encoding="utf-8"))
    result.update(source_url="/api/legal/source", license_url="/api/legal/license", notices_url="/api/legal/notices")
    return result


@router.get("/source")
def corresponding_source() -> FileResponse:
    return FileResponse(_file("corresponding-source.zip"), media_type="application/zip",
                        filename="IceReader-corresponding-source.zip", headers={"Cache-Control": "no-cache"})


@router.get("/license")
def license_text() -> FileResponse:
    return FileResponse(_file("LICENSE"), media_type="text/plain; charset=utf-8")


@router.get("/notices")
def notices_text() -> FileResponse:
    return FileResponse(_file("THIRD-PARTY-NOTICES.txt"), media_type="text/plain; charset=utf-8")


def source_page() -> HTMLResponse:
    return HTMLResponse('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <title>冰读云端服务 · 源码与许可证</title><h1>冰读云端服务</h1>
    <p>本程序采用 GNU AGPL-3.0-or-later，按现状提供，不提供任何担保。</p>
    <p><a href="/api/legal/source">免费下载当前运行版本的对应源码</a></p>
    <p><a href="/api/legal/license">许可证全文</a> ·
    <a href="/api/legal/notices">第三方声明</a> ·
    <a href="/api/legal">版本和源码 SHA-256</a></p></html>''')
