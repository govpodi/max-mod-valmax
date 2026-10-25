import QtQuick
import QtQuick.Window

Window {
    id: root
    width: 720
    height: 480
    x: 180
    y: 150
    visible: true
    title: "ValMAX synthetic photo cache test"
    flags: Qt.Tool | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    color: "#1b1821"
    property alias scrollPosition: history.contentY
    ListView {
        id: history
        anchors.fill: parent
        anchors.margins: 8
        clip: true
        model: 60
        spacing: 0
        cacheBuffer: 500
        reuseItems: false
        delegate: PhotoCell {
            required property int index
            logicalId: index
            view: history
            width: history.width
            height: 180
            candidate: benchmarkCandidate
        }
    }
}
