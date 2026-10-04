"""Collect exact Maven POMs, original source JARs and license declarations."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.error
import xml.etree.ElementTree as ET
import zipfile

from prepare_legal import ROOT, read_url, save_licenses, LICENSE_NAME

NS = {'m': 'http://maven.apache.org/POM/4.0.0'}
CACHE = ROOT / 'build/legal-cache/android'
DESTINATION = ROOT / 'third_party_licenses/android'

def cached(url, path):
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(read_url(url))
    return path.read_bytes()

def component(row):
    group, name, version = row['group'], row['name'], row['version']
    base = 'https://dl.google.com/dl/android/maven2/' if group.startswith(('androidx.', 'com.android.')) else 'https://repo.maven.apache.org/maven2/'
    relative = f'{group.replace(".", "/")}/{name}/{version}/{name}-{version}'
    directory = CACHE / f'{group}-{name}-{version}'
    pom = directory / 'pom.xml'; content = cached(base + relative + '.pom', pom)
    metadata = ET.fromstring(content)
    licenses = [{'name': value.findtext('m:name', '', NS), 'url': value.findtext('m:url', '', NS)} for value in metadata.findall('m:licenses/m:license', NS)]
    if not licenses:
        parent = metadata.find('m:parent', NS)
        if parent is not None:
            parent_row = {key: parent.findtext('m:' + field, '', NS) for key, field in [('group', 'groupId'), ('name', 'artifactId'), ('version', 'version')]}
            parent_relative = f"{parent_row['group'].replace('.', '/')}/{parent_row['name']}/{parent_row['version']}/{parent_row['name']}-{parent_row['version']}.pom"
            parent_xml = ET.fromstring(cached(base + parent_relative, directory / 'parent.xml'))
            licenses = [{'name': value.findtext('m:name', '', NS), 'url': value.findtext('m:url', '', NS)} for value in parent_xml.findall('m:licenses/m:license', NS)]
    if not licenses: raise RuntimeError(f'Maven license declaration missing: {group}:{name}:{version}')
    documents = [('UPSTREAM-POM.xml', content)]
    source = directory / 'sources.jar'
    try:
        cached(base + relative + '-sources.jar', source)
    except RuntimeError as error:
        if '(404)' not in str(error): raise
        if not (name.endswith('-bom') or name.endswith('-ktx') or '-android' not in name and name == 'kotlin-stdlib-common'):
            print('No source JAR (POM retained):', group, name, version, flush=True)
    if source.is_file():
        with zipfile.ZipFile(source) as archive:
            documents += [(Path(entry).name, archive.read(entry)) for entry in archive.namelist() if LICENSE_NAME.match(Path(entry).name) and not entry.endswith('/')]
    if any('apache' in item['name'].lower() for item in licenses):
        documents.append(('Apache-2.0.txt', cached('https://www.apache.org/licenses/LICENSE-2.0.txt', CACHE / 'Apache-2.0.txt')))
    for item in licenses:
        if item['url'].startswith('https://github.com/') and '/blob/' in item['url']:
            raw = item['url'].replace('https://github.com/', 'https://raw.githubusercontent.com/').replace('/blob/', '/')
            documents.append(('UPSTREAM-LICENSE.txt', cached(raw, directory / 'UPSTREAM-LICENSE.txt')))
    if group == 'org.glassfish' and name == 'javax.json':
        documents.append(('LICENSE.txt', cached('https://oss.oracle.com/licenses/CDDL+GPL-1.1', directory / 'LICENSE.txt')))
        with zipfile.ZipFile(source) as archive:
            entry = next(entry for entry in archive.namelist() if entry.endswith('.java'))
            header = archive.read(entry).decode().split('*/', 1)[0] + '*/\n'
        documents.append(('COPYRIGHT.txt', header.encode()))
        row = {**row, 'selected_license': 'GPL-2.0-only WITH Classpath-exception-2.0'}
    target = DESTINATION / f'{group}-{name}-{version}'
    files = save_licenses(target, documents)
    return {**row, 'licenses': licenses, 'license_files': files,
        'source_url': base + relative + '-sources.jar', 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None,
        'source_archive': str(source.relative_to(ROOT)) if source.is_file() else None}

def main():
    rows = json.loads((ROOT / 'build/android-java-dependencies.json').read_text())
    with ThreadPoolExecutor(max_workers=8) as executor: inventory = list(executor.map(component, rows))
    for name, url in [('Chaquopy-MIT.txt', 'https://raw.githubusercontent.com/chaquo/chaquopy/17.0.0/LICENSE.txt'),
                      ('SQLite-Public-Domain.txt', 'https://www.sqlite.org/copyright.html')]:
        content = cached(url, CACHE / name)
        (DESTINATION / name).write_bytes(content)
    (DESTINATION / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Android dependency inventory:', len(inventory), flush=True)

if __name__ == '__main__': main()
