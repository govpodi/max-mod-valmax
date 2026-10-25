import QtQuick
import QtQuick.Window
import ValMAX.Native 1.0 as Native

// Drop-in UI adapter. Queue ownership, ordering, admission budget, monotonic
// motion timing and generation validation live in the C++ object.
Item {
    id: root
    required property var view
    property bool pageActive: true
    property alias settleMs: queue.settleMs
    property alias quietMs: queue.quietMs
    property alias admissionBudgetMs: queue.admissionBudgetMs
    property alias fastPixelsPerSecond: queue.fastPixelsPerSecond
    readonly property int prefetchDirection: queue.prefetchDirection
    readonly property int grants: queue.grants
    readonly property int pendingCount: queue.pendingCount
    readonly property int inflightCount: queue.inflightCount
    readonly property int peakInflight: queue.peakInflight
    readonly property int peakPending: queue.peakPending
    property bool completed: false
    property bool closing: false
    readonly property bool viewActive: pageActive && view && view.visible && root.Window.window &&
        root.Window.window.visible && root.Window.window.visibility !== Window.Minimized
    visible: false
    enabled: false
    function request(row, priority) { if (!closing) queue.request(row, !!priority) }
    function cancel(row) { queue.cancel(row) }
    function complete(row) { queue.complete(row) }
    function clear(requeue) { queue.clear(!!requeue) }
    Native.ListWorkQueue {
        id: queue
        view: root.view
        viewActive: root.completed && !root.closing && root.viewActive
    }
    Connections {
        target: root.view
        function onContentYChanged() { queue.observeMotion() }
        function onModelChanged() { queue.clear(true); queue.observeMotion() }
    }
    FrameAnimation {
        running: root.completed && !root.closing && root.viewActive && queue.pendingCount > 0
        onTriggered: queue.pump()
    }
    Component.onCompleted: { completed = true; queue.observeMotion() }
    Component.onDestruction: { closing = true; queue.clear(false); completed = false }
}
