"""Fail a release if notices/source files are missing or have drifted."""
import hashlib
from pathlib import Path
import json
import sys
from zipfile import ZipFile


def verify(folder: Path):
    for name in ("LICENSE", "COPYRIGHT", "NOTICE.md", "THIRD-PARTY-NOTICES.txt", "release.json", "corresponding-source.zip", "third_party_licenses/inventory.json"):
        if not (folder / name).is_file():
            raise ValueError(f"Missing legal release file: {name}")
    info = json.loads((folder / "release.json").read_text(encoding="utf-8"))
    archive_path = folder / "corresponding-source.zip"
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != info["source_sha256"]:
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
            content = archive.read("dependency-sources/" + row["file"])
            if hashlib.sha256(content).hexdigest() != row["sha256"]:
                raise ValueError(f"Dependency source checksum mismatch: {row['file']}")
        names = archive.namelist()
        if any("/.env" in name or "/data/" in name or "/YukkuriMovieMaker/" in name or name.endswith(".ymmp") for name in names):
            raise ValueError("Private or proprietary resources found in source archive")
        for required in ("IceReader/LICENSE", "IceReader/NOTICE.md", "IceReader/scripts/build_desktop.ps1", "IceReader/backend/requirements-release.txt"):
            if required not in names:
                raise ValueError(f"Source archive missing build material: {required}")
    inventory = json.loads((folder / "third_party_licenses" / "inventory.json").read_text(encoding="utf-8"))
    ebooklib = [item for item in inventory["components"] if item["name"].lower() == "ebooklib"]
    if not ebooklib or not any(item["version"] == "0.19" and item["licenses"] for item in ebooklib):
        raise ValueError("EbookLib 0.19 license inventory missing")
    for component in inventory["components"]:
        if not component["licenses"]:
            raise ValueError(f"No license text: {component['name']}")
        for license in component["licenses"]:
            content = (folder / license["path"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != license["sha256"]:
                raise ValueError(f"License checksum mismatch: {license['path']}")
    print(f"Legal verification passed: {len(manifest['files'])} own files, {len(inventory['components'])} components, matching source SHA-256")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
