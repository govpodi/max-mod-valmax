"""Pinned four-row shell cushion for the three fixed-height native lists.

Apply after viewport_activity_ui. Input, native bodies, history and global SDK
remain unchanged. The shared row/queue owns visible-first load admission.
"""
import hashlib

PREFIX = ":/qt/qml/OneMeUi/"
PINS = {
    "ChatListView.qml": "7f521c0bc08aea1a99508ef49dd831a80348d6e8c768983a1286c7b9630b0de9",
    "ContactListView.qml": "154c7cbd075a6187a96ea1ea615f9a31e18d0af30b2ec7cf96eeecab81294ddd",
    "CallsListView.qml": "0baedbfbeb25c68c7f9b4c1612fa63cb39dc7f8d011129dc8d754b737a2bf942",
}
RESOURCES = tuple(PREFIX + name for name in PINS)
CHAT_HEIGHT = '''                rowHeight: Math.max(
                    Typography.FontUtil.getLineHeight(Typography.FontUtil.Styles.Body, valmaxTextSize)
                    + 2 * Typography.FontUtil.getLineHeight(Typography.FontUtil.Styles.BubbleDescription, valmaxTextSize)
                    + 2 * UiKit.DesignSystem.SpacingCommon._2XS,
                    valmaxStories ? UiKit.DesignSystem.AvatarSize.px64 : UiKit.DesignSystem.AvatarSize.px56)
                    + 2 * (valmaxStories ? UiKit.DesignSystem.SpacingCommon._S : 9)
'''
CHAT_CACHE = '''            id: allChatsListView
            // VALMAX_BOUNDED_PREFETCH_SHELLS
            readonly property bool valmaxPrefetchStories: root.commonDependency.serverParameters.storiesEnabled
            readonly property int valmaxPrefetchTextSize: root.commonDependency.userAppearanceContext.typographySize
            readonly property real valmaxPrefetchRowHeight: Math.max(
                Typography.FontUtil.getLineHeight(Typography.FontUtil.Styles.Body, valmaxPrefetchTextSize)
                + 2 * Typography.FontUtil.getLineHeight(Typography.FontUtil.Styles.BubbleDescription, valmaxPrefetchTextSize)
                + 2 * UiKit.DesignSystem.SpacingCommon._2XS,
                valmaxPrefetchStories ? UiKit.DesignSystem.AvatarSize.px64 : UiKit.DesignSystem.AvatarSize.px56)
                + 2 * (valmaxPrefetchStories ? UiKit.DesignSystem.SpacingCommon._S : 9)
            cacheBuffer: Math.max(320, 4 * valmaxPrefetchRowHeight)
'''
EDITS = {
    "ChatListView.qml": (
        ("            id: allChatsListView\n", CHAT_CACHE),
        (CHAT_HEIGHT, "                rowHeight: allChatsListView.valmaxPrefetchRowHeight\n"),
    ),
    "ContactListView.qml": (("            id: listView\n", "            id: listView\n            // VALMAX_BOUNDED_PREFETCH_SHELLS\n            cacheBuffer: Math.max(320, 4 * root.valmaxContactRowHeight)\n"),),
    "CallsListView.qml": (("    id: root\n", "    id: root\n    // VALMAX_BOUNDED_PREFETCH_SHELLS\n    cacheBuffer: Math.max(320, 4 * root.valmaxCallRowHeight)\n"),),
}


def checked(source, name):
    if type(source) is not str:
        raise ValueError("List prefetch requires source text")
    source = source.replace("\r\n", "\n")
    if hashlib.sha256(source.encode("utf8")).hexdigest() != PINS[name]:
        raise ValueError("List prefetch source/version/order mismatch")
    return source


def once(source, before, after):
    if source.count(before) != 1:
        raise ValueError("List prefetch anchor drift")
    return source.replace(before, after, 1)


def transform(source, name):
    source = checked(source, name)
    for before, after in EDITS[name]:
        source = once(source, before, after)
    return source


def reverse(source, name):
    for before, after in reversed(EDITS[name]):
        source = once(source, after, before)
    return checked(source, name)


def transform_sources(sources):
    if type(sources) is not dict or not PINS.keys() <= sources.keys():
        raise ValueError("Native fixed-list resources are missing")
    result = dict(sources)
    for name in PINS:
        result[name] = transform(result[name], name)
    return result
