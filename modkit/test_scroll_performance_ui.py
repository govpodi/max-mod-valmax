"""Exact reversibility plus real Qt scroll/navigation workload regression."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import unittest

import history_cache_ui
import scroll_performance_ui as target

ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path('D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi')


class ScrollPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = (NATIVE / 'ChatListView.qml').read_text(encoding='utf8').replace('\r\n', '\n')
        raw_history = (NATIVE / 'HistoryListView.qml').read_text(encoding='utf8').replace('\r\n', '\n')
        cls.history = history_cache_ui.transform(raw_history)
        if hashlib.sha256(cls.chat.encode()).hexdigest() != '9190f2dbac9815ab6492fa0061f5673a43d9ceb4b8bc0b9af2e2a92df4b6abc6':
            raise ValueError('Pinned extracted native chat source changed')

    def test_chat_resource_and_prefetch_are_untouched(self):
        source = {'ChatListView.qml': self.chat, 'HistoryListView.qml': self.history}
        result = target.transform_sources(source)
        self.assertEqual(result['ChatListView.qml'], self.chat)
        self.assertNotIn(':/qt/qml/OneMeUi/ChatListView.qml', target.RESOURCES)
        self.assertNotIn('cacheBuffer', Path(target.__file__).read_text(encoding='utf8'))

    def test_history_changes_only_two_visibility_scheduling_calls(self):
        result = target.transform_history(self.history)
        restored = result.replace(target.HISTORY_CONTENT_HANDLER, target.HISTORY_CONTENT_ANCHOR, 1)
        restored = restored.replace(target.HISTORY_HEIGHT_HANDLER, target.HISTORY_HEIGHT_ANCHOR, 1)
        self.assertEqual(restored, self.history)
        self.assertEqual(result.count(target.MARKER), 1)
        for preserved in ('setLastVisibleIndex(lastVisible)', '!root.readOnlyView',
                          'onScrollTo(index)', 'onScrollToCenter(index)',
                          'loadBackwardIfNeeded()', 'loadForwardIfNeeded()'):
            self.assertIn(preserved, result)

    def test_transform_order_and_unexpected_sources_fail_closed(self):
        for source, transform in ((self.history, target.transform_history),):
            for invalid in (None, b'', '', source + '\n', transform(source)):
                with self.subTest(type=type(invalid).__name__, transform=transform.__name__):
                    with self.assertRaises(ValueError):
                        transform(invalid)
        raw = (NATIVE / 'HistoryListView.qml').read_text(encoding='utf8')
        with self.assertRaises(ValueError):
            target.transform_history(raw)

    def test_other_resources_remain_byte_identical(self):
        source = {'ChatListView.qml': self.chat, 'HistoryListView.qml': self.history, 'Unrelated.qml': 'unchanged'}
        changed = target.transform_sources(source)
        self.assertEqual(source['Unrelated.qml'], changed['Unrelated.qml'])
        self.assertEqual(source['ChatListView.qml'], self.chat)
        self.assertEqual(set(changed), set(source))
        self.assertEqual(changed['ChatListView.qml'], self.chat)
        self.assertEqual(set(target.transform_sources({'HistoryListView.qml': self.history})), {'HistoryListView.qml'})
        with self.assertRaises(ValueError):
            target.transform_sources({})

    def test_actual_qt_coalescing_and_native_navigation_guards(self):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8',
            str(ROOT / 'analysis/pin-notifications-v1/scroll_fixture.py')],
            env=dict(os.environ), capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:] + result.stdout[-2000:])
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Cannot assign'):
            self.assertNotIn(error, result.stderr)
        import json
        proof = json.loads(result.stdout)
        before, after = proof['cases']
        self.assertEqual(before['nativeCallbackCallsPer80PositionChanges'], 80)
        self.assertEqual(after['nativeCallbackCallsPer80PositionChanges'], 1)
        self.assertEqual(before['cacheBuffer'], after['cacheBuffer'])
        self.assertEqual(before['reuseItems'], after['reuseItems'])
        self.assertTrue(after['finalVisibleIndexPreserved'])
        self.assertTrue(after['readOnlyAndShiftGuardsPreserved'])
        self.assertFalse(proof['fpsMeasured'])


if __name__ == '__main__':
    unittest.main()
