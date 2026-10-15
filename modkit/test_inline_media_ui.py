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
