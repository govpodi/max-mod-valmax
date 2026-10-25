"""Proof of direct typed launcher binding; synthetic ABI and named guard only."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import pefile

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path('C:/ValMAXBuild/build/launcher')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    test = BUILD / 'valmax_profile_client_test.exe'
    completed = subprocess.run([str(test)], capture_output=True, text=True, check=True, timeout=20)
    native = json.loads(completed.stdout)
    if not native['ok'] or native['productionMutexAcquired'] or native['realCorePrepareCalled']:
        raise AssertionError('Unexpected test scope')
    pe = pefile.PE(str(test))
    imports = {item.dll.decode(): sorted(symbol.name.decode() for symbol in item.imports if symbol.name)
               for item in pe.DIRECTORY_ENTRY_IMPORT}
    if set(name.lower() for name in imports) != {'kernel32.dll'}:
        raise AssertionError('Unexpected dependency')
    library = BUILD / 'valmax_profile_client.lib'
    paths = [ROOT/'launcher/profile_client.h', ROOT/'launcher/profile_client.cpp',
             ROOT/'launcher/profile_client.cmake', ROOT/'launcher/transfer_mutex.h',
             ROOT/'launcher/transfer_mutex.cpp', ROOT/'core/valmax_core.h',
             Path(__file__), Path(__file__).with_name('profile_client_test.cpp')]
    report = {
        'schema': 1, 'ok': True,
        'scope': 'C++ synchronous typed profile binding with an injected ABI fake and renamed synthetic transfer mutex',
        'library': {'path': str(library), 'sha256': sha(library), 'size': library.stat().st_size},
        'test': {'path': str(test), 'sha256': sha(test), 'native': native, 'imports': imports},
        'sourceSha256': {str(path.relative_to(ROOT)): sha(path) for path in paths},
        'confirmed': [
            'Caller must own a live outer TransferGuard on this OS thread before invoking the supplied function',
            'Function pointer comes from the trusted already-loaded core; binding performs no LoadLibrary or module discovery',
            'Strict UTF-16 to UTF-8 rejects unpaired surrogates, embedded NUL and a UTF-8 result over32000 bytes',
            'Cyrillic and supplementary Unicode encode exactly; ABI receives bytes without a terminator on the same thread',
            'Exact result size16/version1, finite code0..5 and valid mode-dependent state are checked',
            'ABI mismatch and finite errors always return unavailable state; no untrusted result path or allocated buffer escapes',
            'Released/wrong-thread guards deny before the fake ABI call',
            'Result only describes config preparation; no launchAllowed flag or spawn action exists'],
        'productionMutexAcquired': False, 'realCorePrepareCalled': False,
        'realProfilesAccessed': False, 'userAppTouched': False,
        'limitations': [
            'The host must retain the trusted loaded core DLL for the entire synchronous call',
            'Typed function pointers are a host trust boundary; a hostile pointer or invalid memory is outside the C ABI contract',
            'C ABI functions must not throw C++ exceptions; Rust exported boundary catches its own panics',
            'UTF conversion is validated here; canonical local path and filesystem guards remain in the Rust profile module',
            'The outer guard must remain on this thread through eventual startup and cannot be moved across a Qt worker',
            'This proof does not load the core DLL or integrate a real launcher; actual same-thread C++ to Rust proof remains a separate gate',
            'Successful config preparation is not runtime readiness, a recovery-marker snapshot or permission to launch']}
    target = ROOT.parent/'analysis/native-migration-v1/profile-client-proof.json'
    target.write_text(json.dumps(report, indent=2)+'\n', encoding='utf8')
    print(json.dumps({'ok': True, 'nativeChecks': native['checks'], 'librarySha256': sha(library), 'proof': str(target)}))

if __name__ == '__main__':
    main()
