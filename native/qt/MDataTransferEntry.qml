import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import UiKitModule as UiKit
import ClientAppearanceModule as Appearance
import ValMAX.Native 1.0

Item {
    id: entry
    width: 0; height: 0; visible: false
    readonly property bool available: typeof TransferLauncher !== "undefined" && TransferLauncher.available
    readonly property bool busy: ownsHandoff || (typeof TransferLauncher !== "undefined" && TransferLauncher.busy)
    property bool ownsHandoff: false
    property var dialog: null
    property string statusText: ""
    function errorText(code) {
        if (code === "handoff_timeout") return "Запуск окна MData не подтверждён вовремя. ValMAX остаётся открыта. Проверь, не появилось ли отдельное окно входа или сообщение об ошибке.";
        return "Не удалось открыть вход с MData. ValMAX остаётся открыта. Проверь полную установку и повтори попытку.";
    }
    function open() {
        if (busy || dialog !== null) return;
        statusText = "";
        dialog = confirmation.createObject(Overlay.overlay);
        if (!dialog) { statusText = errorText("unavailable"); return; }
        dialog.open();
    }
    function confirm() {
        if (busy || !dialog) return;
        if (!available) { statusText = errorText("unavailable"); return; }
        ownsHandoff = true; statusText = "Открытие отдельного окна MData…";
        if (!TransferLauncher.confirmAndStartImport() && ownsHandoff) {
            ownsHandoff = false; statusText = errorText("unavailable");
        }
    }
    Connections {
        target: typeof TransferLauncher !== "undefined" ? TransferLauncher : null
        function onHandoffFinished(helperLaunched, error) {
            if (!entry.ownsHandoff) return;
            entry.ownsHandoff = false;
            if (!helperLaunched) { entry.statusText = entry.errorText(error); return; }
            entry.statusText = "Окно MData запущено. Завершаем ValMAX; импорт ещё не выполнялся.";
            // The confirmed C++ request owns graceful shutdown even if this
            // originating page has already been navigated away from/destroyed.
        }
    }
    Component.onDestruction: { if (dialog) dialog.close(); }
    Component {
        id: confirmation
        UiKit.BaseModalDialog {
            id: confirmDialog
            objectName: "nativeMDataHandoffConfirmation"
            chatAppearance: Appearance.AppearanceTokens
            minimumWidth: Math.min(440, parent ? parent.width - 32 : 440)
            margins: UiKit.DesignSystem.SpacingCommon._L
            title: "Войти с MData"
            subtitle: "ValMAX полностью закроется после запуска отдельного окна MData. В нём ты выберешь файл, введёшь пароль и отдельно подтвердишь добавление аккаунтов. Текущие аккаунты сохранятся."
            confirmText: "Открыть MData и закрыть ValMAX"
            cancelText: entry.busy ? "" : "Отмена"
            confirmButtonEnabled: !entry.busy
            confirmButtonLoading: entry.busy
            closePolicy: entry.busy ? Popup.NoAutoClose : Popup.CloseOnEscape | Popup.CloseOnPressOutside
            onConfirmClicked: entry.confirm()
            onCancelClicked: if (!entry.busy) close()
            onCloseClicked: if (!entry.busy) close()
            onClosed: {
                if (entry.dialog === confirmDialog) entry.dialog = null;
                Qt.callLater(destroy);
            }
            dialogContentItem: Label {
                text: entry.statusText
                color: Appearance.AppearanceTokens.colorTextSecondary
                textFormat: Text.PlainText; wrapMode: Text.WordWrap
            }
        }
    }
}
