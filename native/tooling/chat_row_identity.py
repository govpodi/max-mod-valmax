"""Pinned native-only chat-row identity/lifetime compatibility transform.

Input is the completed native ChatListView resource, after the existing native
transport and optimization transforms.  The ordinary generators remain intact.
Only the lightweight chat shell's recycling and lifetime identity change; its
role forwarding, navigation, popup actions, queue and asynchronous body stay
unchanged.  No client, account data or filesystem access occurs here.
"""
from __future__ import annotations

import hashlib


RESOURCE = ":/qt/qml/OneMeUi/ChatListView.qml"
INPUT_SHA256 = "b8a86a76a5365d9b82a39078318dfc943c26b250eb15f24a6308883c35c36296"
OUTPUT_SHA256 = "4676c79fa38e23441a2c5039bd33b294fab3dad4de2b18680584f9d9f21ffded"
MARKER = "// VALMAX_CHAT_ROW_IDENTITY_V1"

_IDENTITY_BEFORE = '''                identityKey: {
                    try { return String(QmlUtils.contactIdString(commonDependency.selfInfo.id)) + ":" + String(QmlUtils.chatIdString(chatId)); }
                    catch (_) { return ""; }
                }
'''
_IDENTITY_AFTER = '''                identityKey: {
                    try {
                        const accountKey = String(QmlUtils.contactIdString(commonDependency.selfInfo.id));
                        const chatKey = String(QmlUtils.chatIdString(chatId));
                        let clientKey = "";
                        let contactKey = "";
                        try {
                            if (chatClientId !== null && chatClientId !== undefined)
                                clientKey = String(QmlUtils.clientChatIdString(chatClientId));
                        } catch (_) {}
                        try {
                            if (contactId !== null && contactId !== undefined)
                                contactKey = String(QmlUtils.contactIdString(contactId));
                        } catch (_) {}
                        return JSON.stringify([
                            accountKey, chatKey, clientKey, contactKey, String(index)
                        ]);
                    } catch (_) { return ""; }
                }
                function valmaxResetOwner() {
                    valmaxClearInput();
                    if (valmaxCompleted && !valmaxClosing) valmaxResetIdentity();
                }
'''

_EDITS = (
    (
        '            reuseItems: ValMAXOptimization.OptimizationConfig.isEnabled("listScheduling")\n',
        "            " + MARKER + "\n"
        "            // Recreate cheap shells; keep queued asynchronous bodies and bounded prefetch.\n"
        "            reuseItems: false\n",
    ),
    (
        "                required property var chatClientId\n",
        "                required property int index\n"
        "                required property var chatClientId\n",
    ),
    (_IDENTITY_BEFORE, _IDENTITY_AFTER),
    (
        "                    target: chatDelegate\n"
        "                    function onIdentityKeyChanged() {\n",
        "                    target: chatDelegate\n"
        "                    function onCommonDependencyChanged() { chatDelegate.valmaxResetOwner(); }\n"
        "                    function onChatClientIdChanged() { chatDelegate.valmaxResetOwner(); }\n"
        "                    function onContactIdChanged() { chatDelegate.valmaxResetOwner(); }\n"
        "                    function onIdentityKeyChanged() {\n",
    ),
    (
        "                ListView.onPooled: {\n",
        "                Connections {\n"
        "                    target: allChatsListView\n"
        "                    function onModelChanged() { chatDelegate.valmaxResetOwner(); }\n"
        "                }\n"
        "                ListView.onPooled: {\n",
    ),
)


def _text(source: str) -> str:
    if type(source) is not str:
        raise ValueError("Native chat-row identity source must be text")
    return source.replace("\r\n", "\n")


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf8")).hexdigest()


def _replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError("Native chat-row identity anchor changed")
    return source.replace(before, after, 1)


def apply(source: str) -> str:
    source = _text(source)
    if _digest(source) != INPUT_SHA256:
        raise ValueError("Native chat-row identity input/version/order mismatch")
    for before, after in _EDITS:
        source = _replace_once(source, before, after)
    if _digest(source) != OUTPUT_SHA256:
        raise ValueError("Native chat-row identity generated output mismatch")
    return source


def reverse(source: str) -> str:
    source = _text(source)
    if _digest(source) != OUTPUT_SHA256:
        raise ValueError("Native chat-row identity output/version mismatch")
    transformed = source
    for before, after in reversed(_EDITS):
        source = _replace_once(source, after, before)
    if _digest(source) != INPUT_SHA256 or apply(source) != transformed:
        raise ValueError("Native chat-row identity output changed")
    return source
