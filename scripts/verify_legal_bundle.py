"""Fail a release if notices/source files are missing or have drifted."""
import hashlib
from pathlib import Path
import json
import sys
from zipfile import ZipFile
try:
    from desktop_native_sources import file_hash, verify_packaged_runtime
except ModuleNotFoundError:
    from scripts.desktop_native_sources import file_hash, verify_packaged_runtime


def verify(folder: Path, *, desktop: bool = False):
    for name in ("LICENSE", "COPYRIGHT", "NOTICE.md", "THIRD-PARTY-NOTICES.txt", "release.json", "corresponding-source.zip", "third_party_licenses/inventory.json"):
        if not (folder / name).is_file():
            raise ValueError(f"Missing legal release file: {name}")
    info = json.loads((folder / "release.json").read_text(encoding="utf-8"))
    desktop = desktop or (folder.parent.parent / "ffmpeg.dll").is_file()
    if desktop and not info.get("desktop_native_sources"):
        raise ValueError("Desktop runtime source materials are required")
    archive_path = folder / "corresponding-source.zip"
    if file_hash(archive_path) != info["source_sha256"]:
        raise ValueError("Corresponding-source archive checksum mismatch")
    with ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("SOURCE-MANIFEST.json"))
        if manifest["version"] != info["version"] or manifest["project_license"] != "AGPL-3.0-or-later":
            raise ValueError("Source/build version or license mismatch")
        for row in manifest["files"]:
            content = archive.read("IceReader/" + row["path"])
            if hashlib.sha256(content).hexdigest() != row["sha256"]:
                raise ValueError(f"Source checksum mismatch: {row['path']}")
        for row in manifest["dependency_sources"]:
            with archive.open(row.get("archive_path", "dependency-sources/" + row["file"])) as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != row["sha256"]:
                raise ValueError(f"Dependency source checksum mismatch: {row['file']}")
        native = manifest.get("desktop_native_sources")
        if info.get("desktop_native_sources") and not native:
            raise ValueError("Desktop native corresponding-source manifest missing")
        if native:
            supplied = {row["file"]: row["sha256"] for row in manifest["dependency_sources"]}
            for row in native["sources"]:
                if supplied.get(row["file"]) != row["sha256"]:
                    raise ValueError(f"Missing matching native source: {row['file']}")
            packaged_native = json.loads((folder / "third_party_licenses/runtime/desktop-native/manifest.json").read_text(encoding="utf-8"))
            if packaged_native != native:
                raise ValueError("Packaged/source native manifest mismatch")
        names = archive.namelist()
        private_names = {"日语语法切分与上下文翻译实施方案.md", "日语语法切分与上下文翻译优化方案.md",
                         "冰读自适应学习与账号系统升级方案.md", "切分、翻译优化.txt",
                         "platform-integration-report.md", "grammar-development-progress.md"}
        if any(Path(name).name in private_names for name in names):
            raise ValueError("Private research or progress documents found in source archive")
        if any("/.env" in name or "/data/" in name or "/YukkuriMovieMaker/" in name or name.endswith(".ymmp") for name in names):
            raise ValueError("Private or proprietary resources found in source archive")
        for required in ("IceReader/LICENSE", "IceReader/NOTICE.md", "IceReader/scripts/build_desktop.ps1", "IceReader/backend/requirements-release.txt",
                         "IceReader/resources/grammar/manifest.json", "IceReader/resources/grammar/morphology.json",
                         "IceReader/resources/grammar/constructions.json", "IceReader/resources/grammar/idioms.json"):
            if required not in names:
                raise ValueError(f"Source archive missing build material: {required}")
    inventory = json.loads((folder / "third_party_licenses" / "inventory.json").read_text(encoding="utf-8"))
    ebooklib = [item for item in inventory["components"] if item["name"].lower() == "ebooklib"]
    if not ebooklib or not any(item["version"] == "0.19" and item["licenses"] for item in ebooklib):
        raise ValueError("EbookLib 0.19 license inventory missing")
    for component in inventory["components"]:
        license_files = component.get("license_files", component.get("licenses", []))
        if not license_files:
            raise ValueError(f"No license text: {component['name']}")
        for license in license_files:
            content = (folder / license["path"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != license["sha256"]:
                raise ValueError(f"License checksum mismatch: {license['path']}")
    if info.get("desktop_native_sources") and (folder.parent.parent / "ffmpeg.dll").is_file():
        verify_packaged_runtime(folder)
    print(f"Legal verification passed: {len(manifest['files'])} own files, {len(inventory['components'])} components, matching source SHA-256")


if __name__ == "__main__":
    verify(Path(sys.argv[1]), desktop="--desktop" in sys.argv[2:])
