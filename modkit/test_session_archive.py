"""All records/passwords are artificial fixtures; no profile/WinCred access."""
import base64
import copy
from dataclasses import FrozenInstanceError
import json
import os
import struct
import unittest
from unittest import mock

import session_archive as module
from session_archive import ArchivePolicy, SessionArchiveError, decrypt, encrypt, validate_payload


PASSWORD = "Synthetic-Test-Password-123"
POLICY = ArchivePolicy(credential_names=("synthetic_auth_7@MXMOD", "synthetic_Common@MXMOD"),
                       allowed_file_paths=("auth_7", "settings/synthetic.json"))


def b64(value):
    return base64.b64encode(value).decode("ascii")


def payload():
    return {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
            "credentials": [{"name": "synthetic_auth_7@MXMOD",
                             "blob": b64(b"SYNTHETIC-NOT-A-REAL-SESSION\x00\xff")}],
            "files": [{"path": "auth_7", "data": b64(b"SYNTHETIC-FILE-ONLY")} ]}


def frame(header, ciphertext):
    data = module._canonical(header)
    return module.MAGIC + struct.pack(">H", len(data)) + data + ciphertext


class SessionArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = encrypt(payload(), PASSWORD, policy=POLICY)
        cls.header, cls.salt, cls.nonce, cls.aad, cls.ciphertext = module._header(cls.archive)
        cls.synthetic_key = module._derive(PASSWORD.encode(), cls.salt)

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(SessionArchiveError) as raised:
            function(*args, **kwargs)
        self.assertEqual(code, raised.exception.code)
        self.assertEqual(code, str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def seal_plaintext(self, data):
        # Test-only authenticated malformed fixtures. A fresh nonce is used
        # for every distinct plaintext, even though the synthetic key is reused.
        header = copy.deepcopy(self.header)
        nonce = os.urandom(12)
        header["nonce"] = b64(nonce)
        header["plaintextBytes"] = len(data)
        encoded = module._canonical(header)
        aad = module.MAGIC + struct.pack(">H", len(encoded)) + encoded
        return aad + module.AESGCM(self.synthetic_key).encrypt(nonce, data, aad)

    def test_round_trip_and_empty_files_field(self):
        self.assertEqual(payload(), decrypt(self.archive, PASSWORD, policy=POLICY))
        value = payload()
        value["files"] = []
        archive = encrypt(value, PASSWORD, policy=POLICY)
        self.assertEqual(value, decrypt(archive, PASSWORD, policy=POLICY))

    def test_encryption_is_randomized_and_contains_no_fixture_plaintext(self):
        other = encrypt(payload(), PASSWORD, policy=POLICY)
        self.assertNotEqual(self.archive, other)
        self.assertNotEqual(self.salt, module._header(other)[1])
        self.assertNotEqual(self.nonce, module._header(other)[2])
        for value in [PASSWORD.encode(), b"SYNTHETIC-NOT-A-REAL-SESSION", b"synthetic_auth_7@MXMOD"]:
            self.assertNotIn(value, self.archive)

    def test_header_parameters_are_pinned_and_complete(self):
        header = self.header
        self.assertEqual("MAX Desktop", header["sourceClient"])
        self.assertEqual("26.35.1", header["sourceVersion"])
        self.assertEqual("AES-256-GCM", header["cipher"])
        self.assertEqual((32768, 8, 1), tuple(header["kdf"][key] for key in ("n", "r", "p")))
        self.assertEqual((16, 12), (len(self.salt), len(self.nonce)))
        self.assertEqual(header["plaintextBytes"] + 16, len(self.ciphertext))

    def test_wrong_password_and_ciphertext_corruption_share_one_code(self):
        self.error("authentication_failed", decrypt, self.archive,
                   "Different-Synthetic-Password", policy=POLICY)
        for offset in [len(self.aad), len(self.archive) - 1]:
            changed = bytearray(self.archive)
            changed[offset] ^= 1
            self.error("authentication_failed", decrypt, bytes(changed), PASSWORD, policy=POLICY)

    def test_authenticated_salt_and_nonce_cannot_be_changed(self):
        for field in ("salt", "nonce"):
            header = copy.deepcopy(self.header)
            if field == "salt":
                header["kdf"][field] = b64(bytes([1]) * 16)
            else:
                header[field] = b64(bytes([2]) * 12)
            self.error("authentication_failed", decrypt, frame(header, self.ciphertext), PASSWORD,
                       policy=POLICY)

    def test_invalid_password_forms_never_run_kdf(self):
        for password in [None, b"not accepted as bytes", True, "", "short", "x" * 1025,
                         "\x00" + PASSWORD, "\ud800" + PASSWORD, "я" * 513]:
            with self.subTest(length=len(password) if type(password) in (str, bytes) else None):
                with mock.patch.object(module, "_derive", side_effect=AssertionError("KDF ran")):
                    self.error("invalid_password", encrypt, payload(), password, policy=POLICY)
                    self.error("invalid_password", decrypt, self.archive, password, policy=POLICY)

    def test_password_unicode_is_exact_and_not_normalized(self):
        first = "Synthetic-é-Password"
        second = "Synthetic-e\u0301-Password"
        archive = encrypt(payload(), first, policy=POLICY)
        self.assertEqual(payload(), decrypt(archive, first, policy=POLICY))
        self.error("authentication_failed", decrypt, archive, second, policy=POLICY)

    def test_oversized_archive_is_rejected_before_kdf(self):
        with mock.patch.object(module, "_derive", side_effect=AssertionError("KDF ran")):
            self.error("too_large", decrypt, b"x" * (module.MAX_ARCHIVE_BYTES + 1), PASSWORD,
                       policy=POLICY)

    def test_invalid_framing_and_truncation_are_rejected_before_kdf(self):
        cases = [None, bytearray(self.archive), b"", b"PK\x03\x04synthetic-zip",
                 b"x" + self.archive[1:], self.archive[:-1], self.archive + b"trailing",
                 self.archive[:len(module.MAGIC)] + b"\xff\xff" + self.archive[len(module.MAGIC) + 2:],
                 self.archive[:len(module.MAGIC)] + b"\x00\x01" + self.archive[len(module.MAGIC) + 2:]]
        with mock.patch.object(module, "_derive", side_effect=AssertionError("KDF ran")):
            for archive in cases:
                self.error("invalid_archive", decrypt, archive, PASSWORD, policy=POLICY)

    def test_untrusted_kdf_parameters_cannot_increase_work(self):
        cases = [("n", 1 << 40), ("n", True), ("n", "32768"), ("r", 4096),
                 ("r", 0), ("p", 1024), ("p", -1), ("name", "other-kdf")]
        with mock.patch.object(module, "_derive", side_effect=AssertionError("KDF ran")):
            for key, value in cases:
                header = copy.deepcopy(self.header)
                header["kdf"][key] = value
                self.error("invalid_archive", decrypt, frame(header, self.ciphertext), PASSWORD,
                           policy=POLICY)

    def test_header_unknown_keys_types_versions_and_salt_lengths(self):
        cases = []
        for key, value, code in [("schema", True, "invalid_archive"),
                                 ("schema", 2, "invalid_archive"),
                                 ("sourceVersion", "26.35.2", "unsupported_version"),
                                 ("sourceClient", "Another Client", "unsupported_version"),
                                 ("cipher", "AES-128-GCM", "invalid_archive"),
                                 ("plaintextBytes", True, "invalid_archive"),
                                 ("plaintextBytes", module.MAX_PLAINTEXT_BYTES + 1, "invalid_archive"),
                                 ("nonce", b64(b"short"), "invalid_archive")]:
            header = copy.deepcopy(self.header)
            header[key] = value
            cases.append((header, code))
        for salt, code in [(b64(b"short"), "invalid_archive"), (b64(b"x" * 17), "too_large"),
                           ("?" * 24, "invalid_archive")]:
            header = copy.deepcopy(self.header)
            header["kdf"]["salt"] = salt
            cases.append((header, code))
        unknown = copy.deepcopy(self.header)
        unknown["policy"] = {"allow": "all"}
        cases.append((unknown, "invalid_archive"))
        with mock.patch.object(module, "_derive", side_effect=AssertionError("KDF ran")):
            for header, code in cases:
                self.error(code, decrypt, frame(header, self.ciphertext), PASSWORD, policy=POLICY)

    def test_header_json_is_strict_and_canonical_before_kdf(self):
        canonical = module._canonical(self.header)
        cases = [b"\xff", b"{}", canonical.replace(b'"schema":1', b'"schema":1,"schema":1'),
                 canonical.replace(b'"schema":1', b'"schema":NaN'),
                 json.dumps(self.header, indent=1).encode(), canonical + b" "]
        with mock.patch.object(module, "_derive", side_effect=AssertionError("KDF ran")):
            for raw in cases:
                archive = module.MAGIC + struct.pack(">H", len(raw)) + raw + self.ciphertext
                self.error("invalid_archive", decrypt, archive, PASSWORD, policy=POLICY)

    def test_policy_is_mandatory_and_deny_by_default(self):
        with self.assertRaises(TypeError):
            encrypt(payload(), PASSWORD)
        self.error("policy_violation", encrypt, payload(), PASSWORD, policy=None)
        self.error("policy_violation", decrypt, self.archive, PASSWORD, policy=ArchivePolicy())
        self.error("policy_violation", encrypt, payload(), PASSWORD, policy=ArchivePolicy())
        with self.assertRaises(FrozenInstanceError):
            POLICY.credential_names = ()

    def test_namespace_prefix_must_end_on_an_explicit_boundary(self):
        policy = ArchivePolicy(credential_prefixes=("MXMOD/",))
        value = payload()
        value["files"] = []
        value["credentials"] = [{"name": "MXMOD/synthetic", "blob": b64(b"fake") }]
        self.assertEqual(value, validate_payload(value, policy=policy))
        for name in ("MXMOD-other", "MXMOD", "MXMOD/", "OTHER/synthetic"):
            value["credentials"][0]["name"] = name
            self.error("policy_violation", validate_payload, value, policy=policy)

    def test_broad_or_unsafe_policies_are_rejected(self):
        cases = [{"credential_prefixes": ("",)}, {"credential_prefixes": ("*",)},
                 {"credential_prefixes": ("/",)}, {"credential_prefixes": ("MXMOD",)},
                 {"credential_names": ["synthetic"]},
                 {"credential_names": ("a", "A")}, {"allowed_file_paths": ("../outside",)},
                 {"allowed_file_paths": ("a", "A")},
                 {"credential_name_patterns": (".*",)},
                 {"credential_name_patterns": (r"auth_(a+)+@MXMOD",)},
                 {"credential_name_patterns": (r"auth_\d+@MXMOD",)},
                 {"file_path_patterns": (r"[1-9][0-9]{0,19}",)},
                 {"file_path_patterns": (r"../auth_[1-9][0-9]{0,19}",)},
                 {"file_path_patterns": (r"auth.[1-9][0-9]{0,19}",)}]
        for fields in cases:
            self.error("policy_violation", ArchivePolicy, **fields)

    def test_dynamic_id_patterns_are_narrow_and_bounded_uint64(self):
        policy = ArchivePolicy(
            credential_name_patterns=(r"synthetic_auth_[1-9][0-9]{0,19}@MXMOD",),
            file_path_patterns=(r"auth_[1-9][0-9]{0,19}",))
        for account_id in ("1", "9223372036854775807", "18446744073709551615"):
            value = payload()
            value["credentials"][0]["name"] = "synthetic_auth_" + account_id + "@MXMOD"
            value["files"][0]["path"] = "auth_" + account_id
            self.assertEqual(value, validate_payload(value, policy=policy))
        for account_id in ("0", "01", "-1", "18446744073709551616", "9" * 21):
            value = payload()
            value["credentials"][0]["name"] = "synthetic_auth_" + account_id + "@MXMOD"
            self.error("policy_violation", validate_payload, value, policy=policy)
        value = payload()
        value["credentials"][0]["name"] += "EVIL"
        self.error("policy_violation", validate_payload, value, policy=policy)

    def test_dynamic_patterns_support_escaped_literal_dot_and_19_digit_limit(self):
        policy = ArchivePolicy(credential_name_patterns=(r"synthetic\.auth_[1-9][0-9]{0,18}@MXMOD",))
        value = payload()
        value["files"] = []
        value["credentials"][0]["name"] = "synthetic.auth_123@MXMOD"
        self.assertEqual(value, validate_payload(value, policy=policy))
        value["credentials"][0]["name"] = "syntheticXauth_123@MXMOD"
        self.error("policy_violation", validate_payload, value, policy=policy)

    def test_decrypt_rechecks_receiver_policy_after_authentication(self):
        policy = ArchivePolicy(credential_names=POLICY.credential_names)
        self.error("policy_violation", decrypt, self.archive, PASSWORD, policy=policy)

    def test_payload_schema_is_exact_and_client_version_pinned(self):
        cases = []
        for key, value in [("schema", True), ("schema", 2), ("credentials", ()),
                           ("files", None), ("sourceVersion", 26351)]:
            changed = payload()
            changed[key] = value
            cases.append(changed)
        for key in ("files", "credentials"):
            changed = payload()
            del changed[key]
            cases.append(changed)
        changed = payload()
        changed["policy"] = "allow-all"
        cases.append(changed)
        for value in cases:
            self.error("invalid_payload", validate_payload, value, policy=POLICY)
        changed = payload()
        changed["sourceVersion"] = "27.0.0"
        self.error("unsupported_version", validate_payload, changed, policy=POLICY)

    def test_record_schemas_duplicate_names_and_unknown_names(self):
        cases = []
        for field, extra in [("credentials", {"type": "generic"}), ("files", {"type": "symlink"})]:
            value = payload()
            value[field][0].update(extra)
            cases.append(value)
        for field in ("credentials", "files"):
            value = payload()
            value[field].append(copy.deepcopy(value[field][0]))
            cases.append(value)
        for value in cases:
            self.error("invalid_payload", validate_payload, value, policy=POLICY)
        changed = payload()
        changed["credentials"][0]["name"] = "unrelated-app-credential"
        self.error("policy_violation", validate_payload, changed, policy=POLICY)

    def test_path_traversal_absolute_ads_device_names_and_backslashes_rejected(self):
        cases = ["../outside", "a/../../outside", "/auth_7", "//server/share", "C:/auth_7",
                 "a\\auth_7", "auth_7:stream", "a//b", "a/./b", "a/../b", "auth_7.",
                 "auth_7 ", "auth_7/", "NUL", "a/CON.txt", "a/%2e%2e/b", "a/\x00b"]
        for path in cases:
            value = payload()
            value["files"][0]["path"] = path
            self.error("invalid_payload", validate_payload, value, policy=POLICY)
        value = payload()
        value["files"][0]["path"] = "other-safe-file"
        self.error("policy_violation", validate_payload, value, policy=POLICY)

    def test_base64_is_strict_canonical_and_bounded(self):
        for value in [None, b"AAAA", "?", "AA", "AA==\n", "AB==", "я", "A==="]:
            changed = payload()
            changed["credentials"][0]["blob"] = value
            self.error("invalid_payload", validate_payload, changed, policy=POLICY)
        changed = payload()
        changed["credentials"][0]["blob"] = b64(b"x" * (module.MAX_SECRET_BYTES + 1))
        self.error("too_large", validate_payload, changed, policy=POLICY)

    def test_aggregate_decoded_secret_limit_includes_files_and_credentials(self):
        changed = payload()
        changed["credentials"][0]["blob"] = b64(b"x" * module.MAX_SECRET_BYTES)
        changed["files"] = []
        self.assertEqual(changed, validate_payload(changed, policy=POLICY))
        changed["files"] = [{"path": "auth_7", "data": b64(b"y")}]
        self.error("too_large", validate_payload, changed, policy=POLICY)

    def test_record_count_limits(self):
        names = tuple("synthetic_" + str(index) for index in range(256))
        files = tuple("synthetic_" + str(index) for index in range(128))
        policy = ArchivePolicy(credential_names=names, allowed_file_paths=files)
        value = payload()
        value["credentials"] = [{"name": name, "blob": ""} for name in names]
        value["files"] = [{"path": path, "data": ""} for path in files]
        self.assertEqual(value, validate_payload(value, policy=policy))
        too_many = copy.deepcopy(value)
        too_many["credentials"].append({"name": "extra", "blob": ""})
        self.error("too_large", validate_payload, too_many, policy=policy)
        too_many = copy.deepcopy(value)
        too_many["files"].append({"path": "extra", "data": ""})
        self.error("too_large", validate_payload, too_many, policy=policy)

    def test_authenticated_payload_json_rejects_duplicates_nan_invalid_utf8(self):
        data = module._canonical(payload())
        cases = [b"\xff", b"{", data.replace(b'"schema":1', b'"schema":1,"schema":1'),
                 data.replace(b'"schema":1', b'"schema":NaN'),
                 b"[" * 2000 + b"]" * 2000]
        for raw in cases:
            self.error("invalid_payload", decrypt, self.seal_plaintext(raw), PASSWORD, policy=POLICY)

    def test_authenticated_payload_path_and_schema_are_not_trusted(self):
        value = payload()
        value["files"][0]["path"] = "../outside"
        self.error("invalid_payload", decrypt, self.seal_plaintext(module._canonical(value)),
                   PASSWORD, policy=POLICY)
        value = payload()
        value["files"][0]["type"] = "symlink"
        self.error("invalid_payload", decrypt, self.seal_plaintext(module._canonical(value)),
                   PASSWORD, policy=POLICY)

    def test_returned_payload_is_an_independent_validated_copy(self):
        original = payload()
        clean = validate_payload(original, policy=POLICY)
        clean["credentials"].clear()
        self.assertEqual(1, len(original["credentials"]))

    def test_module_never_calls_filesystem_or_credential_apis(self):
        with mock.patch("builtins.open", side_effect=AssertionError("file access")), \
                mock.patch("os.open", side_effect=AssertionError("file access")), \
                mock.patch("os.listdir", side_effect=AssertionError("directory access")):
            archive = encrypt(payload(), PASSWORD, policy=POLICY)
            self.assertEqual(payload(), decrypt(archive, PASSWORD, policy=POLICY))

    def test_missing_crypto_fails_closed(self):
        with mock.patch.object(module, "Scrypt", None):
            self.error("crypto_unavailable", encrypt, payload(), PASSWORD, policy=POLICY)
            self.error("crypto_unavailable", decrypt, self.archive, PASSWORD, policy=POLICY)

    def test_error_messages_accept_only_fixed_codes(self):
        with self.assertRaises(ValueError):
            SessionArchiveError("arbitrary sensitive contents")
        self.assertEqual("invalid_archive", str(SessionArchiveError("invalid_archive")))


if __name__ == "__main__":
    unittest.main()
