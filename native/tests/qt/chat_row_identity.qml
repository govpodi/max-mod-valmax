import QtQuick
import QtQuick.Window
import ValMAX.Native 1.0 as Native
import "RuntimeRows"

Window {
    id: test
    visible: true
    opacity: 0
    flags: Qt.Tool | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    width: 320
    height: 200
    property int checks: 0
    property string failure: ""
    property int phase: 0
    property int rounds: 0
    property int constructions: 0
    property int destructions: 0
    function check(ok, label) {
        if (!ok) { failure = label; Qt.exit(1); throw new Error(label); }
        checks += 1;
    }
    function assertBody(row, label) {
        if (!row.ready) return;
        check(row.item !== null, label + ":ready has item");
        check(row.item.ownerIdentity === row.identityKey, label + ":body identity equals shell");
        check(row.item.ownerSerial === row.valmaxRequestSerial, label + ":body serial equals shell");
    }
    Flickable {
        id: view
        anchors.fill: parent
        contentHeight: 1600
        ValMAXAsyncListRow {
            id: row
            width: view.width
            rowHeight: 40
            view: view
            workQueue: nativeQueue
            identityKey: "account:chat-A"
            rowComponent: Component {
                Item {
                    property string ownerIdentity: ""
                    property int ownerSerial: -1
                    Component.onCompleted: {
                        ownerIdentity = row.identityKey;
                        ownerSerial = row.valmaxRequestSerial;
                        test.constructions += 1;
                    }
                    Component.onDestruction: test.destructions += 1
                    Repeater { model: 30; Rectangle { x: index * 5; width: 4; height: 4; color: "purple" } }
                }
            }
        }
    }
    Native.ListWorkQueue {
        id: nativeQueue
        view: view
        viewActive: true
        admissionBudgetMs: 1000
        quietMs: 0
        fastPixelsPerSecond: 1000000000
    }
    FrameAnimation {
        running: true
        onTriggered: nativeQueue.pump()
    }
    Timer {
        interval: 100
        running: true
        repeat: true
        onTriggered: {
            if (!row.ready) return;
            test.assertBody(row, "settled");
            if (test.phase === 0) {
                test.check(row.item.ownerIdentity === "account:chat-A", "initial identity");
                test.phase = 1;
            }
            if (test.phase === 1) {
                // Force reset and a native grant into the same JavaScript turn.
                // Any Ready item must already belong to the new lease.
                row.identityKey = "account:chat-" + test.rounds;
                test.assertBody(row, "after identity before grant");
                nativeQueue.pump();
                test.assertBody(row, "after same-turn native grant");
                test.rounds += 1;
                if (test.rounds >= 12) test.phase = 2;
            } else if (test.phase === 2) {
                row.pooled = true;
                test.check(!row.ready, "pooled row cannot be Ready");
                row.identityKey = "account:pinned-replacement";
                row.pooled = false;
                nativeQueue.pump();
                test.assertBody(row, "pooled replacement same-turn grant");
                test.phase = 3;
            } else if (test.phase === 3) {
                test.check(row.item.ownerIdentity === "account:pinned-replacement", "reused body new chat");
                test.check(test.constructions >= 14, "new identity reconstructs initialized body");
                test.check(nativeQueue.grants >= 14, "native queue actually exercised");
                Qt.exit(0);
            }
        }
    }
}
