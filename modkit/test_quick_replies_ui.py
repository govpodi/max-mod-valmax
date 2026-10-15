"""Pinned native draft/upload/reset contracts; no client, file upload or profiles."""
from pathlib import Path
import unittest

from quick_replies_ui import RESOURCE_NAMES, OPTIONAL_RESOURCE_NAMES, transform_sources


NATIVE = Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi")


@unittest.skipUnless(all((NATIVE / name).is_file() for name in (*RESOURCE_NAMES, *OPTIONAL_RESOURCE_NAMES)),
                     "Pinned decoded native resources unavailable")
class QuickReplyNativeTransformTests(unittest.TestCase):
    def setUp(self):
        self.sources = {name: (NATIVE / name).read_text(encoding="utf8")
                        for name in (*RESOURCE_NAMES, *OPTIONAL_RESOURCE_NAMES)}

    def test_preserves_native_manual_send_and_upload_contracts(self):
        original = self.sources["BaseMessageSendPanel.qml"].replace("\r\n", "\n")
        result = transform_sources(self.sources)
        message = result["BaseMessageSendPanel.qml"]
        for contract in ("onPhotoOrVideoUploadSelected: fileList => viewContext.uploadView.uploadPhotoOrVideo(fileList)",
                         "onFileUploadSelected: fileList => viewContext.uploadView.uploadFiles(fileList)",
                         "    onSendButtonClicked: root.forceActiveFocusOnInput()"):
            self.assertEqual(1, message.count(contract))
        self.assertEqual(original.count("viewContext.send"), message.count("viewContext.send"))
        self.assertEqual(original.count("requestPostSendConfirmation"), message.count("requestPostSendConfirmation"))
        self.assertNotIn("sendPressed(", result["BaseSendPanel.qml"])
        self.assertEqual(self.sources["BaseMessageSendPanel.qml"], (NATIVE / "BaseMessageSendPanel.qml").read_text(encoding="utf8"))

    def test_native_clear_preserved_before_preview_completion(self):
        message = transform_sources(self.sources)["BaseMessageSendPanel.qml"]
        expected = "        onCleared: {\n            root.clearText();\n            root.modQuickReplyNativeCleared();\n        }"
        self.assertEqual(1, message.count(expected))
        # Completion is attached to the sender reset, never a text/attachment
        # count change, which would race captions and asynchronous uploads.
        base = transform_sources(self.sources)["BaseSendPanel.qml"]
        self.assertNotIn("onTextChanged: modQuickReplyNativeCleared", base)
        self.assertNotIn("onModQuickReplyAttachmentCountChanged: modQuickReplyNativeCleared", base)

    def test_missing_or_ambiguous_reset_upload_anchor_fails_closed(self):
        anchors = ("        onCleared: root.clearText()",
                   "onPhotoOrVideoUploadSelected: fileList => viewContext.uploadView.uploadPhotoOrVideo(fileList)",
                   "onFileUploadSelected: fileList => viewContext.uploadView.uploadFiles(fileList)")
        for anchor in anchors:
            for replacement in ("", anchor + "\n" + anchor):
                with self.subTest(anchor=anchor, duplicate=bool(replacement)):
                    sources = dict(self.sources)
                    sources["BaseMessageSendPanel.qml"] = sources["BaseMessageSendPanel.qml"].replace(anchor, replacement)
                    with self.assertRaises(ValueError):
                        transform_sources(sources)

    def test_optional_native_panel_absent_has_no_invented_upload_methods(self):
        del self.sources["BaseMessageSendPanel.qml"]
        result = transform_sources(self.sources)
        self.assertEqual(set(self.sources), set(result))
        self.assertIn("property bool modQuickReplyAttachmentIntegration: false", result["BaseSendPanel.qml"])
        self.assertNotIn("viewContext.uploadView", result["BaseSendPanel.qml"])
        with self.assertRaises(ValueError):
            transform_sources(result)


if __name__ == "__main__":
    unittest.main()
