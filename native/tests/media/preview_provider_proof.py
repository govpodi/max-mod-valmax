"""Standalone memory-only preview provider using the actual MAX Qt DLLs."""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
BUILD = Path('C:/ValMAXBuild/build/media-preview')
SOURCE = Path('C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def require(value, message):
    if not value:
        raise RuntimeError(message)

def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    fixture = Path(tempfile.mkdtemp(prefix='media-preview-', dir='C:/ValMAXBuild/build'))
    # These immutable hardlinks are never overwritten. No MAX/core/ValMAX DLL,
    # browser executable or user data is copied into the standalone fixture.
    for file in SOURCE.glob('*.dll'):
        if file.name.lower().startswith(('qt6', 'msvcp', 'vcruntime', 'concrt', 'api-ms-', 'ucrt')):
            os.link(file, fixture / file.name)
    for directory in ('plugins', 'qml'):
        shutil.copytree(SOURCE / directory, fixture / directory, copy_function=os.link,
                        ignore=lambda path, names: [name for name in names if name == 'ValMAX'])
    shutil.copy2(SOURCE / 'qt.conf', fixture / 'qt.conf')
    require(sha(fixture / 'Qt6Core.dll') == '22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa', 'Qt runtime changed')
    exe = fixture / 'valmax_preview_provider_test.exe'
    shutil.copy2(BUILD / exe.name, exe)
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('QT_', 'QML', 'WEBVIEW2_', 'PYTHON', 'MAX_MOD', 'MXMOD', 'VALMAX_NATIVE'))}
    for directory in ('home', 'local', 'roaming', 'temp'):
        (fixture / directory).mkdir()
    env.update(PATH=str(fixture) + os.pathsep + os.path.join(os.environ['SystemRoot'], 'System32'),
               QT_QPA_PLATFORM='windows', QT_QUICK_BACKEND='software', QT_DEBUG_PLUGINS='1',
               QML_DISABLE_DISK_CACHE='1', QML_IMPORT_PATH=str(fixture / 'qml'), QML2_IMPORT_PATH=str(fixture / 'qml'),
               USERPROFILE=str(fixture / 'home'), LOCALAPPDATA=str(fixture / 'local'),
               APPDATA=str(fixture / 'roaming'), TEMP=str(fixture / 'temp'), TMP=str(fixture / 'temp'))
    run = subprocess.run([str(exe)], cwd=fixture, env=env, capture_output=True, text=True,
                         timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    (fixture / 'preview.stderr.log').write_text(run.stderr, encoding='utf-8')
    (fixture / 'preview.stdout.log').write_text(run.stdout, encoding='utf-8')
    require(run.returncode == 0, f'provider test failed: {run.returncode}\n{run.stdout}\n{run.stderr[-6000:]}')
    result = json.loads(run.stdout)
    require(result.get('passed') is True and result['qt'] == '6.11.2', 'missing native success')
    for name, path in result['loadedModules'].items():
        require(Path(path).resolve() == (fixture / name).resolve(), 'unexpected runtime path: ' + name)
        require(sha(Path(path)) == sha(SOURCE / name), 'loaded DLL changed: ' + name)
    sources = ['native/media/preview_provider.h', 'native/media/preview_provider.cpp', 'native/media/preview_provider.cmake',
               'native/tests/media/preview/CMakeLists.txt', 'native/tests/media/preview_provider_test.cpp',
               'native/tests/media/preview_provider_proof.py']
    proof = dict(schema=1, verified=True, fixture=str(fixture), native=result,
                 executableSha256=sha(exe), librarySha256=sha(BUILD / 'valmax_media_preview.lib'),
                 runtimeFiles={name: sha(fixture / name) for name in result['loadedModules']},
                 platformPluginSha256=sha(fixture / 'plugins/platforms/qwindows.dll'),
                 sources={name: sha(ROOT / name) for name in sources},
                 nativeProcessExited=True, installedClientModified=False, actualMaxStarted=False,
                 providerRegisteredInProduction=False, networkUsed=False,
                 limitations=[
                     'Actual QQmlEngine Image.Ready proves finish-before-connect delivery; no visible window or GPU render claim.',
                     'Image store is capped at 16 entries/8MiB; at most 8 image-bearing responses. Tiny error responses are owned by Qt, not a bounded native pool.',
                     'A texture already delivered to Qt cannot be revoked by this store. Future UI must clear source on scope changes and use cache:false.',
                     'Host supplies a unique per-engine 128-bit nonce. Epoch and serial never recycle within that store.',
                     'The provider only consumes already-decoded normalized sRGB pixels. Network, decoder scheduling, service wiring and production plugin registration are separate.',
                 ])
    output = ROOT / 'analysis/native-migration-v1/media-preview-provider-proof.json'
    output.write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(verified=True, checks=result['checks'], fixture=str(fixture), proof=str(output))))

if __name__ == '__main__':
    main()
