"""Fetch the exact Qt SDK from Qt's official architecture-specific repository.

Development only. End-user packages must contain their runtime dependencies.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
import hashlib
import json
import urllib.request
import xml.etree.ElementTree as ET
import py7zr

BASE = 'https://download.qt.io/online/qtsdkrepository/windows_x86/desktop/qt6_6112/qt6_6112_msvc2022_64/'
ROOT = Path('C:/ValMAXBuild')
OUT = ROOT / 'Qt'
DOWNLOADS = ROOT / 'downloads/qt'
PROOF = Path(__file__).resolve().parents[2] / 'analysis/native-migration-v1/qt-sdk.json'


def read(url, limit):
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('download_size_limit')
    return data


def acquire(item):
    name, version, archive = item
    url = BASE + name + '/' + version + archive
    expected = read(url + '.sha1', 1024).decode('ascii').split()[0]
    assert len(expected) == 40 and all(c in '0123456789abcdef' for c in expected)
    path = DOWNLOADS / (version + archive)
    if not path.exists():
        body = read(url, 512 * 1024 * 1024)
        assert hashlib.sha1(body).hexdigest() == expected, 'qt_archive_checksum'
        path.write_bytes(body)
    body = path.read_bytes()
    assert hashlib.sha1(body).hexdigest() == expected, 'qt_archive_checksum'
    print(json.dumps({'downloaded': archive, 'bytes': len(body)}), flush=True)
    return path, {'url': url, 'bytes': len(body), 'sha1': expected,
                  'sha256': hashlib.sha256(body).hexdigest()}


def main():
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    xml = read(BASE + 'Updates.xml', 4 * 1024 * 1024)
    (DOWNLOADS / 'Updates.xml').write_bytes(xml)
    packages = ET.fromstring(xml).findall('PackageUpdate')
    wanted = {'qtbase', 'qtdeclarative', 'qtshadertools', 'qttools'}
    tasks = []
    for package in packages:
        name = package.findtext('Name', '')
        if name not in {'qt.qt6.6112.win64_msvc2022_64', 'qt.qt6.6112.addons.qtshadertools.win64_msvc2022_64'}:
            continue
        version = package.findtext('Version', '')
        assert version.startswith('6.11.2-')
        for archive in package.findtext('DownloadableArchives', '').split(', '):
            if archive.split('-')[0] in wanted:
                tasks.append((name, version, archive))
    assert len(tasks) == 4
    with ThreadPoolExecutor(max_workers=2) as pool:
        obtained = list(pool.map(acquire, tasks))
    for path, record in obtained:
        with py7zr.SevenZipFile(path) as archive:
            for entry in archive.list():
                parts = PurePosixPath(entry.filename).parts
                assert parts and '..' not in parts and not PurePosixPath(entry.filename).is_absolute()
                assert ':' not in entry.filename and '\\' not in entry.filename
                assert not entry.is_symlink
            archive.extractall(OUT)
        print(json.dumps({'extracted': path.name}), flush=True)
    receipt = {'version': '6.11.2', 'architecture': 'msvc2022_64', 'root': str(OUT),
               'indexSha256': hashlib.sha256(xml).hexdigest(), 'archives': [r for _, r in obtained]}
    PROOF.parent.mkdir(parents=True, exist_ok=True)
    PROOF.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'sdkReady': True, 'root': str(OUT)}), flush=True)


if __name__ == '__main__':
    main()
