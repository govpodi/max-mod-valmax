import QtQuick
import QtQuick.Window
import "RuntimeRows"

Window {
    id: test
    visible: true
    opacity: 0
    flags: Qt.Tool | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    width: 320; height: 200
    property int checks: 0
    property string failure: ""
    property int phase: 0
    function check(value, description) {
        if (!value) {
            failure = description
            Qt.exit(1)
            throw new Error(description)
        }
        checks++
    }
    Flickable {
        id: testView
        anchors.fill: parent
        contentHeight: 800
        property int generation: 0
        Repeater {
            id: rows
            model: 20
            delegate: ValMAXAsyncListRow {
                required property int index
                view: testView
                workQueue: queue
                width: testView.width
                y: index * 40
                rowHeight: 40
                identityKey: "synthetic-" + testView.generation + "-" + index
                prefetchRows: 4
                rowComponent: Component {
                    Item {
                        objectName: "actualAsyncLoaderBody"
                        Repeater { model: 12; Rectangle { x: index * 12; width: 10; height: 10; color: "purple" } }
                    }
                }
            }
        }
    }
    ValMAXListWorkQueue { id: queue; view: testView }
    Timer {
        interval: 500; repeat: true; running: true
        onTriggered: {
            if (test.phase === 0) {
                for (let i = 0; i < 5; ++i)
                    test.check(rows.itemAt(i).ready && rows.itemAt(i).item.objectName === "actualAsyncLoaderBody", "actual visible Loader ready")
                test.check(queue.grants >= 5, "native admission invoked")
                test.check(!rows.itemAt(19).item, "far rows not created")
                testView.contentY = 400
                test.phase = 1
            } else if (test.phase === 1) {
                for (let i = 10; i < 15; ++i) test.check(rows.itemAt(i).ready, "ready after scroll")
                test.check(!rows.itemAt(0).item, "out of cache row released")
                rows.itemAt(12).pooled = true
                test.check(!rows.itemAt(12).item, "pooled body released")
                testView.generation += 1
                test.phase = 2
            } else if (test.phase === 2) {
                for (let i = 10; i < 15; ++i)
                    if (i !== 12) test.check(rows.itemAt(i).ready, "identity replacement ready")
                test.check(!rows.itemAt(12).item, "pooled row not requeued")
                rows.itemAt(12).pooled = false
                test.phase = 3
            } else {
                test.check(rows.itemAt(12).ready, "pooled row restored")
                test.check(queue.grants >= 15, "multiple native queue cycles")
                Qt.exit(0)
            }
        }
    }
}
