"""Focused native composer transform checks; no app, HTTP or profiles."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import unittest

PROJECT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(PROJECT/'native/tooling'),str(PROJECT/'modkit')]
import build_native_qml
import quick_replies_ui
import inline_media_ui

BASE=Path('D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi')
class NativeInlineMedia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original={name:(BASE/name).read_text(encoding='utf8') for name in (*quick_replies_ui.RESOURCE_NAMES,*quick_replies_ui.OPTIONAL_RESOURCE_NAMES)}
        cls.legacy=inline_media_ui.transform_sources(quick_replies_ui.transform_sources(cls.original))
        cls.preceding=build_native_qml.native_quick_replies_source(cls.legacy['BaseSendPanel.qml'])
        cls.native=build_native_qml.native_inline_media_source(cls.preceding)

    def test_only_native_composer_uses_new_type(self):
        self.assertEqual(self.native.count('ValMAXNative.MediaComposer {'),1)
        self.assertIn('composer: root',self.native)
        self.assertNotIn('ValMAXNative.MediaComposer',self.legacy['BaseSendPanel.qml'])
        self.assertIn('new XMLHttpRequest()',self.legacy['BaseSendPanel.qml'])
        self.assertIn('http://127.0.0.1:18973/inline-media/',self.legacy['BaseSendPanel.qml'])
        self.assertIn('SettingsTransport.createRequest("quick_replies")',self.native)

    def test_no_http_or_file_handoff_in_native_media(self):
        self.assertNotIn('http://127.0.0.1:18973/inline-media',self.native)
        self.assertNotIn('modInlineRequestJson',self.native)
        self.assertNotIn('modInlineWatchdog',self.native)
        self.assertNotIn('modQuickReplyAttachmentsRequested([], [value.url])',self.native)
        self.assertNotIn('file:\\/\\/\\/',self.native)
        fragment=(PROJECT/'native/qt/InlineMediaComposer.qml').read_text(encoding='utf8')
        for forbidden in ('XMLHttpRequest','fileUrl','ownerId','accountId:', 'sendPressed(', 'sendOnlyFiles(', 'uploadPhotoOrVideo('):
            self.assertNotIn(forbidden,fragment)

    def test_explicit_find_only_and_completion_keys_inert(self):
        self.assertEqual(self.native.count('onClicked: root.modInlineSearch()'),1)
        self.assertEqual(self.native.count('modNativeMedia.search('),1)
        self.assertIn('function completeSelected() {}',self.native)
        self.assertNotIn('onTextChanged: modInlineSearch',self.native)
        self.assertIn('Внешний поиск только по кнопке «Найти»',self.native)
        timer=self.native.split('id: modInlineRetryTimer',1)[1].split('UiKit.MeLoader {',1)[0]
        self.assertNotIn('.search(',timer)
        self.assertNotIn('.select(',timer)

    def test_native_private_provider_and_plain_text(self):
        self.assertIn('modelData.previewSource.indexOf("image://valmaxmedia/") === 0',self.native)
        self.assertIn('cache: false; asynchronous: true',self.native)
        self.assertIn('sourceSize: Qt.size(240, 180)',self.native)
        menu=self.native.split('objectName: "inlineMediaMenu"',1)[1].split('id: mentionsListLoader',1)[0]
        self.assertNotIn('http:',menu)
        self.assertIn('text: modelData.title; color: root.chatAppearance.colorTextPrimary; textFormat: Text.PlainText',menu)
        self.assertIn('text: modelData.author + " · " + modelData.license; color: root.chatAppearance.colorTextSecondary; textFormat: Text.PlainText',menu)

    def test_native_caption_only_preserves_manual_send(self):
        handler=self.native.split('onDraftDispatched: function(caption) {',1)[1].split('\n    }',1)[0]
        for expected in ('root.modQuickReplySelected = null;','root.setText(caption, []);','chatInput.moveCursorToEnd();','chatInput.forceActiveFocus();'):
            self.assertIn(expected,handler)
        self.assertNotIn('url',handler.lower())
        self.assertNotIn('sendPressed(',self.native)
        self.assertEqual(self.preceding.count('botCommandSendRequested('),self.native.count('botCommandSendRequested('))
        self.assertIn('modQuickReplyAttachmentsRequested(files, media);',self.native)

    def test_account_chat_draft_hidden_destruction_reset(self):
        for text in ('onModInlineDraftKeyChanged: modInlineReset(false)',
                     'onCurrentOpenedChatIdChanged: { modQuickReplySelected = null; modInlineReset(true); }',
                     'onModQuickReplyAccountIdChanged: { modQuickReplySelected = null; modInlineReset(true); }',
                     'onModInlineVisibleChanged: { if (!modInlineVisible) modInlineReset(false); }',
                     'function onVisibleChanged() { if (!root.visible) root.modInlineReset(false); }',
                     'function onEnabledChanged() { if (!root.enabled) root.modInlineReset(false); }',
                     'Component.onDestruction: {\n    modInlineReset(false);'):
            self.assertEqual(self.native.count(text),1)
        self.assertEqual(self.native.count('Component.onDestruction:'),self.preceding.count('Component.onDestruction:'))
        self.assertIn('readonly property var modInlineRows: modNativeMedia.rows',self.native)

    def test_cooldown_is_display_only_global_admission(self):
        self.assertIn('Date.now() + seconds * 1000',self.native)
        self.assertIn('Math.ceil((root.modInlineRetryDeadline - Date.now()) / 1000)',self.native)
        self.assertEqual(self.native.count('enabled: modNativeMedia.available && !root.modInlineBusy && root.modInlineRetrySeconds === 0'),2)
        self.assertIn('Попробуй позже.',self.native)
        self.assertIn('modNativeMedia.retryAfterSeconds',self.native)
        self.assertIn('seconds <= 86400 && !modInlineRetryCaptured',self.native)
        self.assertIn('modInlineRetryCaptured = true;',self.native)
        self.assertEqual(self.native.count('modInlineRetryCaptured = false;'),2)
        reset=self.native.split('function modInlineReset(accountOrChatChanged) {',1)[1].split('function modInlineClose()',1)[0]
        self.assertNotIn('modInlineRetryDeadline =',reset)

    def test_small_window_layout_and_native_errors_preserved(self):
        menu=self.native.split('objectName: "inlineMediaMenu"',1)[1].split('id: mentionsListLoader',1)[0]
        for text in ('Layout.minimumHeight: 0','Layout.fillHeight: true','ScrollBar.vertical:','objectName: "inlineMediaResult_"'):
            self.assertIn(text,menu)
        for text in ('attachment_limit:', 'no_space:', 'cleanup_failed:', 'result_expired:', 'provider_busy', 'Загружаем выбранный файл…'):
            self.assertIn(text,self.native)

    def test_repeated_unpinned_or_changed_source_rejected(self):
        for source in (self.native,self.legacy['BaseSendPanel.qml'],self.preceding.replace('id: modInlineRetryTimer','id: differentTimer'),
                       self.preceding.replace('onModQuickReplyAccountIdChanged: { modQuickReplySelected = null; modInlineReset(true); }',''),
                       self.preceding.replace('objectName: "inlineMediaPreview_"','objectName: "otherPreview_"')):
            with self.subTest(sourceLength=len(source)),self.assertRaises(ValueError):
                build_native_qml.native_inline_media_source(source)

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(NativeInlineMedia)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():raise SystemExit(1)
    sources=[PROJECT/'native/tooling/build_native_qml.py',PROJECT/'native/qt/InlineMediaComposer.qml',Path(__file__),PROJECT/'modkit/inline_media_composer.qml',PROJECT/'modkit/inline_media_ui.py',PROJECT/'modkit/quick_replies_ui.py',PROJECT/'modkit/quick_replies_composer.qml']
    proof={'schema':1,'ok':True,'tests':result.testsRun,'scope':'focused static native inline-media QML transform on extracted baseline source','sourceSha256':{str(p.relative_to(PROJECT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},'generatedComposerSha256':hashlib.sha256(NativeInlineMedia.native.encode()).hexdigest(),'limitations':['Static transform assertions only; actual registered MediaComposer type and MAX runtime compile checked separately','No HTTP, files selected, profiles, processes or installed client changes']}
    output=PROJECT/'analysis/native-migration-v1/native-inline-media-transform-proof.json';output.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'ok':True,'tests':result.testsRun,'proof':str(output)}))
