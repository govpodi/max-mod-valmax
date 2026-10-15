"""Credential policy/CAS tests with mocked native adapter; no Windows calls."""
import ctypes
from ctypes import wintypes
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import mdata_credentials as module
import session_credentials as native
from session_credentials import CredentialError


class MDataCredentialTests(unittest.TestCase):
    def setUp(self):
        self.api = patch("session_credentials._api", side_effect=AssertionError("Real WinCred forbidden"))
        self.api.start()
        self.addCleanup(self.api.stop)

    def test_six_native_key_kinds_and_strict_namespace_boundary(self):
        expected = {key + "_42@MXMOD" for key in module.ACCOUNT_KEYS} | set(module.COMMON_NAMES)
        self.assertEqual(len(expected), 6)
        self.assertEqual(set(module.targets({"42"})), expected)
        for name in expected:
            self.assertTrue(module.allowed_name(name))
            self.assertEqual(module.owner_id(name), None if "_Common@" in name else "42")
        bad = [None, 42, "access_token_42@ONEME", "MXMOD_access_token_42",
               "access_token_042@MXMOD", "access_token_0@MXMOD", "access_token_-1@MXMOD",
               "access_token_9223372036854775808@MXMOD", "access_token_42@mxmod",
               "access_token_42@MXMOD.extra", "access_token_42@MXMOD\n", "unknown_42@MXMOD"]
        for name in bad:
            with self.subTest(name=name):
                self.assertFalse(module.allowed_name(name))
                with self.assertRaises(CredentialError):
                    module.owner_id(name)

    def test_web_xor_ownership_and_zero_xor_component(self):
        for rid, app in [(42, 500), (7, 7), (9223372036854775807, 11)]:
            target = f"synthetic-key-{app}_{rid ^ app}@MXMOD"
            self.assertTrue(module.allowed_name(target))
            self.assertEqual(module.owner_id(target), str(rid))
        for bad in ["key-7_7@MXMOD", "key-0_42@MXMOD", "key-500_9999999999999999999@MXMOD",
                    "key-500_478@ONEME", "ключ-500_478@MXMOD", "key\x00-500_478@MXMOD",
                    "-500_478@MXMOD", "x" * 193 + "-500_478@MXMOD"]:
            with self.subTest(target=bad):
                self.assertFalse(module.allowed_name(bad))

    def test_constructor_and_invalid_names_do_not_call_native_api(self):
        adapter = module.MDataCredentials()
        self.assertNotIn("token-value", repr(adapter))
        for method, args in [(adapter.read, ("access_token_42@ONEME",)),
                             (adapter.set_owned, ("access_token_42@ONEME", b"fixture", None)),
                             (adapter.delete_owned, ("access_token_42@ONEME", b"fixture"))]:
            with self.assertRaises(CredentialError) as raised:
                method(*args)
            self.assertEqual(raised.exception.code, "invalid_payload")

    def test_set_owned_requires_exact_expected_blob_before_native_write(self):
        adapter = module.MDataCredentials()
        name = "history_storage_key_42@MXMOD"
        with patch.object(adapter, "read", return_value=b"OLD\x00") as read, \
             patch.object(adapter, "_write") as write:
            adapter.set_owned(name, b"NEW\x00", b"OLD\x00")
            read.assert_called_once_with(name)
            write.assert_called_once_with(name, b"NEW\x00")
        for current, expected in [(None, b"OLD"), (b"OLD", None), (b"CHANGED", b"OLD")]:
            with self.subTest(current=current, expected=expected), \
                 patch.object(adapter, "read", return_value=current), \
                 patch.object(adapter, "_write") as write:
                with self.assertRaises(CredentialError) as raised:
                    adapter.set_owned(name, b"NEW", expected)
                self.assertEqual(raised.exception.code, "destination_not_empty")
                write.assert_not_called()

    def test_create_missing_and_common_replacement_use_same_cas_contract(self):
        adapter = module.MDataCredentials()
        for name, old in [("access_token_42@MXMOD", None),
                          ("app_data_storage_key_Common@MXMOD", b"SYNTHETIC-OLD")]:
            with self.subTest(name=name), patch.object(adapter, "read", return_value=old), \
                 patch.object(adapter, "_write") as write:
                adapter.set_owned(name, b"SYNTHETIC-NEW", old)
                write.assert_called_once_with(name, b"SYNTHETIC-NEW")

    def test_delete_owned_delegates_expected_blob_without_enumerating(self):
        adapter = module.MDataCredentials()
        name = "synthetic-500_478@MXMOD"
        with patch.object(adapter, "delete_created") as delete:
            adapter.delete_owned(name, b"SYNTHETIC\x00")
            delete.assert_called_once_with(name, b"SYNTHETIC\x00")

    def test_invalid_id_inventory_refused(self):
        for ids in [{"0"}, {"042"}, {"9223372036854775808"}, set(map(str, range(1, 65)))]:
            with self.subTest(ids=ids), self.assertRaises(CredentialError):
                module.targets(ids)

    def inventory_fixture(self, records):
        """All structs/buffers below are newly allocated synthetic memory."""
        class GuardedCredential(native.CREDENTIALW):
            def __getattribute__(self, name):
                if name in ("CredentialBlob", "CredentialBlobSize"):
                    target = super().__getattribute__("TargetName")
                    if target and not target.endswith("@MXMOD"):
                        raise AssertionError("Foreign credential blob field was touched")
                return super().__getattribute__(name)
        entries, buffers, lookup = [], [], {}
        for target, kind, blob in records:
            entry = GuardedCredential()
            buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
            entry.TargetName, entry.Type = target, kind
            entry.CredentialBlob, entry.CredentialBlobSize = buffer, len(blob)
            entries.append(entry)
            buffers.append(buffer)
            lookup[target] = entry
        pointer = ctypes.POINTER(GuardedCredential)
        values = (pointer * len(entries))(*(ctypes.pointer(entry) for entry in entries))
        reads = []
        def enumerate_names(filter_value, flags, count_output, values_output):
            self.assertIsNone(filter_value)
            self.assertEqual(flags, 0)
            ctypes.cast(count_output, ctypes.POINTER(wintypes.DWORD))[0] = len(entries)
            ctypes.cast(values_output, ctypes.POINTER(ctypes.POINTER(pointer)))[0] = values
            return True
        def exact_read(name, kind, flags, output):
            self.assertTrue(module.allowed_name(name))
            reads.append(name)
            ctypes.cast(output, ctypes.POINTER(pointer))[0] = ctypes.pointer(lookup[name])
            return True
        api = SimpleNamespace(CredEnumerateW=Mock(side_effect=enumerate_names),
                              CredReadW=Mock(side_effect=exact_read), CredFree=Mock())
        # Keep the fake OS allocation alive throughout the mocked calls.
        return GuardedCredential, api, reads, (entries, buffers, values)

    def test_metadata_inventory_filters_names_then_reads_only_own_blobs(self):
        own = [(name, 1, ("SYNTHETIC:" + name).encode("ascii"))
               for name in module.targets({"42"})]
        own.append(("web-key-500_478@MXMOD", 1, b"SYNTHETIC-WEB"))
        records = [("access_token_99@ONEME", 1, b"SYNTHETIC-FOREIGN-NEVER-READ"),
                   ("unrelated-app-credential", 1, b"SYNTHETIC-FOREIGN-2"),
                   ("access_token_77@MXMOD", 2, b"SYNTHETIC-NON-GENERIC")] + own
        guarded, api, reads, keepalive = self.inventory_fixture(records)
        with patch.object(native, "CREDENTIALW", guarded), patch.object(native, "_api", return_value=api):
            adapter = module.MDataCredentials()
            names = adapter.owned_names()
            self.assertEqual(names, tuple(sorted(name for name, _, _ in own)))
            self.assertEqual(reads, [])
            self.assertEqual(api.CredFree.call_count, 1)
            result = {name: adapter.read(name) for name in names}
        self.assertEqual(result, {name: blob for name, _, blob in own})
        self.assertEqual(reads, list(names))
        self.assertEqual(api.CredFree.call_count, 1 + len(own))
        self.assertTrue(keepalive)

    def test_unknown_owned_name_inventory_fails_closed_and_frees_allocation(self):
        records = [("unknown_42@MXMOD", 1, b"SYNTHETIC-UNSUPPORTED")]
        guarded, api, reads, _ = self.inventory_fixture(records)
        with patch.object(native, "CREDENTIALW", guarded), patch.object(native, "_api", return_value=api):
            with self.assertRaises(CredentialError) as raised:
                module.MDataCredentials().owned_names()
        self.assertEqual(raised.exception.code, "invalid_payload")
        self.assertEqual(reads, [])
        api.CredFree.assert_called_once()

    def test_case_alias_inventory_fails_closed_before_exact_read(self):
        # WinCred names are case-insensitive; duplicate canonical ownership
        # must never produce two differently interpreted import records.
        records = [("key-500_478@MXMOD", 1, b"SYNTHETIC-A"),
                   ("KEY-500_478@MXMOD", 1, b"SYNTHETIC-B")]
        guarded, api, reads, _ = self.inventory_fixture(records)
        with patch.object(native, "CREDENTIALW", guarded), patch.object(native, "_api", return_value=api):
            with self.assertRaises(CredentialError) as raised:
                module.MDataCredentials().owned_names()
        self.assertEqual(raised.exception.code, "invalid_payload")
        self.assertEqual(reads, [])
        api.CredFree.assert_called_once()


if __name__ == "__main__":
    unittest.main()
