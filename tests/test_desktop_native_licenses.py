"""Release gates must reject unaudited, missing and corrupted native materials."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest
from scripts import desktop_native_sources as native
from scripts.verify_legal_bundle import verify


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write_tar(path, entries):
    with tarfile.open(path, 'w:gz') as archive:
        for name, value in entries.items():
            value = value.encode()
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))


@pytest.fixture
def audited(tmp_path, monkeypatch):
    runtime = tmp_path / 'node_modules/electron/dist'
    runtime.mkdir(parents=True)
    (runtime / 'version').write_text('42.4.0')
    (runtime / 'ffmpeg.dll').write_bytes(b'official-library')
    cache = tmp_path / 'build/legal-cache/desktop-native'
    cache.mkdir(parents=True)
    write_tar(cache / 'electron-source.tar.gz', {
        'electron-42.4.0/DEPS': 'chromium_version = "148.0.7778.254"',
        'electron-42.4.0/build/args/release.gn': 'is_component_ffmpeg = true',
        'electron-42.4.0/patches/ffmpeg/link_with_loader_path.patch': 'patch',
    })
    write_tar(cache / 'ffmpeg-source.tar.gz', {
        'chromium/config/Chrome/win/x64/config.h': '#define CONFIG_GPL 0\n#define CONFIG_NONFREE 0',
    })
    manifest = {'electron_version': '42.4.0', 'chromium_version': '148.0.7778.254',
                'runtime_hashes': {'ffmpeg.dll': digest(b'official-library')},
                'sources': [{'file': p.name, 'sha256': native.file_hash(p)} for p in sorted(cache.iterdir())]}
    record = tmp_path / 'manifest.json'
    record.write_text(json.dumps(manifest))
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    monkeypatch.setattr(native, 'MANIFEST', record)
    return runtime, cache, record, manifest


def test_matching_native_source_preparation_is_offline(audited, monkeypatch):
    monkeypatch.setattr(native, 'urlopen', lambda *a, **k: pytest.fail('must not use network'))
    paths, manifest = native.prepare(False)
    assert len(paths) == 2 and manifest['electron_version'] == '42.4.0'


@pytest.mark.parametrize('failure', ['runtime-upgrade', 'runtime-corruption', 'missing-source', 'source-corruption', 'gpl-config'])
def test_native_release_fails_closed(audited, failure):
    runtime, cache, record, manifest = audited
    if failure == 'runtime-upgrade':
        (runtime / 'version').write_text('99.0.0')
    elif failure == 'runtime-corruption':
        (runtime / 'ffmpeg.dll').write_bytes(b'modified')
    elif failure == 'missing-source':
        (cache / 'ffmpeg-source.tar.gz').unlink()
    elif failure == 'source-corruption':
        (cache / 'ffmpeg-source.tar.gz').write_bytes(b'broken')
    elif failure == 'gpl-config':
        path = cache / 'ffmpeg-source.tar.gz'
        write_tar(path, {'chromium/config/Chrome/win/x64/config.h': '#define CONFIG_GPL 1\n#define CONFIG_NONFREE 0'})
        next(row for row in manifest['sources'] if row['file']==path.name)['sha256'] = native.file_hash(path)
        record.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError):
        native.prepare(False)


def test_desktop_bundle_cannot_disable_native_source_requirement(tmp_path):
    for name in ['LICENSE', 'COPYRIGHT', 'NOTICE.md', 'THIRD-PARTY-NOTICES.txt',
                 'corresponding-source.zip', 'third_party_licenses/inventory.json']:
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('placeholder')
    (tmp_path/'release.json').write_text(json.dumps({'desktop_native_sources': False}))
    with pytest.raises(ValueError, match='required'):
        verify(tmp_path, desktop=True)
