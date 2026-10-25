// A local, incoming deleted-text bubble. The stock message delegate is a sibling.
import QtQuick
import QtQuick.Controls
import UiKitModule as UiKit

Item {
    id: retained
    objectName: "valmaxRetainedInlineBubbleRow"
    property string messageId: ""
    property string messageText: ""
    property real deletedAtMs: 0
    property real messageTimeMs: -1
    property var chatAppearance: null
    property bool crossDay: false
    property bool dayUnknown: false
    property bool afterEnd: false
    property bool beforeStart: false
    property bool tieUnknown: false
    property bool expanded: false

    width: parent ? parent.width : 0
    implicitHeight: layout.implicitHeight
    height: implicitHeight
    SystemPalette { id: colors }

    Column {
        id: layout
        width: retained.width
        spacing: 4
        Text {
            objectName: "valmaxRetainedInlinePlacementLabel"
            x: 16
            width: Math.max(0, layout.width - 32)
            visible: retained.beforeStart || retained.crossDay || retained.dayUnknown || retained.afterEnd
            text: retained.beforeStart ? "До первого доступного сообщения" + (retained.dayUnknown ? " · дата не подтверждена" : "")
                 : retained.crossDay ? "Между датами"
                 : retained.afterEnd ? "После последнего доступного сообщения" + (retained.dayUnknown ? " · дата не подтверждена" : "")
                 : "Дата сообщения не подтверждена"
            color: colors.windowText
            font.pixelSize: 11
            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
            textFormat: Text.PlainText
        }
        Text {
            x: 16
            width: Math.max(0, layout.width - 32)
            visible: retained.tieUnknown
            text: "Порядок сообщений с одинаковым временем приблизительный"
            color: colors.windowText
            font.pixelSize: 11
            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
            textFormat: Text.PlainText
        }
        Rectangle {
            id: bubble
            objectName: "valmaxRetainedInlineBubble"
            x: 16
            width: Math.max(0, Math.min(480, retained.width - 32))
            height: bubbleContent.implicitHeight + 16
            radius: 14
            color: retained.chatAppearance
                ? retained.chatAppearance.colorBubblesIncomingBackgroundSurfaceSecondary : colors.base
            border.color: retained.chatAppearance
                ? retained.chatAppearance.colorBubblesIncomingStrokeNeutralSecondary : colors.mid
            Column {
                id: bubbleContent
                x: 10; y: 8
                width: Math.max(0, bubble.width - 20)
                spacing: 5
                Row {
                    width: bubbleContent.width
                    height: 20; spacing: 5
                    Rectangle {
                        width: 20; height: 20; radius: 10
                        color: retained.chatAppearance
                            ? retained.chatAppearance.colorBubblesIncomingBackgroundSurfaceSecondary : colors.base
                        UiKit.SvgIcon {
                            objectName: "valmaxRetainedInlineTrashIcon"
                            anchors.centerIn: parent
                            width: 12; height: 12
                            enabled: false
                            imageWidth: 12; imageHeight: 12
                            source: "qrc:/icon_pack/delete_fill.svg"
                            fillColor: retained.chatAppearance
                                ? retained.chatAppearance.colorBubblesIncomingTextBodySecondary : colors.windowText
                        }
                    }
                    Text {
                        objectName: "valmaxRetainedInlineDeletedAt"
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.max(0, parent.width - 25)
                        text: "Удалено " + new Date(retained.deletedAtMs).toLocaleString()
                        color: retained.chatAppearance
                            ? retained.chatAppearance.colorBubblesIncomingTextBodySecondary : colors.windowText
                        font.pixelSize: 11
                        elide: Text.ElideRight
                        wrapMode: Text.NoWrap
                        textFormat: Text.PlainText
                    }
                }
                Text {
                    objectName: "valmaxRetainedInlineMessageAt"
                    width: bubbleContent.width
                    visible: retained.messageTimeMs >= 0
                    text: visible ? "Время сообщения " + new Date(retained.messageTimeMs).toLocaleString() : ""
                    color: retained.chatAppearance
                        ? retained.chatAppearance.colorBubblesIncomingTextBodySecondary : colors.windowText
                    font.pixelSize: 11
                    elide: Text.ElideRight
                    wrapMode: Text.NoWrap
                    textFormat: Text.PlainText
                }
                Flickable {
                    id: bodyScroll
                    objectName: "valmaxRetainedInlineBodyScroll"
                    width: bubbleContent.width
                    height: Math.min(420, bodyText.implicitHeight)
                    contentWidth: width
                    contentHeight: bodyText.implicitHeight
                    flickableDirection: Flickable.VerticalFlick
                    boundsBehavior: Flickable.StopAtBounds
                    interactive: contentHeight > height
                    clip: true
                    Text {
                        id: bodyText
                        objectName: "valmaxRetainedInlineText"
                        width: bodyScroll.width
                        text: retained.expanded || retained.messageText.length <= 700
                            ? retained.messageText : retained.messageText.slice(0, 700) + "…"
                        color: retained.chatAppearance
                            ? retained.chatAppearance.colorBubblesIncomingTextBody : colors.windowText
                        font.pixelSize: 14
                        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                        textFormat: Text.PlainText
                    }
                }
                Button {
                    visible: retained.messageText.length > 700
                    text: retained.expanded ? "Свернуть" : "Показать полностью"
                    onClicked: retained.expanded = !retained.expanded
                }
            }
        }
    }
}
