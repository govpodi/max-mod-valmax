"""Pinned account-reader generation only; no profile/credential/process access."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "native/tooling"), str(ROOT / "modkit")]
import account_preferences_ui as legacy
import build_native_qml as native

DECODED = Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi")
PREFIX = ":/qt/qml/OneMeUi/"


class NativeAccountReaders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Missing pinned input is an error, never a skipped passing proof.
        original = {name: (DECODED / name).read_text(encoding="utf8")
                    for name in (*legacy.REQUIRED_RESOURCES, *legacy.OPTIONAL_RESOURCES)}
        transformed = legacy.transform_sources(original)
        cls.sources = {PREFIX + name: transformed[name].encode("utf8") for name in native.ACCOUNT_READERS}
        cls.sources["synthetic-unrelated.qml"] = b"Item { property string untouched: 'original' }"

    def test_four_actual_readers_keep_all_nontransport_logic(self):
        result = native.native_account_sources(self.sources)
        self.assertEqual(set(result), set(self.sources))
        self.assertEqual(result["synthetic-unrelated.qml"], self.sources["synthetic-unrelated.qml"])
        for name, interval in native.ACCOUNT_READERS.items():
            with self.subTest(resource=name):
                before, after = self.sources[PREFIX + name].decode(), result[PREFIX + name].decode()
                self.assertEqual(after.count('SettingsTransport.createRequest("accounts")'), 1)
                self.assertNotIn("XMLHttpRequest", after)
                self.assertNotIn("127.0.0.1", after)
                self.assertEqual(after.count("import ValMAX.Native 1.0 as ValMAXNative\n"), 1)
                # The existing validator, scheduler, labels, rings and account
                # string-ID conversion remain identical after removing transport.
                restored = after.replace("import ValMAX.Native 1.0 as ValMAXNative\n", "")
                restored = restored.replace('ValMAXNative.SettingsTransport.createRequest("accounts")', "new XMLHttpRequest()")
                restored = restored.replace("xhr.readyState !== 4", "xhr.readyState !== XMLHttpRequest.DONE")
                restored = restored.replace('xhr.open("GET"); xhr.send();', 'xhr.open("GET", "http://127.0.0.1:18973/account-settings"); xhr.send();')
                self.assertEqual(restored, before)
                self.assertIn(f"Timer {{ interval: {interval};", after)
        self.assertIn("modApplyWorkHours", result[PREFIX + "AccountsStackView.qml"].decode())

    def test_optional_account_button_may_be_absent(self):
        sources = dict(self.sources)
        del sources[PREFIX + "AccountButton.qml"]
        self.assertEqual(set(native.native_account_sources(sources)), set(sources))

    def test_required_resource_missing_rejects_without_mutating_input(self):
        for name in set(native.ACCOUNT_READERS) - {"AccountButton.qml"}:
            sources = dict(self.sources)
            del sources[PREFIX + name]
            snapshot = dict(sources)
            with self.assertRaises(ValueError): native.native_account_sources(sources)
            self.assertEqual(sources, snapshot)

    def test_changed_or_duplicate_reader_and_double_transform_reject(self):
        key = PREFIX + "AccountsStackView.qml"
        for extra in (legacy.reader(interval=5000).encode(), None):
            sources = dict(self.sources)
            if extra is None:
                sources[key] = sources[key].replace(b"interval: 5000;", b"interval: 4999;", 1)
            else:
                sources[key] += extra
            with self.assertRaises(ValueError): native.native_account_sources(sources)
        with self.assertRaises(ValueError): native.native_account_sources(native.native_account_sources(self.sources))

    def test_unhandled_future_reader_is_a_build_error(self):
        sources = dict(self.sources)
        sources["future.qml"] = b'xhr.open("GET", "http://127.0.0.1:18973/account-settings");'
        with self.assertRaisesRegex(ValueError, "transport remained"):
            native.native_account_sources(sources)


if __name__ == "__main__":
    unittest.main()
