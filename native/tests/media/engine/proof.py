"""Actual pinned Qt engine/provider lifecycle, synthetic pixels and no MAX."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import shutil
import uuid

ROOT = Path(__file__).resolve().parents[4]
BUILD = Path('C:/ValMAXBuild/build/media-engine')
QT = Path('C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb')
RUNNER = ROOT / 'native/tests/media/flow/proof.py'
spec = importlib.util.spec_from_file_location('owned_engine_job', RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
require = runner.require
sha = runner.sha


def main():
    media = ROOT / 'native/media'
    files = [media / name for name in ('engine_context.cpp', 'engine_context.h', 'engine_context.cmake',
             'preview_provider.cpp', 'preview_provider.h', 'preview_provider.cmake',
             'protocol.h', 'thumbnail.h', 'CMakeLists.txt')]
    files += [*Path(__file__).parent.glob('*.cpp'), Path(__file__), Path(__file__).with_name('CMakeLists.txt'),
              RUNNER, runner.JOB_SOURCE]
    exe = BUILD / 'valmax_media_engine_test.exe'
    require(exe.is_file() and exe.stat().st_mtime_ns >= max(path.stat().st_mtime_ns for path in files if path.suffix in ('.cpp', '.h')),
            'rebuild_required')
    before = {str(path.relative_to(ROOT)): sha(path) for path in files}
    fixture = Path('C:/ValMAXBuild/fixtures/media-engine') / uuid.uuid4().hex
    fixture.mkdir(parents=True)
    for source in QT.glob('*.dll'):
        if source.name.lower().startswith(('qt6', 'msvcp', 'vcruntime', 'concrt', 'api-ms-', 'ucrt')):
            os.link(source, fixture / source.name)
    for directory in ('plugins', 'qml'):
        shutil.copytree(QT / directory, fixture / directory, copy_function=os.link,
                        ignore=lambda path, names: [name for name in names if name == 'ValMAX'])
    shutil.copy2(QT / 'qt.conf', fixture / 'qt.conf')
    pins = {'Qt6Core.dll': '22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa',
            'Qt6Gui.dll': '19265a4e76efc12ef5f5bcf59be6c6a5b9b59ec4c89660797738bd2da7f719cc',
            'plugins/platforms/qwindows.dll': 'd94c584a18829d11fbf3aefd07e7b1e189e0cf063a0515826960037efa7465df'}
    for name, digest in pins.items():
        require(sha(fixture / name) == digest, 'pinned_qt_changed: ' + name)
    require(not any(path.name.lower() in ('max.exe', 'valmax_core.dll', 'core.dll', 'network.dll', 'notifications.dll')
                    for path in fixture.rglob('*')), 'unexpected_client_binary')
    shutil.copy2(exe, fixture / exe.name)
    for directory in ('home', 'local', 'roaming', 'temp'):
        (fixture / directory).mkdir()
    environment = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'SystemDrive', 'COMSPEC', 'PATHEXT') if key in os.environ}
    environment.update(PATH=str(fixture) + os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32'),
        QT_QPA_PLATFORM='windows', QT_QUICK_BACKEND='software', QT_DEBUG_PLUGINS='1', QML_DISABLE_DISK_CACHE='1',
        QML_IMPORT_PATH=str(fixture / 'qml'), QML2_IMPORT_PATH=str(fixture / 'qml'),
        USERPROFILE=str(fixture / 'home'), LOCALAPPDATA=str(fixture / 'local'), APPDATA=str(fixture / 'roaming'),
        TEMP=str(fixture / 'temp'), TMP=str(fixture / 'temp'))
    process = runner.run(fixture, environment, exe.name)
    stdout = (fixture / 'stdout.log').read_text(encoding='utf8')
    stderr = (fixture / 'stderr.log').read_text(encoding='utf8')
    require(process['failure'] is None and not process['timedOut'] and process['exitCode'] == 0,
            f'engine_failed: {process}\n{stdout}\n{stderr[-5000:]}')
    require(process['jobAssignedBeforeResume'] and process['jobProcessesAfterCleanup'] == 0, 'job_cleanup_failed')
    native = json.loads(stdout)
    require(native['passed'] and native['qt'] == '6.11.2', 'native_result_invalid')
    for name, path in native['loadedModules'].items():
        require(Path(path).resolve() == (fixture / name).resolve(), 'loaded_path_mismatch: ' + name)
        require(sha(Path(path)) == sha(QT / name), 'loaded_qt_mismatch: ' + name)
    require(before == {str(path.relative_to(ROOT)): sha(path) for path in files}, 'source_changed_during_proof')
    report = dict(schema=1, verified=True, sources=before, fixture=str(fixture), qtSourceRoot=str(QT),
        native=native, process=process, executableSha256=sha(exe),
        libraries={str(path.relative_to(BUILD)): sha(path) for path in (BUILD / 'media').glob('*.lib')},
        runtimeModules={name: sha(fixture / name) for name in native['loadedModules']}, platformPins=pins,
        stderrSha256=sha(fixture / 'stderr.log'), installedClientModified=False, productionPluginRegistered=False,
        networkUsed=False, mediaStaged=False, messagesSent=False,
        coverage=['12 real QQmlEngine create/publish/Image.Ready/clearSingletons/advance/destroy cycles',
            'clearSingletons actually destroys a disposable singleton while preserving media context, provider and pixels',
            'One active composer per engine; search/account/page change revokes token and pixel epoch immediately',
            'Native account/page IDs above JS safe-integer limit remain exact; no IDs are accepted from QML',
            'Worker checks shared immutable lease and atomic revocation without GUI QObject access',
            'Wrong-thread context methods reject; stale or other-engine capabilities cannot publish or detach',
            'Reentrant invalidated handler can bind another page or delete context without returning stale lease',
            'Provider collision/removal fails without replacement; separate engines have distinct opaque tokens'],
        limitations=['Engine context is not yet registered by the production plugin or connected to the MAX composer.',
            'Native service dispatch still must check ControllerStamp as well as the engine lease after network/decode/staging.',
            'The first integration permits only one active composer per engine, not several simultaneous panels.',
            'A handed Qt texture is not recalled; the test adapter explicitly clears Image.source on invalidation and uses cache:false.',
            'Synthetic in-memory pixels and software QtQuick: no visible/GPU rendering performance claim.',
            'Owned environment and Job bound the fixture and prevent child processes; not an OS file or network sandbox.'])
    output = ROOT / 'analysis/native-migration-v1/media-engine-context-proof.json'
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps(dict(verified=True, checks=native['checks'], proof=str(output), fixture=str(fixture))))


if __name__ == '__main__':
    main()
