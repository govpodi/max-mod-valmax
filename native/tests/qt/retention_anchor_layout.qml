// Synthetic Qt layout proof for a ghost appended after one surviving row.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: proof
    width: 260; height: 600
    property bool showGhost: false
    property bool finished: false
    property string failure: ""
    property int checks: 0
    property int step: 0
    property int ticks: 0
    function check(value, label) {
        checks += 1;
        if (!value) throw new Error(label);
    }
    ListModel {
        id: stockRows
        ListElement { messageId: "101" }
        ListElement { messageId: "103" }
        ListElement { messageId: "104" }
    }
    ListView {
        id: directView
        objectName: "directHistoryView"
        width: proof.width; height: proof.height
        model: stockRows
        delegate: Control {
            id: stockDelegate
            objectName: "directStockDelegate"
            required property int index
            required property string messageId
            width: directView.width
            padding: 0
            contentItem: ColumnLayout {
                spacing: 0
                Rectangle {
                    objectName: "ordinaryMessage"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    implicitHeight: 40
                    color: "#e7e7e7"
                }
            }
            Loader {
                id: retainedAfter
                objectName: "ghostAfterNativeContent"
                parent: stockDelegate.contentItem
                Layout.fillWidth: true
                active: proof.showGhost && stockDelegate.messageId === "101"
                sourceComponent: Rectangle {
                    objectName: "ghostBubble"
                    implicitHeight: 80
                    color: "#d9e6f6"
                }
            }
        }
    }
    ListView {
        id: wrapperView
        objectName: "wrappedHistoryView"
        x: 300
        width: proof.width; height: proof.height
        model: stockRows
        delegate: Item {
            id: outer
            objectName: "wrapperStockDelegate"
            required property int index
            required property string messageId
            width: wrapperView.width
            height: inner.implicitHeight + wrappedGhost.height
            Control {
                id: inner
                objectName: "wrappedNativeControl"
                width: parent.width
                padding: 0
                contentItem: Rectangle { implicitHeight: 40; color: "#e7e7e7" }
            }
            Loader {
                id: wrappedGhost
                objectName: "ghostAfterWrapper"
                y: inner.height
                width: parent.width
                height: active && item ? item.implicitHeight : 0
                active: proof.showGhost && outer.messageId === "101"
                sourceComponent: Rectangle {
                    implicitHeight: 80
                    color: "#d9e6f6"
                }
            }
        }
    }
    ListView {
        id: paddingView
        objectName: "paddingHistoryView"
        x: 600
        width: proof.width; height: proof.height
        model: stockRows
        delegate: Control {
            id: padded
            objectName: "paddingStockDelegate"
            required property int index
            required property string messageId
            readonly property int ghostCount: paddingRepeater.count
            width: paddingView.width
            padding: 0
            bottomPadding: ghostColumn.implicitHeight
            contentItem: Rectangle {
                objectName: "paddingNativeContent"
                implicitHeight: 40
                color: "#e7e7e7"
            }
            Column {
                id: ghostColumn
                objectName: "paddingGhostColumn"
                parent: padded
                anchors.bottom: padded.bottom
                width: padded.width
                Repeater {
                    id: paddingRepeater
                    model: proof.showGhost && padded.messageId === "101" ? 1 : 0
                    delegate: Rectangle {
                        objectName: "paddingGhostBubble"
                        width: ghostColumn.width; height: 80
                        color: "#d9e6f6"
                    }
                }
            }
        }
    }
    function findNamed(item, name) {
        if (!item) return null;
        if (item.objectName === name) return item;
        for (const child of item.children) {
            const found = findNamed(child, name);
            if (found) return found;
        }
        return null;
    }
    Timer {
        id: run
        interval: 5; repeat: true; running: true
        onTriggered: {
            try {
                if (++proof.ticks > 400) throw new Error("layout deadline");
                const direct0 = directView.itemAtIndex(0), direct1 = directView.itemAtIndex(1);
                const wrapped0 = wrapperView.itemAtIndex(0), wrapped1 = wrapperView.itemAtIndex(1);
                const padded0 = paddingView.itemAtIndex(0), padded1 = paddingView.itemAtIndex(1);
                if (!direct0 || !direct1 || !wrapped0 || !wrapped1 || !padded0 || !padded1) return;
                if (proof.step === 0) {
                    proof.check(directView.count === 3 && wrapperView.count === 3 && paddingView.count === 3,
                                "native model row count unchanged");
                    proof.check(direct0.index === 0 && direct1.index === 1
                                && wrapped0.index === 0 && wrapped1.index === 1
                                && padded0.index === 0 && padded1.index === 1,
                                "native indices stable before ghost");
                    proof.check(direct0.height === 40 && direct1.y === 40
                                && wrapped0.height === 40 && wrapped1.y === 40
                                && padded0.height === 40 && padded1.y === 40,
                                "baseline row positions");
                    proof.showGhost = true;
                    proof.step = 1; return;
                }
                if (proof.step === 1) {
                    const directGhost = proof.findNamed(direct0, "ghostAfterNativeContent");
                    if (!directGhost || !directGhost.item) return;
                    directView.forceLayout(); wrapperView.forceLayout(); paddingView.forceLayout();
                    proof.check(direct0.height === 40 && direct1.y === 40
                                && directGhost.implicitHeight === 80,
                                "dynamic reparent alone does not reserve native layout height");
                    const wrapperGhost = proof.findNamed(wrapped0, "ghostAfterWrapper");
                    if (wrapped0.height === 120 && wrapped1.y !== 120) return;
                    proof.check(wrapped0.height === 120 && wrapped1.y === 120,
                                "wrapper row grows after native child: "
                                + wrapped0.height + "," + wrapped1.y + " child="
                                + wrapperGhost.height + " implicit=" + wrapperGhost.implicitHeight);
                    const nativeContent = proof.findNamed(padded0, "paddingNativeContent");
                    const ghostColumn = proof.findNamed(padded0, "paddingGhostColumn");
                    ghostColumn.forceLayout(); paddingView.forceLayout();
                    proof.check(padded0.height === 120 && padded1.y === 120,
                                "bottomPadding grows native root delegate: "
                                + padded0.height + "," + padded1.y + " implicit="
                                + padded0.implicitHeight + " ghost=" + ghostColumn.implicitHeight
                                + "/" + ghostColumn.height + " repeats=" + padded0.ghostCount
                                + " id=" + padded0.messageId);
                    proof.check(nativeContent.height === 40 && ghostColumn.y === 40
                                && ghostColumn.height === 80,
                                "native content stays above reserved ghost padding");
                    proof.check(direct0.index === 0 && direct1.index === 1
                                && wrapped0.index === 0 && wrapped1.index === 1
                                && padded0.index === 0 && padded1.index === 1,
                                "ghost adds no model index");
                    proof.check(directView.itemAtIndex(0) === direct0
                                && wrapperView.itemAtIndex(0) === wrapped0
                                && paddingView.itemAtIndex(0) === padded0,
                                "ListView item identity preserved");
                    proof.showGhost = false;
                    proof.step = 2; return;
                }
                if (proof.step === 2) {
                    const wrapperGhost = proof.findNamed(wrapped0, "ghostAfterWrapper");
                    if (wrapperGhost.item) return;
                    const ghostColumn = proof.findNamed(padded0, "paddingGhostColumn");
                    ghostColumn.forceLayout();
                    directView.forceLayout(); wrapperView.forceLayout(); paddingView.forceLayout();
                    if (wrapped0.height === 40 && wrapped1.y !== 40) return;
                    if (padded0.height === 40 && padded1.y !== 40) return;
                    proof.check(direct0.height === 40 && direct1.y === 40
                                && wrapped0.height === 40 && wrapped1.y === 40
                                && padded0.height === 40 && padded1.y === 40,
                                "layout returns to stock heights: direct=" + direct0.height + "/" + direct1.y
                                + " wrapper=" + wrapped0.height + "/" + wrapped1.y
                                + " padding=" + padded0.height + "/" + padded1.y);
                    proof.finished = true; run.stop();
                }
            } catch (error) {
                proof.failure = String(error);
                proof.finished = true; run.stop();
            }
        }
    }
}
