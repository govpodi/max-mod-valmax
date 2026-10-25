"""Pinned export transport transform, without MAX/processes/profile IO."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "native/tooling"), str(ROOT / "modkit")]
from build_native_qml import native_export_panel
from chat_lock_ui import transform_export_panel
from optimization_ui import transform_export_panel as beta_panel


def source():
    text = (ROOT / "modkit/chat_export_panel.qml").read_text(encoding="utf8")
    return beta_panel(transform_export_panel(text))


class NativeExport(unittest.TestCase):
    def test_native_defaults_to_api2_and_client_warning_is_scoped(self):
        generated = native_export_panel(source())
        self.assertIn('property bool nativeApi2ExportAvailable: true', generated)
        self.assertIn('property string exportMode: "api2-text"', generated)
        self.assertIn('objectName: "valmaxExportBetaExplanation"', generated)
        self.assertIn('text: exportDialog.nativeApi2ExportAvailable && exportDialog.exportMode === "api2-text"', generated)
        self.assertIn('Локально сохранённые удаления в этот архив не входят; возможны пропуски.', generated)

    def test_generated_panel_has_one_root_lifecycle_handler_for_each_signal(self):
        generated = native_export_panel(source())
        self.assertEqual(generated.count("Component.onCompleted:"), 1)
        self.assertEqual(generated.count("onOpened:"), 1)
        self.assertIn("scopeArmed = true\n            valmaxExportLockInitialized = true", generated)
        self.assertIn("if (!valmaxExportAllowed) exportDialog.close()\n            else if (selectedScope && !validSelectedScope()) invalidateSelectedScope()", generated)

    def test_only_local_command_transport_replaced(self):
        old = source()
        new = native_export_panel(old)
        self.assertNotIn("127.0.0.1:18973/chat-export", new)
        self.assertIn("ValMAXNative.ExportTransport.createRequest()", new)
        self.assertIn("property bool nativeHistoryPreviewAvailable: true", new)
        self.assertIn('xhr.open("POST", "api2.history.preview")', new)
        self.assertIn("const xhr = ValMAXNative.ExportTransport.createRequest(); pendingPreviewRequest = xhr;", new)
        self.assertNotIn("new XMLHttpRequest()", new)
        self.assertNotIn("XMLHttpRequest.HEADERS_RECEIVED", new)
        self.assertNotIn("XMLHttpRequest.DONE", new)
        self.assertNotIn("function downloadAudio(url)",new)

    def test_models_manual_start_and_protection_preserved(self):
        old, new = source(), native_export_panel(source())
        for start, end in (("        function dependencyForAccount()", "        function startExport("),
                           ("        function messageDTO(", "        function prepareAttachments("),
                           ("        function historyStep()", "        function finishExport(reason)")):
            expected = old[old.index(start):old.index(end)].replace("historyModel.items.count", "historyCursor.rowCount")
            actual = new[new.index(start):new.index(end)]
            if start == "        function historyStep()":
                actual = actual.replace(
                    '            // A local wrapper may still be initializing. Do not page\n'
                    '            // away from it or enter EOF proof; the timeout above bounds waiting.\n'
                    '            if (historyCursor.pendingCount > 0) return;\n', '', 1)
            self.assertEqual(expected, actual)
        for text in ("isPreventCopyingEnabled", "valmaxChatLockController", "destinationDialog", "onAccepted:"):
            self.assertIn(text, new)
        self.assertIn('"Нативное сохранение этого вложения ещё не подключено. Экспорт не завершён."', new)

    def test_api2_seed_is_not_bound_as_a_native_view(self):
        generated = native_export_panel(source())
        start = generated[generated.index("        function startApi2Export(directory) {"):
                          generated.index("        function startExport(directory) {")]
        self.assertIn("seedContext = accountDependency.historyPanelDependency.openedChatsContext.getOrCreateChatHistory(nativeChatId);", start)
        self.assertIn("String(QmlUtils.chatIdString(message.chatId)) !== chatIdText", start)
        self.assertIn("api2HistoryContext = accountDependency.historyPanelDependency.historyViewFactory.createOnMessage(nativeChatId, message.messageTimeId);", start)
        self.assertIn('selectedView.objectName = "valmax-export-" + jobId;', start)
        self.assertNotIn("api2HistoryContext = accountDependency.historyPanelDependency.openedChatsContext", start)
        self.assertLess(start.index("beginApi2NativeHistory(job)"), start.index("historyViewFactory.createOnMessage"))
        self.assertLess(start.index("historyViewFactory.createOnMessage"), start.index("bindApi2NativeHistory(selectedView, job)"))
        self.assertLess(start.index("bindApi2NativeHistory(selectedView, job)"), start.index("exportDialog.nextApi2Window()"))
        self.assertIn("ValMAXNative.Bridge.beginExportHistory(job.jobId, job.jobToken)", generated)
        self.assertIn("ValMAXNative.Bridge.bindExportHistory(view, job.jobId, job.jobToken)", generated)
        self.assertIn("if (!getContext(api2HistoryContext)) {", generated)

    def test_model_reader_has_bounded_native_ownership(self):
        new = native_export_panel(source())
        self.assertIn("ValMAXNative.HistoryModelCursor", new)
        self.assertNotIn("historyModel", new)
        self.assertIn("historyCursor.reserve(limits.maxPageMessages)", new)
        self.assertIn("historyCursor.commit(lease.ticket, consumed)", new)
        self.assertIn("historyCursor.release(lease.ticket)", new)
        self.assertIn("Date.now() - started >= 2", new)
        self.assertIn("historyCursor.pendingCount > 0", new)
        self.assertLess(new.index("historyCursor.commit(lease.ticket, consumed)"), new.index('request("page",'))

    def test_unready_local_rows_do_not_advance_history(self):
        new = native_export_panel(source())
        step = new[new.index("        function historyStep()"):new.index("        function pollHistoryProof()")]
        guard = step.index("if (historyCursor.pendingCount > 0) return;")
        self.assertLess(step.index('finishExport("timeout")'), guard)
        self.assertLess(step.index("if (stopReason)"), guard)
        self.assertLess(guard, step.index("history.fetchBackward()"))
        self.assertLess(guard, step.index('phase = "history-proof"'))

    def test_downloader_path_requires_native_lease_receipt(self):
        new = native_export_panel(source())
        self.assertIn('typeof media.source !== "string"', new)
        self.assertIn('media.nativePathFileURL = receipt.nativePathFileURL;', new)
        self.assertIn('!localFileURL(receipt.nativePathFileURL)', new)
        self.assertLess(new.index('media.nativePathFileURL = receipt.nativePathFileURL;'), new.index('activeNative = true;', new.index('function nextMedia()')))
        self.assertIn('request("media-start"', new)
        self.assertIn('ValMAXNative.ExportMediaOwner.start', new)
        self.assertNotIn('target: exportDialog.downloader', new)
        self.assertNotIn('function onFileSaveAsFailed()', new)
        self.assertIn('ExportTransport.cancelDetached({jobId: jobId, jobToken: jobToken})', new)

    def test_changed_anchor_fails(self):
        with self.assertRaises(ValueError):
            native_export_panel(source().replace('xhr.open("POST",', 'xhr.open("PUT",', 1))
        with self.assertRaises(ValueError):
            native_export_panel(native_export_panel(source()))
        with self.assertRaises(ValueError):
            native_export_panel(source().replace(
                'if (Date.now() - waitStarted > 30000) { finishExport("timeout"); return; }\n'
                '            if (awaitingHistory && !sawHistoryChange) return;',
                'if (awaitingHistory && !sawHistoryChange) return;', 1))


if __name__ == "__main__":
    unittest.main()
