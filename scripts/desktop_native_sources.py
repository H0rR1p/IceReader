"""Pinned corresponding sources for the official Windows Electron runtime.

Only source archives are downloaded; no SDK, runtime or toolchain is installed.
Changing Electron requires an explicit review of this manifest, including the
Chromium DEPS, patches, native configuration and official binary checksums.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tarfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "third_party_licenses/runtime/desktop-native/manifest.json"


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def prepare(fetch: bool) -> tuple[list[Path], dict]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    runtime = ROOT / "node_modules/electron/dist"
    if (runtime / "version").read_text().strip().removeprefix("v") != manifest["electron_version"]:
        raise RuntimeError("Electron changed; audit and update the native source manifest before releasing")
    for name, expected in manifest["runtime_hashes"].items():
        if file_hash(runtime / name) != expected:
            raise RuntimeError(f"Runtime does not match audited official Electron release: {name}")
    archives = []
    cache = ROOT / "build/legal-cache/desktop-native"
    for item in manifest["sources"]:
        path = cache / item["file"]
        if not path.is_file():
            if not fetch:
                raise RuntimeError(f"Missing native source: {item['file']}; run prepare_legal.py --fetch")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".download")
            try:
                with urlopen(Request(item["url"], headers={"User-Agent": "IceReader-source-builder/1"}), timeout=60) as response, temporary.open("wb") as handle:
                    while chunk := response.read(1024 * 1024):
                        handle.write(chunk)
                if file_hash(temporary) != item["sha256"]:
                    raise RuntimeError(f"Native source download checksum mismatch: {item['file']}")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        if file_hash(path) != item["sha256"]:
            raise RuntimeError(f"Native source cache checksum mismatch: {item['file']}")
        archives.append(path)
    # Verify actual source/config content, not just the existence of a URL.
    electron = next(path for path in archives if path.name.startswith("electron-"))
    ffmpeg = next(path for path in archives if path.name.startswith("ffmpeg-"))
    with tarfile.open(electron) as archive:
        prefix = f"electron-{manifest['electron_version']}/"
        deps = archive.extractfile(prefix + "DEPS").read().decode()
        if manifest["chromium_version"] not in deps:
            raise RuntimeError("Electron/Chromium source version mismatch")
        args = archive.extractfile(prefix + "build/args/release.gn").read().decode()
        if "is_component_ffmpeg = true" not in args:
            raise RuntimeError("FFmpeg shared-library configuration missing")
        archive.getmember(prefix + "patches/ffmpeg/link_with_loader_path.patch")
    with tarfile.open(ffmpeg) as archive:
        config = archive.extractfile("chromium/config/Chrome/win/x64/config.h").read().decode()
        if "#define CONFIG_GPL 0" not in config or "#define CONFIG_NONFREE 0" not in config:
            raise RuntimeError("Unexpected FFmpeg GPL/nonfree configuration")
    return archives, manifest


def verify_packaged_runtime(legal_folder: Path) -> None:
    """Check the shipped DLLs, not only the dependency cache used to build."""
    manifest = json.loads((legal_folder / "third_party_licenses/runtime/desktop-native/manifest.json").read_text(encoding="utf-8"))
    runtime = legal_folder.parent.parent
    # EXE resource edits change its hash; the build separately verifies the
    # application icon. The libraries themselves must remain byte-identical.
    for name, expected in manifest["runtime_hashes"].items():
        if name == "electron.exe":
            continue
        if file_hash(runtime / name) != expected:
            raise ValueError(f"Packaged runtime does not match corresponding source: {name}")
