"""Run the bridge lifecycle tests with exact MAX QtCore, without MAX or a GUI."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pefile

ROOT = Path('D:/ValMAX')
RUNTIME = Path('C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb')
BUILD = Path('C:/ValMAXBuild/build/qt-bridge-lifetime')
EXPECTED_CORE = '22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if sha(RUNTIME / 'Qt6Core.dll') != EXPECTED_CORE:
        raise RuntimeError('The pinned MAX QtCore changed')
    fixture = Path(tempfile.mkdtemp(prefix='qt-bridge-lifetime-', dir='C:/ValMAXBuild/build'))
    dependencies: dict[str, str] = {}
    pending = [RUNTIME / 'Qt6Core.dll']
    while pending:
        source = pending.pop()
        if source.name in dependencies:
            continue
        target = fixture / source.name
        shutil.copy2(source, target)
        dependencies[source.name] = sha(target)
        with pefile.PE(str(source), fast_load=True) as pe:
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],
                pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT']])
            for item in list(getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])) + list(getattr(pe, 'DIRECTORY_ENTRY_DELAY_IMPORT', [])):
                dependency = RUNTIME / item.dll.decode('ascii')
                if dependency.is_file():
                    pending.append(dependency)
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(('QT_', 'QML', 'PYTHON', 'VALMAX_', 'MXMOD', 'ONEME'))}
    env['PATH'] = str(fixture) + ';C:/Windows/System32;C:/Windows'
    previous_mode = ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    results = {}
    try:
        for name in ('valmax_protection_delivery_test.exe', 'valmax_privacy_delivery_test.exe'):
            exe = fixture / name
            shutil.copy2(BUILD / name, exe)
            run = subprocess.run([str(exe)], cwd=fixture, env=env, capture_output=True,
                                 text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            (fixture / (name + '.stdout.txt')).write_text(run.stdout, encoding='utf-8')
            (fixture / (name + '.stderr.txt')).write_text(run.stderr, encoding='utf-8')
            if run.returncode:
                raise RuntimeError(f'{name}: {run.returncode}: {run.stdout}\n{run.stderr}')
            result = json.loads(run.stdout)
            if result.get('ok') is not True:
                raise RuntimeError(f'{name}: missing success result')
            results[name] = {'result': result, 'sha256': sha(exe), 'exitCode': run.returncode,
                             'stderr': run.stderr}
    finally:
        ctypes.windll.kernel32.SetErrorMode(previous_mode)
    sources = ['native/qt/native_bridge.cpp', 'native/qt/native_bridge.h', 'native/qt/CMakeLists.txt',
               'native/tests/qt/protection_delivery_test.cpp', 'native/tests/qt/privacy_delivery_test.cpp',
               'native/tests/qt/bridge_lifetime_proof.py']
    proof = {'schema': 1, 'passed': True, 'checks': sum(v['result']['checks'] for v in results.values()),
             'fixture': str(fixture), 'runtimeFiles': dependencies, 'tests': results,
             'sourceHashes': {p: sha(ROOT / p) for p in sources},
             'installedClientModified': False, 'maxStarted': False, 'realProfilesUsed': False,
             'limitations': ['QCoreApplication only; no platform plugin, window or browser is created.',
                             'Rust ABI and protection hosts are mocked to exercise bridge scheduling and destruction.',
                             'This is not a production plugin, installer or early-bootstrap validation.']}
    output = ROOT / 'analysis/native-migration-v1/qt-bridge-lifetime-proof.json'
    output.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'checks': proof['checks'], 'proof': str(output), 'fixture': str(fixture)}))


if __name__ == '__main__':
    main()
