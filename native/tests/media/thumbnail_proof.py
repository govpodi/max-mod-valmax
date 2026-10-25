"""Actual bundled-Qt thumbnail decoder, owned synthetic bytes only."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import pefile

ROOT = Path(__file__).resolve().parents[3]
BUILD = Path('C:/ValMAXBuild/build/media-thumbnail')
RUNTIME = Path('C:/ValMAXBuild/build/max-engine-probes/probe-2335f7ff38f4437d8a23497fc19f704d/runtime')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    fixture = Path('C:/ValMAXBuild/build/media-thumbnail-fixtures') / uuid.uuid4().hex
    fixture.mkdir(parents=True)
    roots = ['Qt6Core.dll', 'Qt6Gui.dll', 'plugins/imageformats/qjpeg.dll',
             'plugins/imageformats/qgif.dll', 'plugins/imageformats/qwebp.dll']
    pending = [RUNTIME / name for name in roots]
    hashes = {}
    while pending:
        src = pending.pop()
        name = src.relative_to(RUNTIME).as_posix()
        if name in hashes:
            continue
        assert src.is_file(), name
        target = fixture / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        hashes[name] = sha(target)
        with pefile.PE(str(src), fast_load=True) as pe:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],
                                                   pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT']])
            for entry in list(getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])) + list(getattr(pe, 'DIRECTORY_ENTRY_DELAY_IMPORT', [])):
                child = RUNTIME / entry.dll.decode('ascii')
                if child.is_file():
                    pending.append(child)
    assert hashes['Qt6Core.dll'] == '22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa'
    assert not any(Path(n).name.lower().startswith(('max', 'qt6network', 'qt6qml', 'python')) for n in hashes)
    exe = fixture / 'valmax_media_thumbnail_test.exe'
    shutil.copy2(BUILD / exe.name, exe)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('QT_', 'QML_', 'PYTHON'))}
    env['PATH'] = str(fixture) + ';C:/Windows/System32;C:/Windows'
    run = subprocess.run([str(exe)], cwd=fixture, env=env, capture_output=True, text=True,
                         timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if run.returncode:
        raise RuntimeError(f'thumbnail test failed {run.returncode}: {run.stdout}\n{run.stderr}')
    result = json.loads(run.stdout)
    assert result['passed'] and result['qt'] == '6.11.2'
    files = ['native/media/thumbnail.h', 'native/media/thumbnail.cpp', 'native/media/thumbnail.cmake',
             'native/media/protocol.h', 'native/media/protocol.cpp', 'native/media/CMakeLists.txt',
             'native/tests/media/thumbnail_test.cpp', 'native/tests/media/thumbnail_proof.py']
    proof = dict(schema=1, passed=True, native=result, fixture=str(fixture), runtimeFiles=hashes,
                 executableSha256=sha(exe), librarySha256=sha(BUILD / 'valmax_media_thumbnail.lib'),
                 sources={name: sha(ROOT / name) for name in files},
                 stderr=run.stderr, installedClientModified=False, networkUsed=False,
                 limitations=[
                     'Synchronous primitive tested on workers; QML provider, service scheduling and cancellation are separate.',
                     'First frame only. Input at most 2MiB, dimensions at most 4096, at most 4Mi pixels; output at most 512x512.',
                     'Header and pixel bounds are not a sandbox or a hard limit on third-party codec internal allocations.',
                     'QCoreApplication only; no window, accounts, paths from UI or platform plugin initialization.',
                 ])
    out = ROOT / 'analysis/native-migration-v1/media-thumbnail-proof.json'
    out.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'checks': result['checks'], 'proof': str(out), 'fixture': str(fixture)}))

if __name__ == '__main__':
    main()
