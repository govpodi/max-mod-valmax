import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: transferWindowRoot
    objectName: "transferWindow"
    width: 620
    height: 690
    minimumWidth: 380
    minimumHeight: 440
    visible: true
    title: transferController.recoveryMode ? "ValMAX · Восстановление MData" : "ValMAX · Перенос аккаунтов"
    font.family: "Segoe UI"
    font.pixelSize: 14
    readonly property bool dark: Qt.styleHints.colorScheme === Qt.Dark
    readonly property color canvas: dark ? "#17151d" : "#f5f4f8"
    readonly property color surface: dark ? "#24212d" : "#ffffff"
    readonly property color ink: dark ? "#f4f1fb" : "#211b2d"
    readonly property color muted: dark ? "#bab2c9" : "#6d657d"
    readonly property color accent: dark ? "#aa83ff" : "#7443dc"
    color: canvas
    palette.window: canvas
    palette.base: surface
    palette.text: ink
    palette.buttonText: ink
    palette.button: surface
    palette.highlight: accent
    palette.highlightedText: "#ffffff"
    onClosing: function(close) { close.accepted = transferController.requestClose() }

    component ActionButton: Button {
        id: button
        property bool primary: false
        implicitWidth: Math.max(100, implicitContentWidth + leftPadding + rightPadding)
        implicitHeight: 44
        font.weight: primary ? Font.DemiBold : Font.Medium
        leftPadding: 16; rightPadding: 16
        topPadding: 10; bottomPadding: 10
        hoverEnabled: true
        contentItem: Text {
            text: button.text
            textFormat: Text.PlainText
            font: button.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            color: button.primary ? (transferWindowRoot.dark ? "#241337" : "#ffffff") : transferWindowRoot.ink
            opacity: button.enabled ? 1 : 0.5
        }
        background: Rectangle {
            radius: 11
            color: {
                const base = button.primary ? transferWindowRoot.accent : (transferWindowRoot.dark ? "#332d40" : "#eeeaf5")
                if (!button.enabled) return transferWindowRoot.dark ? "#36313e" : "#ded9e8"
                return button.down ? Qt.darker(base, 1.12) : button.hovered ? Qt.darker(base, 1.05) : base
            }
            border.width: button.visualFocus ? 2 : 0
            border.color: transferWindowRoot.dark ? "#e0caff" : "#49238f"
        }
    }
    component ArchivePassword: TextField {
        implicitHeight: 44
        leftPadding: 12; rightPadding: 12
        topPadding: 10; bottomPadding: 10
        color: transferWindowRoot.ink
        placeholderTextColor: transferWindowRoot.muted
        background: Rectangle {
            radius: 10
            color: transferWindowRoot.dark ? "#1d1a24" : "#faf9fc"
            border.width: parent.activeFocus ? 2 : 1
            border.color: parent.activeFocus ? transferWindowRoot.accent : (transferWindowRoot.dark ? "#53495e" : "#d6cee2")
            opacity: parent.enabled ? 1 : 0.6
        }
    }

    FileDialog {
        id: chooser
        title: "Выберите архив MData"
        nameFilters: ["Архив MData (*.mdata)"]
        fileMode: FileDialog.OpenFile
        onAccepted: transferController.chooseFile(selectedFile)
    }
    Dialog {
        id: recoveryConfirmation
        objectName: "recoveryConfirmation"
        parent: transferWindowRoot.contentItem
        popupType: Popup.Item
        width: Math.min(transferWindowRoot.width - 32, 470)
        height: contentItem.implicitHeight + topPadding + bottomPadding
        x: Math.round((transferWindowRoot.width - width) / 2)
        y: Math.max(16, Math.round((transferWindowRoot.height - height) / 2))
        padding: 20
        modal: true
        title: "Восстановить профиль?"
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { radius: 16; color: transferWindowRoot.surface }
        header: null
        footer: null
        contentItem: Column {
            spacing: 16
            Label {
                objectName: "recoveryConfirmationTitle"
                width: parent.width
                text: recoveryConfirmation.title
                textFormat: Text.PlainText; color: transferWindowRoot.ink; wrapMode: Text.WordWrap
                font.bold: true; font.pixelSize: 18
            }
            Label {
                objectName: "recoveryConfirmationBody"
                width: parent.width
                text: "Текущий профиль ValMAX будет заменён резервной копией из незавершённой операции MData. Закройте ValMAX через трей и не открывайте её до завершения."
                textFormat: Text.PlainText; color: transferWindowRoot.ink; wrapMode: Text.WordWrap
            }
            RowLayout {
                width: parent.width
                spacing: 12
                ActionButton {
                    objectName: "recoveryRejectButton"
                    Layout.fillWidth: true
                    text: "Отмена"
                    onClicked: recoveryConfirmation.reject()
                }
                ActionButton {
                    objectName: "recoveryAcceptButton"
                    Layout.fillWidth: true
                    text: "Восстановить"; primary: true
                    onClicked: recoveryConfirmation.accept()
                }
            }
        }
        onAccepted: transferController.confirmRecovery(password.text)
    }
    Connections {
        target: transferController
        function onClearPassword() { password.clear() }
        function onReadyToClose() { transferWindowRoot.close() }
    }
    ScrollView {
        objectName: "transferScrollView"
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: parent.width
            spacing: 18
            Item { Layout.preferredHeight: 7 }
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 28; Layout.rightMargin: 28
                Rectangle {
                    width: 48; height: 48; radius: 14; color: transferWindowRoot.accent
                    Label { anchors.centerIn: parent; text: "V"; color: "white"; font.pixelSize: 26; font.bold: true }
                }
                ColumnLayout {
                    spacing: 3
                    Label { text: "ValMAX"; color: transferWindowRoot.ink; font.pixelSize: 24; font.bold: true }
                    Label { text: transferController.recoveryMode ? "Восстановление MData" : "Перенос аккаунтов"; color: transferWindowRoot.muted; font.pixelSize: 14 }
                }
                Item { Layout.fillWidth: true }
                Label { text: "BETA"; color: transferWindowRoot.accent; font.pixelSize: 11; font.bold: true }
            }
            Label {
                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                text: transferController.recoveryMode
                    ? "Восстановите весь профиль из резервной копии незавершённой операции. Копия определяется записью MData; выбирать другой профиль не нужно."
                    : "Добавьте аккаунты из своего архива MData. Текущие аккаунты останутся на месте."
                textFormat: Text.PlainText; color: transferWindowRoot.muted; wrapMode: Text.WordWrap; font.pixelSize: 14
            }
            Pane {
                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                padding: 20
                background: Rectangle { radius: 16; color: transferWindowRoot.surface }
                contentItem: ColumnLayout {
                    spacing: 12
                    Label { text: "Архив"; color: transferWindowRoot.ink; font.bold: true; visible: !transferController.recoveryMode }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: !transferController.recoveryMode
                        Label {
                            objectName: "archiveName"
                            Layout.fillWidth: true
                            text: transferController.fileName || "Файл не выбран"
                            textFormat: Text.PlainText; color: transferWindowRoot.muted; elide: Text.ElideMiddle
                        }
                        ActionButton {
                            objectName: "chooseFileButton"
                            text: "Выбрать…"
                            enabled: transferController.available && !transferController.busy
                            onClicked: chooser.open()
                        }
                    }
                    Label { text: "Пароль архива"; color: transferWindowRoot.ink; font.bold: true }
                    ArchivePassword {
                        id: password
                        objectName: "passwordField"
                        Layout.fillWidth: true
                        echoMode: TextInput.Password
                        maximumLength: 1024
                        placeholderText: transferController.previewReady ? "Пароль проверен" : "Введите пароль MData"
                        enabled: transferController.available && !transferController.busy && (!transferController.recoveryMode || transferController.recoveryReady)
                        selectByMouse: true
                        onTextEdited: transferController.inputsChanged()
                        onAccepted: {
                            if (text.length === 0 || transferController.busy) return
                            if (transferController.recoveryMode) {
                                if (transferController.recoveryReady) recoveryConfirmation.open()
                            } else transferController.preview(text)
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Пароль используется только для открытия архива на этом компьютере."
                        textFormat: Text.PlainText; color: transferWindowRoot.muted; wrapMode: Text.WordWrap; font.pixelSize: 12
                    }
                }
            }
            Pane {
                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                visible: transferController.accounts.length > 0
                padding: 20
                background: Rectangle { radius: 16; color: transferWindowRoot.surface }
                contentItem: ColumnLayout {
                    spacing: 14
                    Label { text: "Аккаунты в архиве"; color: transferWindowRoot.ink; font.bold: true }
                    Repeater {
                        model: transferController.accounts
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Rectangle {
                                width: 38; height: 38; radius: 19; color: transferWindowRoot.dark ? "#3b2b55" : "#eee5ff"
                                Label { anchors.centerIn: parent; text: "V"; color: transferWindowRoot.accent; font.bold: true }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 3
                                Label {
                                    Layout.fillWidth: true
                                    text: modelData.displayName || ("Аккаунт " + modelData.accountId)
                                    textFormat: Text.PlainText; color: transferWindowRoot.ink; wrapMode: Text.Wrap; font.bold: true
                                }
                                Label { text: "ID: " + modelData.accountId; textFormat: Text.PlainText; color: transferWindowRoot.muted; font.pixelSize: 12 }
                            }
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                spacing: 12
                BusyIndicator { running: transferController.busy; visible: running; Layout.preferredWidth: 30; Layout.preferredHeight: 30 }
                Label {
                    objectName: "statusLabel"
                    Layout.fillWidth: true
                    text: transferController.status
                    textFormat: Text.PlainText; wrapMode: Text.WordWrap
                    color: transferController.errorCode ? (transferWindowRoot.dark ? "#ffacb5" : "#ad2649") : transferWindowRoot.ink
                    font.pixelSize: 14
                }
            }
            ActionButton {
                objectName: "primaryButton"
                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                Layout.preferredHeight: 44
                text: transferController.recoveryMode
                    ? (transferController.recoveryReady ? "Восстановить профиль…" : "Проверить состояние")
                    : (transferController.previewReady ? "Добавить аккаунты" : "Проверить архив")
                enabled: transferController.available && !transferController.busy &&
                    (transferController.recoveryMode ? (!transferController.recoveryReady || password.text.length > 0)
                        : (transferController.previewReady || (transferController.fileName.length > 0 && password.text.length > 0)))
                primary: true
                onClicked: {
                    if (transferController.recoveryMode) {
                        if (transferController.recoveryReady) recoveryConfirmation.open()
                        else transferController.refreshRecovery()
                    } else if (transferController.previewReady) transferController.confirmImport()
                    else transferController.preview(password.text)
                }
            }
            ActionButton {
                objectName: "cancelButton"
                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                text: transferController.busy ? "Отменить" : "Закрыть"
                enabled: !transferController.cancelRequested
                onClicked: transferController.busy ? transferController.cancel() : transferWindowRoot.close()
            }
            Label {
                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                text: transferController.recoveryMode
                    ? "Клиент не запускается автоматически. Откройте ValMAX только после успешного восстановления."
                    : "Клиент не запускается автоматически. Откройте ValMAX после успешного переноса."
                textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: transferWindowRoot.muted; font.pixelSize: 12
            }
            Item { Layout.preferredHeight: 10 }
        }
    }
}
