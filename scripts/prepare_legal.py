"""Generate versioned license notices and a corresponding-source archive.

Run with the build Python environment and installed npm dependencies. Network
fetches are explicit (--fetch); archives are verified against PyPI/npm hashes.
Generated source artifacts are private to build/legal until distribution.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata as metadata
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import time
from zipfile import ZIP_DEFLATED, ZipFile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
LICENSE_NAME = re.compile(r"^(licen[cs]e|copying|notice|authors)([._-]|$)", re.I)
SOURCE_DIRS = {"backend", "src", "desktop", "scripts", "ymm4-bridge", "public", "assets"}
SOURCE_ROOTS = {"README.md", "LICENSE", "COPYRIGHT", "SOURCE-BUILD.txt", "THIRD-PARTY-NOTICES.txt", "package.json",
                "package-lock.json", "electron-builder.yml", "index.html", "start.ps1",
                "vite.config.ts", "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json",
                "Dockerfile.cloud", "compose.cloud.yml", "cloud.env.example", ".gitignore", ".dockerignore", ".gitattributes"}
EXCLUDED_PARTS = {"node_modules", "__pycache__", ".pytest_cache", "bin", "obj"}


def read_url(url: str) -> bytes:
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "IceReader-license-builder/1"}), timeout=40) as response:
                return response.read()
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise RuntimeError(f"Download failed: {url} ({error.code})") from error
        except (URLError, TimeoutError) as error:
            if attempt == 2:
                raise RuntimeError(f"Download failed: {url}") from error
        time.sleep(attempt + 1)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def requirements(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("-r "):
            yield from requirements(path.parent / line[3:].strip())
        elif line and not line.startswith("#"):
            yield Requirement(line)


def python_dependencies(cloud=False):
    selected, pending = {}, []
    for scope, file in (("runtime", "requirements-cloud.txt"), ("build", "requirements-build.txt")):
        if cloud and scope == "build":
            continue
        pending.extend((req, scope) for req in requirements(ROOT / "backend" / file))
    visited = set()
    while pending:
        req, scope = pending.pop()
        if req.marker and not req.marker.evaluate():
            continue
        name = canonicalize_name(req.name)
        key = (name, scope, tuple(sorted(req.extras)))
        if key in visited:
            continue
        visited.add(key)
        dist = metadata.distribution(req.name)
        if req.specifier and dist.version not in req.specifier:
            raise RuntimeError(f"Installed {name} {dist.version} does not satisfy {req}")
        entry = selected.setdefault(name, {"dist": dist, "scopes": set()})
        entry["scopes"].add(scope)
        for dependency in dist.requires or []:
            child = Requirement(dependency)
            if child.marker and not any(child.marker.evaluate({"extra": extra}) for extra in (req.extras or {""})):
                continue
            # Marker is already evaluated in the parent's extras environment.
            child.marker = None
            pending.append((child, scope))
    return selected


def cached_download(url: str, path: Path, expected: str, algorithm="sha256", fetch=False):
    if not path.is_file():
        if not fetch:
            raise RuntimeError(f"Source cache missing: {path.name}; run --fetch")
        content = read_url(url)
        if hashlib.new(algorithm, content).hexdigest() != expected:
            raise RuntimeError(f"Source checksum mismatch: {path.name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    if hashlib.new(algorithm, path.read_bytes()).hexdigest() != expected:
        raise RuntimeError(f"Cached source checksum mismatch: {path.name}")
    return path


def pypi_source(name: str, version: str, fetch: bool):
    cache = ROOT / "build" / "legal-cache" / "pypi"
    meta_path = cache / f"{name}-{version}.json"
    if not meta_path.is_file():
        if not fetch:
            raise RuntimeError(f"PyPI source metadata missing: {name} {version}; run --fetch")
        write_json(meta_path, json.loads(read_url(f"https://pypi.org/pypi/{quote(name)}/{quote(version)}/json")))
    info = json.loads(meta_path.read_text(encoding="utf-8"))
    archive = next((item for item in info["urls"] if item["packagetype"] == "sdist"), None)
    if not archive:
        raise RuntimeError(f"No upstream source distribution for {name} {version}")
    path = cached_download(archive["url"], cache / Path(archive["filename"]).name,
                           archive["digests"]["sha256"], fetch=fetch)
    return path, archive["url"]


def archive_licenses(path: Path):
    if path.suffix == ".zip":
        with ZipFile(path) as archive:
            return [(name, archive.read(name)) for name in archive.namelist()
                    if LICENSE_NAME.match(Path(name).name) and not name.endswith("/")]
    with tarfile.open(path, "r:*") as archive:
        return [(item.name, archive.extractfile(item).read()) for item in archive.getmembers()
                if item.isfile() and (LICENSE_NAME.match(Path(item.name).name) or
                    ("licenses" in Path(item.name).parts and Path(item.name).suffix.lower() in {".txt", ".md", ".rst"}))]


def pinned_github_license(repository, revision, cache, fetch):
    match = re.search(r"github.com[/:]([^/]+/[^/#]+)", repository or "")
    if not match or not revision:
        return []
    repo = match.group(1).removesuffix(".git")
    for filename in ("LICENSE", "LICENSE-MIT", "LICENSE-APACHE", "LICENSE.md", "LICENSE.txt", "License", "LICENCE", "license", "license.md", "license.txt", "COPYING"):
        local = cache / filename
        if local.is_file():
            return [(filename, local.read_bytes())]
        if fetch:
            try:
                content = read_url(f"https://raw.githubusercontent.com/{repo}/{revision}/{filename}")
            except Exception:
                continue
            cache.mkdir(parents=True, exist_ok=True)
            local.write_bytes(content)
            return [(filename, content)]
    return []


def native_rust_sources(archives, licenses_dir, fetch):
    """Retain locked Rust sources and notices from native Python wheels."""
    crates = {}
    for path in archives:
        if path.suffix == ".zip":
            continue
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                if member.isfile() and member.name.endswith("Cargo.lock"):
                    lock = tomllib.loads(archive.extractfile(member).read().decode())
                    for package in lock.get("package", []):
                        if package.get("source", "").startswith("registry+"):
                            crates[(package["name"], package["version"])] = package
    # SudachiPy's sdist contains the Rust source but omits the workspace lock.
    lock_path = licenses_dir / "supplemental" / "sudachipy-0.6.10" / "Cargo.lock"
    if not lock_path.is_file() and fetch:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_bytes(read_url("https://raw.githubusercontent.com/WorksApplications/sudachi.rs/v0.6.10/Cargo.lock"))
    if lock_path.is_file():
        for package in tomllib.loads(lock_path.read_text()).get("package", []):
            if package.get("source", "").startswith("registry+"):
                crates[(package["name"], package["version"])] = package
    elif not fetch:
        raise RuntimeError("SudachiPy workspace Cargo.lock missing; run --fetch")

    def collect(package):
        name, version = package["name"], package["version"]
        url = f"https://static.crates.io/crates/{name}/{name}-{version}.crate"
        archive = cached_download(url, ROOT / "build" / "legal-cache" / "crates" / f"{name}-{version}.crate",
                                  package["checksum"], fetch=fetch)
        documents = archive_licenses(archive)
        with tarfile.open(archive) as source:
            cargo = tomllib.loads(source.extractfile(f"{name}-{version}/Cargo.toml").read().decode())
            vcs = next((item for item in source.getmembers() if item.name.endswith(".cargo_vcs_info.json")), None)
            revision = json.loads(source.extractfile(vcs).read()).get("git", {}).get("sha1") if vcs else None
            if not documents:
                documents = pinned_github_license(cargo["package"].get("repository"), revision,
                           ROOT / "build" / "legal-cache" / "crate-notices" / f"{name}-{version}", fetch)
        if not documents:
            # Cargo.lock also includes benchmark/test and other-platform crates.
            # Preserve the actual author/license declaration when the upstream
            # archive and fixed Git revision supply no standalone full text.
            documents = [("UPSTREAM-DECLARATION.json", json.dumps({
                "name": name, "version": version, "package": cargo["package"],
                "note": "Locked native source superset includes test and platform-only crates. Upstream provides no standalone license text; declaration retained without inventing copyright."}, indent=2).encode())]
        row = {"ecosystem": "cargo", "name": name, "version": version,
               "license": cargo["package"].get("license", "See upstream text"),
               "scopes": ["native-source-and-platform-superset"], "source_url": url,
               "license_text_status": "upstream-declaration-only" if documents[0][0] == "UPSTREAM-DECLARATION.json" else "upstream-text",
               "licenses": save_licenses(licenses_dir / "cargo" / f"{name}-{version}", documents)}
        return row, archive

    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(collect, [crates[key] for key in sorted(crates)]))


def license_metadata(dist):
    explicit = dist.metadata.get("License-Expression") or dist.metadata.get("License")
    if explicit:
        return explicit
    return " / ".join(value.removeprefix("License :: ") for value in dist.metadata.get_all("Classifier", [])
                      if value.startswith("License :: ")) or "See upstream license text"


def npm_metadata(name: str, version: str, fetch: bool):
    cache = ROOT / "build" / "legal-cache" / "npm"
    meta_path = cache / (quote(name, safe="") + "-" + version + ".json")
    if not meta_path.is_file():
        if not fetch:
            raise RuntimeError(f"npm source metadata missing: {name} {version}; run --fetch")
        write_json(meta_path, json.loads(read_url(f"https://registry.npmjs.org/{quote(name, safe='')}/{version}")))
    return json.loads(meta_path.read_text(encoding="utf-8"))


def npm_source(info, fetch):
    name, version = info["name"], info["version"]
    cache = ROOT / "build" / "legal-cache" / "npm"
    dist = info["dist"]
    import base64
    integrity = dist.get("integrity", "")
    if integrity:
        algorithm, encoded = integrity.split("-", 1)
        expected = base64.b64decode(encoded).hex()
    else:
        algorithm, expected = "sha1", dist["shasum"]
    path = cached_download(dist["tarball"], cache / (quote(name, safe="") + "-" + version + ".tgz"),
                           expected, algorithm, fetch)
    return path, dist["tarball"]


def npm_missing_license(info, fetch):
    archive, _ = npm_source(info, fetch)
    licenses = archive_licenses(archive)
    if licenses:
        return licenses
    repository = info.get("repository", {})
    repository = repository.get("url", "") if isinstance(repository, dict) else repository
    match = re.search(r"github.com[/:]([^/]+/[^/#]+)", repository)
    revision = info.get("gitHead") or ("v" + info["version"])
    if match and revision:
        repo = match.group(1).removesuffix(".git")
        cache = ROOT / "build" / "legal-cache" / "notices" / (quote(info["name"], safe="") + "-" + info["version"])
        for filename in ("LICENSE", "LICENSE.md", "LICENSE.txt", "License", "LICENCE", "license", "license.md", "license.txt", "LICENSE-MIT", "COPYING"):
            local = cache / filename
            if local.is_file():
                return [(filename, local.read_bytes())]
            if fetch:
                try:
                    content = read_url(f"https://raw.githubusercontent.com/{repo}/{revision}/{filename}")
                except Exception:
                    continue
                cache.mkdir(parents=True, exist_ok=True)
                local.write_bytes(content)
                return [(filename, content)]
    # Some npm authors put the full grant in their README instead of LICENSE.
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            if member.isfile() and Path(member.name).name.lower().startswith("readme"):
                content = source.extractfile(member).read()
                if b"Permission is hereby granted" in content or b"Redistribution and use in source" in content:
                    return [("README-license.txt", content)]
    # Certain build-only upstreams publish only an SPDX declaration. Preserve
    # their actual declaration; do not invent an author's copyright notice.
    # Runtime packages never qualify for this fallback (checked by caller).
    return [("UPSTREAM-DECLARATION.json", json.dumps({"name": info["name"], "version": info["version"],
             "license": info.get("license"), "repository": info.get("repository"), "gitHead": info.get("gitHead"),
             "note": "Build-only upstream publishes no standalone license text at this version; declaration is not a substitute for a runtime license."}, indent=2).encode())]


def save_licenses(directory: Path, records):
    files = []
    for index, (original, content) in enumerate(records):
        # Names retain directory provenance; no tar/zip extraction is used.
        name = f"{index:02d}-" + re.sub(r"[^A-Za-z0-9_.-]", "_", original)
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files.append({"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(content)})
    return files


def npm_runtime_closure(lock):
    # React/Dexie/JSZip are runtime modules. Vite/plugin-react are build tools
    # even though plugin-react currently appears in package.json dependencies.
    pending = ["node_modules/" + name for name in ("react", "react-dom", "dexie", "jszip")]
    result = set()
    while pending:
        key = pending.pop()
        if key in result:
            continue
        result.add(key)
        item = lock["packages"][key]
        for name in item.get("dependencies", {}):
            parent = key
            while True:
                candidate = (parent + "/" if parent else "") + "node_modules/" + name
                if candidate in lock["packages"]:
                    break
                if "/node_modules/" in parent:
                    parent = parent.rsplit("/node_modules/", 1)[0]
                elif parent != "":
                    parent = ""
                else:
                    raise RuntimeError(f"Cannot resolve npm dependency {key}: {name}")
            pending.append(candidate.lstrip("/"))
    return result


def own_source_files():
    files = []
    for name in SOURCE_ROOTS:
        if (ROOT / name).is_file():
            files.append(ROOT / name)
    for name in SOURCE_DIRS:
        for path in (ROOT / name).rglob("*"):
            relative = path.relative_to(ROOT)
            if not path.is_file() or any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.suffix in {".pyc", ".log", ".ymmp"} or path.name.startswith(".env"):
                continue
            files.append(path)
    return sorted(files)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--cloud", action="store_true", help="Cloud API build, no npm or PyInstaller runtime")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "legal")
    args = parser.parse_args()
    licenses_dir = ROOT / "third_party_licenses"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows, source_archives = [], []
    python_packages = python_dependencies(args.cloud)

    def prepare_python(pair):
        name, selected = pair
        dist, scopes = selected["dist"], selected["scopes"]
        original = []
        for file in dist.files or []:
            # Only license documents, never a package called packaging.licenses.
            if LICENSE_NAME.match(Path(file).name) and Path(dist.locate_file(file)).is_file():
                original.append((str(file), Path(dist.locate_file(file)).read_bytes()))
        archive = None
        source_url = f"https://pypi.org/project/{dist.metadata['Name']}/{dist.version}/"
        if "runtime" in scopes or not original:
            archive, source_url = pypi_source(name, dist.version, args.fetch)
        if not original:
            original = archive_licenses(archive)
        elif archive:
            original += archive_licenses(archive)
        supplemental = licenses_dir / "supplemental" / f"{name}-{dist.version}"
        if not original and supplemental.is_dir():
            original = [(file.name, file.read_bytes()) for file in sorted(supplemental.iterdir())
                        if file.is_file() and LICENSE_NAME.match(file.name)]
        if not original:
            raise RuntimeError(f"Missing Python license: {name} {dist.version}")
        directory = licenses_dir / "python" / f"{name}-{dist.version}"
        files = save_licenses(directory, original)
        return {"ecosystem": "python", "name": dist.metadata["Name"], "version": dist.version,
                "license": license_metadata(dist), "scopes": sorted(scopes), "source_url": source_url,
                "licenses": files}, archive if "runtime" in scopes else None

    with ThreadPoolExecutor(max_workers=6) as pool:
        for row, archive in pool.map(prepare_python, sorted(python_packages.items())):
            rows.append(row)
            if archive:
                source_archives.append(archive)
            print(f"License: {row['name']} {row['version']}", flush=True)

    print("Collecting locked native Rust sources/notices", flush=True)
    for row, archive in native_rust_sources(source_archives, licenses_dir, args.fetch):
        rows.append(row)
        source_archives.append(archive)

    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    runtime = npm_runtime_closure(lock)
    skipped = []

    def prepare_npm(pair):
        key, info = pair
        package_dir = ROOT / key
        if not (package_dir / "package.json").is_file():
            return None, None
        installed = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
        name, version = installed["name"], installed["version"]
        if version != info["version"]:
            raise RuntimeError(f"npm installed/lock version mismatch: {key}")
        original = [(file.name, file.read_bytes()) for file in sorted(package_dir.iterdir())
                    if file.is_file() and LICENSE_NAME.match(file.name)]
        upstream = None
        archive = None
        if not original or key in runtime:
            upstream = npm_metadata(name, version, args.fetch)
        if not original:
            original = npm_missing_license(upstream, args.fetch)
        declaration_only = any(name == "UPSTREAM-DECLARATION.json" for name, _ in original)
        if declaration_only and key in runtime:
            raise RuntimeError(f"No complete runtime license text found: {name} {version}")
        if key in runtime:
            archive, source_url = npm_source(upstream, args.fetch)
        else:
            source_url = f"https://www.npmjs.com/package/{name}/v/{version}"
        directory = licenses_dir / "npm" / (quote(name, safe="") + "-" + version)
        return {"ecosystem": "npm", "name": name, "version": version, "license": info.get("license", "See upstream"),
                "chosen_license": "MIT" if name == "jszip" else info.get("license", "See upstream"),
                "scopes": ["runtime" if key in runtime or name == "electron" else "build"],
                "license_text_status": "upstream-declaration-only" if declaration_only else "upstream-text",
                "source_url": source_url, "lock_path": key, "licenses": save_licenses(directory, original)}, archive

    items = [] if args.cloud else sorted((key, value) for key, value in lock["packages"].items() if key)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (key, _), (row, archive) in zip(items, pool.map(prepare_npm, items)):
            if row:
                rows.append(row)
            else:
                skipped.append(key)
            if archive:
                source_archives.append(archive)

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        candidates = sorted((licenses_dir / "runtime" / "python").glob("*LICENSE.txt"))
        if not candidates:
            raise RuntimeError("Python runtime LICENSE.txt is missing")
        python_license = candidates[0]
    rows.append({"ecosystem": "runtime", "name": "Python", "version": sys.version.split()[0],
                 "license": "PSF-2.0 and bundled notices", "scopes": ["runtime"],
                 "source_url": "https://www.python.org/downloads/source/",
                 "licenses": save_licenses(licenses_dir / "runtime" / "python", [("LICENSE.txt", python_license.read_bytes())])})
    electron_notices = ROOT / "node_modules" / "electron" / "dist" / "LICENSES.chromium.html"
    if not args.cloud and not electron_notices.is_file():
        raise RuntimeError("Electron/Chromium runtime notices are missing")
    if not args.cloud:
        save_licenses(licenses_dir / "runtime" / "chromium", [("LICENSES.chromium.html", electron_notices.read_bytes())])
    # certifi's upstream license file is a short notice, not the complete MPL.
    mpl = licenses_dir / "texts" / "MPL-2.0.txt"
    if not mpl.is_file():
        raise RuntimeError("Add the official full MPL-2.0 text under third_party_licenses/texts/")

    inventory = {"project_license": "AGPL-3.0-or-later", "components": rows,
                 "uninstalled_optional_platform_packages": skipped,
                 "scope_note": "Includes installed build tools for transparency; this is not a count of bundled runtime modules."}
    write_json(licenses_dir / "inventory.json", inventory)
    project = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    notices = """冰读第三方组件与内容许可 / IceReader third-party notices
========================================================

IceReader application source: AGPL-3.0-or-later; see LICENSE and COPYRIGHT.
EbookLib 0.19 is retained under AGPL-3.0-or-later. Its upstream license,
authors and exact source distribution accompany this release.

Complete component/version inventory: third_party_licenses/inventory.json
Full upstream license and copyright texts: third_party_licenses/
Corresponding source: legal/corresponding-source.zip in the installation,
or the application's “源码与许可证” / “下载对应源码” controls.
The archive contains the exact application source, build scripts, lock files,
and original source archives of bundled Python and JavaScript dependencies.
JSZip is used under its MIT option. certifi retains MPL-2.0; its unmodified
source (including the certificate bundle) is supplied in the source archive.
PyInstaller uses GPL with a bootloader exception; preserve that exception.
Electron/Chromium notices remain LICENSE.electron.txt / LICENSES.chromium.html.
Build-only tools are listed for transparency, not as shipped app modules.

External software and content
-----------------------------
YMM4, its proprietary voice engines/packs and user books/dictionaries are
not licensed by IceReader's AGPL grant and are not bundled in the installer.
The independently developed YMM bridge source is part of IceReader; building
it requires the user's separately licensed YMM4 SDK/installation.
Its limited additional linking permission is stated in ymm4-bridge/COPYRIGHT;
it does not relicense YMM4, its SDK/engines, or any third-party code.
The project author has confirmed that the logo, icons and click audio are
original works created by H0rR1p, distributed with IceReader. All rights in
these assets are reserved unless separately licensed; the AGPL code license
does not grant rights to those assets or trademarks.
Icons are derived from public/bingdu-logo.png. No Aozora source/assets are used.
Architecture reference: https://github.com/meokisama/aozora (GPL-3.0).

Historical versions
-------------------
Earlier binaries already included EbookLib without a complete notice bundle.
The new notices do not retroactively change those binaries. Distributors
must make the matching historical source/build materials available for any
old versions they still distribute; a moving GitHub main link is insufficient.
Use this source-packaging workflow for every release, including modified ones.
"""
    (ROOT / "THIRD-PARTY-NOTICES.txt").write_text(notices, encoding="utf-8")
    shutil.copy2(ROOT / "LICENSE", output / "LICENSE")
    shutil.copy2(ROOT / "COPYRIGHT", output / "COPYRIGHT")
    shutil.copy2(ROOT / "THIRD-PARTY-NOTICES.txt", output / "THIRD-PARTY-NOTICES.txt")
    shutil.copytree(licenses_dir, output / "third_party_licenses", dirs_exist_ok=True)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "source-snapshot-in-container"
    files = own_source_files()
    source_manifest = {"version": project["version"], "base_commit": commit,
                       "includes_working_tree_changes": True, "project_license": "AGPL-3.0-or-later",
                       "files": [{"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path.read_bytes())} for path in files],
                       "dependency_sources": [{"file": path.name, "sha256": sha256(path.read_bytes())} for path in source_archives]}
    temporary = output / "corresponding-source.zip.tmp"
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, "IceReader/" + path.relative_to(ROOT).as_posix())
        for path in licenses_dir.rglob("*"):
            if path.is_file():
                archive.write(path, "IceReader/" + path.relative_to(ROOT).as_posix())
        for path in sorted(set(source_archives)):
            archive.write(path, "dependency-sources/" + path.name)
        archive.writestr("SOURCE-MANIFEST.json", json.dumps(source_manifest, ensure_ascii=False, indent=2))
        archive.writestr("IceReader/backend/requirements-release.txt", "\n".join(
            f"{row['name']}=={row['version']}" for row in rows if row["ecosystem"] == "python") + "\n")
    temporary.replace(output / "corresponding-source.zip")
    source_hash = sha256((output / "corresponding-source.zip").read_bytes())
    write_json(output / "release.json", {"version": project["version"], "base_commit": commit,
                                         "source_sha256": source_hash, "license": "AGPL-3.0-or-later"})
    print(f"Legal bundle: {len(rows)} components, {len(source_archives)} dependency source archives; {output}")


if __name__ == "__main__":
    main()
