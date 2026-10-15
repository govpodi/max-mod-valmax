"""Pinned A/B candidate: recycle chat-list roots, release avatar resources in pool.
Input ChatDelegate is AFTER production chat_lock_ui.transform_delegate.
No native contexts/model roles are snapshotted, rewritten or detached manually.
"""
import hashlib
PREFIX = ":/qt/qml/OneMeUi/"
RESOURCES = (PREFIX + "ChatListView.qml", PREFIX + "ChatDelegate.qml")
CHAT_DELEGATE_SHA256 = '6308f57ca5f391a3abcf50345dff794760295e54ff5cf9559a37406805875302'
CHAT_LIST_SHA256 = '9190f2dbac9815ab6492fa0061f5673a43d9ceb4b8bc0b9af2e2a92df4b6abc6'
ROOT_ANCHOR = '    id: root\n'
ROOT_INSERT = '\n    // VALMAX_CHAT_POOL_CANDIDATE\n    property bool valmaxPooled: false\n    enabled: !valmaxPooled\n    hoverEnabled: !valmaxPooled\n    cursorShapeEnabled: !valmaxPooled\n\n    function valmaxPool() {\n        valmaxPooled = true;\n    }\n    function valmaxSyncMessageContext() {\n        const current = messageContext;\n        hasMessage = current !== null && current !== undefined && !current.isNull;\n        lastMessageContext = current !== null && current !== undefined ? current : MessageUtils.emptyMessageContext();\n    }\n    function valmaxReuse() {\n        valmaxSyncMessageContext();\n        valmaxPooled = false;\n    }\n\n'
MESSAGE_ANCHOR = '    onMessageContextChanged: {\n        hasMessage = !messageContext.isNull;\n        lastMessageContext = messageContext;\n    }\n'
MESSAGE_HANDLER = '    onMessageContextChanged: valmaxSyncMessageContext()\n'
AVATAR_ANCHOR = '        Loader {\n            id: avatar\n'
AVATAR_HANDLER = '        Loader {\n            id: avatar\n            active: !root.valmaxPooled\n'
LIST_ID_ANCHOR = '            id: allChatsListView\n'
LIST_ID_HANDLER = '            id: allChatsListView\n            reuseItems: true\n'
LIST_DELEGATE_ANCHOR = '                id: chatDelegate\n'
LIST_DELEGATE_HANDLER = '                id: chatDelegate\n                ListView.onPooled: {\n                    chatDelegate.valmaxPool();\n                    allChatsListView.delegateRemoved(chatDelegate.chatId);\n                }\n                ListView.onReused: chatDelegate.valmaxReuse()\n'

def once(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError("Chat-pool anchor mismatch")
    return source.replace(anchor, replacement, 1)


def checked(source, expected):
    if type(source) is not str:
        raise ValueError("Chat-pool source must be text")
    normalized = source.replace("\r\n", "\n")
    if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != expected:
        raise ValueError("Chat-pool source/version/order mismatch")
    return normalized


def transform_delegate(source):
    source = checked(source, CHAT_DELEGATE_SHA256)
    source = once(source, ROOT_ANCHOR, ROOT_ANCHOR + ROOT_INSERT)
    source = once(source, AVATAR_ANCHOR, AVATAR_HANDLER)
    return once(source, MESSAGE_ANCHOR, MESSAGE_HANDLER)


def transform_chat_list(source):
    source = checked(source, CHAT_LIST_SHA256)
    source = once(source, LIST_ID_ANCHOR, LIST_ID_HANDLER)
    return once(source, LIST_DELEGATE_ANCHOR, LIST_DELEGATE_HANDLER)


def reverse_delegate(source):
    source = once(source, MESSAGE_HANDLER, MESSAGE_ANCHOR)
    source = once(source, AVATAR_HANDLER, AVATAR_ANCHOR)
    source = once(source, ROOT_ANCHOR + ROOT_INSERT, ROOT_ANCHOR)
    return checked(source, CHAT_DELEGATE_SHA256)


def reverse_chat_list(source):
    source = once(source, LIST_DELEGATE_HANDLER, LIST_DELEGATE_ANCHOR)
    source = once(source, LIST_ID_HANDLER, LIST_ID_ANCHOR)
    return checked(source, CHAT_LIST_SHA256)


def transform_sources(sources):
    if "ChatListView.qml" not in sources or "ChatDelegate.qml" not in sources:
        raise ValueError("Chat-pool native resource missing")
    result = dict(sources)
    result["ChatListView.qml"] = transform_chat_list(result["ChatListView.qml"])
    result["ChatDelegate.qml"] = transform_delegate(result["ChatDelegate.qml"])
    return result
