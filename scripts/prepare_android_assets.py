"""Install pinned Sudachi core data from a hash-verified official PyPI wheel."""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
VERSION = '20250515'

def main():
    cache = ROOT / 'build/android-assets'; cache.mkdir(parents=True, exist_ok=True)
    metadata_path = cache / 'sudachidict-core.json'
    if not metadata_path.is_file():
        with urllib.request.urlopen(f'https://pypi.org/pypi/sudachidict_core/{VERSION}/json') as response:
            metadata_path.write_bytes(response.read())
    metadata = json.loads(metadata_path.read_text())
    wheel = next(item for item in metadata['urls'] if item['filename'].endswith('.whl'))
    path = cache / wheel['filename']
    if not path.is_file():
        with urllib.request.urlopen(wheel['url'] + '?icereader_android=1') as response, path.open('wb') as output: shutil.copyfileobj(response, output)
    if hashlib.sha256(path.read_bytes()).hexdigest() != wheel['digests']['sha256']: raise RuntimeError('Dictionary wheel checksum mismatch')
    target = ROOT / 'android/app/src/main/assets/nlp/system_core.dic'; target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        entry = next(name for name in archive.namelist() if name.endswith('/resources/system.dic'))
        with archive.open(entry) as source, target.open('wb') as output: shutil.copyfileobj(source, output)
        license_dir = ROOT / f'third_party_licenses/android/sudachidict-core-{VERSION}'; license_dir.mkdir(parents=True, exist_ok=True)
        for name in archive.namelist():
            if Path(name).name.upper().startswith(('LICENSE', 'NOTICE', 'COPYING')):
                (license_dir / Path(name).name).write_bytes(archive.read(name))
    (target.parent / 'dictionary.json').write_text(json.dumps({'version': VERSION, 'wheel_sha256': wheel['digests']['sha256'],
        'dictionary_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}, indent=2) + '\n')
    print('Sudachi core', VERSION, target.stat().st_size, flush=True)

if __name__ == '__main__': main()
