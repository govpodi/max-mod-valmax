"""Own hidden Qt flow fixture: fake HTTP, genuine PNG decode, actual QML Image."""
from __future__ import annotations
import ctypes as c
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import time
import uuid

ROOT = Path(__file__).resolve().parents[4]
BUILD = Path('C:/ValMAXBuild/build/media-flow')
QT = Path('C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb')
JOB_SOURCE = ROOT / 'native/tests/qt/run_webview_blank_native.py'
# Definitions only. This does not invoke its test, a browser, MAX, or a profile.
spec = importlib.util.spec_from_file_location('owned_media_flow_job', JOB_SOURCE)
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)
w = job.w


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(fixture, environment, executable_name='valmax_media_flow_test.exe'):
    block = c.create_unicode_buffer('\0'.join(f'{k}={v}' for k, v in sorted(environment.items(), key=lambda item: item[0].upper())) + '\0\0')
    exe = fixture / executable_name
    handle = None
    process = job.PROCESS()
    outputs = []
    assigned = False
    remaining = None
    timed_out = False
    failure = None
    code = w.DWORD(259)
    error_mode = job.bind('SetErrorMode', w.UINT, [w.UINT])
    old_mode = error_mode(0x8003)
    start = time.monotonic()
    try:
        handle = job.create_job(None, None)
        job.require(handle, 'create_job')
        limits = job.LIMITS()
        limits.basic.flags = 0x2008  # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS
        limits.basic.processes = 1
        job.require(job.set_job(handle, 9, c.byref(limits), c.sizeof(limits)), 'set_job')
        security = job.SA(c.sizeof(job.SA), None, True)
        for path in ('NUL', str(fixture / 'stdout.log'), str(fixture / 'stderr.log')):
            opened = job.create_file(path, 0x80000000 if path == 'NUL' else 0x40000000, 1,
                                     c.byref(security), 3 if path == 'NUL' else 2, 0x80, None)
            job.require(opened and opened != c.c_void_p(-1).value, 'output_handle')
            outputs.append(opened)
        startup = job.STARTUP()
        startup.cb = c.sizeof(startup)
        startup.flags = 0x101
        startup.show = 0
        startup.stdin, startup.stdout, startup.stderr = outputs
        command = c.create_unicode_buffer('"' + str(exe) + '"')
        job.require(job.create_process(str(exe), command, None, None, True, 0x08000404, block,
                                       str(fixture), c.byref(startup), c.byref(process)), 'create_suspended')
        actual = c.create_unicode_buffer(32768)
        length = w.DWORD(32768)
        job.require(job.query_path(process.process, 0, actual, c.byref(length)), 'query_owned_path')
        require(os.path.normcase(actual.value) == os.path.normcase(str(exe)), 'owned_executable_mismatch')
        job.require(job.assign(handle, process.process), 'job_before_resume')
        assigned = True
        job.require(job.resume(process.thread) != 0xffffffff, 'resume')
        waited = job.wait(process.process, 30000)
        require(waited in (0, 258), 'wait_failed')
        timed_out = waited == 258
        job.require(job.exit_code(process.process, c.byref(code)), 'exit_code')
    except Exception as error:
        failure = str(error)
    finally:
        if handle:
            job.terminate_job(handle, 91)
            for _ in range(50):
                pids = job.PIDS()
                if job.query_job(handle, 3, c.byref(pids), c.sizeof(pids), None):
                    remaining = pids.listed
                    if remaining == 0:
                        break
                time.sleep(.02)
        if process.process and not assigned:
            job.bind('TerminateProcess', w.BOOL, [w.HANDLE, w.UINT])(process.process, 91)
        for item in (process.thread, process.process, *outputs, handle):
            if item:
                job.close(item)
        error_mode(old_mode)
    return dict(failure=failure, timedOut=timed_out, exitCode=code.value, jobAssignedBeforeResume=assigned,
                activeProcessLimit=1, jobProcessesAfterCleanup=remaining, elapsedSeconds=round(time.monotonic()-start, 3))


def main():
    media = ROOT / 'native/media'
    files = [*media.glob('*.cpp'), *media.glob('*.h'), *media.glob('*.cmake'), media / 'CMakeLists.txt',
             media / 'html_entities.inc', *Path(__file__).parent.glob('*.cpp'), Path(__file__),
             Path(__file__).with_name('CMakeLists.txt'), JOB_SOURCE]
    exe = BUILD / 'valmax_media_flow_test.exe'
    compiled = [file for file in files if file.suffix in ('.cpp', '.h', '.inc')]
    require(exe.is_file() and exe.stat().st_mtime_ns >= max(file.stat().st_mtime_ns for file in compiled), 'rebuild_required')
    before = {str(file.relative_to(ROOT)): sha(file) for file in files}
    fixture = Path('C:/ValMAXBuild/build/media-flow-fixtures') / uuid.uuid4().hex
    fixture.mkdir(parents=True)
    copied = {}
    for source in QT.glob('*.dll'):
        if source.name.lower().startswith(('qt6', 'msvcp', 'vcruntime', 'concrt', 'api-ms-', 'ucrt')):
            os.link(source, fixture / source.name)
            copied[source.name] = sha(source)
    for directory in ('plugins', 'qml'):
        shutil.copytree(QT / directory, fixture / directory, copy_function=os.link,
                        ignore=lambda path, names: [name for name in names if name == 'ValMAX'])
    shutil.copy2(QT / 'qt.conf', fixture / 'qt.conf')
    pins = {'Qt6Core.dll': '22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa',
            'Qt6Gui.dll': '19265a4e76efc12ef5f5bcf59be6c6a5b9b59ec4c89660797738bd2da7f719cc',
            'plugins/platforms/qwindows.dll': 'd94c584a18829d11fbf3aefd07e7b1e189e0cf063a0515826960037efa7465df'}
    for name, digest in pins.items():
        require(sha(fixture / name) == digest, 'pinned_qt_changed: ' + name)
    require(not any(file.name.lower() in ('max.exe', 'valmax_core.dll', 'core.dll', 'network.dll', 'notifications.dll')
                    for file in fixture.rglob('*')), 'unexpected_client_binary')
    shutil.copy2(exe, fixture / exe.name)
    for directory in ('home', 'local', 'roaming', 'temp'):
        (fixture / directory).mkdir()
    environment = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'SystemDrive', 'COMSPEC', 'PATHEXT') if key in os.environ}
    environment.update(PATH=str(fixture) + os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32'),
        QT_QPA_PLATFORM='windows', QT_QUICK_BACKEND='software', QT_DEBUG_PLUGINS='1',
        QML_DISABLE_DISK_CACHE='1', QML_IMPORT_PATH=str(fixture / 'qml'), QML2_IMPORT_PATH=str(fixture / 'qml'),
        USERPROFILE=str(fixture / 'home'), LOCALAPPDATA=str(fixture / 'local'), APPDATA=str(fixture / 'roaming'),
        TEMP=str(fixture / 'temp'), TMP=str(fixture / 'temp'))
    process = run(fixture, environment)
    stdout = (fixture / 'stdout.log').read_text(encoding='utf8')
    stderr = (fixture / 'stderr.log').read_text(encoding='utf8')
    require(process['failure'] is None and not process['timedOut'] and process['exitCode'] == 0,
            f'flow_failed: {process}\n{stdout}\n{stderr[-5000:]}')
    require(process['jobAssignedBeforeResume'] and process['jobProcessesAfterCleanup'] == 0, 'job_cleanup_failed')
    native = json.loads(stdout)
    require(native['passed'] and native['qt'] == '6.11.2', 'native_result_invalid')
    for name, path in native['loadedModules'].items():
        require(Path(path).resolve() == (fixture / name).resolve(), 'loaded_path_mismatch: ' + name)
        require(sha(Path(path)) == sha(QT / name), 'loaded_qt_mismatch: ' + name)
    require(before == {str(file.relative_to(ROOT)): sha(file) for file in files}, 'source_changed_during_proof')
    report = dict(schema=1, verified=True, sources=before, fixture=str(fixture), qtSourceRoot=str(QT),
        native=native, process=process, executableSha256=sha(exe),
        libraries={str(path.relative_to(BUILD)): sha(path) for path in (BUILD / 'media').glob('*.lib')},
        runtimeModules={name: copied[name] for name in native['loadedModules']}, platformPins=pins,
        stderrSha256=sha(fixture / 'stderr.log'), installedClientModified=False, productionPluginRegistered=False,
        networkUsed=False, mediaStaged=False, messagesSent=False,
        coverage=['Actual controller, protocol, scheduler and transport with explicit fake reply factory on dedicated QThread',
            'Every fake reply includes two separate legitimate Set-Cookie headers using actual QHttpHeaders; Qt raw aggregation contains LF',
            'Genuine PNG encoded in memory, decoded by real DecodeQueue worker, normalized pixels published to PreviewStore',
            'Actual QQmlEngine Image reaches Ready twice using image:// tokens and cache:false without visible windows',
            'New accepted search invalidates captured ControllerStamp while provider epoch remains unchanged; late decode is not published',
            'Explicit QML source clear and provider epoch advance before replacement image; engine teardown closes store',
            'Selected original returns exact bounded bytes plus Candidate to native callback only; no file stage or automatic send',
            'Owned controller/transport/scheduler/decode shutdown, service thread joined, process exits and Job contains no remaining processes'],
        limitations=['Fake HTTP proves local integration, not Wikimedia availability, TLS/proxy behavior or server interaction.',
            'No production Qt bridge, client draft attachment, native service bootstrap or UI registration is implemented by this fixture.',
            'Image.Ready is not a visible rendering/GPU performance measurement.',
            'A Qt-owned texture is not revoked by native cache; this fixture explicitly clears QML source and disables its cache.',
            'Synthetic promise transfers and commands are test orchestration, not a proposed production service architecture.',
            'The decoder barrier wraps the real decoder only to force a stale result; production has no such barrier.',
            'The isolated environment/Job prevents child processes and bounds test duration; it is not an OS file or network sandbox.'])
    output = ROOT / 'analysis/native-migration-v1/media-flow-proof.json'
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps(dict(verified=True, checks=native['checks'], proof=str(output), fixture=str(fixture))))


if __name__ == '__main__':
    main()
