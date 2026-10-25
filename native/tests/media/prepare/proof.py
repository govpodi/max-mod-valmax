"""Owned hidden fixtures for strict native media preparation; no real profiles."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import uuid

ROOT = Path(__file__).resolve().parents[4]
BUILD = Path('C:/ValMAXBuild/build/media-prepare')
QT = Path('C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb')
CORE_HASH = '6d51da3b847411e72f38b101c78e40573342e636cfdbadb3594aa9957c689b54'
CORE = Path('C:/ValMAXBuild/build/all-feature-inputs-' + CORE_HASH) / 'valmax_core.dll'
spec = importlib.util.spec_from_file_location('owned_media_prepare_process', ROOT / 'native/tests/media/flow/proof.py')
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def require(value, message):
    if not value:
        raise RuntimeError(message)

def main():
    files = [ROOT / 'native/media' / ('runtime_prepare.' + ext) for ext in ('cpp', 'h', 'cmake')]
    files += [Path(__file__), Path(__file__).with_name('prepare_test.cpp'), Path(__file__).with_name('CMakeLists.txt'),
              ROOT / 'native/media/staging_client.h', ROOT / 'native/media/staging_client.cpp', ROOT / 'native/media/service_host.h',
              ROOT / 'native/core/valmax_core.h']
    before = {str(p.relative_to(ROOT)): sha(p) for p in files}
    frozen = {str(p): sha(p) for p in map(Path, ('C:/ValMAXBuild/target/release/valmax_core.dll',
        'C:/ValMAXBuild/target/release/deps/valmax_core.dll', 'C:/ValMAXBuild/build/all-feature-inputs-core-startup/valmax_core.dll',
        'C:/ValMAXBuild/build/all-feature-inputs/valmax_core.dll', str(CORE)))}
    require(sha(CORE) == CORE_HASH, 'frozen core mismatch')
    exe = BUILD / 'valmax_media_prepare_test.exe'
    require(exe.stat().st_mtime_ns >= max(p.stat().st_mtime_ns for p in files if p.suffix in ('.cpp', '.h')), 'rebuild required')
    runs = []
    for mode in ('exact', 'modified_checksum'):
        fixture = Path('C:/ValMAXBuild/build/media-prepare-fixtures') / uuid.uuid4().hex
        fixture.mkdir(parents=True)
        copied = {}
        for source in QT.glob('*.dll'):
            if source.name.lower().startswith(('qt6', 'msvcp', 'vcruntime', 'concrt', 'api-ms-', 'ucrt')):
                os.link(source, fixture / source.name)
                copied[source.name] = sha(source)
        require(copied.get('Qt6Core.dll') == '22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa', 'exact MAX Qt required')
        shutil.copy2(CORE, fixture / 'valmax_core.dll')
        shutil.copy2(exe, fixture / exe.name)
        if mode == 'modified_checksum':
            data = bytearray((fixture / 'valmax_core.dll').read_bytes())
            offset = struct.unpack_from('<I', data, 0x3c)[0]
            require(data[offset:offset+4] == b'PE\0\0', 'PE signature required')
            checksum_offset = offset + 24 + 64
            data[checksum_offset] ^= 1  # Header metadata only; same size and unchanged code.
            (fixture / 'valmax_core.dll').write_bytes(data)
            require(sha(fixture / 'valmax_core.dll') != CORE_HASH, 'negative fixture was not changed')
        for name in ('home', 'local', 'roaming', 'temp', 'synthetic-profile-root'):
            (fixture / name).mkdir()
        env = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'SystemDrive', 'COMSPEC', 'PATHEXT') if k in os.environ}
        env.update(PATH=str(fixture) + os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32'),
            USERPROFILE=str(fixture / 'home'), LOCALAPPDATA=str(fixture / 'local'), APPDATA=str(fixture / 'roaming'),
            TEMP=str(fixture / 'temp'), TMP=str(fixture / 'temp'), VALMAX_SYNTHETIC_PREPARE_ROOT=str(fixture / 'synthetic-profile-root'))
        if mode != 'exact':
            env['VALMAX_SYNTHETIC_BAD_CORE'] = '1'
        process = flow.run(fixture, env, exe.name)
        stdout = (fixture / 'stdout.log').read_text('utf8')
        stderr = (fixture / 'stderr.log').read_text('utf8')
        (BUILD / 'last-run.json').write_text(json.dumps({'mode': mode, 'fixture': str(fixture), 'process': process, 'stdout': stdout, 'stderr': stderr}, indent=2), encoding='utf8')
        require(process['failure'] is None and not process['timedOut'] and process['exitCode'] == 0, 'prepare failed; see build/media-prepare/last-run.json')
        require(process['jobAssignedBeforeResume'] and process['jobProcessesAfterCleanup'] == 0, 'owned job cleanup failed')
        result = json.loads(stdout.strip().splitlines()[-1])
        require(result['passed'], 'native failure')
        runs.append({'mode': mode, 'fixture': str(fixture), 'process': process, 'result': result,
            'loadedCoreSha256': sha(fixture / 'valmax_core.dll'), 'qt': copied,
            'stdoutSha256': sha(fixture / 'stdout.log'), 'stderrSha256': sha(fixture / 'stderr.log')})
    require(before == {str(p.relative_to(ROOT)): sha(p) for p in files}, 'source changed during test')
    require(frozen == {p: sha(p) for p in frozen}, 'frozen core changed')
    proof = {'schema': 1, 'ok': True, 'scope': 'native worker-only media preparation, strict host config, loaded core identity, pinned fixed staging child',
        'checks': sum(r['result']['checks'] for r in runs), 'sourceSha256': before, 'runs': runs,
        'testExecutable': {'path': str(exe), 'sha256': sha(exe)}, 'frozenDllsUnchanged': frozen,
        'limitations': ['Already-loaded DLL verification is not initial-load safety or executable-memory integrity.',
            'Resources must remain held until the service asynchronous run has fully drained.',
            'Filesystem/hash system calls have no hard time bound and are restricted to a worker.',
            'No real profiles, installed application, HTTP, or installer were used.',
            'Runtime directory is lexically checked but not accessed; no runtime_open or unrelated settings writes.',
            'No forced disk-full/failing-close injection or concurrent directory replacement stress in this focused fixture.']}
    report = ROOT / 'analysis/native-migration-v1/media-runtime-prepare-proof.json'
    report.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'ok': True, 'checks': proof['checks'], 'report': str(report)}))

if __name__ == '__main__':
    main()
