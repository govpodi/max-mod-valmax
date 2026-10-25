"""Explicit live smoke probe; normal synthetic/CI tests never invoke this file."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import pefile
from thumbnail_proof import ROOT, RUNTIME, sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live-public-wikimedia', action='store_true', required=True)
    parser.parse_args()
    build = Path('C:/ValMAXBuild/build/media-live')
    names = [f'native/media/{name}.{suffix}' for name in
             ('protocol', 'scheduler', 'transport', 'result_cache', 'controller', 'thumbnail')
             for suffix in ('h', 'cpp')]
    names += ['native/tests/media/live_probe.cpp', 'native/tests/media/live_probe.py',
              'native/tests/media/live/CMakeLists.txt']
    sources = {name: sha(ROOT / name) for name in names}
    binary = build / 'valmax_media_live_probe.exe'
    assert all((ROOT / name).stat().st_mtime_ns <= binary.stat().st_mtime_ns
               for name in names if name.endswith(('.cpp', '.h'))), 'stale_probe'
    fixture = Path('C:/ValMAXBuild/fixtures/media-live') / uuid.uuid4().hex
    fixture.mkdir(parents=True)
    pending = [RUNTIME / name for name in ('Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Network.dll',
               'plugins/tls/qschannelbackend.dll', 'plugins/imageformats/qjpeg.dll',
               'plugins/imageformats/qgif.dll', 'plugins/imageformats/qwebp.dll')]
    copied = {}
    while pending:
        source = pending.pop()
        name = source.relative_to(RUNTIME).as_posix()
        if name in copied:
            continue
        target = fixture / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied[name] = sha(target)
        with pefile.PE(str(source), fast_load=True) as pe:
            pe.parse_data_directories(directories=[1, 13])
            for entry in list(getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])) + list(getattr(pe, 'DIRECTORY_ENTRY_DELAY_IMPORT', [])):
                child = RUNTIME / entry.dll.decode('ascii')
                if child.is_file():
                    pending.append(child)
    assert copied['Qt6Core.dll'] == '22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa'
    assert not any(Path(n).name.lower().startswith(('max', 'core.dll', 'network.dll', 'python')) for n in copied)
    exe = fixture / binary.name
    shutil.copy2(binary, exe)
    env = {k: os.environ[k] for k in ('SystemRoot', 'WINDIR', 'COMSPEC') if k in os.environ}
    env['PATH'] = str(fixture) + ';C:/Windows/System32;C:/Windows'
    try:
        run = subprocess.run([str(exe), '--live-public-wikimedia'], cwd=fixture, env=env,
            capture_output=True, text=True, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW)
        # Native probe reports only finite outcomes and counts. Qt warning bodies
        # are deliberately not logged. No provider rows/URLs/response bytes saved.
        result = json.loads(run.stdout)
        assert run.returncode in (0, 2)
        assert not run.stderr
    except subprocess.TimeoutExpired:
        result = {'ok': False, 'watchdogExpired': True}
    assert sources == {name: sha(ROOT / name) for name in names}, 'source_changed_during_probe'
    proof = dict(schema=1, liveProbeExecuted=True, passed=bool(result.get('ok')),
        result=result, executableSha256=sha(exe), runtimeSha256=copied, sources=sources,
        fixture=str(fixture), installedClientModified=False, localServerStarted=False,
        maxProfilesUsed=False, mediaFilesWritten=False, messagesSent=False,
        scope='Public Commons search, one preview/decode and one bounded original; real QtNetwork on dedicated QThread',
        limitations=[
            'Availability depends on provider, network and configured system proxy; failure is not a synthetic test pass.',
            'One successful path would not prove all TLS/proxy/PAC/authentication variants or future availability.',
            'No QML/native composer, selected-file staging, production plugin or installer verification.',
        ])
    out = ROOT / 'analysis/native-migration-v1/media-live-probe.json'
    out.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': proof['passed'], 'result': result, 'proof': str(out)}))


if __name__ == '__main__':
    main()
