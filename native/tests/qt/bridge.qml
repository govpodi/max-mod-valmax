import QtQml
import ValMAX.Native 1.0

QtObject {
    id: test
    property int checks: 0
    property string failure: ""
    property string currentId: ""
    property var received: []
    property var batches: []
    property int phase: 0
    property var cancelledIds: []
    property var completedIds: []
    function check(value, description) {
        if (!value) {
            failure = description
            Qt.exit(1)
            throw new Error(description)
        }
        checks++
    }
    property Connections bridgeSignals: Connections {
        target: Bridge
        function onCancelled(requestId) { test.cancelledIds.push(requestId) }
        function onCompleted(requestId) {
            test.completedIds.push(requestId)
            if (test.phase === 1 && requestId === test.currentId) {
                test.check(JSON.stringify(test.received) === "[0,1,2,3,4,5,6,7,8,9,10,11]", "ordered rows and deduplication")
                test.check(JSON.stringify(test.batches) === "[5,5,2]", "bounded batches")
                test.check(Bridge.pendingRequests === 0, "queue drained")
                test.phase = 2
                test.currentId = Bridge.enqueueRows([20,21,22,23,24,25,26]).requestId
            }
        }
        function onRowsReady(requestId, rows) {
            test.check(rows.length > 0 && rows.length <= 5, "batch limit")
            if (test.phase === 1) {
                test.batches.push(rows.length)
                for (let row of rows) test.received.push(row)
            } else if (test.phase === 2 && requestId === test.currentId) {
                test.check(Bridge.cancel(requestId), "cancel from callback")
                test.check(test.cancelledIds.indexOf(requestId) >= 0, "cancelled signal")
                test.phase = 3
                test.finish.start()
            }
        }
    }
    property Timer start: Timer {
        interval: 1; running: true
        onTriggered: {
            test.check(Bridge.apiVersion === 1 && Bridge.transport === "in-process", "native metadata")
            for (let invalid of [[], [-1], [1.5], ["1"], [true], [NaN], [Infinity], [1000000001], [null], [{}]])
                test.check(Bridge.enqueueRows(invalid).error === "invalid_rows", "strict row validation")
            test.check(Bridge.enqueueRows(new Array(1025).fill(0)).error === "invalid_rows", "bounded input")
            for (let i = 0; i < 8; ++i) test.check(Bridge.enqueueRows([i]).accepted, "queue accepted")
            test.check(Bridge.enqueueRows([10]).error === "queue_full", "bounded pending work")
            Bridge.cancelAll()
            test.check(Bridge.pendingRequests === 0 && test.cancelledIds.length === 8, "cancel all")
            test.check(!Bridge.cancel("missing"), "unknown cancellation")
            test.phase = 1
            test.currentId = Bridge.enqueueRows([0,1,2,3,4,5,6,7,8,9,10,11,1,2]).requestId
            test.check(test.received.length === 0, "work is asynchronous")
        }
    }
    property Timer finish: Timer {
        interval: 30
        onTriggered: {
            test.check(Bridge.pendingRequests === 0, "cancel removed work")
            test.check(test.completedIds.indexOf(test.currentId) < 0, "cancelled work never completes")
            Qt.exit(0)
        }
    }
}
