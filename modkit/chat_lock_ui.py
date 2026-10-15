"""Pinned native QML opening locks. This does not encrypt message history."""
from pathlib import Path

PREFIX = ":/qt/qml/OneMeUi/"
RESOURCES = tuple(PREFIX + name for name in ("MeWindow.qml", "MessengerView.qml", "ContactMoreMenu.qml", "ChatDelegate.qml", "SearchDelegate.qml"))
CONTROLLER_RESOURCE = ":/valmax/ChatLockController.qml"
MARKER = "// VALMAX_CHAT_LOCK"


def once(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError("Unexpected native chat-lock anchor")
    return source.replace(anchor, replacement, 1)


def checked(source):
    if type(source) is not str or MARKER in source:
        raise ValueError("Chat lock requires an unmodified lock transform")
    return source.replace("\r\n", "\n")


def controller_source():
    return Path(__file__).with_name("chat_lock_controller.qml").read_text(encoding="utf8").replace("\r\n", "\n")


HOST = '''    // VALMAX_CHAT_LOCK_HOST
    readonly property var valmaxChatLockController: valmaxChatLockLoader.item
    Loader {
        id: valmaxChatLockLoader
        source: "qrc:/valmax/ChatLockController.qml"
        asynchronous: false
        onLoaded: {
            item.hostItem = root.Overlay.overlay;
            item.colorTokens = ClientAppearance.AppearanceTokens;
        }
    }
    Connections {
        target: appDelegate
        function onFocusLost() { if (valmaxChatLockLoader.item) valmaxChatLockLoader.item.lockAll(); }
    }
    Connections {
        target: root
        function onVisibilityChanged() {
            if ((!root.visible || root.visibility === Window.Minimized) && valmaxChatLockLoader.item)
                valmaxChatLockLoader.item.lockAll();
        }
    }
'''

SCOPE = '''    // VALMAX_CHAT_LOCK_SCOPE
    property var valmaxOwnedChatPreview: null
    property string valmaxOwnedChatPreviewId: ""
    readonly property var valmaxChatLockController: root.Window.window ? root.Window.window.valmaxChatLockController : null
    readonly property string valmaxChatLockAccount: {
        try { return String(QmlUtils.contactIdString(root.commonDependency.selfInfo.id)); }
        catch (_) { return ""; }
    }
    readonly property string valmaxChatLockChat: {
        try { return root.hasOpenedChat ? String(QmlUtils.chatIdString(root.chatPropertiesContext.get().chatId)) : ""; }
        catch (_) { return ""; }
    }
    readonly property bool valmaxChatBlocked: !valmaxChatLockController || !valmaxChatLockController.ready ||
        (root.hasOpenedChat && valmaxChatLockController.isLocked(valmaxChatLockAccount, valmaxChatLockChat))
    readonly property bool valmaxChatProtected: root.hasOpenedChat && valmaxChatLockController &&
        valmaxChatLockController.ready && valmaxChatLockController.isProtected(valmaxChatLockAccount, valmaxChatLockChat)
    function valmaxSynchronizeChatLock() {
        if (!valmaxChatLockController) return;
        if (root.isActiveAccount) valmaxChatLockController.setCurrentScope(valmaxChatLockAccount, valmaxChatLockChat);
        else {
            valmaxChatLockController.releaseAccount(valmaxChatLockAccount);
            valmaxCloseOwnedChatPreview();
        }
    }
    function valmaxCloseOwnedChatPreview() {
        if (valmaxOwnedChatPreview) {
            try { valmaxOwnedChatPreview.close(); } catch (_) {}
            valmaxOwnedChatPreview = null;
        }
        valmaxOwnedChatPreviewId = "";
    }
    function valmaxCheckOwnedChatPreview() {
        if (valmaxOwnedChatPreview && (!valmaxChatLockController ||
                valmaxChatLockController.isLocked(valmaxChatLockAccount, valmaxOwnedChatPreviewId))) valmaxCloseOwnedChatPreview();
    }
    Connections {
        target: root.valmaxChatLockController
        function onReadyChanged() { root.valmaxCheckOwnedChatPreview(); }
        function onRevisionChanged() { root.valmaxCheckOwnedChatPreview(); }
        function onClockMsChanged() { root.valmaxCheckOwnedChatPreview(); }
    }
    onValmaxChatLockControllerChanged: valmaxSynchronizeChatLock()
    onValmaxChatLockAccountChanged: valmaxSynchronizeChatLock()
    onValmaxChatLockChatChanged: valmaxSynchronizeChatLock()
'''

GATE = '''    // VALMAX_CHAT_LOCK_GATE
    // The native MeStackView has no visibility/enabled binding of its own.
    // Restoring its effective false value while an ancestor is hidden can
    // leave it hidden permanently. Drive both states in both directions.
    Binding { target: valmaxProtectedHistory.contentItem; property: "visible"; value: !root.valmaxChatBlocked; restoreMode: Binding.RestoreNone }
    Binding { target: valmaxProtectedHistory.contentItem; property: "enabled"; value: !root.valmaxChatBlocked; restoreMode: Binding.RestoreNone }
    Rectangle {
        objectName: "valmaxChatLockGate"
        anchors.fill: parent
        z: 100000
        visible: root.valmaxChatBlocked
        color: ClientAppearance.AppearanceTokens.colorBackgroundPrimary
        ColumnLayout {
            anchors.centerIn: parent
            width: Math.max(0, Math.min(360, parent.width - 2 * UiKit.DesignSystem.SpacingCommon._L))
            spacing: UiKit.DesignSystem.SpacingCommon._M
            UiKit.SvgIcon {
                Layout.alignment: Qt.AlignHCenter
                source: "qrc:/icon_pack/privacy.svg"
                fillColor: ClientAppearance.AppearanceTokens.colorIconSecondary
                imageWidth: UiKit.DesignSystem.SizeBase.px48
                imageHeight: UiKit.DesignSystem.SizeBase.px48
            }
            UiKit.MeText {
                Layout.fillWidth: true
                text: root.valmaxChatProtected ? "Чат защищён PIN" : "Проверяем доступ к чату…"
                typography: Typography.FontUtil.Styles.BodyStrong
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                color: ClientAppearance.AppearanceTokens.colorTextPrimary
            }
            UiKit.MeText {
                Layout.fillWidth: true
                text: root.valmaxChatProtected ? "Введите PIN, чтобы открыть этот чат." : "Подождите, пока завершится проверка."
                typography: Typography.FontUtil.Styles.Description
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                color: ClientAppearance.AppearanceTokens.colorTextMute
            }
            UiKit.MePrimaryButton {
                Layout.fillWidth: true
                visible: root.valmaxChatProtected
                text: "Ввести PIN"
                onClicked: root.valmaxChatLockController.openUnlock(root.valmaxChatLockAccount, root.valmaxChatLockChat)
            }
            UiKit.MeSecondaryButton {
                Layout.fillWidth: true
                visible: !root.valmaxChatLockController || !root.valmaxChatLockController.ready
                enabled: root.valmaxChatLockController !== null
                text: "Повторить проверку"
                onClicked: root.valmaxChatLockController.refreshCatalog()
            }
            UiKit.MeGhostButton {
                Layout.fillWidth: true
                visible: root.hasOpenedChat
                text: "Назад к чатам"
                onClicked: root.accountDependency.historyPanelDependency.openedChatsContext.clear()
            }
        }
    }
'''

DELEGATE_MASK = '''    // VALMAX_CHAT_LOCK_DELEGATE
    readonly property var valmaxChatLockController: root.Window.window ? root.Window.window.valmaxChatLockController : null
    readonly property string valmaxChatLockAccount: {
        try { return String(QmlUtils.contactIdString(root.commonDependency.selfInfo.id)); }
        catch (_) { return ""; }
    }
    readonly property string valmaxChatLockChat: {
        try { return String(QmlUtils.chatIdString(root.chatId)); }
        catch (_) { return ""; }
    }
    readonly property bool valmaxChatLockMask: !valmaxChatLockController || valmaxChatLockController.maskScope(valmaxChatLockAccount, valmaxChatLockChat)
    Binding { target: root.contentItem; property: "visible"; value: !root.valmaxChatLockMask; restoreMode: Binding.RestoreNone }
    Binding { target: root.contentItem; property: "enabled"; value: !root.valmaxChatLockMask; restoreMode: Binding.RestoreNone }
    Rectangle {
        anchors.fill: parent
        z: 10000
        visible: root.valmaxChatLockMask
        color: ClientAppearance.AppearanceTokens.colorBackgroundPrimary
        Text {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: 20
            text: "Чат защищён PIN"
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: ClientAppearance.AppearanceTokens.colorTextMute
        }
    }
'''


def transform_window(source):
    return once(checked(source), "ApplicationWindow {\n    id: root\n", "ApplicationWindow {\n    id: root\n\n" + HOST)


def transform_messenger(source):
    source = checked(source)
    if source.count("    contentItem: SplitView {\n") != 1:
        raise ValueError("Unexpected native messenger layout")
    source = once(source, "import QtQuick\n", "import QtQuick\nimport QtQuick.Window\n")
    source = once(source, "import QtQuick.Layouts\n", "import QtQuick.Layouts\nimport TypographyModule as Typography\n")
    source = once(source, "Control {\n    id: root\n", "Control {\n    id: root\n\n" + SCOPE)
    source = once(source, "    onIsActiveAccountChanged: {\n", "    onIsActiveAccountChanged: {\n        valmaxSynchronizeChatLock();\n")
    source = once(source, "    Component.onCompleted: {\n", "    Component.onCompleted: {\n        valmaxSynchronizeChatLock();\n")
    source = once(source, "    Component.onDestruction: {\n", "    Component.onDestruction: {\n        if (valmaxChatLockController) valmaxChatLockController.releaseAccount(valmaxChatLockAccount);\n")
    source = once(source, "        function onChatPreviewClicked(chatId, lastReadMessage) {\n", '''        function onChatPreviewClicked(chatId, lastReadMessage) {
            if (!root.valmaxChatLockController || root.valmaxChatLockController.isLocked(root.valmaxChatLockAccount, String(QmlUtils.chatIdString(chatId)))) {
                root.snackbarManager.showError("Откройте чат и введите PIN для просмотра.");
                return;
            }
''')
    source = once(source, "            chatPreviewDialog.createObject(root.Overlay.overlay, {\n", '''            root.valmaxCloseOwnedChatPreview();
            root.valmaxOwnedChatPreviewId = String(QmlUtils.chatIdString(chatId));
            root.valmaxOwnedChatPreview = chatPreviewDialog.createObject(root.Overlay.overlay, {
''')
    # Keep the leading list and settings available. Only this history page's
    # native contents are hidden; the gate belongs to its StackView lifecycle.
    return once(source, "        id: historyPage\n\n        HistoryPanel {\n",
                "        id: historyPage\n\n        HistoryPanel {\n            id: valmaxProtectedHistory\n" + GATE + "\n")


def transform_menu(source):
    source = checked(source)
    if "import QtQuick.Window\n" not in source:
        source = once(source, "import QtQuick\n", "import QtQuick\nimport QtQuick.Window\n")
    item = '''    // VALMAX_CHAT_LOCK_MENU
    UiKit.ActionMenuItem {
        text: "PIN-код чата"
        iconSource: "qrc:/icon_pack/privacy.svg"
        onTriggered: {
            let opened = false;
            try {
                const window = Overlay.overlay.Window.window;
                if (window.valmaxChatLockController) opened = window.valmaxChatLockController.openSettings(
                    String(QmlUtils.contactIdString(root.commonDependency.selfInfo.id)),
                    String(QmlUtils.chatIdString(root.chatId)), root.chatName);
            } catch (_) {}
            if (!opened) { root.commonDependency.snackbarManager.showError("Защита чатов пока недоступна. Повторите проверку."); return; }
            root.close(); root.finished();
        }
    }

'''
    if "// VALMAX_CHAT_EXPORT" in source:
        source = once(source, "            if (root.valmaxExportOpening) return;\n", '''            try {
                const locks = Overlay.overlay.Window.window.valmaxChatLockController;
                if (!locks || locks.isLocked(String(QmlUtils.contactIdString(root.commonDependency.selfInfo.id)), String(QmlUtils.chatIdString(root.chatId)))) {
                    root.commonDependency.snackbarManager.showError("Откройте чат и введите PIN перед экспортом.");
                    return;
                }
            } catch (_) { return; }
            if (root.valmaxExportOpening) return;
''')
    return once(source, "    delegate: UiKit.ActionMenuItem {}\n", "    delegate: UiKit.ActionMenuItem {}\n\n" + item)


def transform_delegate(source):
    source = checked(source)
    source = once(source, "import QtQuick\n", "import QtQuick\nimport QtQuick.Window\n")
    return once(source, "UiKit.MeHoverableDelegate {\n    id: root\n", "UiKit.MeHoverableDelegate {\n    id: root\n\n" + DELEGATE_MASK)


def transform_export_panel(source):
    """Guard an already open export popup, including a pending native chooser."""
    source = checked(source)
    source = once(source, "import QtQuick\n", "import QtQuick\nimport QtQuick.Window\n")
    source = once(source, '        objectName: "valmaxChatExportDialog"\n', '''        objectName: "valmaxChatExportDialog"
        // VALMAX_CHAT_LOCK_EXPORT
        readonly property var valmaxChatLocks: parent && parent.Window.window ? parent.Window.window.valmaxChatLockController : null
        readonly property bool valmaxExportAllowed: valmaxChatLocks && !valmaxChatLocks.isLocked(ownId, chatIdText)
        property bool valmaxExportLockInitialized: false
        Component.onCompleted: valmaxExportLockInitialized = true
        onOpened: if (!valmaxExportAllowed) exportDialog.close()
        onValmaxExportAllowedChanged: {
            if (valmaxExportLockInitialized && !valmaxExportAllowed) {
                destinationDialog.close();
                if (busy) cancelExport();
                exportDialog.close();
            }
        }
''')
    source = once(source, "        function startExport(directory) {\n", '''        function startExport(directory) {
            if (!valmaxExportAllowed) {
                destinationDialog.close();
                failureText = "Чат закрыт. Введите PIN перед экспортом.";
                return;
            }
''')
    source = once(source, "        onConfirmClicked: {\n", '''        onConfirmClicked: {
            if (!valmaxExportAllowed) return;
''')
    return source


def transform_sources(sources):
    result = dict(sources)
    functions = {"MeWindow.qml": transform_window, "MessengerView.qml": transform_messenger,
                 "ContactMoreMenu.qml": transform_menu, "ChatDelegate.qml": transform_delegate,
                 "SearchDelegate.qml": transform_delegate}
    for name, function in functions.items():
        if name not in result:
            raise ValueError("Chat-lock native resource missing")
        result[name] = function(result[name])
    return result
