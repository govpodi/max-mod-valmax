import QtQuick
import QtQuick.Window
import ClientAppearanceModule as ClientAppearance

Item {
    id: root
    required property var view
    required property Component rowComponent
    required property real rowHeight
    property real avatarSize: 44
    property real avatarX: 4
    property real textOffset: 60
    property bool pageActive: true
    property bool pooled: false
    property bool rowVisible: true
    property bool selected: false
    property string identityKey: ""
    property real viewportMargin: 0
    property int prefetchRows: 4
    readonly property real prefetchMargin: workQueue ? Math.max(0, prefetchRows) * rowHeight : 0
    // Separate visibility from cache lifetime. This is a geometric budget,
    // not a growing cache of every previously visited identity.
    property int readyCacheRows: 8
    readonly property real readyCacheMargin: Math.max(viewportMargin, prefetchMargin,
        Math.min(view ? Math.max(0, view.height) : 0, Math.max(0, readyCacheRows) * rowHeight))
    property int gracePeriodMs: 80
    property bool deferNewLoads: view ? view.moving : false
    property var workQueue: null
    property var valmaxPreviousQueue: null
    property int valmaxRequestSerial: 0

    readonly property var item: body.item
    readonly property bool ready: eligible && body.status === Loader.Ready && body.item !== null
    readonly property bool loading: eligible && !ready
    readonly property bool viewportEligible: pageActive && !pooled && rowVisible && view && view.visible && root.Window.window &&
        height > 0 && y + height > view.contentY - viewportMargin && y < view.contentY + view.height + viewportMargin
    readonly property bool eligible: viewportEligible && root.Window.window.visible && root.Window.window.visibility !== Window.Minimized
    // Loading ahead must not expand native input, focus or visual eligibility.
    readonly property bool loadEligible: eligible || (workQueue && prefetchMargin > 0 &&
        pageActive && !pooled && rowVisible && view && view.visible && root.Window.window &&
        root.Window.window.visible && root.Window.window.visibility !== Window.Minimized && height > 0 &&
        (workQueue.prefetchDirection >= 0 ?
            (y + height > view.contentY && y < view.contentY + view.height + prefetchMargin) :
            (y + height > view.contentY - prefetchMargin && y < view.contentY + view.height)))
    readonly property bool cacheEligible: !pooled && rowVisible && view && root.Window.window &&
        height > 0 && y + height > view.contentY - readyCacheMargin && y < view.contentY + view.height + readyCacheMargin
    readonly property bool valmaxRetainReady: cacheEligible && valmaxBodyReady && !valmaxClosing

    property bool valmaxLoadPermitted: false
    property bool valmaxLoadFailed: false
    property bool valmaxBodyReady: false
    property bool valmaxCompleted: false
    property bool valmaxClosing: false

    signal loaded()

    height: rowVisible ? rowHeight : 0
    implicitHeight: height
    visible: rowVisible

    function valmaxCancel() {
        if (workQueue) workQueue.cancel(root);
        valmaxRequestSerial += 1;
        grace.stop();
        valmaxBodyReady = false;
        valmaxLoadPermitted = false;
    }
    function valmaxRefresh() {
        if (!valmaxCompleted || valmaxClosing) return;
        if (!loadEligible) {
            grace.stop();
            if (!valmaxRetainReady) valmaxCancel();
            return;
        }
        if (valmaxLoadPermitted || valmaxLoadFailed) return;
        if (workQueue) {
            grace.stop();
            workQueue.request(root, false);
            return;
        }
        if (!deferNewLoads || gracePeriodMs <= 0) {
            grace.stop();
            valmaxLoadPermitted = true;
        } else if (!grace.running) grace.restart();
    }
    function valmaxGrantLoad(key, serial) {
        if (!valmaxCompleted || valmaxClosing || !loadEligible || valmaxLoadFailed ||
            key !== identityKey || serial !== valmaxRequestSerial) return false;
        valmaxLoadPermitted = true;
        return true;
    }
    function valmaxRequestImmediateLoad() {
        if (!valmaxCompleted || valmaxClosing || !eligible || valmaxLoadFailed || valmaxLoadPermitted) return;
        if (view && typeof view.cancelFlick === "function") view.cancelFlick();
        if (workQueue) workQueue.request(root, true);
        else { grace.stop(); valmaxLoadPermitted = true; }
    }
    function valmaxResetIdentity() {
        valmaxCancel();
        valmaxLoadFailed = false;
        valmaxRefresh();
    }
    onWorkQueueChanged: {
        if (valmaxPreviousQueue && valmaxPreviousQueue !== workQueue) valmaxPreviousQueue.cancel(root);
        valmaxPreviousQueue = workQueue;
        if (valmaxCompleted) { valmaxCancel(); valmaxRefresh(); }
    }
    onEligibleChanged: valmaxRefresh()
    onLoadEligibleChanged: valmaxRefresh()
    onViewportEligibleChanged: valmaxRefresh()
    onCacheEligibleChanged: {
        if (!cacheEligible) valmaxCancel();
        else valmaxRefresh();
    }
    onDeferNewLoadsChanged: valmaxRefresh()
    onIdentityKeyChanged: if (valmaxCompleted) valmaxResetIdentity()
    onRowComponentChanged: if (valmaxCompleted) valmaxResetIdentity()
    onGracePeriodMsChanged: if (valmaxCompleted && !valmaxLoadPermitted) { grace.stop(); valmaxRefresh(); }
    Component.onCompleted: { valmaxCompleted = true; valmaxRefresh(); }
    Component.onDestruction: { valmaxClosing = true; valmaxCompleted = false; valmaxCancel(); }

    Timer {
        id: grace
        interval: Math.max(0, root.gracePeriodMs)
        onTriggered: {
            if (root.valmaxCompleted && !root.valmaxClosing && root.eligible && !root.valmaxLoadFailed)
                root.valmaxLoadPermitted = true;
        }
    }
    Loader {
        id: body
        anchors.fill: parent
        active: root.valmaxLoadPermitted && (root.loadEligible || root.valmaxRetainReady)
        asynchronous: true
        sourceComponent: root.rowComponent
        visible: root.ready
        enabled: root.ready
        onLoaded: if (root.loadEligible && root.valmaxLoadPermitted) {
            root.valmaxBodyReady = true;
            if (root.workQueue) root.workQueue.complete(root);
            if (root.eligible) root.loaded();
        }
        onStatusChanged: {
            if (status === Loader.Error) {
                root.valmaxLoadFailed = true;
                root.valmaxCancel();
            }
        }
    }
    Item {
        id: skeleton
        anchors.fill: parent
        visible: root.eligible && !root.ready
        enabled: false
        Rectangle {
            anchors.fill: parent
            color: root.selected ? ClientAppearance.AppearanceTokens.colorStatesBackgroundCardSelectedPressed : ClientAppearance.AppearanceTokens.colorBackgroundPrimary
        }
        Rectangle {
            x: root.avatarX
            y: (parent.height - root.avatarSize) / 2
            width: root.avatarSize
            height: root.avatarSize
            radius: root.avatarSize / 2
            color: ClientAppearance.AppearanceTokens.colorIconSecondary
            opacity: 0.12
        }
        Rectangle {
            x: root.textOffset
            y: parent.height / 2 - 15
            width: Math.max(0, Math.min(180, (parent.width - x - root.avatarX) * 0.65))
            height: 12
            radius: 3
            color: ClientAppearance.AppearanceTokens.colorIconSecondary
            opacity: 0.12
        }
        Rectangle {
            x: root.textOffset
            y: parent.height / 2 + 7
            width: Math.max(0, Math.min(130, (parent.width - x - root.avatarX) * 0.43))
            height: 10
            radius: 3
            color: ClientAppearance.AppearanceTokens.colorIconSecondary
            opacity: 0.12
        }
    }
    Accessible.role: Accessible.ListItem
    Accessible.name: "Загрузка…"
    Accessible.ignored: ready || !eligible
}
