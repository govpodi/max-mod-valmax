"""Portable source contracts: immutable native input -> pool -> async shell.

No Qt process, account context, MAX process or mutable resource fixture is used.
The parent may copy this file beside chat_async_ui.py in modkit.
"""
from pathlib import Path
import hashlib
import importlib.util
import sys
import unittest

PROJECT = Path('D:/ValMAX')
BASE = Path('D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi')
sys.path.insert(0, str(PROJECT / 'modkit'))
import chat_lock_ui
import chat_pool_ui

SOURCE = Path(__file__).with_name('chat_async_ui.py')
if not SOURCE.exists():
    SOURCE = Path(__file__).parent.parent / 'chat_async_ui.py'
spec = importlib.util.spec_from_file_location('chat_async_unit_candidate', SOURCE)
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)

GOLDEN_SHA256 = 'b3f86ee2f22265135f942a5530513cd9078b62ee7dd12d9835b46a81cb665159'


def sha(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()


def source(name):
    return (BASE / name).read_text('utf8').replace('\r\n', '\n')


class ChatAsyncUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native_list = source('ChatListView.qml')
        cls.native_delegate = source('ChatDelegate.qml')
        cls.protected_delegate = chat_lock_ui.transform_delegate(cls.native_delegate)
        cls.pool_sources = chat_pool_ui.transform_sources({
            'ChatListView.qml': cls.native_list, 'ChatDelegate.qml': cls.protected_delegate,
        })
        cls.original = cls.pool_sources['ChatListView.qml']
        cls.output = candidate.transform(cls.original)

    def test_pinned_golden_and_lf_crlf_input(self):
        self.assertEqual(sha(self.original), candidate.INPUT_SHA256)
        self.assertEqual(sha(self.output), GOLDEN_SHA256)
        self.assertEqual(candidate.transform(self.original.replace('\n', '\r\n')), self.output)

    def test_reverse_exact_original_and_crlf_output(self):
        self.assertEqual(candidate.reverse(self.output, self.original), self.original)
        self.assertEqual(candidate.reverse(self.output.replace('\n', '\r\n'), self.original), self.original)

    def test_transform_rejects_native_before_pool_double_patch_and_drift(self):
        for value in (self.native_list, self.output, self.original + '\n',
                      self.original.replace('root.currentChatId', 'root.notCurrentChatId', 1)):
            with self.subTest(valueHash=sha(value)):
                with self.assertRaises(ValueError): candidate.transform(value)

    def test_bad_types_transform_reverse_and_sources(self):
        for value in (None, b'QML', 0, True, [], {}):
            with self.subTest(type=type(value).__name__):
                with self.assertRaises(ValueError): candidate.transform(value)
                with self.assertRaises(ValueError): candidate.reverse(value, self.original)
                with self.assertRaises(ValueError): candidate.reverse(self.output, value)
        for value in (None, b'QML', 0, [], {}):
            with self.assertRaises(ValueError): candidate.transform_sources(value)

    def test_reverse_rejects_output_and_original_drift(self):
        for value in (self.output + '\n', self.output.replace('item.clicked();', 'item.rightMouseButtonClicked();', 1),
                      self.output.replace('valmaxPendingGeneration === allChatsListView.valmaxNavigationGeneration', 'true', 1),
                      self.output.replace('onPressedChanged:', 'onActiveChanged:', 1)):
            with self.assertRaises(ValueError): candidate.reverse(value, self.original)
        with self.assertRaises(ValueError): candidate.reverse(self.output, self.original + '\n')

    def test_dictionary_preserves_native_protected_heavy_delegate(self):
        original = {**self.pool_sources, 'Other.qml': 'unchanged'}
        result = candidate.transform_sources(original)
        self.assertEqual(original['ChatListView.qml'], self.original)
        self.assertEqual(result['ChatListView.qml'], self.output)
        self.assertEqual(result['ChatDelegate.qml'], self.pool_sources['ChatDelegate.qml'])
        self.assertEqual(result['Other.qml'], 'unchanged')
        self.assertIn('VALMAX_CHAT_LOCK_DELEGATE', result['ChatDelegate.qml'])
        self.assertIn('valmaxChatLockMask', result['ChatDelegate.qml'])
        self.assertIn('restoreMode: Binding.RestoreNone', result['ChatDelegate.qml'])

    def test_native_thirteen_roles_and_forwards(self):
        self.assertEqual(len(candidate.ROLES), 13)
        self.assertEqual(len({name for _, name in candidate.ROLES}), 13)
        native = self.pool_sources['ChatDelegate.qml']
        inner = self.output.split('rowComponent: Component {', 1)[1].split('                onCreateFolderForChat:', 1)[0]
        for kind, name in candidate.ROLES:
            self.assertIn(f'required property {kind} {name}\n', native)
            self.assertEqual(self.output.count(f'required property {kind} {name}\n'), 1)
            self.assertEqual(inner.count(f'{name}: chatDelegate.{name}\n'), 1)
        self.assertNotIn('Number(chatId)', self.output)
        self.assertNotIn('parseInt(chatId)', self.output)

    def test_native_event_suffix_unchanged(self):
        old = candidate.delegate_block(self.original)
        tail = old[old.index('                onCreateFolderForChat:'):]
        self.assertTrue(candidate.shell(old).endswith(tail))
        self.assertIn('onRightMouseButtonClicked: chatDelegate.rightMouseButtonClicked()', self.output)
        self.assertIn('onPreviewClicked: chatDelegate.previewClicked()', self.output)
        self.assertIn('Component.onDestruction: allChatsListView.delegateRemoved(chatDelegate.chatId)', self.output)

    def test_navigation_generation_and_identity_guards(self):
        self.assertIn('property int valmaxNavigationGeneration: 0', self.output)
        self.assertIn('valmaxPendingGeneration = allChatsListView.valmaxNavigationGeneration;', self.output)
        self.assertIn('valmaxPendingGeneration === allChatsListView.valmaxNavigationGeneration', self.output)
        self.assertIn('valmaxPendingIdentity === identityKey', self.output)
        self.assertIn('function onCurrentChatIdChanged() { allChatsListView.valmaxNavigationGeneration += 1; }', self.output)
        self.assertIn('function onClicked() { allChatsListView.valmaxNavigationGeneration += 1; }', self.output)
        self.assertIn('valmaxPendingGeneration = -1;', self.output)
        self.assertNotIn('forceCompletion', self.output)

    def test_placeholder_keyboard_mouse_accessibility_and_cancel(self):
        self.assertIn('acceptedModifiers: Qt.KeyboardModifierMask', self.output)
        self.assertIn('valmaxLeftPressIdentity === chatDelegate.identityKey', self.output)
        self.assertIn('valmaxRightPressIdentity === chatDelegate.identityKey', self.output)
        self.assertIn('valmaxSpaceIdentity === chatDelegate.identityKey', self.output)
        self.assertIn('Keys.forwardTo: ready && item && !valmaxSpacePressed ? [item] : []', self.output)
        self.assertIn('event.accepted = false;', self.output)
        self.assertIn('Accessible.onPressAction: chatDelegate.clicked()', self.output)
        self.assertIn('if (pooled || !eligible || !identityKey) return;', self.output)
        self.assertIn('chatDelegate.valmaxClearInput();', self.output)
        self.assertIn('cancelFlick();', self.output)


if __name__ == '__main__':
    unittest.main(verbosity=2)
