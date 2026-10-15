import QtQuick
import QtQuick.Window

// Own one instance per native ListView. No model values or private content are
// copied here: only live row references plus their invalidation generation.
Item {
    id: root
    required property var view
    property bool pageActive: true
    property int settleMs: 80
    property int quietMs: 32
    property double lastMovementTime: 0
    // Bounds only synchronous JS admission/setup, not native incubation work.
    property real admissionBudgetMs: 2
    property real fastPixelsPerSecond: 1800
    property var pending: []
    property var inflight: []
    property double fastUntil: 0
    property double lastMotionTime: 0
    property double lastClockTime: 0
    property real lastContentY: 0
    property int prefetchDirection: 1
    property bool completed: false
    property bool closing: false
    property int grants: 0
    property int peakInflight: 0
    property int peakPending: 0
    readonly property bool viewActive: pageActive && view && view.visible && root.Window.window &&
        root.Window.window.visible && root.Window.window.visibility !== Window.Minimized

    visible: false
    enabled: false

    function matches(entry) {
        try {
            return entry && entry.row && entry.row.valmaxCompleted && !entry.row.valmaxClosing &&
                entry.row.identityKey === entry.key && entry.row.valmaxRequestSerial === entry.serial;
        } catch (_) { return false; }
    }
    function waiting(entry) {
        return matches(entry) && entry.row.loadEligible && !entry.row.valmaxLoadPermitted && !entry.row.valmaxLoadFailed;
    }
    function strictlyVisible(row) {
        try {
            // Native vertical ListView delegates and contentY share content
            // coordinates. Any partial overlap belongs to the critical view;
            // viewportMargin is deliberately excluded from this tier.
            return row && row.eligible && view && row.y + row.height > view.contentY &&
                row.y < view.contentY + view.height;
        } catch (_) { return false; }
    }
    function request(row, priority) {
        if (closing || !row || !row.loadEligible || row.valmaxLoadPermitted || row.valmaxLoadFailed) return;
        let next = [];
        let found = false;
        for (let i = 0; i < pending.length; ++i) {
            const entry = pending[i];
            if (entry.row === row) {
                if (!found) {
                    next.push({ row: row, key: row.identityKey, serial: row.valmaxRequestSerial,
                        priority: priority || entry.priority });
                    found = true;
                }
            } else if (waiting(entry)) next.push(entry);
        }
        if (!found) next.push({ row: row, key: row.identityKey, serial: row.valmaxRequestSerial,
            priority: !!priority });
        pending = next;
        peakPending = Math.max(peakPending, next.length);
    }
    function cancel(row) {
        let next = [];
        for (let i = 0; i < pending.length; ++i)
            if (pending[i].row !== row && waiting(pending[i])) next.push(pending[i]);
        pending = next;
        next = [];
        for (let i = 0; i < inflight.length; ++i)
            if (inflight[i].row !== row && matches(inflight[i])) next.push(inflight[i]);
        inflight = next;
    }
    function complete(row) { cancel(row); }
    function now() {
        const current = Date.now();
        // A wall-clock correction must never turn the short settle gate into
        // a minutes-long admission pause. Each queue owns its clock state.
        if (lastClockTime > current) { fastUntil = 0; lastMotionTime = 0; lastMovementTime = 0; }
        lastClockTime = current;
        return current;
    }
    function observeMotion() {
        if (!view) return;
        const timestamp = now();
        const position = view.contentY;
        if (completed && lastMotionTime > 0) {
            const distance = Math.abs(position - lastContentY);
            const elapsed = Math.max(1, timestamp - lastMotionTime);
            if (distance >= 2) prefetchDirection = position > lastContentY ? 1 : -1;
            if (distance >= 2) lastMovementTime = timestamp;
            if (distance >= 2 && distance * 1000 / elapsed >= fastPixelsPerSecond)
                fastUntil = timestamp + Math.max(0, settleMs);
        }
        lastContentY = position;
        lastMotionTime = timestamp;
    }
    function pump() {
        if (!completed || closing || !viewActive) return;
        const timestamp = now();
        const quiet = lastMovementTime === 0 || timestamp - lastMovementTime >= Math.max(0, quietMs);
        const ordinaryLimit = quiet ? 5 : 1;
        const grantLimit = quiet ? 5 : 1;
        const center = view.contentY + view.height / 2;
        let starts = 0;
        let backgroundStarts = 0;
        while (starts < grantLimit) {
            let active = [];
            for (let i = 0; i < inflight.length; ++i)
                if (matches(inflight[i]) && inflight[i].row.valmaxLoadPermitted && !inflight[i].row.valmaxBodyReady)
                    active.push(inflight[i]);
            inflight = active;

            // New margin incubators enter Qt's shared creation list ahead of
            // older work. Do not let off-screen preload delay a visible row
            // that is still pending or Loading. Existing Loaders keep running.
            let strictVisibleOutstanding = false;
            let ordinaryBackgroundLoading = 0;
            for (let i = 0; i < active.length; ++i)
                if (strictlyVisible(active[i].row)) strictVisibleOutstanding = true;
                else if (!active[i].priority) ordinaryBackgroundLoading += 1;
            if (!strictVisibleOutstanding) {
                for (let i = 0; i < pending.length; ++i)
                    if (waiting(pending[i]) && strictlyVisible(pending[i].row)) {
                        strictVisibleOutstanding = true;
                        break;
                    }
            }

            let next = [];
            let winner = -1;
            let bestScore = Infinity;
            for (let i = 0; i < pending.length; ++i) {
                const entry = pending[i];
                if (!waiting(entry)) continue;
                const position = next.length;
                next.push(entry);
                // One urgent action can use the second slot without cancelling
                // another row's valid asynchronous creation or forcing it sync.
                const capacity = entry.priority ? Math.max(2, ordinaryLimit) : ordinaryLimit;
                if (active.length >= capacity || (!entry.priority && timestamp < fastUntil)) continue;
                if (!entry.priority && strictVisibleOutstanding && !strictlyVisible(entry.row)) continue;
                if (!entry.priority && !strictlyVisible(entry.row) &&
                    (!quiet || ordinaryBackgroundLoading >= 1 || backgroundStarts >= 1)) continue;
                const score = (entry.priority ? -1000000000 : 0) + Math.abs(entry.row.y + entry.row.height / 2 - center);
                if (score < bestScore) { winner = position; bestScore = score; }
            }
            if (winner < 0) { pending = next; return; }
            // Do not spend a second admission after an expensive synchronous
            // Loader setup. Native component work remains Qt's async work.
            if (starts > 0 && now() - timestamp >= Math.max(0, admissionBudgetMs)) { pending = next; return; }
            const entry = next[winner];
            next.splice(winner, 1);
            pending = next;
            inflight = active.concat([entry]);
            peakInflight = Math.max(peakInflight, inflight.length);
            starts += 1;
            if (!entry.priority && !strictlyVisible(entry.row)) backgroundStarts += 1;
            if (entry.row.valmaxGrantLoad(entry.key, entry.serial)) grants += 1;
            else cancel(entry.row);
        }
    }
    function clear(requeue) {
        const active = inflight;
        const queued = pending;
        pending = [];
        inflight = [];
        fastUntil = 0;
        for (let i = 0; i < active.length; ++i) {
            try {
                if (matches(active[i])) {
                    active[i].row.valmaxCancel();
                    if (requeue && !closing) active[i].row.valmaxRefresh();
                }
            } catch (_) {}
        }
        if (requeue && !closing) {
            for (let i = 0; i < queued.length; ++i) {
                try { if (matches(queued[i])) queued[i].row.valmaxRefresh(); } catch (_) {}
            }
        }
    }
    Connections {
        target: root.view
        function onContentYChanged() { root.observeMotion(); }
        function onModelChanged() { root.clear(true); root.observeMotion(); }
    }
    FrameAnimation {
        running: root.completed && !root.closing && root.viewActive && root.pending.length > 0
        onTriggered: root.pump()
    }
    Component.onCompleted: { completed = true; observeMotion(); }
    Component.onDestruction: { closing = true; clear(false); completed = false; }
}
