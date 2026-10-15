"""Pinned row-instance retention marker; preserve compiled native bubble types."""
import hashlib
from pathlib import Path

PREFIX = ":/qt/qml/OneMeUi/"
RESOURCES = (PREFIX + "HistoryListView.qml",)
PRESERVED_COMPILED_RESOURCES = (':/qt/qml/OneMeUi/HistoryMessageDelegate.qml', ':/qt/qml/OneMeUi/VideoMessageBubbleContent.qml', ':/qt/qml/OneMeUi/MessageTimeCapsule.qml')
SOURCE_SHA256 = 'dbc7312be1073c14355715c4658187839d45f2093e4add09d1fbc1775ab4c7b2'
CONTROLLER_SHA256 = 'c53cd12aa5734adab819f17b92aa58f58726ab5f4b68ba51e19d440f85e0820b'
CONTROLLER_RESOURCE = ":/valmax/MessageRetentionController.qml"
LEADING_RESOURCE = PREFIX + "LeadingSettingsBar.qml"
MARKER = "// VALMAX_MESSAGE_RETENTION"
TOOLTIP = "Сообщение удалено · сохранено на этом устройстве"


def once(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError("Unexpected message-retention QML anchor")
    return source.replace(anchor, replacement, 1)


def untouched(source):
    if type(source) is not str or MARKER in source:
        raise ValueError("Message retention requires untouched native QML")
    return source.replace("\r\n", "\n")


def controller_source():
    raw = Path(__file__).with_name("message_retention_controller.qml").read_bytes()
    if hashlib.sha256(raw).hexdigest() != CONTROLLER_SHA256:
        raise ValueError("Message-retention controller source drift")
    return raw.decode("utf8").replace("\r\n", "\n")


def transform_leading(source):
    if type(source) is not str or 'settingsValMAXRetention' in source:
        raise ValueError("Message retention settings require untouched entry")
    source = source.replace("\r\n", "\n")
    panel = Path(__file__).with_name('message_retention_settings_panel.qml').read_text(encoding='utf8').replace('\r\n', '\n')
    anchor = "    Component {\n        id: settingsDev\n"
    source = once(source, anchor, panel.rstrip() + '\n\n' + anchor)
    entry_anchor = '''                OpenPageSettingsCellImpl {
                    title: qsTranslate("Settings", "Privacy")
'''
    entry = '''                OpenPageSettingsCellImpl {
                    title: "Удалённые сообщения"
                    iconSource: "qrc:/icon_pack/delete_fill.svg"
                    settingsPage: settingsValMAXRetention
                }

'''
    return once(source, entry_anchor, entry + entry_anchor)


HOST = '''    // VALMAX_MESSAGE_RETENTION
    Loader {
        id: valmaxRetentionLoader
        source: "qrc:/valmax/MessageRetentionController.qml"
        onLoaded: {
            item.accountId = Qt.binding(function() { return String(QmlUtils.contactIdString(root.commonDependency.selfInfo.id)); });
            item.chatId = Qt.binding(function() { return String(QmlUtils.chatIdString(root.chatPropertiesContext.get().chatId)); });
            item.active = Qt.binding(function() { return root.visible && root.isActiveChat && root.isHistoryPanelActive && root.window && root.window.visible && root.window.visibility !== Window.Minimized; });
        }
    }
'''

DELEGATE_BINDING = '''
                readonly property var valmaxRetentionRegistrar: valmaxRetentionLoader.item
                readonly property int valmaxRetentionScope: valmaxRetentionRegistrar ? valmaxRetentionRegistrar.scopeGeneration : -1
                readonly property string valmaxRetentionMessageId: {
                    try { return String(QmlUtils.messageIdString(messageContext.get().messageId)); }
                    catch (_) { return ""; }
                }
                readonly property string valmaxRetentionMessageChatId: {
                    try { return String(QmlUtils.chatIdString(messageContext.get().chatId)); }
                    catch (_) { return ""; }
                }
                readonly property string valmaxRetentionControllerChatId: valmaxRetentionRegistrar ? valmaxRetentionRegistrar.chatId : ""
                property var valmaxRegisteredRegistrar: null
                property string valmaxRegisteredMessageId: ""
                property int valmaxRegisteredScope: -1
                property bool valmaxRetentionInitialized: false
                property bool valmaxRetentionSyncing: false
                readonly property bool valmaxRetainedDeleted: valmaxRetentionRegistrar && valmaxRetentionMessageChatId === valmaxRetentionRegistrar.chatId ? valmaxRetentionRegistrar.contains(valmaxRetentionMessageId) : false
                function valmaxSyncRetentionRegistration() {
                    if (!valmaxRetentionInitialized || valmaxRetentionSyncing) return;
                    const registrar = valmaxRetentionRegistrar && valmaxRetentionMessageChatId === valmaxRetentionRegistrar.chatId ? valmaxRetentionRegistrar : null;
                    const currentScope = registrar ? registrar.scopeGeneration : -1;
                    const desiredId = registrar ? valmaxRetentionMessageId : "";
                    if (valmaxRegisteredRegistrar === registrar && valmaxRegisteredMessageId === desiredId && valmaxRegisteredScope === currentScope) return;
                    valmaxRetentionSyncing = true;
                    try {
                        if (valmaxRegisteredRegistrar && valmaxRegisteredMessageId) valmaxRegisteredRegistrar.unregister(valmaxRegisteredMessageId, valmaxRegisteredScope);
                        valmaxRegisteredRegistrar = null; valmaxRegisteredMessageId = ""; valmaxRegisteredScope = -1;
                        if (registrar && valmaxRetentionMessageId) {
                            const scope = registrar.register(valmaxRetentionMessageId);
                            if (scope >= 0) { valmaxRegisteredRegistrar = registrar; valmaxRegisteredMessageId = valmaxRetentionMessageId; valmaxRegisteredScope = scope; }
                        }
                    } finally { valmaxRetentionSyncing = false; }
                }
                onValmaxRetentionRegistrarChanged: valmaxSyncRetentionRegistration()
                onValmaxRetentionScopeChanged: valmaxSyncRetentionRegistration()
                onValmaxRetentionMessageIdChanged: valmaxSyncRetentionRegistration()
                onValmaxRetentionMessageChatIdChanged: valmaxSyncRetentionRegistration()
                onValmaxRetentionControllerChatIdChanged: valmaxSyncRetentionRegistration()
                Component.onDestruction: {
                    valmaxRetentionInitialized = false;
                    if (valmaxRegisteredRegistrar && valmaxRegisteredMessageId) valmaxRegisteredRegistrar.unregister(valmaxRegisteredMessageId, valmaxRegisteredScope);
                    valmaxRegisteredRegistrar = null;
                }
'''


OUTPUT_SHA256 = 'b3917edef153c8967f0ae9db8888dacd8041d1aaa7d78b8cb4ce13de25951b0f'

BADGE = '\n                // VALMAX_RETENTION_ROW_BADGE: own gutter overlay, no native bubble layout changes.\n                Loader {\n                    id: valmaxRetainedBadgeLoader\n                    objectName: "valmaxRetainedBadgeLoader"\n                    readonly property bool valmaxVisuallyOutgoing: messageDelegate.viewContext.get().isVisuallyOutgoing\n                    parent: messageDelegate\n                    active: messageDelegate.valmaxRetainedDeleted\n                    visible: active && status === Loader.Ready && width > 0\n                    width: Math.max(0, Math.min(20, UiKit.DesignSystem.SpacingCommon._L - 4, Math.max(0, messageDelegate.height)))\n                    height: width\n                    x: valmaxRetainedBadgeLoader.valmaxVisuallyOutgoing ? Math.max(0, messageDelegate.width - width - 2) : 2\n                    y: Math.max(0, messageDelegate.height - height - 2)\n                    z: 1\n                    sourceComponent: Item {\n                        id: valmaxRetainedBadge\n                        objectName: "valmaxRetainedDeleteBadge"\n                        clip: true\n                        Accessible.name: "Сообщение удалено · сохранено на этом устройстве"\n                        Accessible.ignored: !visible\n                        ToolTip.text: "Сообщение удалено · сохранено на этом устройстве"\n                        ToolTip.visible: valmaxRetainedBadgeHover.hovered\n                        HoverHandler { id: valmaxRetainedBadgeHover }\n                        Rectangle {\n                            anchors.fill: parent\n                            radius: width / 2\n                            color: valmaxRetainedBadgeLoader.valmaxVisuallyOutgoing ? messageDelegate.chatAppearance.colorBubblesOutgoingBackgroundSurfaceSecondary : messageDelegate.chatAppearance.colorBubblesIncomingBackgroundSurfaceSecondary\n                        }\n                        UiKit.SvgIcon {\n                            anchors.centerIn: parent\n                            enabled: false\n                            Accessible.ignored: true\n                            imageHeight: 12\n                            imageWidth: 12\n                            source: "qrc:/icon_pack/delete_fill.svg"\n                            fillColor: valmaxRetainedBadgeLoader.valmaxVisuallyOutgoing ? messageDelegate.chatAppearance.colorBubblesOutgoingTextBodySecondary : messageDelegate.chatAppearance.colorBubblesIncomingTextBodySecondary\n                        }\n                    }\n                }\n'
EDITS = (
    ("import QtQuick\n", "import QtQuick\nimport QtQuick.Window\n"),
    ("    id: root\n", "    id: root\n\n" + HOST),
    ("                id: messageDelegate\n", "                id: messageDelegate\n" + DELEGATE_BINDING + BADGE),
    ("                Component.onCompleted: tryReadReaction()\n", "                Component.onCompleted: { valmaxRetentionInitialized = true; valmaxSyncRetentionRegistration(); tryReadReaction(); }\n"),
)


def checked(source, expected):
    if type(source) is not str:
        raise ValueError("Retention history requires text")
    source = source.replace("\r\n", "\n")
    if hashlib.sha256(source.encode("utf8")).hexdigest() != expected:
        raise ValueError("Retention history version/source/order mismatch")
    return source


def transform_history(source):
    source = checked(source, SOURCE_SHA256)
    for before, after in EDITS:
        source = once(source, before, after)
    return checked(source, OUTPUT_SHA256)


def reverse_history(source):
    source = checked(source, OUTPUT_SHA256)
    for before, after in reversed(EDITS):
        source = once(source, after, before)
    return checked(source, SOURCE_SHA256)


def transform_sources(sources):
    if type(sources) is not dict or "HistoryListView.qml" not in sources:
        raise ValueError("Pinned history resource is required")
    result = dict(sources)
    result["HistoryListView.qml"] = transform_history(result["HistoryListView.qml"])
    return result
