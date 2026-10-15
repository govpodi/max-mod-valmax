"""Pure BytesIO fixtures only. No profile, filesystem, WinCred or MAX reads."""
import base64
from contextlib import contextmanager
import copy
import io
import json
import os
import struct
import unittest
from unittest.mock import patch

import mdata_archive as module
from mdata_archive import (MDataArchiveError, MDataPolicy, decrypt_stream,
                           encrypt_stream, valid_file_path, validate_manifest)
from session_archive import ArchivePolicy

PASSWORD = "Synthetic-MData-Password-123"
NAME = "access_token_42@MXMOD"


def make_manifest(files):
    return {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
            "credentials": [{"name": NAME, "blob": base64.b64encode(b"SYNTHETIC-NOT-REAL").decode()}],
            "files": [{"path": path, "size": len(data)} for path, data in files.items()]}


def make_policy(paths):
    return MDataPolicy(ArchivePolicy(credential_names=(NAME,)), lambda path: path in paths)


def frame_parts(archive):
    size = struct.unpack_from(">H", archive, len(module.MAGIC))[0]
    prefix = len(module.MAGIC) + 2 + size
    header = json.loads(archive[len(module.MAGIC) + 2:prefix])
    result, position = [], prefix
    while position < len(archive):
        length, flag = struct.unpack_from(">IB", archive, position)
        end = position + 5 + length + 16
        result.append(archive[position:end])
        position = end
    return header, archive[:prefix], result


class BoundedRead:
    def __init__(self, data, maximum=None):
        self.stream = io.BytesIO(data)
        self.maximum = maximum
        self.calls = []

    def read(self, size):
        assert 0 <= size <= module.CHUNK_BYTES
        self.calls.append(size)
        return self.stream.read(min(size, self.maximum) if self.maximum else size)


class MDataArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = {"user42/История.db": b"SYNTHETIC-DATABASE\x00" * 125000,
                     "settings/привет%.ini": "Только искусственный пример".encode(),
                     "user42/пустой.txt": b""}
        cls.manifest = make_manifest(cls.files)
        cls.policy = make_policy(cls.files)
        cls.archive, cls.summary = cls.encrypt(cls.manifest, cls.files, cls.policy)

    @staticmethod
    def encrypt(manifest, files, policy, source=None, **options):
        output = io.BytesIO()
        if source is None:
            source = lambda path: io.BytesIO(files[path])
        result = encrypt_stream(manifest, PASSWORD, source, output, policy=policy, **options)
        return output.getvalue(), result

    @staticmethod
    def decrypt(archive, policy, maximum=None):
        staging, opened = {}, []
        @contextmanager
        def sink(path, size):
            opened.append((path, size))
            output = io.BytesIO()
            yield output
            staging[path] = output.getvalue()
        manifest = decrypt_stream(BoundedRead(archive, maximum), PASSWORD, sink, policy=policy)
        return manifest, staging, opened

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(MDataArchiveError) as raised:
            function(*args, **kwargs)
        self.assertEqual(code, raised.exception.code)
        self.assertEqual(code, str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def decrypt_error(self, code, archive, *, password=PASSWORD, policy=None):
        @contextmanager
        def sink(_path, _size):
            yield io.BytesIO()
        self.error(code, decrypt_stream, io.BytesIO(archive), password, sink,
                   policy=self.policy if policy is None else policy)

    def altered_header(self, change):
        header, _prefix, frames = frame_parts(self.archive)
        change(header)
        raw = module._canonical(header)
        return module.MAGIC + struct.pack(">H", len(raw)) + raw + b"".join(frames)

    def sealed_plaintext(self, data):
        """Authenticated malformed fixture, fresh random nonce prefix each time."""
        header, _aad, _frames = frame_parts(self.archive)
        salt, prefix = os.urandom(16), os.urandom(8)
        header["kdf"]["salt"] = base64.b64encode(salt).decode()
        header["noncePrefix"] = base64.b64encode(prefix).decode()
        header["plaintextBytes"] = len(data)
        raw = module._canonical(header)
        aad = module.MAGIC + struct.pack(">H", len(raw)) + raw
        output = io.BytesIO()
        cipher = module._cipher(module._derive(PASSWORD.encode(), salt))
        writer = module._ChunkWriter(output, cipher, prefix, aad, len(data))
        writer.feed(data)
        writer.finish()
        return output.getvalue()

    def test_multichunk_round_trip_unicode_binary_and_empty_files(self):
        manifest, files, opened = self.decrypt(self.archive, self.policy)
        self.assertEqual(self.manifest, manifest)
        self.assertEqual(self.files, files)
        self.assertEqual(len(self.files), len(opened))
        self.assertGreater(self.summary["dataChunks"], 1)
        self.assertEqual(len(self.archive), self.summary["archiveBytes"])
        self.assertEqual(sum(map(len, self.files.values())), self.summary["fileBytes"])
        self.assertEqual({"schema", "files", "fileBytes", "dataChunks", "archiveBytes"}, set(self.summary))

    def test_archive_kind_requires_an_exact_supported_bytes_prefix(self):
        self.assertEqual("profile", module.archive_kind(module.MAGIC))
        self.assertEqual("login", module.archive_kind(module.LOGIN_MAGIC))
        for prefix in (b"", module.MAGIC[:-1], module.LOGIN_MAGIC + b"extra",
                       b"MXMODMD\x03", b"UNKNOWN\x02", None, "MXMODMD\x02",
                       bytearray(module.LOGIN_MAGIC), memoryview(module.LOGIN_MAGIC)):
            with self.subTest(prefix_type=type(prefix).__name__):
                self.error("invalid_archive", module.archive_kind, prefix)

    def test_default_encryption_is_unchanged_profile_v1(self):
        files = {"synthetic.bin": b"SYNTHETIC"}
        manifest, policy = make_manifest(files), make_policy(files)
        with patch.object(module.os, "urandom", side_effect=[b"s" * 16, b"n" * 8]):
            default, default_counts = self.encrypt(manifest, files, policy)
        with patch.object(module.os, "urandom", side_effect=[b"s" * 16, b"n" * 8]):
            explicit, explicit_counts = self.encrypt(manifest, files, policy, archive_kind="profile")
        self.assertEqual(b"MXMODMD\x01", module.MAGIC)
        self.assertTrue(default.startswith(module.MAGIC))
        self.assertEqual(default, explicit)
        self.assertEqual(default_counts, explicit_counts)
        self.assertEqual((manifest, files), self.decrypt(default, policy)[:2])

    def test_login_v2_round_trip_keeps_manifest_and_counts_schema(self):
        files = {"synthetic-login.json": b"SYNTHETIC-NOT-REAL"}
        manifest, policy = make_manifest(files), make_policy(files)
        archive, counts = self.encrypt(manifest, files, policy, archive_kind="login")
        self.assertEqual(b"MXMODMD\x02", module.LOGIN_MAGIC)
        self.assertTrue(archive.startswith(module.LOGIN_MAGIC))
        self.assertEqual("login", module.archive_kind(archive[:len(module.MAGIC)]))
        self.assertEqual((manifest, files), self.decrypt(archive, policy)[:2])
        self.assertEqual({"schema", "files", "fileBytes", "dataChunks", "archiveBytes"}, set(counts))
        self.assertEqual(len(archive), counts["archiveBytes"])
        self.assertEqual(set(frame_parts(self.archive)[0]), set(frame_parts(archive)[0]))

    def test_archive_kind_tampering_is_authenticated_before_callbacks(self):
        files = {"synthetic-login.json": b"SYNTHETIC-NOT-REAL"}
        manifest, policy = make_manifest(files), make_policy(files)
        def unexpected(*_args):
            self.fail("Tampered format must not run preflight or open staging")
        for original, replacement in (("profile", module.LOGIN_MAGIC), ("login", module.MAGIC)):
            archive, _counts = self.encrypt(manifest, files, policy, archive_kind=original)
            changed = replacement + archive[len(module.MAGIC):]
            self.error("authentication_failed", decrypt_stream, io.BytesIO(changed), PASSWORD,
                       unexpected, policy=policy, preflight=unexpected)

    def test_login_v2_wrong_password_is_rejected_before_callbacks(self):
        files = {"synthetic-login.json": b"SYNTHETIC-NOT-REAL"}
        manifest, policy = make_manifest(files), make_policy(files)
        archive, _counts = self.encrypt(manifest, files, policy, archive_kind="login")
        def unexpected(*_args):
            self.fail("Wrong password must not run preflight or open staging")
        self.error("authentication_failed", decrypt_stream, io.BytesIO(archive),
                   "Different-Synthetic-Password", unexpected, policy=policy, preflight=unexpected)

    def test_invalid_encryption_kind_is_rejected_without_output_or_source(self):
        def unexpected(*_args):
            self.fail("Invalid format must not open sources or run crypto")
        for kind in ("", "other", None, True, [], {}, b"login"):
            output = io.BytesIO()
            with self.subTest(kind_type=type(kind).__name__), \
                    patch.object(module, "_derive", side_effect=unexpected):
                self.error("policy_violation", encrypt_stream, self.manifest, PASSWORD,
                           unexpected, output, policy=self.policy, archive_kind=kind)
            self.assertEqual(b"", output.getvalue())

    def test_malformed_archive_magic_is_rejected_before_kdf(self):
        with patch.object(module, "_derive", side_effect=AssertionError("KDF ran")):
            for prefix in (b"MXMODMD\x00", b"MXMODMD\x03", b"UNKNOWN\x02"):
                self.decrypt_error("invalid_archive", prefix + self.archive[len(module.MAGIC):])

    def test_expected_kind_round_trips_both_authenticated_formats(self):
        files = {"synthetic-login.json": b"SYNTHETIC-NOT-REAL"}
        manifest, policy = make_manifest(files), make_policy(files)
        for kind in ("profile", "login"):
            archive, _counts = self.encrypt(manifest, files, policy, archive_kind=kind)
            staged = {}
            @contextmanager
            def sink(path, _size):
                output = io.BytesIO()
                yield output
                staged[path] = output.getvalue()
            restored = decrypt_stream(io.BytesIO(archive), PASSWORD, sink, policy=policy,
                                      expected_kind=kind)
            self.assertEqual(manifest, restored)
            self.assertEqual(files, staged)

    def test_expected_kind_mismatch_precedes_crypto_preflight_and_sinks(self):
        files = {"synthetic-login.json": b"SYNTHETIC-NOT-REAL"}
        manifest, policy = make_manifest(files), make_policy(files)
        def unexpected(*_args):
            self.fail("Unexpected archive kind must fail before crypto or staging")
        for kind, expected in (("profile", "login"), ("login", "profile")):
            archive, _counts = self.encrypt(manifest, files, policy, archive_kind=kind)
            with patch.object(module, "_derive", side_effect=unexpected):
                self.error("invalid_archive", decrypt_stream, io.BytesIO(archive), PASSWORD,
                           unexpected, policy=policy, preflight=unexpected,
                           expected_kind=expected)

    def test_invalid_expected_kind_is_rejected_before_source_read(self):
        class ForbiddenRead:
            def read(self, _size):
                raise AssertionError("Invalid expected kind must fail before source reads")
        def unexpected(*_args):
            self.fail("Invalid expected kind must not invoke callbacks")
        for kind in ("", "other", True, [], {}, b"login"):
            with self.subTest(kind_type=type(kind).__name__):
                self.error("policy_violation", decrypt_stream, ForbiddenRead(), PASSWORD,
                           unexpected, policy=self.policy, preflight=unexpected,
                           expected_kind=kind)

    def test_short_reads_and_partial_writes_are_supported(self):
        files = {"данные.bin": b"fake" * 8000}
        manifest, policy = make_manifest(files), make_policy(files)
        class PartialWrite(io.BytesIO):
            def write(self, data):
                return super().write(data[:13])
        output = PartialWrite()
        @contextmanager
        def source(path):
            yield BoundedRead(files[path], maximum=7)
        encrypt_stream(manifest, PASSWORD, source, output, policy=policy)
        restored, results, _opened = self.decrypt(output.getvalue(), policy, maximum=11)
        self.assertEqual(manifest, restored)
        self.assertEqual(files, results)

    def test_randomized_salt_and_nonce_prefix(self):
        other, _summary = self.encrypt(self.manifest, self.files, self.policy)
        first_header, _, _ = frame_parts(self.archive)
        second_header, _, _ = frame_parts(other)
        self.assertNotEqual(self.archive, other)
        self.assertNotEqual(first_header["kdf"]["salt"], second_header["kdf"]["salt"])
        self.assertNotEqual(first_header["noncePrefix"], second_header["noncePrefix"])
        for value in [PASSWORD.encode(), NAME.encode(), "История.db".encode(), b"SYNTHETIC-NOT-REAL"]:
            self.assertNotIn(value, self.archive)

    def test_terminal_is_authenticated_empty_and_uses_next_counter(self):
        header, aad, frames = frame_parts(self.archive)
        self.assertEqual((0, 1), struct.unpack(">IB", frames[-1][:5]))
        self.assertEqual(21, len(frames[-1]))
        self.assertEqual(self.summary["dataChunks"] + 1, len(frames))
        salt, prefix = base64.b64decode(header["kdf"]["salt"]), base64.b64decode(header["noncePrefix"])
        cipher = module._cipher(module._derive(PASSWORD.encode(), salt))
        counter = struct.pack(">I", len(frames) - 1)
        self.assertEqual(b"", cipher.decrypt(prefix + counter, frames[-1][5:], aad + counter + frames[-1][:5]))

    def test_wrong_password_never_opens_a_staging_file(self):
        calls = []
        def sink(*args):
            calls.append(args)
            raise AssertionError("sink must not be opened")
        self.error("authentication_failed", decrypt_stream, io.BytesIO(self.archive),
                   "Different-Synthetic-Password", sink, policy=self.policy)
        self.assertEqual([], calls)

    def test_ciphertext_corruption_never_returns_a_manifest(self):
        _header, prefix, _frames = frame_parts(self.archive)
        for offset in (len(prefix) + 5, len(self.archive) - 1):
            changed = bytearray(self.archive)
            changed[offset] ^= 1
            self.decrypt_error("authentication_failed", bytes(changed))

    def test_truncation_at_chunk_boundary_requires_terminal_and_eof(self):
        _header, prefix, frames = frame_parts(self.archive)
        for shortened in (self.archive[:-1], prefix + b"".join(frames[:-1]),
                          prefix + frames[0], prefix + b"".join(frames[:-2]) + frames[-1]):
            with self.assertRaises(MDataArchiveError):
                self.decrypt(shortened, self.policy)
        self.decrypt_error("invalid_archive", self.archive + b"extra")

    def test_reordered_duplicated_and_spliced_chunks_are_rejected(self):
        _header, prefix, frames = frame_parts(self.archive)
        self.assertGreaterEqual(len(frames), 4)
        cases = [prefix + frames[1] + frames[0] + b"".join(frames[2:]),
                 prefix + frames[0] + b"".join(frames),
                 prefix + b"".join(frames[:-2]) + frames[-1]]
        for archive in cases:
            self.decrypt_error("authentication_failed", archive)

    def test_frame_length_flag_and_empty_data_are_bounded(self):
        _header, prefix, frames = frame_parts(self.archive)
        for length, flag in [(module.CHUNK_BYTES + 1, 0), (1, 2), (1, 1), (0, 0)]:
            archive = prefix + struct.pack(">IB", length, flag) + frames[0][5:] + b"".join(frames[1:])
            self.decrypt_error("invalid_archive", archive)

    def test_header_rejects_untrusted_kdf_before_running_it(self):
        with patch.object(module, "_derive", side_effect=AssertionError("KDF ran")):
            for field, value in [("n", 1 << 40), ("r", 4096), ("p", 2048), ("n", True)]:
                archive = self.altered_header(lambda header: header["kdf"].__setitem__(field, value))
                self.decrypt_error("invalid_archive", archive)

    def test_header_limits_version_and_unknown_keys_before_kdf(self):
        cases = [("chunkBytes", module.CHUNK_BYTES + 1, "invalid_archive"),
                 ("chunkBytes", True, "invalid_archive"),
                 ("plaintextBytes", module.MAX_PLAINTEXT_BYTES + 1, "invalid_archive"),
                 ("plaintextBytes", True, "invalid_archive"),
                 ("sourceVersion", "27.0.0", "unsupported_version"),
                 ("schema", 2, "invalid_archive"), ("policy", {}, "invalid_archive")]
        with patch.object(module, "_derive", side_effect=AssertionError("KDF ran")):
            for field, value, code in cases:
                archive = self.altered_header(lambda header: header.__setitem__(field, value))
                self.decrypt_error(code, archive)
            self.decrypt_error("invalid_archive", b"invalid header")

    def test_changed_well_formed_header_is_authenticated(self):
        archive = self.altered_header(lambda header: header.__setitem__("noncePrefix", base64.b64encode(b"x" * 8).decode()))
        self.decrypt_error("authentication_failed", archive)

    def test_unicode_paths_percent_literal_and_windows_limits(self):
        for path in ["история/база.db", "файлы/привет 👋.txt", "данные/100%.txt", "данные/%2e%2e.txt",
                     "папка/" + "я" * 255]:
            self.assertTrue(valid_file_path(path), path)
        for path in ["../x", "a/../x", "/x", "//host/x", "C:/x", "x:ads", "a\\x", "a//x",
                     "a/./x", "x/", "x.", "x ", "NUL", "dir/CON.txt", "LPT¹.txt", "CON .txt",
                     "a/\x00x", "a/\n", "a/\u0085", "a/\ud800", "я" * 256,
                     "👋" * 128, "/".join(["я" * 255] * 5)]:
            self.assertFalse(valid_file_path(path), repr(path))

    def test_manifest_schema_unknown_fields_symlink_type_and_duplicate_paths(self):
        cases = []
        for field, value in [("schema", True), ("files", None), ("policy", "allow-all")]:
            item = copy.deepcopy(self.manifest)
            item[field] = value
            cases.append(item)
        for extra in ({"type": "symlink"}, {"mode": 0o755}):
            item = copy.deepcopy(self.manifest)
            item["files"][0].update(extra)
            cases.append(item)
        item = copy.deepcopy(self.manifest)
        item["files"].append(dict(item["files"][0]))
        cases.append(item)
        item = copy.deepcopy(self.manifest)
        item["files"][0]["size"] = True
        cases.append(item)
        for item in cases:
            self.error("invalid_payload", validate_manifest, item, policy=self.policy)

    def test_manifest_count_total_bytes_and_size_validation_before_source(self):
        value = make_manifest({})
        value["files"] = [{"path": "file" + str(index), "size": 0} for index in range(module.MAX_FILES + 1)]
        broad = MDataPolicy(self.policy.credential_policy, lambda _path: True)
        self.error("too_large", validate_manifest, value, policy=broad)
        for size, code in [(module.MAX_FILE_BYTES + 1, "too_large"), (-1, "invalid_payload"),
                           (1.5, "invalid_payload")]:
            value["files"] = [{"path": "file", "size": size}]
            self.error(code, validate_manifest, value, policy=broad)
        value["files"] = [{"path": "one", "size": module.MAX_FILE_BYTES}, {"path": "two", "size": 1}]
        self.error("too_large", validate_manifest, value, policy=broad)

    def test_4gib_manifest_size_boundary_without_allocating_4gib(self):
        value = make_manifest({})
        value["files"] = [{"path": "synthetic-large.db", "size": module.MAX_FILE_BYTES}]
        policy = make_policy({"synthetic-large.db"})
        self.assertEqual(value, validate_manifest(value, policy=policy))
        self.error("source_changed", self.encrypt, value, {"synthetic-large.db": b""}, policy)

    def test_manifest_size_budget_rejected(self):
        value = make_manifest({})
        value["files"] = [{"path": f"file{index}/" + "я" * 200 + "/" + "я" * 200 + "/" + "я" * 200,
                           "size": 0} for index in range(8000)]
        policy = MDataPolicy(self.policy.credential_policy, lambda _path: True)
        self.error("too_large", validate_manifest, value, policy=policy)

    def test_policy_is_required_and_checked_before_opening_sources_or_sinks(self):
        with self.assertRaises(TypeError):
            encrypt_stream(self.manifest, PASSWORD, lambda _path: None, io.BytesIO())
        rejecting = MDataPolicy(self.policy.credential_policy, lambda _path: False)
        self.error("policy_violation", validate_manifest, self.manifest, policy=rejecting)
        calls = []
        def forbidden(*args):
            calls.append(args)
            raise AssertionError("callback opened")
        self.error("policy_violation", encrypt_stream, self.manifest, PASSWORD, forbidden,
                   io.BytesIO(), policy=rejecting)
        self.error("policy_violation", decrypt_stream, io.BytesIO(self.archive), PASSWORD,
                   forbidden, policy=rejecting)
        self.assertEqual([], calls)

    def test_short_or_growing_source_fails_without_complete_terminal(self):
        for source_data in [b"short", b"123456789EXTRA"]:
            files = {"fake.db": source_data}
            value = make_manifest({"fake.db": b"123456789"})
            self.error("source_changed", self.encrypt, value, files, make_policy(files))

    def test_empty_profile_round_trip_requires_terminal_too(self):
        files = {}
        value = make_manifest(files)
        policy = make_policy(files)
        archive, _counts = self.encrypt(value, files, policy)
        self.assertEqual(value, self.decrypt(archive, policy)[0])
        self.decrypt_error("invalid_archive", archive[:-21], policy=policy)

    def test_authenticated_manifest_declared_file_bytes_must_match_header(self):
        value = make_manifest({"fake.db": b"123"})
        raw = module._canonical(value)
        archive = self.sealed_plaintext(struct.pack(">I", len(raw)) + raw + b"12")
        self.decrypt_error("invalid_archive", archive, policy=make_policy({"fake.db"}))

    def test_authenticated_manifest_limit_duplicate_keys_and_invalid_utf8(self):
        for raw in [b"\xff\xff", b'{"schema":1,"schema":1}', b'{"schema":NaN}']:
            archive = self.sealed_plaintext(struct.pack(">I", len(raw)) + raw)
            self.decrypt_error("invalid_payload", archive)
        archive = self.sealed_plaintext(struct.pack(">I", module.MAX_MANIFEST_BYTES + 1) + b"{}")
        self.decrypt_error("too_large", archive)

    def test_staging_is_not_committed_if_only_terminal_is_corrupted(self):
        corrupt = self.archive[:-1] + bytes([self.archive[-1] ^ 1])
        staged, committed = {}, {}
        @contextmanager
        def sink(path, _size):
            value = io.BytesIO()
            yield value
            staged[path] = value.getvalue()
        try:
            manifest = decrypt_stream(io.BytesIO(corrupt), PASSWORD, sink, policy=self.policy)
            committed.update(staged)
            self.fail("Corrupt terminal must not return a manifest")
        except MDataArchiveError as error:
            self.assertEqual("authentication_failed", error.code)
            staged.clear()  # The controller's mandatory staging abort contract.
        self.assertEqual({}, staged)
        self.assertEqual({}, committed)

    def test_callback_errors_have_fixed_messages(self):
        def bad_source(_path):
            raise OSError("sensitive synthetic filename/password")
        self.error("io_error", encrypt_stream, self.manifest, PASSWORD, bad_source,
                   io.BytesIO(), policy=self.policy)
        def bad_sink(_path, _size):
            raise OSError("sensitive synthetic file content")
        self.error("io_error", decrypt_stream, io.BytesIO(self.archive), PASSWORD,
                   bad_sink, policy=self.policy)

    def test_zero_write_wrong_read_type_and_crypto_unavailable_fail_closed(self):
        class ZeroWrite:
            def write(self, _data):
                return 0
        self.error("io_error", encrypt_stream, self.manifest, PASSWORD,
                   lambda path: io.BytesIO(self.files[path]), ZeroWrite(), policy=self.policy)
        class WrongRead:
            def read(self, _size):
                return None
        self.error("io_error", decrypt_stream, WrongRead(), PASSWORD,
                   lambda _path, _size: io.BytesIO(), policy=self.policy)
        with patch.object(module._session, "Scrypt", None):
            self.error("crypto_unavailable", self.encrypt, self.manifest, self.files, self.policy)

    def test_no_filesystem_apis_called(self):
        files = {"синтетика.bin": b"ONLY-SYNTHETIC"}
        value, policy = make_manifest(files), make_policy(files)
        with patch("builtins.open", side_effect=AssertionError("file access")), \
                patch("os.open", side_effect=AssertionError("file access")), \
                patch("os.listdir", side_effect=AssertionError("directory access")):
            archive, _counts = self.encrypt(value, files, policy)
            self.assertEqual(files, self.decrypt(archive, policy)[1])

    def test_error_class_and_policy_repr_do_not_accept_or_expose_secrets(self):
        with self.assertRaises(ValueError):
            MDataArchiveError("sensitive arbitrary text")
        self.assertNotIn(NAME, repr(self.policy))

    def test_trusted_credential_callback_accepts_only_explicit_own_web_grammar(self):
        web_name = "synthetic-web-123_81@MXMOD"
        value = make_manifest({})
        value["credentials"].append({"name": web_name, "blob": "eA=="})
        calls = []
        def trusted(name):
            calls.append(name)
            return name in {NAME, web_name}
        policy = MDataPolicy(ArchivePolicy(), lambda _path: False, trusted)
        clean = validate_manifest(value, policy=policy)
        self.assertEqual(value, clean)
        self.assertEqual([NAME, web_name], calls)
        archive, _counts = self.encrypt(value, {}, policy)
        self.assertEqual(value, self.decrypt(archive, policy)[0])
        # The pre-existing default policy has not gained a dynamic exception.
        self.error("policy_violation", validate_manifest, value, policy=make_policy({}))

    def test_credential_callback_rejection_exception_and_truthy_value_fail_closed(self):
        value = make_manifest({})
        for validator in (lambda _name: False, lambda _name: 1):
            self.error("policy_violation", validate_manifest, value,
                       policy=MDataPolicy(ArchivePolicy(), lambda _path: False, validator))
        def broken(_name):
            raise RuntimeError("sensitive synthetic credential")
        self.error("policy_violation", validate_manifest, value,
                   policy=MDataPolicy(ArchivePolicy(), lambda _path: False, broken))

    def test_credential_callback_never_receives_invalid_or_non_ascii_names(self):
        for name, code in [("ключ@MXMOD", "unsupported_credential"),
                           ("synthetic\x00@MXMOD", "invalid_payload"),
                           ("synthetic\ud800@MXMOD", "invalid_payload"),
                           ("x" * 257, "invalid_payload")]:
            calls = []
            policy = MDataPolicy(ArchivePolicy(), lambda _path: False,
                                 lambda value: calls.append(value) or True)
            value = make_manifest({})
            value["credentials"][0]["name"] = name
            self.error(code, validate_manifest, value, policy=policy)
            self.assertEqual([], calls)

    def test_credential_callback_keeps_duplicate_count_and_blob_size_limits(self):
        policy = MDataPolicy(ArchivePolicy(), lambda _path: False, lambda _name: True)
        value = make_manifest({})
        value["credentials"].append(dict(value["credentials"][0]))
        self.error("invalid_payload", validate_manifest, value, policy=policy)
        value["credentials"] = [{"name": "synthetic_" + str(index), "blob": "eA=="}
                                for index in range(257)]
        self.error("too_large", validate_manifest, value, policy=policy)
        value = make_manifest({})
        value["credentials"][0]["blob"] = base64.b64encode(b"x" * (4 * 1024 * 1024 + 1)).decode()
        self.error("too_large", validate_manifest, value, policy=policy)

    def test_dynamic_credential_callback_must_come_from_trusted_policy(self):
        value = make_manifest({})
        value["credential_name_validator"] = "allow-all"
        self.error("invalid_payload", validate_manifest, value, policy=self.policy)
        self.error("policy_violation", MDataPolicy, ArchivePolicy(), lambda _path: False, "allow-all")

    def test_preflight_receives_only_verified_counts_before_every_sink(self):
        events = []
        def preflight(total, count):
            events.append(("preflight", total, count))
        @contextmanager
        def sink(path, size):
            self.assertEqual(events[0], ("preflight", sum(map(len, self.files.values())), len(self.files)))
            events.append(("sink", size))
            yield io.BytesIO()
        result = decrypt_stream(io.BytesIO(self.archive), PASSWORD, sink, policy=self.policy, preflight=preflight)
        self.assertEqual(result, self.manifest)
        self.assertEqual(sum(event[0] == "preflight" for event in events), 1)

    def test_preflight_refusal_propagates_unchanged_without_any_sink(self):
        refusal = RuntimeError("synthetic capacity refusal")
        def preflight(_total, _count):
            raise refusal
        def sink(*_args):
            self.fail("Plaintext sink opened before capacity refusal")
        with self.assertRaises(RuntimeError) as caught:
            decrypt_stream(io.BytesIO(self.archive), PASSWORD, sink, policy=self.policy, preflight=preflight)
        self.assertIs(caught.exception, refusal)

    def test_password_failure_precedes_space_callback(self):
        def unexpected(*_args):
            self.fail("Wrong password must not check space or open sinks")
        self.error("authentication_failed", decrypt_stream, io.BytesIO(self.archive), "Wrong-password-123",
                   unexpected, policy=self.policy, preflight=unexpected)

    def test_empty_archive_still_checks_space_once(self):
        manifest, files = make_manifest({}), {}
        policy = make_policy(files)
        archive, _ = self.encrypt(manifest, files, policy)
        calls = []
        result = decrypt_stream(io.BytesIO(archive), PASSWORD, lambda *_args: self.fail("Empty sink"),
                                policy=policy, preflight=lambda total, count: calls.append((total, count)))
        self.assertEqual(calls, [(0, 0)])
        self.assertEqual(result, manifest)

    def test_capacity_callback_does_not_bypass_terminal_authentication(self):
        @contextmanager
        def sink(*_args):
            yield io.BytesIO()
        calls = []
        self.error("invalid_archive", decrypt_stream, io.BytesIO(self.archive[:-1]), PASSWORD, sink,
                   policy=self.policy, preflight=lambda total, count: calls.append((total, count)))
        self.assertEqual(len(calls), 1)

    def test_late_disk_full_errors_remain_insufficient_space(self):
        errors = [OSError(28, "synthetic disk full"), OSError(5, "synthetic Windows disk full")]
        errors[1].winerror = 112
        for error in errors:
            class FullOutput:
                def write(self, _data):
                    raise error
            self.error("insufficient_space", encrypt_stream, self.manifest, PASSWORD,
                       lambda path: io.BytesIO(self.files[path]), FullOutput(), policy=self.policy)
        @contextmanager
        def full_sink(*_args):
            raise OSError(28, "synthetic disk full")
            yield
        self.error("insufficient_space", decrypt_stream, io.BytesIO(self.archive), PASSWORD,
                   full_sink, policy=self.policy)


if __name__ == "__main__":
    unittest.main()
