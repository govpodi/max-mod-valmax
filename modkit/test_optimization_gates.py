"""Pinned performance gates and preservation checks; no native client/profile."""
import hashlib
from pathlib import Path
import unittest

import optimization_gates as gates

FIXTURE = Path("D:/ValMAX/analysis/scroll-redesign-v1/prefetch-speed-v2/build-plan/stage-009/generated")


class OptimizationGatesTests(unittest.TestCase):
    def source(self, name):
        if not FIXTURE.is_dir():
            self.skipTest("Immutable accepted stage-009 fixture unavailable")
        return (FIXTURE / name).read_text("utf8")

    def test_every_native_resource_roundtrips_and_rejects_drift(self):
        for name in gates.PINS:
            with self.subTest(name=name):
                original = self.source(name)
                output = gates.transform(original, name)
                self.assertEqual(gates.reverse(output, original, name), original)
                self.assertEqual(gates.transform(original.replace("\n", "\r\n"), name), output)
                for bad in (None, "", original + "\n", output):
                    with self.assertRaises(ValueError):
                        gates.transform(bad, name)
                with self.assertRaises(ValueError):
                    gates.reverse(output + "\n", original, name)

    def test_complete_resource_contract_and_preservation(self):
        sources = {p.name: p.read_text("utf8") for p in FIXTURE.glob("*.qml")}
        result = gates.transform_sources(sources)
        self.assertEqual(set(result), set(sources))
        for name in set(sources) - gates.PINS.keys():
            self.assertEqual(result[name], sources[name], name)
        with self.assertRaises(ValueError):
            gates.transform_sources({})

    def test_shared_helpers_are_exact_pinned_and_config_is_added(self):
        helpers = {name: Path(__file__).with_name(name.rsplit("/", 1)[1]).read_bytes()
                   for name in gates.EXTRA_PINS}
        result = gates.transform_extra_resources(helpers)
        self.assertEqual(set(result), set(helpers) | {gates.CONFIG_RESOURCE, gates.QMLDIR_RESOURCE})
        for resource, original in helpers.items():
            self.assertEqual(hashlib.sha256(original).hexdigest(), gates.EXTRA_PINS[resource])
            output = result[resource].decode("utf8")
            self.assertIn(gates.IMPORT, output)
            with self.assertRaises(ValueError):
                gates.transform_extra(output, resource)
        with self.assertRaises(ValueError):
            gates.transform_extra_resources({})

    def test_window_size_remains_synchronous_with_every_option(self):
        source = gates.transform(self.source("MeWindow.qml"), "MeWindow.qml")
        self.assertIn("onWidthChanged: windowState.width = width", source)
        self.assertIn("onHeightChanged: windowState.height = height", source)
        self.assertIn("else windowState.x = x", source)
        self.assertIn("else windowState.y = y", source)

    def test_pin_masks_retention_and_identity_guards_remain_unconditional(self):
        source = gates.transform(self.source("ChatDelegate.qml"), "ChatDelegate.qml")
        self.assertIn("root.enabled && !root.valmaxPooled && !root.valmaxChatLockMask &&", source)
        history = gates.transform(self.source("HistoryListView.qml"), "HistoryListView.qml")
        self.assertIn("valmaxRetentionLoader", history)
        self.assertIn("valmaxSyncRetentionRegistration()", history)
        self.assertIn("valmaxRetainedBadgeLoader", history)
        helpers = gates.transform_extra(Path(__file__).with_name("ValMAXAsyncListRow.qml").read_text("utf8"),
                                        ":/valmax/ValMAXAsyncListRow.qml")
        self.assertIn("key !== identityKey || serial !== valmaxRequestSerial", helpers)
        self.assertIn("onIdentityKeyChanged: if (valmaxCompleted) valmaxResetIdentity()", helpers)
        for key in ("chatLocks", "retention", "notifications", "ghost", "auth"):
            self.assertNotIn(key, gates.KEYS)

    def test_unknown_keys_and_resources_rejected(self):
        with self.assertRaises(ValueError):
            gates.flag("notASetting")
        with self.assertRaises(ValueError):
            gates.transform("", "Other.qml")

    def test_config_has_only_local_json_and_no_new_module(self):
        source = gates.configuration_resources()[gates.CONFIG_RESOURCE].decode("utf8")
        self.assertNotIn("import QtCore", source)
        self.assertNotIn("http://", source)
        self.assertNotIn("https://", source)
        self.assertIn('request.open("GET", url, false)', source)
        self.assertIn("request.responseText.length > 4096", source)
        self.assertIn("Object.freeze(readSnapshot())", source)
        self.assertIn('"optimization-active.json"', source)
        for key in gates.KEYS:
            self.assertIn('"' + key + '"', source)


if __name__ == "__main__":
    unittest.main()
