"""Python/Rust PIN compatibility on disposable synthetic stores, never real profiles."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'modkit'))
import chat_lock_service as old
import notification_policy as policy

DRIVER = Path('C:/ValMAXBuild/target/release/valmax-chat-lock-test-driver.exe')
CHECKS = []

class FakeProtector:
    def protect(self, value):
        return b'OWN-TEST-PROTECTED:' + value
    def unprotect(self, value):
        if not value.startswith(b'OWN-TEST-PROTECTED:'):
            raise old.ChatLockError('protector_unavailable')
        return value[len(b'OWN-TEST-PROTECTED:'):]

def check(label, result):
    if not result:
        raise AssertionError(label)
    CHECKS.append(label)

def request(action, owner='42', chat='-77', pin='681439', **extra):
    value = {'schema': 1, 'accountId': owner, 'chatId': chat}
    if action in ('set', 'unlock', 'remove'):
        value['pin'] = pin
    if action == 'set':
        value['confirmPin'] = pin
    value.update(extra)
    return {'action': action, 'value': value}

def native(directory, requests, *, dpapi=False, now=1_000_000):
    data = {'directory': str(directory), 'dpapi': dpapi, 'nowMs': now, 'requests': requests}
    completed = subprocess.run([str(DRIVER)], input=json.dumps(data).encode(), capture_output=True, timeout=30)
    check('native_process_exit', completed.returncode == 0)
    check('native_stderr_empty', not completed.stderr)
    result = json.loads(completed.stdout)
    check('native_response_shape', set(result) == {'responses'} and len(result['responses']) == len(requests))
    return result['responses']

def legacy(service, action, value):
    try:
        return {'ok': True, 'value': service.execute(action, value)}
    except old.ChatLockError as error:
        return {'ok': False, 'error': error.code}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    root = Path('C:/ValMAXBuild/fixtures')
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pin-interop-', dir=root) as temporary:
        base = Path(temporary).resolve()
        check('fixture_inside_build_root', base.parent == root.resolve())
        for dpapi in (False, True):
            directory = base / ('windows-dpapi' if dpapi else 'fake-protector')
            protector = old.WindowsProtector() if dpapi else FakeProtector()
            service = old.ChatLockService(directory, protector=protector, clock=lambda: 1000.0, monotonic=lambda: 1000.0)
            first = request('set', owner='9007199254740993', chat='-9223372036854775808')
            old_set = legacy(service, first['action'], first['value'])
            check('python_creates_compatible_verifier', old_set['ok'])
            unlock = request('unlock', owner='9007199254740993', chat='-9223372036854775808')
            result = native(directory, [unlock, {'action': 'policy'}], dpapi=dpapi)
            check('rust_unlocks_python_verifier', result[0] == old_set)
            check('native_policy_matches_python', result[1] == {'ok': True, 'value': policy.from_catalog(service.catalog())})
            second = request('set', chat='0', pin='318642')
            result = native(directory, [second], dpapi=dpapi)[0]
            check('rust_creates_compatible_verifier', result['ok'])
            unlock = request('unlock', chat='0', pin='318642')
            check('python_unlocks_rust_verifier', legacy(service, unlock['action'], unlock['value']) == result)
            # Five failed attempts are durable and the same Python backoff applies.
            invalid = request('unlock', chat='0', pin='000000')
            failures = native(directory, [invalid] * 5, dpapi=dpapi)
            check('native_failed_attempts_are_durable', all(v == {'ok': False, 'error': 'wrong_pin'} for v in failures))
            fresh = old.ChatLockService(directory, protector=protector, clock=lambda: 1000.0, monotonic=lambda: 1000.0)
            check('python_observes_native_backoff', legacy(fresh, 'unlock', unlock['value']) == {'ok': False, 'error': 'rate_limited'})
            result = native(directory, [unlock], dpapi=dpapi, now=1_030_000)[0]
            check('native_backoff_exact_boundary', result['ok'] and result['value']['locked'] is False)
            before = (directory / 'chat-locks.json').read_bytes()
            bad = [request('status', owner='0'), request('status', chat='-0'), request('status', chat='01'),
                   request('status', owner='9223372036854775808'), request('status', schema=True),
                   request('status', unexpected='synthetic'), request('set', pin='１２３４５６')]
            for requested, actual in zip(bad, native(directory, bad, dpapi=dpapi), strict=True):
                check('native_python_validation_error_matches', actual == legacy(service, requested['action'], requested['value']))
            check('invalid_inputs_preserve_bytes', before == (directory / 'chat-locks.json').read_bytes())
            remove = request('remove', chat='0', pin='318642')
            check('native_removes_rust_created_pin', native(directory, [remove], dpapi=dpapi)[0]['value']['protected'] is False)
            check('python_observes_native_removal', len(service.catalog()['protected']) == 1)
            # App is not logged into any real MAX account; these are fixture scopes only.
            raw = (directory / 'chat-locks.json').read_text()
            check('stored_verifier_has_no_plain_pin', '681439' not in raw and '318642' not in raw and '"pin"' not in raw)
    sources = ['native/core/src/chat_lock.rs', 'native/core/src/notification_policy.rs',
               'native/core/tests/chat_lock_native.rs', 'native/core/src/bin/chat_lock_test_driver.rs',
               'modkit/chat_lock_service.py', 'modkit/notification_policy.py', 'native/Cargo.lock']
    proof = {'schema': 1, 'status': 'pass', 'checks': len(CHECKS), 'checkNames': CHECKS,
             'syntheticOnly': True, 'realProfilesAccessed': False, 'networkUsed': False,
             'nativeRuntimeIntegrated': False, 'nativeNotificationHooksVerified': False,
             'driverSha256': sha(DRIVER), 'sourceSha256': {p: sha(PROJECT / p) for p in sources}}
    output = PROJECT / 'analysis/native-migration-v1/chat-lock-interop-proof.json'
    output.write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'pass', 'checks': len(CHECKS), 'proof': str(output)}))

if __name__ == '__main__':
    main()
