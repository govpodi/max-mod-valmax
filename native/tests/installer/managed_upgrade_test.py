"""Run the real Inno fixture installer against its separate AppId and work root.

The product installer is never invoked. Artifacts and preserved trees stay under
the explicitly selected build root.
Only the exact test registry namespaces below may be removed by this harness.
"""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import time
import uuid
import winreg

ROOT = Path(__file__).resolve().parents[3]
ISCC = Path('D:/CodexLabs/max-mod-26.35.1/tools/InnoSetup/ISCC.exe')
APP_ID = '{80AA932D-1E5C-4509-BE97-20D43D271ADE}'
KEY = rf'Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}_is1'
TRANSACTIONS = rf'Software\ValMAX\InstallerTransactions\{APP_ID}'
VIEW = winreg.KEY_WOW64_64KEY


def registry_tree(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, name, 0, winreg.KEY_READ | VIEW) as key:
            values = {}
            index = 0
            while True:
                try:
                    item, value, kind = winreg.EnumValue(key, index)
                except OSError as error:
                    if error.winerror == 259:
                        break
                    raise
                values[item] = [value.hex() if isinstance(value, bytes) else value, kind]
                index += 1
            children = {}
            index = 0
            while True:
                try:
                    item = winreg.EnumKey(key, index)
                except OSError as error:
                    if error.winerror == 259:
                        break
                    raise
                children[item] = registry_tree(name + '\\' + item)
                index += 1
            return {'values': values, 'children': children}
    except FileNotFoundError:
        return None


def cleanup_test_key(name):
    if name not in (KEY, TRANSACTIONS) and not name.startswith(TRANSACTIONS + '\\') and not name.startswith(KEY + '\\'):
        raise RuntimeError('Refusing any registry cleanup outside the exact fixture namespace')
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, name, 0, winreg.KEY_READ | VIEW) as key:
            children = []
            while True:
                try:
                    children.append(winreg.EnumKey(key, len(children)))
                except OSError as error:
                    if error.winerror == 259:
                        break
                    raise
        for child in children:
            cleanup_test_key(name + '\\' + child)
        winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, name, VIEW)
    except FileNotFoundError:
        pass


def inventory(path):
    return {str(file.relative_to(path)).replace('\\', '/'):
            hashlib.sha256(file.read_bytes()).hexdigest()
            for file in sorted(path.rglob('*')) if file.is_file()}


class Fixture:
    def __init__(self, process_executable, work_root):
        self.root = work_root / 'fixtures' / ('installer-upgrade-' + uuid.uuid4().hex)
        self.root.mkdir(parents=True, exist_ok=False)
        self.app = self.root / 'app'
        self.log_index = 0
        self.checks = []
        self.profile = self.root / 'synthetic-profile-outside-app'
        self.profile.mkdir()
        (self.profile / 'opaque-settings-and-session.bin').write_bytes(bytes(range(256)) * 4)
        self.profile_before = inventory(self.profile)
        self.process = None
        self.process_executable = process_executable
        self.owns_registry = False
        self.webview_called = self.root / 'webview2-bootstrapper-called.marker'
        self.webview_present = self.root / 'webview2-present.marker'
        self.environment = os.environ.copy()
        self.environment.update(TEMP=str(work_root / 'tmp'), TMP=str(work_root / 'tmp'),
                                VALMAX_FIXTURE_WEBVIEW2_CALLED=str(self.webview_called),
                                VALMAX_FIXTURE_WEBVIEW2_PRESENT=str(self.webview_present),
                                VALMAX_FIXTURE_WEBVIEW2_MODE='failure')

    def check(self, name, condition):
        if not condition:
            raise AssertionError(name)
        self.checks.append(name)

    def compile(self, version, define=None):
        payload = self.root / ('payload-' + version)
        (payload / 'runtime/transfer').mkdir(parents=True)
        (payload / 'ValMAX.exe').write_bytes(b'synthetic-launcher-not-run-' + version.encode())
        (payload / 'runtime/transfer/valmax-transfer.exe').write_bytes(b'synthetic-transfer-not-run')
        (payload / 'native-package.json').write_text(json.dumps({'schema': 1, 'synthetic': True, 'version': version}))
        (payload / 'runtime/native-version.txt').write_text(version)
        output = self.root / ('setup-' + version)
        output.mkdir()
        command = [str(ISCC), '/Qp', '/DPayload=' + str(payload), '/DOutput=' + str(output),
                   '/DProductVersion=' + version, '/DValMAXInstallerFixture=1',
                   '/DFixtureRoot=' + str(self.root).replace('/', '\\')]
        if define in ('FixtureEvergreenPrerequisite', 'FixtureEvergreenSilentPrerequisite',
                      'FixtureEvergreenConsentRace'):
            command.append('/DFixtureEvergreenPrerequisite=1')
            if define != 'FixtureEvergreenSilentPrerequisite':
                command.append('/DFixtureConsentConfirmed=1')
            if define == 'FixtureEvergreenConsentRace':
                command.append('/DFixtureConsentLaunchesClient=1')
            command.extend(['/DWebView2Bootstrapper=' + str(self.process_executable),
                            '/DNativeWebView2BootstrapperSha256=' +
                            hashlib.sha256(self.process_executable.read_bytes()).hexdigest()])
        elif define:
            command.append('/D' + define + '=1')
        command.append(str(ROOT / 'native/installer/valmax-native.iss'))
        result = subprocess.run(command, env=self.environment, capture_output=True, timeout=90)
        (output / 'compile.log').write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError('Fixture compilation failed: ' + (result.stdout + result.stderr).decode(errors='replace')[-1600:])
        return output / ('ValMAX-' + version + '-Setup.exe')

    def run(self, executable, expected_success=True):
        self.log_index += 1
        command = [str(executable), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                   '/LOG=' + str(self.root / ('install-' + str(self.log_index) + '.log'))]
        result = subprocess.run(command, env=self.environment, capture_output=True, timeout=90,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if expected_success and result.returncode:
            raise RuntimeError('Fixture setup/uninstall failed: ' + str(result.returncode))
        if not expected_success and result.returncode == 0:
            raise AssertionError('Expected a nonzero fixture exit')
        return result.returncode

    def compile_release_gates(self):
        payload = self.root / 'payload-0.0.1-fixture'
        stamp = payload / 'native-release-state.iss'
        actual = hashlib.sha256((payload / 'native-package.json').read_bytes()).hexdigest()
        cases = [
            ('missing', None, False),
            ('blocked', ('developer-staging', 0, 4, actual), False),
            ('mismatched', ('user-release', 1, 0, '0' * 64), False),
            ('matched-compile-only', ('user-release', 1, 0, actual), True),
        ]
        output = self.root / 'release-gate-compile-only'
        output.mkdir()
        for label, values, accepted in cases:
            if values:
                mode, eligible, blockers, digest = values
                stamp.write_text('#define NativeDistributionMode "' + mode + '"\n'
                                 '#define NativeReleaseEligible ' + str(eligible) + '\n'
                                 '#define NativeReleaseBlockerCount ' + str(blockers) + '\n'
                                 '#define NativePackageManifestSha256 "' + digest + '"\n'
                                 '#define NativeWebView2Mode "fixed"\n'
                                 '#define NativeQtSourceMode "inline"\n'
                                 '#define NativeFFmpegSourceMode "inline"\n')
            elif stamp.exists():
                stamp.unlink()
            command = [str(ISCC), '/Qp', '/O-', '/DPayload=' + str(payload),
                       '/DOutput=' + str(output), '/DProductVersion=0.0.0-compile-only',
                       str(ROOT / 'native/installer/valmax-native.iss')]
            result = subprocess.run(command, env=self.environment, capture_output=True, timeout=60)
            (output / (label + '.log')).write_bytes(result.stdout + result.stderr)
            self.check('production-release-compile-gate-' + label, (result.returncode == 0) == accepted)
        self.check('production-gates-never-emit-setup-exe', not list(output.glob('*.exe')))

    def uninstall(self):
        self.run(self.app / 'unins000.exe')
        # Inno's original uninstaller can exit before its temporary worker.
        # Wait for the owned fixture's actual completion, not only that launcher.
        deadline = time.monotonic() + 20
        log = self.root / ('install-' + str(self.log_index) + '.log')
        while time.monotonic() < deadline:
            text = log.read_text(encoding='utf-8-sig') if log.exists() else ''
            if 'Uninstallation process succeeded.' in text and not (self.app / '_unins-done.tmp').exists() and not (self.app / 'unins000.exe').exists():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError('Owned fixture uninstaller did not finish')
        self.check('uninstall-registration-removed-' + str(self.log_index), registry_tree(KEY) is None)
        self.check('external-profile-intact-' + str(self.log_index), inventory(self.profile) == self.profile_before)

    def add_old_files(self):
        (self.app / 'legacy-python-module.py').write_text('synthetic old module, not executed')
        (self.app / 'unknown-user-note.txt').write_bytes(b'preserve this unknown file verbatim')

    def add_registry_sentinels(self):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_WRITE | VIEW) as key:
            winreg.SetValueEx(key, 'SyntheticBinary', 0, winreg.REG_BINARY, b'\x00\xff\x80')
            winreg.SetValueEx(key, 'SyntheticMulti', 0, winreg.REG_MULTI_SZ, ['one', 'two'])
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY + '\\FixtureChild', 0, winreg.KEY_WRITE | VIEW) as key:
            winreg.SetValueEx(key, 'Typed', 0, winreg.REG_DWORD, 27)

    def execute_evergreen_consent_race(self):
        self.check('consent-race-fixture-key-unoccupied', registry_tree(KEY) is None)
        self.check('consent-race-transaction-key-unoccupied', registry_tree(TRANSACTIONS) is None)
        self.owns_registry = True
        initial = self.compile('0.0.10-fixture')
        race = self.compile('0.0.11-fixture', 'FixtureEvergreenConsentRace')
        retry = self.compile('0.0.12-fixture', 'FixtureEvergreenPrerequisite')
        self.run(initial)
        original_launcher = (self.app / 'ValMAX.exe').read_bytes()
        shutil.copyfile(self.process_executable, self.app / 'ValMAX.exe')
        app_before, registry_before = inventory(self.app), registry_tree(KEY)
        try:
            self.run(race, False)
            log = (self.root / ('install-' + str(self.log_index) + '.log')).read_text(encoding='utf-8-sig')
            self.check('consent-race-fixture-client-started',
                       'fixture_evergreen_consent_started_client' in log)
            self.check('consent-race-initial-target-preflight-before-consent',
                       log.index('fixture_evergreen_target_preflight_ok') <
                       log.index('fixture_evergreen_consent_started_client'))
            self.check('consent-race-post-consent-process-preflight-blocked',
                       'fixture_evergreen_post_consent_preflight_ok' not in log)
            self.check('consent-race-bootstrapper-never-started', not self.webview_called.exists())
            self.check('consent-race-no-upgrade-journal',
                       not Path(str(self.app) + '.valmax-update.ini').exists())
            self.check('consent-race-app-and-registration-preserved',
                       inventory(self.app) == app_before and registry_tree(KEY) == registry_before)
            self.check('consent-race-profile-preserved', inventory(self.profile) == self.profile_before)
        finally:
            # The only spawned EXE is the owned /fixture-short program. It exits
            # itself after five seconds; wait before restoring its file bytes.
            time.sleep(5.5)
            (self.app / 'ValMAX.exe').write_bytes(original_launcher)
        self.environment['VALMAX_FIXTURE_WEBVIEW2_MODE'] = 'success'
        self.run(retry)
        self.check('consent-race-follow-up-setup-can-retry',
                   self.webview_called.is_file() and self.webview_present.is_file() and
                   (self.app / 'runtime/native-version.txt').read_text() == '0.0.12-fixture')
        self.check('consent-race-follow-up-leaves-profile', inventory(self.profile) == self.profile_before)

    def execute(self):
        self.check('fixture-key-unoccupied-at-start', registry_tree(KEY) is None)
        self.check('transaction-key-unoccupied-at-start', registry_tree(TRANSACTIONS) is None)
        self.owns_registry = True
        v1, v2 = self.compile('0.0.1-fixture'), self.compile('0.0.2-fixture')
        fail = self.compile('0.0.3-fixture', 'FixtureFailAfterCopy')
        crash = self.compile('0.0.4-fixture', 'FixtureCrashAfterMove')
        fallback = self.compile('0.0.5-fixture', 'FixtureForcePathFallback')
        preview_name = self.compile('0.0.6-fixture', 'FixturePreviewName')
        beta_name = self.compile('0.0.7-fixture', 'FixtureBetaName')
        evergreen = self.compile('0.0.8-fixture', 'FixtureEvergreenPrerequisite')
        evergreen_silent = self.compile('0.0.9-fixture', 'FixtureEvergreenSilentPrerequisite')
        self.compile_release_gates()
        self.run(fail, False)
        self.check('fresh-failure-registration-absent', registry_tree(KEY) is None)
        self.check('fresh-failure-partial-preserved-outside-app', not self.app.exists() and any(path.is_dir() for path in self.root.glob('app.valmax-incomplete-*')))
        self.check('fresh-failure-clears-journal', not Path(str(self.app) + '.valmax-update.ini').exists())
        self.run(v1)
        self.check('fresh-native-marker-written', (self.app / '.valmax-installation.ini').is_file())
        self.check('fresh-version-registered', registry_tree(KEY)['values']['DisplayVersion'][0] == '0.0.1-fixture')
        evergreen_before, evergreen_registry = inventory(self.app), registry_tree(KEY)
        self.run(evergreen_silent, False)
        silent_log = (self.root / ('install-' + str(self.log_index) + '.log')).read_text(encoding='utf-8-sig')
        self.check('evergreen-silent-without-runtime-never-launches-bootstrapper',
                   not self.webview_called.exists() and 'fixture_evergreen_silent_refused' in silent_log)
        self.check('evergreen-silent-without-runtime-preserves-app-and-registration',
                   inventory(self.app) == evergreen_before and registry_tree(KEY) == evergreen_registry)
        self.check('evergreen-silent-without-runtime-leaves-no-journal',
                   not Path(str(self.app) + '.valmax-update.ini').exists())
        for mode in ('failure', 'restart', 'restart_initiated', 'success_no_registry'):
            self.webview_called.unlink(missing_ok=True)
            self.environment['VALMAX_FIXTURE_WEBVIEW2_MODE'] = mode
            code = self.run(evergreen, False)
            log = (self.root / ('install-' + str(self.log_index) + '.log')).read_text(encoding='utf-8-sig')
            self.check('evergreen-' + mode + '-bootstrapper-invoked', self.webview_called.is_file())
            self.check('evergreen-' + mode + '-leaves-app-unchanged', inventory(self.app) == evergreen_before)
            self.check('evergreen-' + mode + '-leaves-registration-unchanged', registry_tree(KEY) == evergreen_registry)
            self.check('evergreen-' + mode + '-leaves-no-journal',
                       not Path(str(self.app) + '.valmax-update.ini').exists())
            self.check('evergreen-' + mode + '-unlocks-for-retry',
                       'fixture_evergreen_mutex_released' in log and
                       'fixture_transaction_label_created' not in log)
            if mode in ('restart', 'restart_initiated'):
                self.check('evergreen-restart-returns-reboot-code', code == 8)
        self.webview_called.unlink(missing_ok=True)
        original_launcher = (self.app / 'ValMAX.exe').read_bytes()
        shutil.copyfile(self.process_executable, self.app / 'ValMAX.exe')
        try:
            self.process = subprocess.Popen([str(self.app / 'ValMAX.exe')],
                                            creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(0.1)
            self.check('evergreen-running-client-fixture-started', self.process.poll() is None)
            running_before = inventory(self.app)
            self.run(evergreen, False)
            self.check('evergreen-running-client-skips-prerequisite', not self.webview_called.exists())
            self.check('evergreen-running-client-leaves-app-unchanged', inventory(self.app) == running_before)
            self.check('evergreen-running-client-leaves-registration-unchanged',
                       registry_tree(KEY) == evergreen_registry)
        finally:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            (self.app / 'ValMAX.exe').write_bytes(original_launcher)
        link = self.app / 'unsafe-fixture-link'
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.profile)], check=True,
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.run(evergreen, False)
            self.check('evergreen-unsafe-target-skips-prerequisite', not self.webview_called.exists())
            self.check('evergreen-unsafe-target-preserves-profile', inventory(self.profile) == self.profile_before)
        finally:
            os.rmdir(link)  # Exact owned junction only; never recursive.
        self.environment['VALMAX_FIXTURE_WEBVIEW2_MODE'] = 'success'
        self.run(evergreen)
        log = (self.root / ('install-' + str(self.log_index) + '.log')).read_text(encoding='utf-8-sig')
        order = ('fixture_evergreen_target_preflight_ok', 'WebView2 bootstrapper exit code: 0',
                 'fixture_evergreen_post_preflight_ok', 'fixture_transaction_label_created')
        self.check('evergreen-success-order-before-transaction',
                   all(part in log for part in order) and
                   list(map(log.index, order)) == sorted(map(log.index, order)))
        self.check('evergreen-success-installs-fixture-prerequisite',
                   self.webview_called.is_file() and self.webview_present.is_file())
        self.check('evergreen-success-upgrades-owned-app',
                   (self.app / 'runtime/native-version.txt').read_text() == '0.0.8-fixture')
        self.webview_called.unlink()
        self.run(evergreen)
        self.check('evergreen-already-present-skips-bootstrapper', not self.webview_called.exists())
        self.check('evergreen-already-present-keeps-prerequisite', self.webview_present.is_file())
        self.run(evergreen_silent)
        self.check('evergreen-silent-with-runtime-needs-no-consent-or-bootstrapper',
                   not self.webview_called.exists() and self.webview_present.is_file())
        self.add_old_files()
        self.add_registry_sentinels()
        before = inventory(self.app)
        registry_before = registry_tree(KEY)
        original_launcher = (self.app / 'ValMAX.exe').read_bytes()
        shutil.copyfile(self.process_executable, self.app / 'ValMAX.exe')
        try:
            self.process = subprocess.Popen([str(self.app / 'ValMAX.exe')],
                                            creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(0.1)
            self.check('owned-process-fixture-running', self.process.poll() is None)
            running_before = inventory(self.app)
            self.run(v2, False)
            self.check('running-client-refused-before-rename', inventory(self.app) == running_before)
            self.check('running-client-registry-preserved', registry_tree(KEY) == registry_before)
        finally:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            (self.app / 'ValMAX.exe').write_bytes(original_launcher)
        browser = self.app / 'runtime/msedgewebview2.exe'
        shutil.copyfile(self.process_executable, browser)
        try:
            self.process = subprocess.Popen([str(browser)], creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(0.1)
            self.check('owned-webview-fixture-running', self.process.poll() is None)
            running_before = inventory(self.app)
            self.run(v2, False)
            self.check('running-owned-webview-refused-before-rename', inventory(self.app) == running_before)
            self.check('running-owned-webview-registry-preserved', registry_tree(KEY) == registry_before)
        finally:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            browser.unlink()
        webengine = self.app / 'runtime/owned space Юникод/QtWebEngineProcess.exe'
        webengine.parent.mkdir()
        shutil.copyfile(self.process_executable, webengine)
        try:
            self.process = subprocess.Popen([str(webengine)], creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(0.1)
            self.check('owned-qtwebengine-fixture-running', self.process.poll() is None)
            running_before = inventory(self.app)
            self.run(fallback, False)
            self.check('running-owned-qtwebengine-refused-before-rename', inventory(self.app) == running_before)
            self.check('running-owned-qtwebengine-registry-preserved', registry_tree(KEY) == registry_before)
            self.run(self.app / 'unins000.exe', False)
            self.check('running-owned-qtwebengine-refuses-uninstall', inventory(self.app) == running_before)
            self.check('running-owned-qtwebengine-uninstall-registration-preserved', registry_tree(KEY) == registry_before)
        finally:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            webengine.unlink()
            webengine.parent.rmdir()
        journal = Path(str(self.app) + '.valmax-update.ini')
        for previous in ('malformed', '1'):
            journal.write_text('[transaction]\nschema=1\nappId=' + APP_ID + '\napp=' + str(self.app) +
                               '\nnonce=' + 'f' * 32 + '\nprevious=' + previous + '\n')
            self.run(v2, False)
            self.check('invalid-journal-tree-unchanged-' + previous, inventory(self.app) == before)
            self.check('invalid-journal-registry-unchanged-' + previous, registry_tree(KEY) == registry_before)
            self.check('invalid-journal-preserved-' + previous, journal.is_file())
            journal.unlink()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateMutexW(None, True, 'Local\\ValMAXInstallerFixture-' + APP_ID)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.run(v2, False)
            self.check('busy-transfer-mutex-refuses-without-file-changes', inventory(self.app) == before)
            self.check('busy-transfer-mutex-preserves-registry', registry_tree(KEY) == registry_before)
        finally:
            kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
        self.run(fail, False)
        self.check('failure-reached-post-copy-injection', 'synthetic_failure_after_copy' in (self.root / ('install-' + str(self.log_index) + '.log')).read_text(encoding='utf-8-sig'))
        self.check('failure-restores-entire-old-tree', inventory(self.app) == before)
        self.check('failure-restores-all-registry-types-and-subkeys', registry_tree(KEY) == registry_before)
        self.check('failure-clears-pending-journal', not Path(str(self.app) + '.valmax-update.ini').exists())
        self.check('failure-preserves-outside-profile', inventory(self.profile) == self.profile_before)
        self.run(crash, False)
        self.check('crash-preserves-journal', Path(str(self.app) + '.valmax-update.ini').is_file())
        self.check('crash-keeps-complete-old-backup', any(inventory(path) == before for path in self.root.glob('app.valmax-backup-*') if path.is_dir()))
        self.run(v2)
        self.check('next-setup-recovers-and-upgrades', (self.app / 'runtime/native-version.txt').read_text() == '0.0.2-fixture')
        self.check('legacy-python-does-not-leak', not (self.app / 'legacy-python-module.py').exists())
        self.check('unknown-file-preserved-in-old-backup', any((path / 'unknown-user-note.txt').read_bytes() == b'preserve this unknown file verbatim' for path in self.root.glob('app.valmax-backup-*') if (path / 'unknown-user-note.txt').is_file()))
        backups = {path.name: inventory(path) for path in self.root.glob('app.valmax-backup-*') if path.is_dir()}
        self.check('backup-byte-identical', before in backups.values())
        self.check('upgrade-registration-updated', registry_tree(KEY)['values']['DisplayVersion'][0] == '0.0.2-fixture')
        self.check('upgrade-preserves-outside-profile', inventory(self.profile) == self.profile_before)
        foreign = self.root / 'app lookalike Юникод/QtWebEngineProcess.exe'
        foreign.parent.mkdir()
        shutil.copyfile(self.process_executable, foreign)
        try:
            self.process = subprocess.Popen([str(foreign)], creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(0.1)
            self.check('foreign-qtwebengine-fixture-running', self.process.poll() is None)
            self.run(fallback)
            self.check('fallback-ignores-outside-prefix-unicode-image', (self.app / 'runtime/native-version.txt').read_text() == '0.0.5-fixture')
            self.check('foreign-qtwebengine-process-left-running', self.process.poll() is None)
        finally:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
        backups = {path.name: inventory(path) for path in self.root.glob('app.valmax-backup-*') if path.is_dir()}
        self.uninstall()
        self.check('uninstaller-leaves-old-backups-byte-identical', all(inventory(self.root / name) == records for name, records in backups.items()))
        self.check('uninstaller-leaves-backup-registry-receipt', registry_tree(TRANSACTIONS) is not None)
        # Unknown destination is preserved, even with a familiar executable name.
        self.app.mkdir(exist_ok=True)
        (self.app / 'ValMAX.exe').write_bytes(b'unknown-not-an-installation')
        unknown = inventory(self.app)
        self.run(v1, False)
        self.check('unknown-directory-refused-without-change', inventory(self.app) == unknown)
        (self.app / 'ValMAX.exe').unlink()
        self.app.rmdir()
        # A junction is rejected; its target outside app must be untouched.
        self.run(v1)
        link = self.app / 'junction'
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.profile)], check=True,
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        original_marker = (self.app / '.valmax-installation.ini').read_bytes()
        self.run(v2, False)
        self.check('junction-refused-before-copy', (self.app / '.valmax-installation.ini').read_bytes() == original_marker)
        self.check('junction-target-profile-preserved', inventory(self.profile) == self.profile_before)
        os.rmdir(link)  # Exact junction only; never recursive.
        self.uninstall()
        # Legacy fixture follows the actual old packager's root contract.
        self.run(v1)
        (self.app / '.valmax-installation.ini').unlink()
        for name in ('MAX Mod.exe', 'package-files.json', 'runtime/mod/mod-manifest.json', 'ValMAX-инструкция.txt'):
            path = self.app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('synthetic legacy ' + name).encode())
        legacy_temp = self.app / '.mxmod-helper-temp'
        legacy_temp.mkdir()
        (legacy_temp / 'opaque-old-helper-receipt.bin').write_bytes(b'opaque synthetic legacy bytes\x00\xff')
        runtime_temp = self.app / 'runtime/.mxmod-helper-temp'
        # A helper-temp junction is forbidden even at an otherwise supported
        # location. Remove only this exact test junction, never its target.
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(runtime_temp), str(self.profile)], check=True,
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        junction_before = inventory(self.profile)
        self.run(preview_name, False)
        self.check('legacy-runtime-helper-junction-refused', not (self.app / '.valmax-installation.ini').exists())
        self.check('legacy-runtime-helper-junction-target-unchanged', inventory(self.profile) == junction_before)
        os.rmdir(runtime_temp)
        runtime_temp.mkdir()
        (runtime_temp / 'opaque-runtime-receipt.bin').write_bytes(b'opaque runtime helper bytes\x00\xff')
        # Reserved contents prove that the allowed helper directory stays
        # opaque; traversal would reject this deliberately named child.
        (runtime_temp / 'ONEME').mkdir()
        (runtime_temp / 'ONEME/synthetic-opaque-sentinel.bin').write_bytes(b'not a real profile')
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_WRITE | VIEW) as key:
            winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, 'ValMAX Installer Fixture, версия 8.8.4')
            winreg.SetValueEx(key, 'DisplayVersion', 0, winreg.REG_SZ, '8.8.4')
        for name in ('ValMAX Installer Fixture impostor', 'ValMAX native preview',
                     'ValMAX Installer Fixture native preview extra, версия 8.8.4'):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_WRITE | VIEW) as key:
                winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, name)
            before_spoof, registry_spoof = inventory(self.app), registry_tree(KEY)
            self.run(preview_name, False)
            self.check('legacy-spoofed-family-refused-' + str(len(name)), inventory(self.app) == before_spoof)
            self.check('legacy-spoofed-family-registry-unchanged-' + str(len(name)), registry_tree(KEY) == registry_spoof)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_WRITE | VIEW) as key:
            winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, 'ValMAX Installer Fixture, версия 8.8.4')
        for relative in ('other/.mxmod-helper-temp', 'runtime/deeper/.mxmod-helper-temp'):
            invalid = self.app / relative
            invalid.mkdir(parents=True)
            (invalid / 'owned-test.bin').write_bytes(b'wrong helper location')
            invalid_before, invalid_registry = inventory(self.app), registry_tree(KEY)
            self.run(preview_name, False)
            self.check('legacy-nested-helper-refused-' + relative, inventory(self.app) == invalid_before)
            self.check('legacy-nested-helper-registry-unchanged-' + relative, registry_tree(KEY) == invalid_registry)
            (invalid / 'owned-test.bin').unlink()
            invalid.rmdir()
            invalid.parent.rmdir()  # The exact fresh test parent only.
        legacy_before, legacy_registry = inventory(self.app), registry_tree(KEY)
        self.run(fail, False)
        self.check('legacy-helper-rollback-restores-both-opaque-paths', inventory(self.app) == legacy_before)
        self.check('legacy-helper-rollback-restores-canonical-registration', registry_tree(KEY) == legacy_registry)
        self.run(preview_name)
        self.check('registered-legacy-contract-upgraded', (self.app / '.valmax-installation.ini').is_file())
        self.check('canonical-legacy-upgrades-to-preview-family',
                   registry_tree(KEY)['values']['DisplayName'][0] == 'ValMAX Installer Fixture native preview')
        self.check('legacy-tree-backed-up-verbatim', any(inventory(path) == legacy_before for path in self.root.glob('app.valmax-backup-*') if path.is_dir()))
        self.check('legacy-files-not-in-native-tree', not (self.app / 'MAX Mod.exe').exists())
        self.check('legacy-helper-temp-not-in-native-tree', not (self.app / '.mxmod-helper-temp').exists())
        self.check('legacy-helper-temp-preserved-in-backup', any(
            (path / '.mxmod-helper-temp/opaque-old-helper-receipt.bin').read_bytes() == b'opaque synthetic legacy bytes\x00\xff'
            for path in self.root.glob('app.valmax-backup-*') if (path / '.mxmod-helper-temp/opaque-old-helper-receipt.bin').is_file()))
        self.check('legacy-runtime-helper-temp-not-in-native-tree', not (self.app / 'runtime/.mxmod-helper-temp').exists())
        self.check('legacy-runtime-helper-temp-preserved-in-backup', any(
            (path / 'runtime/.mxmod-helper-temp/opaque-runtime-receipt.bin').read_bytes() == b'opaque runtime helper bytes\x00\xff'
            for path in self.root.glob('app.valmax-backup-*') if (path / 'runtime/.mxmod-helper-temp/opaque-runtime-receipt.bin').is_file()))
        self.run(v2)
        self.check('preview-family-upgrades-back-to-canonical-family',
                   registry_tree(KEY)['values']['DisplayName'][0] == 'ValMAX Installer Fixture')
        beta_family = 'ValMAX Installer Fixture BETA'
        beta_version = '0.0.7-fixture'
        for label, display_name in (
            ('plain', beta_family),
            ('english-version', beta_family + ' version ' + beta_version),
            ('russian-version', beta_family + ', версия ' + beta_version),
        ):
            self.run(beta_name)
            self.check('beta-fixture-registered-' + label,
                       registry_tree(KEY)['values']['DisplayName'][0] == beta_family)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_WRITE | VIEW) as key:
                winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, display_name)
            beta_before = inventory(self.app)
            self.run(v2)
            self.check('beta-family-upgrade-accepted-' + label,
                       (self.app / 'runtime/native-version.txt').read_text() == '0.0.2-fixture')
            self.check('beta-family-backup-exact-' + label,
                       any(inventory(path) == beta_before for path in self.root.glob('app.valmax-backup-*') if path.is_dir()))
            self.check('beta-family-registry-updated-' + label,
                       registry_tree(KEY)['values']['DisplayName'][0] == 'ValMAX Installer Fixture')
            self.check('beta-family-external-profile-preserved-' + label,
                       inventory(self.profile) == self.profile_before)
        self.run(beta_name)
        for label, display_name in (
            ('extra-word', beta_family + ' extra'),
            ('wrong-version', beta_family + ', версия 0.0.8-fixture'),
        ):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_WRITE | VIEW) as key:
                winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, display_name)
            beta_before, registry_before = inventory(self.app), registry_tree(KEY)
            self.run(v2, False)
            self.check('beta-spoof-rejected-tree-' + label, inventory(self.app) == beta_before)
            self.check('beta-spoof-rejected-registry-' + label, registry_tree(KEY) == registry_before)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_WRITE | VIEW) as key:
            winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, beta_family)
        self.uninstall()
        self.check('final-profile-sentinel-byte-identical', inventory(self.profile) == self.profile_before)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=('full', 'evergreen-consent-race'), default='full')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--process-executable', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    args = parser.parse_args()
    work_root = args.work_root.resolve(strict=True)
    if not args.work_root.is_absolute() or not work_root.is_dir() or work_root.name != 'ValMAXBuild':
        raise ValueError('An existing absolute ValMAXBuild work root is required')
    if not (work_root / 'build').is_dir() or not (work_root / 'tmp').is_dir():
        raise ValueError('The work root must contain build and tmp directories')
    process_executable = args.process_executable.resolve(strict=True)
    if not process_executable.is_relative_to((work_root / 'build').resolve()) or process_executable.name != 'ValMAX.exe':
        raise ValueError('The owned fixture process must be built under the selected work root')
    fixture = Fixture(process_executable, work_root)
    error = None
    try:
        if args.scenario == 'full':
            fixture.execute()
        else:
            fixture.execute_evergreen_consent_race()
    except Exception as caught:
        error = str(caught)
    finally:
        # Exact fixture namespace only; retained files remain for inspection.
        # A real production AppId is never present in these cleanup calls.
        if fixture.owns_registry:
            cleanup_test_key(KEY)
            cleanup_test_key(TRANSACTIONS)
    report = {'schema': 1, 'ok': error is None, 'scenario': args.scenario, 'syntheticOnly': True,
              'productionInstallerRun': False, 'realProfilesOrWinCredAccessed': False,
              'fixtureRoot': str(fixture.root), 'fixtureAppId': APP_ID,
              'checks': fixture.checks, 'passed': len(fixture.checks), 'error': error,
              'sourceHashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in ['native/installer/valmax-native.iss',
                                            'native/installer/managed-upgrade.iss',
                                            'native/tests/installer/managed_upgrade_test.py']}}
    args.report.write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps({'ok': report['ok'], 'checks': report['passed'], 'error': error, 'fixture': str(fixture.root)}))
    return 0 if error is None else 1


if __name__ == '__main__':
    raise SystemExit(main())
