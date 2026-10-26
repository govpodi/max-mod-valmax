"""Native retention configuration/status validation and release boundary.

Exercises the generated panel's real JS validator and active-state expression
in QJSEngine under QCoreApplication. No GUI, MAX, local server or profile is used.
"""
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "native/tooling"), str(ROOT / "modkit")]
import build_native_qml as native
from message_retention_ui import controller_source
from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine


def status(enabled=False):
    return {"schema": 2, "revision": "a" * 64, "enabled": enabled,
            "capabilities": {"capture": False, "lookup": False,
                             "enable": False, "disable": True},
            "runtime": {"ready": False, "applied": False, "enabled": False,
                        "historySyncVerified": False, "error": "retention_not_integrated",
                        "retainedEvents": None, "retainedMessages": None}}


def function_source(source, name, arguments):
    # QML's function-level closing brace is at the same indentation. This
    # deliberately rejects source drift instead of guessing at JS braces in
    # regular expressions or string literals.
    start = re.search(r"(?m)^([ \t]*)function " + re.escape(name)
                      + r"\(" + re.escape(arguments) + r"\) \{", source)
    if start is None:
        raise AssertionError("Expected generated function missing: " + name)
    closing = re.search(r"(?m)^" + re.escape(start.group(1)) + r"\}", source[start.end():])
    if closing is None:
        raise AssertionError("Expected generated function closing brace missing")
    return source[start.start():start.end() + closing.end()].strip()


class NativeRetentionSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.engine = QJSEngine()
        cls.legacy_panel = (ROOT / "modkit/message_retention_settings_panel.qml").read_text(encoding="utf8")
        cls.legacy_controller = controller_source()
        cls.panel = native.native_retention_settings_panel(cls.legacy_panel)
        cls.controller = native.native_retention_controller(cls.legacy_controller)
        cls.validator = function_source(cls.panel, "validResponse", "value")
        match = re.search(r"(?m)^\s*readonly property bool confirmedActive: (.+)$", cls.panel)
        if match is None:
            raise AssertionError("Actual generated active-state expression missing")
        cls.active_expression = match.group(1)
        missed = re.search(r'RetentionText \{ objectName: "retentionMissed"; Layout.fillWidth: true; '
                           r'visible: ([^;]+); text: ([^;]+); typography:', cls.panel)
        if missed is None:
            raise AssertionError("Actual generated missed-event warning missing")
        cls.missed_visible, cls.missed_text = missed.groups()
        counts = re.search(r'RetentionText \{ objectName: "retentionCounts"; Layout.fillWidth: true; '
                           r'visible: ([^;]+); text: ([^;]+); typography:', cls.panel)
        if counts is None:
            raise AssertionError("Actual generated retention counts label missing")
        cls.counts_visible, cls.counts_text = counts.groups()
        cls.technical = {}
        for name in ("retentionTechnicalStatus", "retentionTechnicalQueue",
                     "retentionRouteOwners", "retentionRouteCauses"):
            marker = f'objectName: "{name}";'
            lines = [line for line in cls.panel.splitlines() if marker in line]
            if len(lines) != 1 or '; visible: ' not in lines[0] or '; text: ' not in lines[0]:
                raise AssertionError(f"Actual generated diagnostic label missing: {name}")
            visible = lines[0].split('; visible: ', 1)[1].split('; text: ', 1)[0]
            expression = lines[0].split('; text: ', 1)[1].rsplit('; typography:', 1)[0]
            cls.technical[name] = (visible, expression)

    def evaluate(self, source):
        result = self.engine.evaluate(source)
        self.assertFalse(result.isError(), result.toString())
        return result

    def accepts(self, value):
        return self.evaluate("(" + self.validator + ")(" + json.dumps(value) + ")").toBool()

    def active(self, value, trusted=True):
        setup = ("var value=" + json.dumps(value) + "; var savedEnabled=value.enabled; "
                 "var runtime=value.runtime; var capabilities=value.capabilities; "
                 "var runtimeTrusted=" + json.dumps(trusted) + "; "
                 "var retentionPage={savedEnabled:savedEnabled,runtime:runtime,"
                 "capabilities:capabilities,runtimeTrusted:runtimeTrusted}; ")
        return self.evaluate("(function(){" + setup + "return ("
                             + self.active_expression + ");})()").toBool()

    def test_schema_two_unknown_counts_and_both_saved_preferences_are_valid(self):
        for enabled in (False, True):
            with self.subTest(savedPreference=enabled):
                value = status(enabled)
                self.assertTrue(self.accepts(value))
                self.assertIsNone(value["runtime"]["retainedEvents"])
                self.assertIsNone(value["runtime"]["retainedMessages"])

    def test_count_label_distinguishes_unknown_from_zero_and_known_counts(self):
        def label(events, messages, trusted=True):
            setup = "var retentionPage=" + json.dumps({
                "runtimeTrusted": trusted,
                "runtime": {"retainedEvents": events, "retainedMessages": messages},
            }) + ";"
            visible = self.evaluate("(function(){" + setup + "return ("
                                    + self.counts_visible + ");})()").toBool()
            text = self.evaluate("(function(){" + setup + "return ("
                                 + self.counts_text + ");})()").toString()
            return visible, text

        unknown = "Общее количество удалений и сохранённых сообщений недоступно."
        self.assertEqual(label(None, None), (True, unknown))
        self.assertEqual(label(None, 7), (True, unknown))
        self.assertEqual(label(4, None), (True, unknown))
        self.assertEqual(label(0, 0),
                         (True, "Отмечено удалений: 0. Сообщений сохранено: 0."))
        self.assertEqual(label(4, 7),
                         (True, "Отмечено удалений: 4. Сообщений сохранено: 7."))
        self.assertFalse(label(None, None, trusted=False)[0])
        self.assertIn("Бета: входящий текст сохраняется", self.panel)
        self.assertIn("Удаления, которые обработчик не увидел, не восстанавливаются; ранее сохранённые сообщения остаются", self.panel)
        self.assertNotIn("повторная синхронизация ещё проверяется", self.panel)

    def test_saved_true_never_means_capture_active(self):
        self.assertFalse(self.active(status(True)))
        self.assertFalse(self.active(status(False)))
        self.assertFalse(self.active(status(True), trusted=False))

    def test_dropped_callbacks_show_incomplete_archive_warning(self):
        value = status(True)
        value["capabilities"].update(capture=True, lookup=True)
        value["runtime"].update(ready=True, applied=True, enabled=True, error=None,
                                queue={"queued": 0, "inFlight": 0, "pending": 0,
                                       "captured": 5, "marked": 2, "missing": 0,
                                       "missed": 2, "failures": 0})
        self.assertTrue(self.accepts(value))
        self.assertTrue(self.active(value))
        def warning(missed, trusted):
            setup = "var retentionPage=" + json.dumps({
                "runtimeTrusted": trusted, "runtime": {"queue": {"missed": missed}}}) + ";"
            visible = self.evaluate("(function(){" + setup + "return ("
                                    + self.missed_visible + ");})()").toBool()
            label = self.evaluate("(function(){" + setup + "return ("
                                  + self.missed_text + ");})()").toString()
            return visible, label
        self.assertFalse(warning(0, True)[0])
        visible, label = warning(2, True)
        self.assertTrue(visible)
        self.assertIn("(2)", label)
        self.assertIn("может быть неполным", label)
        self.assertFalse(warning(2, False)[0])

    def test_technical_status_reports_only_fixed_error_and_aggregate_counters(self):
        value = status(True)
        value["runtime"]["error"] = "route"
        value["runtime"]["queue"] = {"queued": 1, "inFlight": 1, "pending": 2,
                                      "captured": 3, "marked": 4, "missing": 5,
                                      "missed": 6, "failures": 7}
        self.assertTrue(self.accepts(value))
        setup = ("var retentionPage=" + json.dumps({
            "runtimeTrusted": True, "runtime": value["runtime"],
            "accountId": "PRIVATE_ACCOUNT_CANARY", "chatId": "PRIVATE_CHAT_CANARY",
            "text": "PRIVATE_TEXT_CANARY", "profilePath": "PRIVATE_PATH_CANARY",
            "token": "PRIVATE_TOKEN_CANARY",
        }) + ";")
        visible, expression = self.technical["retentionTechnicalStatus"]
        self.assertTrue(self.evaluate("(function(){" + setup + "return (" + visible + ");})()").toBool())
        state = self.evaluate("(function(){" + setup + "return (" + expression + ");})()").toString()
        self.assertIn("Код: route", state)
        self.assertIn("захват активен: нет", state)
        visible, expression = self.technical["retentionTechnicalQueue"]
        self.assertTrue(self.evaluate("(function(){" + setup + "return (" + visible + ");})()").toBool())
        queue = self.evaluate("(function(){" + setup + "return (" + expression + ");})()").toString()
        for field in ("Очередь: 1", "выполняется: 1", "ожидает записи: 2",
                      "захвачено: 3", "сохранено: 4", "без записи: 5",
                      "пропущено: 6", "ошибок: 7"):
            self.assertIn(field, queue)
        for forbidden in ("PRIVATE_ACCOUNT_CANARY", "PRIVATE_CHAT_CANARY", "PRIVATE_TEXT_CANARY",
                          "PRIVATE_PATH_CANARY", "PRIVATE_TOKEN_CANARY"):
            self.assertNotIn(forbidden, state + queue)
        value["runtime"]["accountId"] = "PRIVATE_ACCOUNT_CANARY"
        self.assertFalse(self.accepts(value))
        del value["runtime"]["accountId"]
        value["runtime"]["error"] = "C:/PRIVATE_PATH_CANARY"
        self.assertFalse(self.accepts(value))

    def test_technical_status_accepts_only_known_native_error_codes(self):
        for code in (None, "preflight", "route", "queue_full", "buffer_full",
                     "storage", "stalled", "shutdown", "retention_unavailable",
                     "retention_not_ready", "host_status_stale", "retention_not_integrated"):
            with self.subTest(code=code):
                value = status()
                value["runtime"]["error"] = code
                self.assertTrue(self.accepts(value))

    def test_route_diagnostics_are_aggregate_and_do_not_claim_readiness(self):
        diagnostics = {"schema": 1, "listeners": 3, "activeOwners": 2,
                       "ownerConstructed": 4, "joinedUpserts": 5,
                       "invalidHistory": 6, "noOwner": 7,
                       "invalidOwner": 8, "unmatchedOwner": 9,
                       "routeFailures": 10, "lastScopeFailureReason": 5,
                       "boundedSkips": 11}
        setup = ("var ValMAXNative={Bridge:{retentionDiagnostics:"
                 + json.dumps(diagnostics) + "}}; var retentionPage="
                 + json.dumps({"runtimeTrusted": True,
                               "accountId": "PRIVATE_ACCOUNT_CANARY",
                               "text": "PRIVATE_TEXT_CANARY"}) + ";")
        labels = []
        for name in ("retentionRouteOwners", "retentionRouteCauses"):
            visible, expression = self.technical[name]
            self.assertTrue(self.evaluate("(function(){" + setup + "return ("
                                          + visible + ");})()").toBool())
            labels.append(self.evaluate("(function(){" + setup + "return ("
                                        + expression + ");})()").toString())
        self.assertIn("сейчас 2", labels[0])
        self.assertIn("обнаружено после запуска перехвата: 4", labels[0])
        self.assertIn("нет владельца 7", labels[1])
        self.assertIn("нет точного совпадения 9", labels[1])
        self.assertIn("код причины 5", labels[1])
        for forbidden in ("PRIVATE_ACCOUNT_CANARY", "PRIVATE_TEXT_CANARY"):
            self.assertNotIn(forbidden, " ".join(labels))
        self.assertNotIn("retentionDiagnostics", self.validator)

    def test_capture_disabled_rejects_fabricated_ready_applied_enabled(self):
        for field in ("ready", "applied", "enabled"):
            with self.subTest(fabricatedRuntime=field):
                value = status(True)
                value["runtime"][field] = True
                self.assertFalse(self.accepts(value))

    def test_error_null_and_ready_flags_cannot_bypass_capture_capability(self):
        value = status(True)
        value["runtime"].update(ready=True, applied=True, enabled=True, error=None,
                                retainedEvents=0, retainedMessages=0)
        self.assertFalse(self.accepts(value))
        self.assertFalse(self.active(value))

    def test_wrong_status_versions_types_and_keys_reject(self):
        cases = []
        for key, replacement in (("schema", 1), ("schema", "2"), ("revision", "x" * 64),
                                 ("revision", "A" * 64), ("enabled", "true")):
            value = status(); value[key] = replacement; cases.append(value)
        missing = status(); del missing["capabilities"]; cases.append(missing)
        extra = status(); extra["archive"] = []; cases.append(extra)
        for value in cases + [None, [], True]:
            with self.subTest(value=value):
                self.assertFalse(self.accepts(value))

    def test_capability_and_runtime_shape_or_count_type_drift_reject(self):
        cases = []
        for branch, key, replacement in (("capabilities", "capture", "false"),
                                          ("capabilities", "lookup", 0),
                                          ("runtime", "retainedEvents", "0"),
                                          ("runtime", "retainedMessages", -1)):
            value = status(); value[branch][key] = replacement; cases.append(value)
        missing = status(); del missing["runtime"]["historySyncVerified"]; cases.append(missing)
        extra = status(); extra["capabilities"]["unverified"] = False; cases.append(extra)
        for value in cases:
            with self.subTest(value=value):
                self.assertFalse(self.accepts(value))

    def test_native_transport_only_and_legacy_inputs_stay_legacy(self):
        self.assertIn('ValMAXNative.RetentionTransport.createRequest("settings")', self.panel)
        for source in (self.panel, self.controller):
            for forbidden in ("XMLHttpRequest", "127.0.0.1", "localhost", "X-Max-Mod-Local-Write"):
                self.assertNotIn(forbidden, source)
        self.assertIn("new XMLHttpRequest()", self.legacy_panel)
        self.assertIn("127.0.0.1:18973", self.legacy_controller)
        self.assertEqual(self.legacy_controller, controller_source())

    def test_panel_missing_duplicate_or_already_native_reject(self):
        mutations = (self.legacy_panel.replace("id: settingsValMAXRetention", "id: changedRetention", 1),
                     self.legacy_panel.replace("new XMLHttpRequest()", "new ChangedRequest()", 1),
                     self.legacy_panel + self.legacy_panel,
                     self.panel)
        for source in mutations:
            with self.subTest(sourceLength=len(source)):
                with self.assertRaises(ValueError):
                    native.native_retention_settings_panel(source)

    def test_controller_missing_duplicate_or_already_native_reject(self):
        mutations = (self.legacy_controller.replace("id: retentionController", "id: changedRetention", 1),
                     self.legacy_controller.replace("new XMLHttpRequest()", "new ChangedRequest()", 1),
                     self.legacy_controller + self.legacy_controller,
                     self.controller)
        for source in mutations:
            with self.subTest(sourceLength=len(source)):
                with self.assertRaises(ValueError):
                    native.native_retention_controller(source)

    def test_native_config_does_not_clear_retention_release_gate(self):
        import package_native_client as package
        report = package.release_preflight()
        self.assertFalse(report["releaseEligible"])
        retention = next(item for item in report["blockers"] if item["id"] == "message_retention")
        self.assertIn("selected-chat in-history BETA bubbles", retention["reason"])
        self.assertIn("approximate", retention["reason"])
        self.assertIn("account-scoped local archive", retention["reason"])
        self.assertNotIn("legacy local helper", retention["reason"])


if __name__ == "__main__":
    unittest.main()
