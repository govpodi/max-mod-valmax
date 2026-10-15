"""Synthetic preferences and runtime confirmation; no native client or WinCred."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from ghost_store import GhostSettingsStore, GhostSettingsError, defaults
from ghost_runtime import GhostRuntime, checked_snapshot


class GhostStoreTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / "analysis" / "test-temp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="valmax-ghost-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "profile" / "ghost-settings.json"
        self.store = GhostSettingsStore(self.path)

    def test_default_read_is_side_effect_free_and_cas_preserves_competing_save(self):
        first = self.store.snapshot()
        self.assertEqual(first["settings"], defaults())
        self.assertFalse(self.path.parent.exists())
        settings = {**defaults(), "enabled": True}
        saved = self.store.replace(first["revision"], settings)
        settings["hideRead"] = False
        self.assertTrue(self.store.snapshot()["settings"]["hideRead"])
        with self.assertRaises(GhostSettingsError) as raised:
            self.store.replace(first["revision"], settings)
        self.assertEqual(raised.exception.code, "revision_conflict")
        self.assertEqual(self.store.snapshot(), saved)

    def test_invalid_flags_duplicate_json_and_link_are_never_overwritten(self):
        initial = self.store.snapshot()
        for settings in ({**defaults(), "enabled": 1}, {"enabled": True}, {**defaults(), "hideLocation": True}):
            with self.assertRaises(GhostSettingsError):
                self.store.replace(initial["revision"], settings)
        self.path.parent.mkdir(exist_ok=True)
        bad = b'{"schema":1,"schema":1,"settings":{}}'
        self.path.write_bytes(bad)
        with self.assertRaises(GhostSettingsError):
            self.store.replace(initial["revision"], defaults())
        self.assertEqual(self.path.read_bytes(), bad)
        self.path.unlink()
        source = self.path.parent / "another.json"
        source.write_text("untouched")
        os.link(source, self.path)
        with self.assertRaises(GhostSettingsError):
            self.store.snapshot()
        self.assertEqual(source.read_text(), "untouched")

    def test_failed_publish_leaves_valid_previous_settings_and_cleans_owned_temp(self):
        first = self.store.snapshot()
        self.store.replace(first["revision"], defaults())
        before = self.path.read_bytes()
        with patch("ghost_store.os.replace", side_effect=OSError("synthetic")):
            with self.assertRaises(GhostSettingsError):
                self.store.replace(first["revision"], {**defaults(), "enabled": True})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_legacy_file_migrates_without_write_or_enabling_added_filters(self):
        self.path.parent.mkdir()
        original = b'{"schema":1,"settings":{"enabled":true,"hideTyping":true,"hideRead":false}}'
        self.path.write_bytes(original)
        migrated = self.store.snapshot()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(migrated["settings"], {**defaults(), "enabled": True, "hideRead": False})
        saved = self.store.replace(migrated["revision"], {"enabled": False, "hideTyping": True, "hideRead": False})
        self.assertEqual(saved["settings"], {**defaults(), "hideRead": False})
        self.assertEqual(self.store.snapshot(), saved)


class GhostRuntimeTests(unittest.TestCase):
    def test_new_filter_needs_its_own_real_capacity_and_ack(self):
        runtime = GhostRuntime(clock=lambda: 1.0)
        desired = {"revision": "a" * 64, "settings": {**defaults(), "enabled": True, "hideOnline": True, "hideStories": True}}
        state = {"ready": True, "features": {"hideTyping": True, "hideRead": True}, "revision": desired["revision"],
                 **desired["settings"], "blocked": {"typing": 0, "read": 0}, "error": None}
        runtime.update(1, state)
        self.assertFalse(runtime.status(desired)["applied"])
        self.assertFalse(runtime.status(desired)["features"]["hideOnline"])
        state["features"].update(hideOnline=True, hideStories=True)
        state["blocked"].update(presence=2, stories=3)
        runtime.update(1, state)
        self.assertEqual(runtime.status(desired)["state"], "active")
        self.assertEqual(runtime.status(desired)["blocked"]["presence"], 2)
        self.assertEqual(runtime.status(desired)["blocked"]["stories"], 3)

    def test_file_save_is_not_confirmation_stale_and_partial_observations_are_unknown(self):
        now = [1.0]
        runtime = GhostRuntime(clock=lambda: now[0])
        desired = {"revision": "a" * 64, "settings": {**defaults(), "enabled": True}}
        self.assertEqual(runtime.status(desired)["state"], "unavailable")
        state = {"ready": True, "features": {"hideTyping": True, "hideRead": True}, "revision": "b" * 64,
                 **desired["settings"], "blocked": {"typing": 0, "read": 0}, "error": None}
        runtime.update(1, state)
        self.assertEqual(runtime.status(desired)["state"], "pending")
        state["revision"] = desired["revision"]
        runtime.update(1, state)
        self.assertEqual(runtime.status(desired)["state"], "active")
        state["ready"] = False; state["features"]["hideRead"] = False; state["error"] = "hook_failed"
        runtime.update(2, state)
        self.assertEqual(runtime.status(desired)["state"], "unavailable")
        runtime.remove(2); now[0] = 11.01
        self.assertFalse(runtime.status(desired)["applied"])

    def test_spoofed_confirmation_or_sensitive_extra_fields_are_rejected(self):
        value = {"ready": True, "features": {"hideTyping": True, "hideRead": False}, "revision": "a" * 64,
                 **defaults(), "blocked": {"typing": 0, "read": 0}, "error": None}
        with self.assertRaises(ValueError):
            checked_snapshot(value)
        value["features"]["hideRead"] = True
        for dirty in ({**value, "token": "must-not-pass"}, {**value, "blocked": {"typing": True, "read": 0}}):
            with self.assertRaises(ValueError):
                checked_snapshot(dirty)
