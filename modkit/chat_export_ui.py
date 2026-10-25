"""Fail-closed native contact-menu transformation for chat export."""
from pathlib import Path

RESOURCE = ":/qt/qml/OneMeUi/ContactMoreMenu.qml"
WINDOW_RESOURCE = ":/qt/qml/OneMeUi/MeWindow.qml"
PANEL_RESOURCE = ":/valmax/ChatExportPanel.qml"
MARKER = "// VALMAX_CHAT_EXPORT"
IMPORTS = """import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQml.Models as ExportModels
import QtQuick.Dialogs as ExportDialogs
import UiKitModule as UiKit
import ClientAppearanceModule as ExportAppearance
import TypographyModule as ExportTypography
import PreviewGridModule as ExportPreview
import QmlSingletonesModule
import ru.oneme.components 1.0
"""


WINDOW_HOST = '''    // VALMAX_CHAT_EXPORT_HOST
    function openValMAXChatExport(properties) {
        if (valmaxChatExportLoader.active) return false;
        properties.parent = root.Overlay.overlay;
        valmaxChatExportLoader.setSource("qrc:/valmax/ChatExportPanel.qml", properties);
        valmaxChatExportLoader.active = true;
        return valmaxChatExportLoader.status !== Loader.Error;
    }
    Loader {
        id: valmaxChatExportLoader
        active: false
        asynchronous: false
        onLoaded: item.open()
        onStatusChanged: if (status === Loader.Error) { active = false; source = ""; }
    }
    Connections {
        target: valmaxChatExportLoader.item
        function onExportFinished() {
            Qt.callLater(function() { valmaxChatExportLoader.active = false; valmaxChatExportLoader.source = ""; });
        }
    }
'''


def factory_source():
    """Return the direct standalone panel, loaded in MeWindow's stable context."""
    return Path(__file__).with_name("chat_export_panel.qml").read_text(encoding="utf8").replace("\r\n", "\n")


def once(source, anchor, value):
    if source.count(anchor) != 1:
        raise ValueError("Unexpected chat-export native QML anchor")
    return source.replace(anchor, value, 1)


def transform(source):
    if type(source) is not str or MARKER in source:
        raise ValueError("Chat export needs untouched native contact menu")
    result = source.replace("\r\n", "\n")
    result = once(result, "import QmlSingletonesModule\n", "import QmlSingletonesModule\nimport QtQuick.Window\n")
    result = once(result, "    signal finished\n", "    signal finished\n    property bool valmaxExportOpening: false\n")
    anchor = '    UiKit.ActionMenuItem {\n        visible: !root.isBot && !root.chatIsBlocked && root.inContactList'
    item = '''    function valmaxExportScope() {
        try {
            const model = application.accountModel;
            const owner = root.commonDependency;
            const propertiesContext = root.chatPropertiesContext;
            const properties = propertiesContext.get();
            const nativeChatId = root.chatId;
            const ownId = String(QmlUtils.contactIdString(owner.selfInfo.id));
            const chatId = String(QmlUtils.chatIdString(nativeChatId));
            const propertyChatId = String(QmlUtils.chatIdString(properties.chatId));
            const contactId = String(QmlUtils.contactIdString(root.contactId));
            const propertyContactId = String(QmlUtils.contactIdString(properties.dialogContactId));
            const title = root.chatName;
            const accountKey = owner.selfInfo.accountId;
            if (root.chatType !== Chat.Type.Dialog || !/^-?[0-9]{1,20}$/.test(ownId)
                    || !/^-?[0-9]{1,20}$/.test(chatId) || chatId !== propertyChatId
                    || !/^-?[0-9]{1,20}$/.test(contactId)
                    || contactId !== propertyContactId || typeof title !== "string"
                    || properties.isPreventCopyingEnabled !== false
                    || accountKey === undefined || accountKey === null
                    || model.currentAccountId !== accountKey
                    || model.activeAccountDependency.commonDependency !== owner) return null;
            return {owner: owner, propertiesContext: propertiesContext, accountKey: accountKey,
                ownId: ownId, nativeChatId: nativeChatId, chatId: chatId,
                contactId: contactId, title: title};
        } catch (_) { return null; }
    }
    // VALMAX_CHAT_EXPORT
    UiKit.ActionMenuItem {
        text: "Скачать чат"
        iconSource: "qrc:/valmax/icons/chat-export.svg"
        enabled: !root.chatPropertiesContext.get().isPreventCopyingEnabled
        onTriggered: {
            if (root.valmaxExportOpening) return;
            const scope = root.valmaxExportScope();
            if (!scope) {
                try { root.commonDependency.snackbarManager.showError("Чат или аккаунт изменился. Откройте меню снова."); } catch (_) {}
                return;
            }
            root.valmaxExportOpening = true;
            let opened = false;
            try {
                // The persistent MeWindow Loader owns the panel's QML context.
                const window = Overlay.overlay.Window.window;
                opened = window.openValMAXChatExport({
                commonDependency: scope.owner,
                nativeChatId: scope.nativeChatId,
                nativeChatProperties: scope.propertiesContext,
                chatTitle: scope.title,
                selectedScope: scope
            });
            } catch (_) {}
            if (!opened) {
                root.valmaxExportOpening = false;
                root.commonDependency.snackbarManager.showError("Экспорт уже открыт или сейчас недоступен.");
                return;
            }
            root.close();
            root.finished();
        }
    }

'''
    result = once(result, anchor, item + anchor)
    if not result.endswith("}\n"):
        raise ValueError("Unexpected native contact menu terminator")
    return result


def transform_window(source):
    if type(source) is not str or "// VALMAX_CHAT_EXPORT_HOST" in source:
        raise ValueError("Chat export needs untouched native window host")
    return once(source.replace("\r\n", "\n"), "    id: root\n", "    id: root\n\n" + WINDOW_HOST)


def transform_sources(sources):
    result = dict(sources)
    name = RESOURCE.rsplit("/", 1)[1]
    window = WINDOW_RESOURCE.rsplit("/", 1)[1]
    if name not in result or window not in result:
        raise ValueError("Native contact menu resource missing")
    result[name] = transform(result[name])
    result[window] = transform_window(result[window])
    return result
