"""Pinned composition after quick_replies_ui, no synthetic MAX inline bot API."""
from pathlib import Path


def once(source: str, anchor: str, replacement: str) -> str:
    if source.count(anchor) != 1:
        raise ValueError("Unverified inline-media QML anchor: " + anchor[:80])
    return source.replace(anchor, replacement, 1)


def transform_sources(sources: dict[str, str], directory: Path | None = None) -> dict[str, str]:
    result = dict(sources)
    base = result["BaseSendPanel.qml"].replace("\r\n", "\n")
    if "modInlineRows" in base or "modQuickReplyAttachmentIntegration" not in base:
        raise ValueError("Inline media requires exactly one preceding quick-replies transform")
    snippet = ((directory or Path(__file__).parent) / "inline_media_composer.qml").read_text(encoding="utf-8")
    declarations, menu = snippet.split("// BEGIN INLINE MEDIA MENU", 1)
    menu, suffix = menu.split("// END INLINE MEDIA MENU", 1)
    if suffix.strip():
        raise ValueError("Unexpected inline media source suffix")
    base = once(base, "    // formats\n", declarations + "\n    // formats\n")
    base = once(base, "        UiKit.MeLoader {\n            id: mentionsListLoader", menu.strip() + "\n        UiKit.MeLoader {\n            id: mentionsListLoader")
    base = once(base, "    readonly property var activeCompletionList: {\n", "    readonly property var activeCompletionList: {\n        if (root.modInlineVisible) return modInlineLoader.item || null;\n")
    base = once(base, "activeAndVisible: root.canOpenMentionList && chatInput.mentionStarted", "activeAndVisible: root.canOpenMentionList && chatInput.mentionStarted && !root.modInlineVisible")
    base = once(base, "quickRepliesStarted: root.modQuickRepliesVisible", "quickRepliesStarted: root.modQuickRepliesVisible || root.modInlineVisible")
    base = once(base, "onQuickRepliesCancelPressed: root.modQuickRepliesDismissedQuery = chatInput.text", "onQuickRepliesCancelPressed: { if (root.modInlineVisible) root.modInlineClose(); else root.modQuickRepliesDismissedQuery = chatInput.text; }")
    base = once(base, "onCurrentOpenedChatIdChanged: modQuickReplySelected = null", "onCurrentOpenedChatIdChanged: { modQuickReplySelected = null; modInlineReset(true); }")
    base = once(base, "onModQuickReplyAccountIdChanged: modQuickReplySelected = null", "onModQuickReplyAccountIdChanged: { modQuickReplySelected = null; modInlineReset(true); }")
    # Reuse the existing single destruction handler; avoid duplicate QML handlers.
    base = once(base, "Component.onDestruction: {\n    if (modQuickRepliesRequest", "Component.onDestruction: {\n    modInlineReset(false);\n    if (modQuickRepliesRequest")
    result["BaseSendPanel.qml"] = base
    return result
