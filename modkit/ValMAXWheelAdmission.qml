import QtQuick
import QtQuick.Window

// Analysis candidate: only ordinary vertical angle-wheel input. Pixel/phased
// gestures and nested controls retain native dispatch. No message/model reads.
MouseArea {
    id: root
    required property var view
    property bool pageActive: true
    property bool externalControlActive: false
    property bool supportedGeometry: true
    property var owner: null
    property var accountOwner: null
    property real pixelsPerNotch: Math.max(0, Qt.styleHints.wheelScrollLines) * 24
    property real maxBacklogNotches: 1
    property int expiryMs: 120
    property real nominalPixelsPerSecond: pixelsPerNotch * 30
    property double lastInputTime: 0
    property double lastClockTime: 0
    property real pendingPixels: 0
    property bool applying: false
    property bool moving: false
    property bool awaitingPresentation: false
    property int presentationEpoch: 0
    property int awaitingEpoch: -1
    property var presentationObserver: null
    property bool componentReady: false
    readonly property var presentationWindow: root.Window.window
    property int observationSerial: 0
    property int ownedObservationSerial: -1
    property bool observationPending: false
    property int observationChecks: 0
    property int acceptedEvents: 0
    property int appliedFrames: 0
    property real droppedPixels: 0
    property real peakPendingPixels: 0
    readonly property real backlogLimit: Math.max(0, Math.min(view ? view.height / 2 : 0,
        Math.max(0, maxBacklogNotches) * Math.max(0, pixelsPerNotch)))
    readonly property bool allowed: pageActive && supportedGeometry && view && view.visible &&
        view.enabled && view.interactive && !externalControlActive && root.Window.window &&
        root.Window.window.visible && root.Window.window.visibility !== Window.Minimized

    // Inside the content item: behind nested controls, before the ancestor
    // Flickable's wheelEvent fallback. A negative-z sibling of contentItem is
    // reached too late because Flickable consumes first (actual event proof).
    parent: view.contentItem
    x: view.contentX
    y: view.contentY
    width: view.width
    height: view.height
    z: -1
    acceptedButtons: Qt.NoButton
    hoverEnabled: false
    scrollGestureEnabled: false
    enabled: allowed

    signal movementStarted()
    signal movementEnded()

    function now() {
        const stamp = Date.now();
        if (stamp < lastClockTime) cancel();
        lastClockTime = stamp;
        return stamp;
    }
    function cancel() {
        droppedPixels += Math.abs(pendingPixels);
        pendingPixels = 0;
        lastInputTime = 0;
        if (moving) { moving = false; movementEnded(); }
    }
    function reconnectPresentation() {
        cancel();
        presentationEpoch += 1;
        awaitingPresentation = false;
        awaitingEpoch = -1;
        if (presentationObserver) presentationObserver.destroy();
        presentationObserver = null;
        if (presentationWindow)
            presentationObserver = presentationConnection.createObject(root,
                { observedWindow: presentationWindow, observedEpoch: presentationEpoch });
    }
    function acknowledge(window, epoch) {
        if (window !== presentationWindow || epoch !== presentationEpoch || epoch !== awaitingEpoch) return;
        awaitingPresentation = false;
        awaitingEpoch = -1;
    }
    function observeWheel() {
        observationSerial += 1;
        observationPending = true;
        Qt.callLater(finishWheelObservation, observationSerial);
    }
    function finishWheelObservation(serial) {
        observationChecks += 1;
        if (serial !== observationSerial) return;
        observationPending = false;
        // A nested child or native pixel/modifier path took this event. Its
        // movement must not run alongside the preceding parent's wheel tail.
        if (ownedObservationSerial !== serial) cancel();
    }
    function lowerBound() {
        return view.originY - view.topMargin;
    }
    function upperBound() {
        return Math.max(lowerBound(), view.originY + view.contentHeight - view.height + view.bottomMargin);
    }
    function reconcileGeometry() {
        if (!pendingPixels || applying) return;
        const lower = lowerBound(), upper = upperBound();
        // Model/layout fixups own an already out-of-bounds current position.
        // Never turn a pending wheel step into a large synthetic anchor jump.
        if (view.contentY < lower - 0.01 || view.contentY > upper + 0.01) { cancel(); return; }
        const bounded = Math.max(lower - view.contentY, Math.min(upper - view.contentY, pendingPixels));
        droppedPixels += Math.abs(pendingPixels - bounded);
        pendingPixels = bounded;
        if (Math.abs(pendingPixels) < 0.01) cancel();
    }
    function acceptWheel(event) {
        if (!allowed || event.phase !== Qt.NoScrollPhase || event.modifiers !== Qt.NoModifier ||
            event.buttons !== Qt.NoButton || event.pixelDelta.x !== 0 || event.pixelDelta.y !== 0 ||
            event.angleDelta.y === 0 || Math.abs(event.angleDelta.x) >= Math.abs(event.angleDelta.y)) {
            // Native pixel/phase motion or another input mode owns the next move.
            cancel();
            return false;
        }
        const distance = -event.angleDelta.y / 120 * pixelsPerNotch;
        const lower = lowerBound(), upper = upperBound();
        if (!Number.isFinite(distance) || !Number.isFinite(lower) || !Number.isFinite(upper) ||
            backlogLimit <= 0 || (distance < 0 && view.contentY <= lower) ||
            (distance > 0 && view.contentY >= upper)) {
            cancel();
            return false;
        }
        const timestamp = now();
        if (pendingPixels * distance < 0) { droppedPixels += Math.abs(pendingPixels); pendingPixels = 0; }
        const desired = pendingPixels + distance;
        const bounded = Math.max(-backlogLimit, Math.min(backlogLimit, desired));
        droppedPixels += Math.abs(desired - bounded);
        pendingPixels = bounded;
        peakPendingPixels = Math.max(peakPendingPixels, Math.abs(pendingPixels));
        lastInputTime = timestamp;
        acceptedEvents += 1;
        ownedObservationSerial = observationSerial;
        if (!moving) {
            // Stop any preceding native timeline before taking ownership.
            applying = true;
            try { view.cancelFlick(); } finally { applying = false; }
            moving = true;
            movementStarted();
        }
        return true;
    }
    function advance(frameSeconds) {
        if (!allowed || !pendingPixels) { cancel(); return; }
        // Cancellation/reversal/new input do not release this token. Only a
        // Qt frameSwapped notification for the same window/epoch does. This
        // is presentation-notification pacing, not a GPU completion fence.
        if (awaitingPresentation || observationPending) return;
        const timestamp = now();
        if (!pendingPixels) return;
        const stale = timestamp - lastInputTime > Math.max(0, expiryMs);
        reconcileGeometry();
        if (!pendingPixels) return;
        // This is observed animation cadence, not measured render/GPU time.
        // Never divide wheel-arrival intervals to infer an artificial velocity.
        const nominal = 1 / 60;
        const dt = Number.isFinite(frameSeconds) && frameSeconds > 0 ? frameSeconds : nominal;
        const pacing = Math.min(1, nominal / dt);
        const distanceLimit = Math.max(1, nominalPixelsPerSecond * Math.min(dt, nominal) * pacing);
        // A delayed frame drains at most one bounded remaining notch once; it
        // does not expire an otherwise normal single wheel step or catch up
        // a growing history of delivered input.
        const limit = stale ? backlogLimit : distanceLimit;
        const step = Math.max(-limit, Math.min(limit, pendingPixels));
        const next = Math.max(lowerBound(), Math.min(upperBound(), view.contentY + step));
        const actual = next - view.contentY;
        if (!Number.isFinite(next) || !actual) { cancel(); return; }
        pendingPixels -= actual;
        awaitingPresentation = true;
        awaitingEpoch = presentationEpoch;
        applying = true;
        try { view.contentY = next; appliedFrames += 1; }
        finally { applying = false; }
        if (Math.abs(pendingPixels) < 0.01 || Math.abs(actual - step) > 0.01) cancel();
    }

    onWheel: event => { event.accepted = acceptWheel(event); }
    onAllowedChanged: if (!allowed) cancel()
    onOwnerChanged: cancel()
    onAccountOwnerChanged: cancel()
    onBacklogLimitChanged: cancel()
    onPresentationWindowChanged: if (componentReady) reconnectPresentation()

    // PointHandler must see the press before a native child Button acquires
    // its exclusive grab. This transparent viewport item has no visual node,
    // wheel handler or exclusive grab; underlying controls retain delivery.
    Item {
        parent: root.view
        width: root.view.width
        height: root.view.height
        z: 1000000
        enabled: root.allowed
        WheelHandler {
            target: null
            blocking: false
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: root.observeWheel()
        }
        PointHandler {
            enabled: root.moving
            target: null
            acceptedButtons: Qt.AllButtons
            onActiveChanged: if (active) root.cancel()
        }
    }
    Connections {
        target: root.view
        function onContentYChanged() { if (!root.applying) root.cancel(); }
        function onOriginYChanged() { root.reconcileGeometry(); }
        function onContentHeightChanged() { root.reconcileGeometry(); }
        function onHeightChanged() { root.cancel(); }
        function onTopMarginChanged() { root.reconcileGeometry(); }
        function onBottomMarginChanged() { root.reconcileGeometry(); }
        function onDraggingChanged() { if (root.view.dragging) root.cancel(); }
    }
    Connections {
        target: root.Window.window
        function onActiveChanged() { if (!root.Window.window.active) root.cancel(); }
        function onActiveFocusItemChanged() { root.cancel(); }
    }
    Component {
        id: presentationConnection
        Connections {
            required property var observedWindow
            required property int observedEpoch
            target: observedWindow
            function onFrameSwapped() { root.acknowledge(observedWindow, observedEpoch); }
        }
    }
    FrameAnimation {
        running: root.allowed && root.pendingPixels !== 0
        onTriggered: root.advance(frameTime)
    }
    Component.onCompleted: { componentReady = true; reconnectPresentation(); }
    Component.onDestruction: cancel()
}
