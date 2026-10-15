"""Bound off-screen chat delegates while preserving native live geometry."""
from __future__ import annotations

import hashlib

RESOURCE = ":/qt/qml/OneMeUi/HistoryListView.qml"
SOURCE_SHA256 = "4822391789c27219e91edd760a1ee769559bda9d6fbf66d0b6c7df6f154d5d57"
MARKER = "// VALMAX_BOUNDED_HISTORY_CACHE"
CACHE_ANCHOR = "            cacheBuffer: 500\n"
CACHE_DECLARATION = '''            // VALMAX_BOUNDED_HISTORY_CACHE
            // Retain the native insertion cushion, but a single tall message
            // must not retain thousands of off-screen pixels indefinitely.
            property real valmaxLargestDelegate: 500
            cacheBuffer: Math.max(500, Math.min(valmaxLargestDelegate, height * 2))
'''
GROWTH_ANCHOR = '''                    if (height > listView.cacheBuffer) {
                        listView.cacheBuffer = height; //r.zhilkin: prevent unnecessary item unloading upon insertion
                    }
'''
GROWTH_HANDLER = '''                    if (height > listView.valmaxLargestDelegate) {
                        listView.valmaxLargestDelegate = height;
                    }
'''


def transform(source: str) -> str:
    if type(source) is not str or MARKER in source:
        raise ValueError("History cache requires untouched pinned native QML")
    source = source.replace("\r\n", "\n")
    if hashlib.sha256(source.encode("utf8")).hexdigest() != SOURCE_SHA256:
        raise ValueError("Unexpected native history source")
    for anchor, replacement in ((CACHE_ANCHOR, CACHE_DECLARATION), (GROWTH_ANCHOR, GROWTH_HANDLER)):
        if source.count(anchor) != 1:
            raise ValueError("Unexpected native history cache anchor")
        source = source.replace(anchor, replacement, 1)
    return source


def transform_sources(sources: dict[str, str]) -> dict[str, str]:
    name = RESOURCE.rsplit("/", 1)[1]
    if type(sources) is not dict or name not in sources:
        raise ValueError("Pinned history resource is required")
    result = dict(sources)
    result[name] = transform(result[name])
    return result
