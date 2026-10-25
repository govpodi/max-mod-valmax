"""Keep the native preview window unavailable until its hosts acknowledge.

This is a UI interaction gate. It cannot pause MAX's native background work,
which may begin before the QML window exists. The release gate for protection
before first native activity therefore remains separate.
"""
from __future__ import annotations


READINESS = '''    readonly property bool valmaxInteractionHostsReady:
        valmaxNativeBridge.runtimeState === "ready"
        && (valmaxNativeBridge.ghostRuntimeState === "active"
            || valmaxNativeBridge.ghostRuntimeState === "off")
        && valmaxNativeBridge.notificationRuntimeState === "ready"
    // First sign-in and adding another account must remain possible even if
    // host readiness is delayed. Only the account/chat surface is gated.
    readonly property bool valmaxInteractionPermitted:
        isLoginState || application.accountModel.count === 0
        || valmaxInteractionHostsReady
    readonly property string valmaxInteractionStatusText:
        (valmaxNativeBridge.runtimeState === "error"
         || valmaxNativeBridge.runtimeState === "unconfigured")
        ? "Локальные модули ValMAX не открылись. Закрой приложение и повтори запуск."
        : valmaxNativeBridge.runtimeState !== "ready"
          ? "Открываем локальные модули ValMAX…"
          : "Ожидаем подтверждения локальных фильтров ValMAX."
    readonly property string valmaxGhostReadinessText:
        valmaxNativeBridge.ghostRuntimeState === "active"
        ? "Режим призрака: включён, фильтры подтверждены."
        : valmaxNativeBridge.ghostRuntimeState === "off"
          ? "Режим призрака: выключен, состояние подтверждено."
          : "Режим призрака: подтверждения нет."
    readonly property string valmaxNotificationReadinessText:
        valmaxNativeBridge.notificationRuntimeState === "ready"
        ? "Защита уведомлений PIN-чатов: подтверждена."
        : "Защита уведомлений PIN-чатов: подтверждения нет."
'''

OVERLAY = '''    // Limits interactions in this window only. Native background events may
    // have started before QML; this is not an early-process readiness claim.
    Rectangle {
        id: valmaxStartupInteractionCover
        objectName: "valmaxStartupInteractionCover"
        anchors.fill: stack
        z: 100
        visible: !root.valmaxInteractionPermitted && !root.isClosingState
        color: root.color

        Column {
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 520)
            spacing: 12

            Text {
                objectName: "valmaxStartupGateStatus"
                width: parent.width
                text: root.valmaxInteractionStatusText
                color: root.palette.windowText
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                font.pixelSize: 18
            }
            Text {
                objectName: "valmaxStartupGateGhostStatus"
                width: parent.width
                text: root.valmaxGhostReadinessText
                color: root.palette.windowText
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                objectName: "valmaxStartupGateNotificationStatus"
                width: parent.width
                text: root.valmaxNotificationReadinessText
                color: root.palette.windowText
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
            }
            Text {
                width: parent.width
                text: "До подтверждения действий в окне нет. Фоновые события клиента этим экраном не приостанавливаются."
                color: root.palette.windowText
                opacity: 0.7
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
            }
            Button {
                objectName: "valmaxStartupGateClose"
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Закрыть ValMAX"
                onClicked: appDelegate.closeMessenger()
            }
        }
    }

'''


def _once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError("Native startup interaction source anchor changed")
    return source.replace(before, after, 1)


def transform(source: str) -> str:
    """Transform the native-only MeWindow source after Bridge import/injection."""
    if type(source) is not str or "import ValMAX.Native 1.0 as ValMAXNative\n" not in source:
        raise ValueError("Native Bridge window required")
    source = _once(source,
        "    readonly property var valmaxNativeBridge: ValMAXNative.Bridge\n",
        "    readonly property var valmaxNativeBridge: ValMAXNative.Bridge\n" + READINESS)
    source = _once(source,
        "    StackView {\n        id: stack\n\n        anchors.fill: parent\n",
        "    StackView {\n        id: stack\n\n        anchors.fill: parent\n"
        "        enabled: root.valmaxInteractionPermitted\n")
    source = _once(source, "    onClosing: closeEvent => {\n", OVERLAY + "    onClosing: closeEvent => {\n")
    return source
