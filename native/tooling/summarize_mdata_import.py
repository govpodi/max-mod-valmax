"""Record verified native compact-import components; no live profile data."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path('C:/ValMAXBuild/build')
TARGET = Path('C:/ValMAXBuild/target-mdata-import/release/deps')
OUT = ROOT / 'analysis/native-migration-v1/mdata-import-native-proof.json'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    log = BUILD / 'mdata-import-rust-final.log'
    text = log.read_text(encoding='utf-8-sig')
    suites = re.findall(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;', text)
    assert len(suites) == 21 and all(failed == ignored == '0' for _, failed, ignored in suites)
    passed = sum(int(item[0]) for item in suites)
    assert passed >= 305 and 'test result: FAILED' not in text
    ffi_file = BUILD / 'transfer-ffi/result.json'
    ffi = json.loads(ffi_file.read_text(encoding='utf-8-sig'))
    assert ffi['ok'] and ffi['checks'] >= 20
    assert ffi['realCredentialCalls'] is False and ffi['successfulImportTested'] is False
    scanner_file = ROOT / 'analysis/native-migration-v1/closed-clients-native-proof.json'
    scanner = json.loads(scanner_file.read_text(encoding='utf-8-sig'))
    assert scanner['verified'] and scanner['checks'] >= 53
    for name, expected in scanner['sourceHashes'].items():
        assert digest(ROOT / name) == expected, name
    sources = ['native/core/valmax_core.h', 'native/Cargo.lock', 'native/Cargo.toml', 'native/core/Cargo.toml',
               'native/tests/transfer/ffi_test.cpp', 'native/tests/transfer/run_ffi.ps1']
    sources.extend(p.relative_to(ROOT).as_posix() for p in (ROOT / 'native/core/src').rglob('*.rs'))
    sources.extend(p.relative_to(ROOT).as_posix() for p in (ROOT / 'native/core/tests').rglob('*.rs'))
    core = TARGET / 'valmax_core.dll'
    assert ffi['coreSha256'] == digest(core) and ffi['coreUnchanged'] is True
    assert digest(Path(ffi['coreDll'])) == digest(core)
    ffi_exe = BUILD / 'transfer-ffi/valmax_transfer_ffi_test.exe'
    imports_log = BUILD / 'mdata-import-core-imports.log'
    imports = re.findall(r'^\s{4}([\w.-]+\.dll)\s*$', imports_log.read_text(encoding='utf-8-sig'), re.M)
    assert imports and not any('python' in name.lower() for name in imports)
    report = {
        'schema': 1, 'verified': True,
        'rust': {'passed': passed, 'suites': len(suites), 'failed': 0, 'ignored': 0,
                 'log': str(log), 'sha256': digest(log)},
        'core': {'path': str(core), 'bytes': core.stat().st_size, 'sha256': digest(core)},
        'imports': imports,
        'cppActualDllBoundary': {**ffi, 'exeSha256': digest(ffi_exe), 'reportSha256': digest(ffi_file)},
        'closedClientScanner': {'checks': scanner['checks'], 'proof': scanner_file.name,
                               'sha256': digest(scanner_file)},
        'sourceHashes': {name: digest(ROOT / name) for name in sorted(set(sources))},
        'coverage': [
            'Authenticated compact v2 input, confirmed SHA256 bound to the same bytes, no plaintext staging',
            'All-account preflight, stale user/auth preservation, empty and absent token distinction',
            'Owned auth before token commit; reverse rollback including committed-but-error writes',
            'Token rollback failure preserves metadata; explicit repeated import completes missing accounts',
            'Native transfer lease, cancellation, recovery marker, capacity and fresh closed-client gates',
            'Actual encrypted files and Windows filesystem with synthetic credential adapters',
            'Actual C++ to Rust DLL ABI failure/cancel paths, no production credential calls',
            'C++ all-process scanner, immutable original-pair policy, bounded settle/wait state machine',
        ],
        'nativeCompactImportBackend': True,
        'oneShotHostUiIntegrated': False, 'runtimeImportRouteEnabled': False,
        'fullProfileRestoreImplemented': False, 'installerIntegrationVerified': False,
        'userInstallationChanged': False, 'realProfileUsed': False, 'realWinCredUsed': False,
        'limitations': [
            'Import service positive checks use fake credentials and host callbacks; server session acceptance is untested.',
            'C++ DLL boundary positive import is intentionally not run against the user credential store.',
            'The scanner is not yet wired to a packaged one-shot Qt helper; no new import UI is shipped.',
            'Closed-process snapshots and cooperative mutex do not cover arbitrary renamed/direct-start clients.',
            'Windows I/O and scrypt are not preemptible; deadlines/cancellation do not guarantee hard latency.',
            'Config may remain after later failure; rollback_failed can leave a valid subset of imported accounts.',
            'Legacy full-profile MData/MaxSession recovery, final native launcher and installer remain separate work.',
            'Three existing repr(C) WinAPI declaration warnings remain; builds are not claimed warning-free.',
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('x', encoding='utf-8', newline='\n') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write('\n')
    print(json.dumps({'proof': str(OUT), 'rustTests': passed, 'cppChecks': ffi['checks'],
                      'scannerChecks': scanner['checks'], 'coreSha256': report['core']['sha256']}))


if __name__ == '__main__':
    main()
