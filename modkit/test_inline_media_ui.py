from pathlib import Path
import unittest
import inline_media_ui
import quick_replies_ui

NATIVE = Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi")


class NativeTransformTests(unittest.TestCase):
    def setUp(self):
        self.sources = {name: (NATIVE/name).read_text(encoding="utf-8") for name in (*quick_replies_ui.RESOURCE_NAMES,*quick_replies_ui.OPTIONAL_RESOURCE_NAMES)}
        self.quick = quick_replies_ui.transform_sources(self.sources)
    def test_preserves_manual_send_and_upload(self):
        result = inline_media_ui.transform_sources(self.quick)
        self.assertEqual(self.quick["BaseMessageSendPanel.qml"], result["BaseMessageSendPanel.qml"])
        self.assertEqual(self.quick["MessageTextInput.qml"], result["MessageTextInput.qml"])
        base = result["BaseSendPanel.qml"]
        self.assertIn('root.modQuickReplyAttachmentsRequested([], [value.url])',base)
        self.assertNotIn('sendPressed(',base)
        self.assertEqual(self.quick['BaseSendPanel.qml'].count('botCommandSendRequested('),base.count('botCommandSendRequested('))
        self.assertNotIn('typingSendContext',base)
        self.assertEqual(self.quick['BaseSendPanel.qml'].count('Component.onDestruction:'),base.count('Component.onDestruction:'))
    def test_draft_changes_and_chat_switches_cancel_stale_results(self):
        base = inline_media_ui.transform_sources(self.quick)["BaseSendPanel.qml"]
        self.assertIn('onModInlineDraftKeyChanged: modInlineReset(false)',base)
        self.assertIn('onCurrentOpenedChatIdChanged: { modQuickReplySelected = null; modInlineReset(true); }',base)
        self.assertIn('onModQuickReplyAccountIdChanged: { modQuickReplySelected = null; modInlineReset(true); }',base)
        self.assertIn('generation !== root.modInlineGeneration',base)
        self.assertIn('chatInput.text !== originalDraft',base)
    def test_enter_tab_are_completion_only_and_requests_are_explicit(self):
        base = inline_media_ui.transform_sources(self.quick)["BaseSendPanel.qml"]
        self.assertIn('quickRepliesStarted: root.modQuickRepliesVisible || root.modInlineVisible',base)
        self.assertIn('function completeSelected() {}',base)
        self.assertEqual(1,base.count('onClicked: root.modInlineSearch()'))
        self.assertNotIn('onTextChanged: modInlineSearch',base)
        self.assertNotIn('triggeredOnStart: true; running: root.modInline',base)
        self.assertIn('Внешний поиск только по кнопке',base)
        self.assertIn('Wikimedia Commons',base)
    def test_native_height_cap_can_shrink_results_above_editor(self):
        base = inline_media_ui.transform_sources(self.quick)["BaseSendPanel.qml"]
        loader = base.split('objectName: "inlineMediaLoader"', 1)[1].split('activeAndVisible:', 1)[0]
        self.assertIn('Layout.fillHeight: true',loader)
        self.assertIn('Layout.minimumHeight: 0',loader)
        rows = base.split('objectName: "inlineMediaResults"', 1)[1].split('model: root.modInlineRows', 1)[0]
        self.assertIn('Layout.fillHeight: true',rows)
        self.assertIn('Layout.minimumHeight: 0',rows)
        self.assertIn('ScrollBar.vertical:',rows)
        self.assertIn('objectName: "inlineMediaResult_" + index',base)
    def test_provider_failure_keeps_results_and_describes_download_separately(self):
        base = inline_media_ui.transform_sources(self.quick)["BaseSendPanel.qml"]
        search = base.split('function modInlineSearch()',1)[1].split('function modInlineSelect(',1)[0]
        self.assertNotIn('modInlineRows = []',search)
        self.assertIn('value && value.retryAfterSeconds',base)
        self.assertIn('Wikimedia Commons временно ограничила загрузку выбранного файла.',base)
        self.assertIn('Wikimedia Commons временно ограничила поиск.',base)
        self.assertIn('modInlineRetryRoute === "select" && root.modInlineRetrySeconds > 0',base)
        self.assertIn('modInlineRetryRoute === "search" && root.modInlineRetrySeconds > 0',base)
    def test_transform_fails_if_unpinned_or_repeated(self):
        with self.assertRaises(ValueError):
            inline_media_ui.transform_sources(self.sources)
        transformed = inline_media_ui.transform_sources(self.quick)
        with self.assertRaises(ValueError):
            inline_media_ui.transform_sources(transformed)
        for anchor in ('quickRepliesStarted: root.modQuickRepliesVisible',
                       'activeAndVisible: root.canOpenMentionList && chatInput.mentionStarted',
                       'onCurrentOpenedChatIdChanged: modQuickReplySelected = null'):
            broken = dict(self.quick)
            broken['BaseSendPanel.qml'] = broken['BaseSendPanel.qml'].replace(anchor,'')
            with self.subTest(anchor=anchor),self.assertRaises(ValueError):
                inline_media_ui.transform_sources(broken)


if __name__ == "__main__":
    unittest.main()
