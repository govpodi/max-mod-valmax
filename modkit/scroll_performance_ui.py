"""Coalesce native history visibility bookkeeping; chat list stays unchanged.

No delegate reuse, model changes, PIN bindings, read/typing filters, or media
quality changes. Apply immediately after history_cache_ui and before retention.
"""
from __future__ import annotations

import hashlib

HISTORY_RESOURCE = ':/qt/qml/OneMeUi/HistoryListView.qml'
RESOURCES = (HISTORY_RESOURCE,)
# Exact immutable history after the existing bounded-history-cache transform.
HISTORY_SHA256 = 'ec86adf33c76b96dff6546b416a6620fdd2a67fb29f2dd114ad1b94de20ca304'
MARKER = '// VALMAX_SCROLL_WORK_BUDGET'
HISTORY_CONTENT_ANCHOR = '''            onContentYChanged: {
                if (!(isPrimaryShiftLocked && root.hasFocusableTopWidget)) {
                    updateLastVisible();
                }
            }
'''
HISTORY_CONTENT_HANDLER = '''            // VALMAX_SCROLL_WORK_BUDGET
            onContentYChanged: {
                if (!(isPrimaryShiftLocked && root.hasFocusableTopWidget)) {
                    Qt.callLater(root.updateLastVisible);
                }
            }
'''
HISTORY_HEIGHT_ANCHOR = '''            onHeightChanged: {
                updateLastVisible();
            }
'''
HISTORY_HEIGHT_HANDLER = '''            onHeightChanged: {
                Qt.callLater(root.updateLastVisible);
            }
'''


def checked(source: str, expected: str) -> str:
    if type(source) is not str or MARKER in source:
        raise ValueError('Scroll transform requires pinned, unmodified input')
    source = source.replace('\r\n', '\n')
    if hashlib.sha256(source.encode('utf8')).hexdigest() != expected:
        raise ValueError('Unexpected native scroll resource/order')
    return source


def once(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError('Unexpected native scroll anchor')
    return source.replace(anchor, replacement, 1)


def transform_history(source: str) -> str:
    source = checked(source, HISTORY_SHA256)
    source = once(source, HISTORY_CONTENT_ANCHOR, HISTORY_CONTENT_HANDLER)
    return once(source, HISTORY_HEIGHT_ANCHOR, HISTORY_HEIGHT_HANDLER)


def transform_sources(sources: dict[str, str]) -> dict[str, str]:
    if type(sources) is not dict or 'HistoryListView.qml' not in sources:
        raise ValueError('Pinned history resource is required')
    result = dict(sources)
    result['HistoryListView.qml'] = transform_history(result['HistoryListView.qml'])
    return result
