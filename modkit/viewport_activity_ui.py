"""Version-pinned viewport activity guards for retained native row contents.

Apply ChatDelegate after chat_lock_ui and chat_pool_ui. Native observers can
remain geometrically inViewport while the visual body/page/window is hidden.
"""
import hashlib

PREFIX = ":/qt/qml/OneMeUi/"
RESOURCES = (PREFIX + "ChatDelegate.qml", PREFIX + "PresenceTextStatusNew.qml")
CHAT_INPUT_SHA256 = '1c9c11bd58e19d53603ee2cfec909b8551e0e356774f4916aafb32e463802db8'
PRESENCE_INPUT_SHA256 = '1afccfdd81525dca7aaab4a462b625fe4f1c96585f89974847f72ca031f15f57'
MARKER = "// VALMAX_VISUAL_VIEWPORT_ACTIVITY"
CHAT_VISUAL = """    // VALMAX_VISUAL_VIEWPORT_ACTIVITY
    readonly property bool valmaxVisualActive: root.visible && root.enabled && !root.valmaxPooled && !root.valmaxChatLockMask &&
        root.Window.window && root.Window.window.visible && root.Window.window.visibility !== Window.Minimized

"""
PRESENCE_VISUAL = """    // VALMAX_VISUAL_VIEWPORT_ACTIVITY
    readonly property bool valmaxVisualActive: root.visible && root.enabled && root.Window.window &&
        root.Window.window.visible && root.Window.window.visibility !== Window.Minimized

"""
CHAT_REPLACEMENTS = (
    ("return avatarItem.inViewport;", "return root.valmaxVisualActive && avatarItem.inViewport;", 4),
    ("return avatarViewport.inViewport;", "return root.valmaxVisualActive && avatarViewport.inViewport;", 1),
    ("return avatarViewport.badgeInViewport;", "return root.valmaxVisualActive && avatarViewport.badgeInViewport;", 1),
    ("readonly property bool badgeInViewport: inViewport && avatarItem.useOnlineBadge",
     "readonly property bool badgeInViewport: root.valmaxVisualActive && inViewport && avatarItem.useOnlineBadge", 1),
)

def checked(source, expected):
    if type(source) is not str:
        raise ValueError("Viewport activity requires text")
    source = source.replace("\r\n", "\n")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != expected:
        raise ValueError("Viewport activity source/version/order mismatch")
    return source

def replace_count(source, old, new, count):
    if source.count(old) != count:
        raise ValueError("Viewport activity anchor drift")
    return source.replace(old, new)

def transform_chat(source):
    source = checked(source, CHAT_INPUT_SHA256)
    source = replace_count(source, "    id: root\n\n", "    id: root\n\n" + CHAT_VISUAL, 1)
    for old, new, count in CHAT_REPLACEMENTS:
        source = replace_count(source, old, new, count)
    return source

def reverse_chat(source):
    for old, new, count in reversed(CHAT_REPLACEMENTS):
        source = replace_count(source, new, old, count)
    source = replace_count(source, "    id: root\n\n" + CHAT_VISUAL, "    id: root\n\n", 1)
    return checked(source, CHAT_INPUT_SHA256)

def transform_presence(source):
    source = checked(source, PRESENCE_INPUT_SHA256)
    source = replace_count(source, "import QtQuick\n", "import QtQuick\nimport QtQuick.Window\n", 1)
    source = replace_count(source, "    id: root\n\n", "    id: root\n\n" + PRESENCE_VISUAL, 1)
    return replace_count(source, "return root.inViewport;", "return root.valmaxVisualActive && root.inViewport;", 2)

def reverse_presence(source):
    source = replace_count(source, "return root.valmaxVisualActive && root.inViewport;", "return root.inViewport;", 2)
    source = replace_count(source, "    id: root\n\n" + PRESENCE_VISUAL, "    id: root\n\n", 1)
    source = replace_count(source, "import QtQuick\nimport QtQuick.Window\n", "import QtQuick\n", 1)
    return checked(source, PRESENCE_INPUT_SHA256)

def transform_sources(sources):
    if type(sources) is not dict or not {"ChatDelegate.qml", "PresenceTextStatusNew.qml"} <= sources.keys():
        raise ValueError("Native viewport activity resources missing")
    result = dict(sources)
    result["ChatDelegate.qml"] = transform_chat(result["ChatDelegate.qml"])
    result["PresenceTextStatusNew.qml"] = transform_presence(result["PresenceTextStatusNew.qml"])
    return result
