"""Cross-language archive contract tests using synthetic data only.

The old Python implementation is a build-time oracle, never a native dependency.
"""
from pathlib import Path
import base64
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'modkit'))
import session_archive as old

DRIVER = Path('C:/ValMAXBuild/target/release/valmax-core-test-driver.exe')
PASSWORD = 'Синтетический пароль №42'
POLICY = old.ArchivePolicy(credential_names=('access_token_7@MXMOD',), allowed_file_paths=('auth_7',))
POLICY_JSON = {name: list(getattr(POLICY, name)) for name in POLICY.__dataclass_fields__}
PAYLOAD = {'schema': 1, 'sourceClient': 'MAX Desktop', 'sourceVersion': '26.35.1',
           'credentials': [{'name': 'access_token_7@MXMOD', 'blob': base64.b64encode(b'synthetic-token-not-an-account').decode()}],
           'files': [{'path': 'auth_7', 'data': base64.b64encode('Синтетический аккаунт'.encode()).decode()}]}


def native(operation, **fields):
    body = json.dumps({'operation': operation, **fields}, ensure_ascii=False, separators=(',', ':')).encode()
    result = subprocess.run([str(DRIVER)], input=body, capture_output=True, timeout=30)
    assert result.returncode == 0 and not result.stderr, 'native_driver_failure'
    return json.loads(result.stdout)


def main():
    assert DRIVER.is_file()
    checks = []
    def check(name, ok):
        assert ok, name
        checks.append(name)
    value = native('version')
    check('native_abi', value['ok'] and value['result']['abi'] == 1)
    archive = old.encrypt(PAYLOAD, PASSWORD, policy=POLICY)
    result = native('session.decrypt', archive=base64.b64encode(archive).decode(), password=PASSWORD, policy=POLICY_JSON)
    check('python_encrypt_rust_decrypt_unicode', result.get('result', {}).get('payload') == PAYLOAD)
    result = native('session.encrypt', payload=PAYLOAD, password=PASSWORD, policy=POLICY_JSON)
    check('rust_encrypt_ok', result['ok'])
    generated = base64.b64decode(result['result']['archive'], validate=True)
    check('rust_encrypt_python_decrypt_unicode', old.decrypt(generated, PASSWORD, policy=POLICY) == PAYLOAD)
    check('fresh_entropy', native('session.encrypt', payload=PAYLOAD, password=PASSWORD, policy=POLICY_JSON)['result']['archive'] != result['result']['archive'])
    for name, candidate, password, policy, wanted in [
        ('wrong_password', archive, PASSWORD + 'x', POLICY_JSON, 'authentication_failed'),
        ('tampered_ciphertext', archive[:-1] + bytes([archive[-1] ^ 1]), PASSWORD, POLICY_JSON, 'authentication_failed'),
        ('trailing_bytes', archive + b'x', PASSWORD, POLICY_JSON, 'invalid_archive'),
        ('truncated', archive[:20], PASSWORD, POLICY_JSON, 'invalid_archive'),
        ('deny_default_policy', archive, PASSWORD, {}, 'policy_violation'),
        ('empty_password', archive, '', POLICY_JSON, 'invalid_password'),
        ('short_password', archive, '123', POLICY_JSON, 'invalid_password'),
        ('nul_password', archive, PASSWORD + '\0', POLICY_JSON, 'invalid_password'),
    ]:
        answer = native('session.decrypt', archive=base64.b64encode(candidate).decode(), password=password, policy=policy)
        check(name, answer.get('error') == wanted)
    for name, mutation in [
        ('duplicate_credential', {'credentials': PAYLOAD['credentials'] * 2}),
        ('traversal', {'files': [{'path': '../auth_7', 'data': ''}]}),
        ('invalid_base64', {'files': [{'path': 'auth_7', 'data': 'ZA='}]}),
        ('bool_schema', {'schema': True}),
        ('extra_field', {'unexpected': 1}),
        ('wrong_version', {'sourceVersion': '0'}),
    ]:
        data = {**PAYLOAD, **mutation}
        try:
            old.validate_payload(data, policy=POLICY)
            expected = None
        except old.SessionArchiveError as error:
            expected = error.code
        answer = native('session.validate', payload=data, policy=POLICY_JSON)
        check(name, expected is not None and answer.get('error') == expected)
    proof = {'schema': 1, 'passed': len(checks), 'checks': checks,
             'driverSha256': hashlib.sha256(DRIVER.read_bytes()).hexdigest(),
             'pythonOracleSha256': hashlib.sha256((ROOT / 'modkit/session_archive.py').read_bytes()).hexdigest(),
             'realProfilesAccessed': False, 'networkUsedByTests': False,
             'scope': 'Native archive codec compatibility; not full MData import, credentials or client integration.'}
    target = ROOT / 'analysis/native-migration-v1/session-interop.json'
    target.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(proof, ensure_ascii=False))


if __name__ == '__main__':
    main()
