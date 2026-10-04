"""Build the unmodified official SQLite amalgamation for Android, with FTS5."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
VERSION = '3460100'
URL = f'https://www.sqlite.org/2024/sqlite-amalgamation-{VERSION}.zip'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ndk', type=Path, default=os.environ.get('ANDROID_NDK_HOME'))
    args = parser.parse_args()
    if not args.ndk: parser.error('Set ANDROID_NDK_HOME or pass --ndk (NDK r27d or newer).')
    cache = ROOT / 'build/android-native'; cache.mkdir(parents=True, exist_ok=True)
    archive = cache / 'sqlite-amalgamation.zip'
    if not archive.is_file():
        with urllib.request.urlopen(URL) as source, archive.open('wb') as target:
            import shutil
            shutil.copyfileobj(source, target)
    expected = json.loads((ROOT / 'mobile/sqlite-source.json').read_text())['sha256']
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected: raise RuntimeError('SQLite source checksum mismatch')
    with zipfile.ZipFile(archive) as source: source.extractall(cache)
    code = cache / f'sqlite-amalgamation-{VERSION}/sqlite3.c'
    toolchain = args.ndk / 'toolchains/llvm/prebuilt/windows-x86_64/bin'
    for abi, target in [('arm64-v8a', 'aarch64-linux-android29'), ('x86_64', 'x86_64-linux-android29')]:
        destination = ROOT / f'android/app/src/main/jniLibs/{abi}/libbingdusqlite.so'
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(toolchain / 'clang.exe'), f'--target={target}', '-O2', '-fPIC', '-shared',
            '-DSQLITE_ENABLE_FTS5', '-DSQLITE_ENABLE_MATH_FUNCTIONS', '-DSQLITE_THREADSAFE=1',
            '-DSQLITE_OMIT_LOAD_EXTENSION', '-Wl,-z,max-page-size=16384', str(code), '-lm', '-o', str(destination)], check=True)
        print(abi, hashlib.sha256(destination.read_bytes()).hexdigest(), flush=True)

if __name__ == '__main__': main()
