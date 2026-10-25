// Synthetic ordered-history exercise. Real range, bubble and transport QML;
// a fake bridge and a small QAbstractListModel stand in for MAX.
import QtQuick
import QtQuick.Controls
import ValMAX.Native 1.0 as Native
import ValMAX 1.0 as ValMAX

Window {
    id: proof
    objectName: "retentionPlacementWindow"
    width: 440; height: 900; visible: true
    property string account: "11"
    property string chat: "21"
    property bool finished: false
    property string failure: ""
    property int checks: 0
    property int step: 0
    property int ticks: 0
    property int nativeClicks: 0
    property bool sawCustomTimestamp: false
    property bool sawRangeRequest: false
    property var nativeTimes: []
    readonly property var rangeObject: range
    readonly property var viewObject: listView

    QtObject {
        id: appearance
        property color colorBubblesIncomingBackgroundSurfaceSecondary: "#d6e2f0"
        property color colorBubblesIncomingStrokeNeutralSecondary: "#57718e"
        property color colorBubblesIncomingTextBodySecondary: "#334e6a"
        property color colorBubblesIncomingTextBody: "#142c44"
    }
    ListView {
        id: listView
        objectName: "retentionPlacementListView"
        anchors.fill: parent
        model: historyModel
        cacheBuffer: 3000
        section.property: "messageDayStartTime"
        section.delegate: Rectangle {
            width: listView.width; height: 18; color: "#dddddd"
        }
        function firstVisibleIndex() { return count > 0 ? 0 : -1; }
        function lastVisibleIndex() { return count > 0 ? count - 1 : -1; }
        ValMAX.MessageRetentionRange {
            id: range
            view: listView
            accountId: proof.account
            chatId: proof.chat
            active: true
            hasMoreBackward: false
            hasMoreForward: false
        }
        delegate: Item {
            id: historyRow
            objectName: "retentionPlacementHistoryRow"
            required property int index
            required property var messageContext
            property alias isHighlighted: nativeItem.isHighlighted
            width: listView.width
            height: ghostBefore.implicitHeight + nativeItem.height + ghostAfter.implicitHeight
            Control {
                id: nativeItem
                objectName: "retentionPlacementNativeItem"
                property bool isHighlighted: false
                y: ghostBefore.implicitHeight
                width: parent.width; height: 40
                background: Rectangle { color: "#fafafa" }
                TapHandler { onTapped: proof.nativeClicks += 1 }
            }
            Column {
                id: ghostBefore
                objectName: "retentionPlacementBefore"
                width: historyRow.width; spacing: 4
                Repeater {
                    model: range.rowsBeforeIndex[String(historyRow.index)] || []
                    delegate: ValMAX.MessageRetentionBubble {
                        required property var modelData
                        width: ghostBefore.width
                        messageId: modelData.messageId
                        messageText: modelData.text
                        deletedAtMs: modelData.deletedAtMs
                         messageTimeMs: modelData.messageTimeMs
                         beforeStart: true; dayUnknown: modelData.dayUnknown
                        tieUnknown: modelData.tieUnknown
                        chatAppearance: appearance
                    }
                    onItemAdded: ghostBefore.forceLayout()
                    onItemRemoved: ghostBefore.forceLayout()
                }
                Button {
                    objectName: "retentionPlacementMoreBefore"
                    visible: range.pagingBefore && range.hasMore
                             && range.pagingAnchorIndex === historyRow.index
                    text: "Показать ещё"
                    onClicked: range.loadMore()
                    onVisibleChanged: ghostBefore.forceLayout()
                }
                Component.onCompleted: forceLayout()
            }
            Column {
                id: ghostAfter
                objectName: "retentionPlacementAfter"
                y: nativeItem.y + nativeItem.height
                width: historyRow.width; spacing: 4
                Repeater {
                    model: range.rowsByIndex[String(historyRow.index)] || []
                    delegate: ValMAX.MessageRetentionBubble {
                        required property var modelData
                        width: ghostAfter.width
                        messageId: modelData.messageId
                        messageText: modelData.text
                        deletedAtMs: modelData.deletedAtMs
                         messageTimeMs: modelData.messageTimeMs
                        crossDay: modelData.crossDay
                        dayUnknown: modelData.dayUnknown
                        afterEnd: modelData.afterEnd
                        tieUnknown: modelData.tieUnknown
                        chatAppearance: appearance
                    }
                    onItemAdded: ghostAfter.forceLayout()
                    onItemRemoved: ghostAfter.forceLayout()
                }
                Button {
                    objectName: "retentionPlacementMoreAfter"
                    visible: !range.pagingBefore && range.hasMore
                             && range.pagingAnchorIndex === historyRow.index
                    text: "Показать ещё"
                    onClicked: range.loadMore()
                    onVisibleChanged: ghostAfter.forceLayout()
                }
                Component.onCompleted: forceLayout()
            }
        }
    }
    function check(value, label) {
        checks += 1;
        if (!value) throw new Error(label);
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
    function row(messageId, orderKey, messageText) {
        return {messageId: messageId, orderKey: orderKey, sentAtMs: null,
                receivedAtMs: Number(orderKey) + 10, deletedAtMs: keyAt(3) + 1000,
                text: messageText};
    }
    function keyAt(index) { return nativeTimes[index]; }
    function page(accountId, items, nextCursor, revision) {
        return {schema: 1, accountId: accountId, chatId: chat,
                fromOrderKey: "1", toOrderKey: "9223372036854775807",
                items: items, nextCursor: nextCursor, revision: revision};
    }
    function firstRows() {
        const first = keyAt(0), secondDay = keyAt(2), last = keyAt(3);
        return [row("100", String(first - 100), "before native start"),
                row("102", String(first), "same key between native rows"),
                row("103", String(first), "collision while native row exists"),
                row("150", String(first + 100), "cross day marker"),
                row("9223372036854775807", String(secondDay + 50), "large id"),
                row("107", String(last + 100), "after native end")];
    }
    function mapLength(map, index) {
        const rows = map[String(index)];
        return rows ? rows.length : 0;
    }
    Timer {
        id: run
        interval: 8; repeat: true; running: true
        onTriggered: {
            try {
                if (++proof.ticks > 1200) throw new Error("synthetic Qt deadline");
                if (proof.step === 0) {
                    if (Native.Bridge.requests.length < 1) return;
                    const request = Native.Bridge.requests[0].value;
                    proof.sawRangeRequest = request.operation === "retention.range";
                    proof.check(proof.sawRangeRequest && request.value.accountId === "11"
                                && request.value.chatId === "21" && request.value.limit === 20,
                                "scoped real range transport");
                    proof.check(request.value.fromOrderKey === "1"
                                && request.value.toOrderKey === "9223372036854775807",
                                "inclusive native history edge bounds");
                    proof.check(range.currentBounds.keys.length === 4
                                && range.currentBounds.keys[0].day === "2024-7-3"
                                && range.currentBounds.keys[2].day === "2024-7-4"
                                && historyModel.data(historyModel.index(0, 0), 0x101) === 0,
                                "local day from real timestamp despite numeric non-day role 0x101");
                    proof.nativeTimes = range.currentBounds.keys.map(function(value) { return Number(value.key); });
                    proof.check(range.messageTimeMs("1000") === -1
                                && range.messageTimeMs("9223372036854775807") === -1
                                && range.messageTimeMs(range.currentBounds.keys[0].key) === keyAt(0),
                                "implausible or unsafe order keys do not become dates");
                    const uncertain = range.anchorFor({messageId: "1", orderKey: "1000"}, range.currentBounds);
                    proof.check(uncertain && uncertain.beforeStart && uncertain.dayUnknown,
                                "truly unknown original date keeps its warning");
                    proof.sawCustomTimestamp = true;
                    Native.Bridge.success(0, proof.page("11", proof.firstRows(), "next-one", "a".repeat(64)));
                    proof.step = 1; return;
                }
                if (proof.step === 1) {
                    if (proof.mapLength(range.rowsByIndex, 0) !== 1) return;
                    const rows = [0, 1, 2, 3].map(function(i) { return listView.itemAtIndex(i); });
                    if (rows.some(function(value) { return value === null; })) return;
                    const beforeColumn = proof.findNamed(rows[0], "retentionPlacementBefore");
                    const afterColumn = proof.findNamed(rows[0], "retentionPlacementAfter");
                    beforeColumn.forceLayout(); afterColumn.forceLayout(); listView.forceLayout();
                    proof.check(proof.mapLength(range.rowsBeforeIndex, 0) === 1
                                && proof.mapLength(range.rowsByIndex, 0) === 1,
                                "before first and same-key gap in row zero");
                    proof.check(range.rowsByIndex["0"][0].tieUnknown,
                                "same-time native gap is labeled approximate");
                    proof.check(proof.mapLength(range.rowsByIndex, 1) === 1
                                && range.rowsByIndex["1"][0].crossDay
                                && !range.rowsByIndex["1"][0].dayUnknown,
                                "cross-day interval attached to preceding native row");
                    const sameDayLocation = proof.findNamed(afterColumn, "valmaxRetainedInlinePlacementLabel");
                    const crossColumn = proof.findNamed(rows[1], "retentionPlacementAfter");
                    crossColumn.forceLayout();
                    const crossLocation = proof.findNamed(crossColumn, "valmaxRetainedInlinePlacementLabel");
                    proof.check(sameDayLocation && !sameDayLocation.visible
                                && crossLocation && crossLocation.text === "Между датами",
                                "same-day warning hidden and cross-day boundary honestly marked");
                    proof.check(proof.mapLength(range.rowsByIndex, 2) === 1
                                && proof.mapLength(range.rowsByIndex, 3) === 1,
                                "large id and after-end interval placed");
                    proof.check(range.cachedRows.length === 5,
                                "existing native row deduplicates local marker");
                    const ghost = proof.findNamed(rows[0], "valmaxRetainedInlineBubble");
                    const icon = proof.findNamed(rows[0], "valmaxRetainedInlineTrashIcon");
                    const text = proof.findNamed(rows[0], "valmaxRetainedInlineText");
                    proof.check(ghost && icon && text
                                && icon.source.toString() === "qrc:/icon_pack/delete_fill.svg"
                                && text.textFormat === Text.PlainText,
                                "real bubble QML and trash icon URL in wrapper");
                    const originalTime = proof.findNamed(rows[0], "valmaxRetainedInlineMessageAt");
                    const location = proof.findNamed(rows[0], "valmaxRetainedInlinePlacementLabel");
                    proof.check(originalTime && originalTime.visible
                                && originalTime.text.indexOf("Время сообщения") === 0
                                && location && location.text === "До первого доступного сообщения",
                                "new bubble renders validated original time without false date warning");
                    const afterEndColumn = proof.findNamed(rows[3], "retentionPlacementAfter");
                    const afterEndLocation = proof.findNamed(afterEndColumn, "valmaxRetainedInlinePlacementLabel");
                    proof.check(afterEndLocation
                                && afterEndLocation.text === "После последнего доступного сообщения",
                                "after-end location does not claim an unknown known date");
                    proof.check(rows[0].height > 40 && rows[1].y > rows[0].y + 40
                                && rows[0].index === 0 && rows[1].index === 1,
                                "wrapper expands ListView without model index shift: "
                                + rows[0].height + "/" + rows[1].y);
                    proof.check(rows[0].messageContext.get().messageId === "101"
                                && rows[0].isHighlighted === false,
                                "itemAtIndex exposes stock message context and highlight proxy");
                    proof.check(range.hasMore && proof.findNamed(rows[3], "retentionPlacementMoreAfter").visible,
                                "in-chat paging control visible");
                    range.loadMore(); proof.step = 2; return;
                }
                if (proof.step === 2) {
                    if (Native.Bridge.requests.length < 2) return;
                    proof.check(Native.Bridge.requests[1].value.value.cursor === "next-one",
                                "opaque next-page cursor");
                    Native.Bridge.success(1, proof.page("11", [], "next-two", "a".repeat(64)));
                    proof.check(range.hasMore && range.cachedRows.length === 5,
                                "empty marker-only page retains more action and bubbles");
                    range.loadMore(); proof.step = 3; return;
                }
                if (proof.step === 3) {
                    if (Native.Bridge.requests.length < 3) return;
                    Native.Bridge.success(2, proof.page("11", [proof.row("108", String(keyAt(3) + 200), "later local row")],
                                                         null, "a".repeat(64)));
                    proof.check(!range.hasMore && proof.mapLength(range.rowsByIndex, 3) === 2,
                                "later page appended in original interval");
                    historyModel.replaceMiddle();
                    proof.check(Object.keys(range.rowsByIndex).length === 0,
                                "dataChanged hides stale anchors immediately");
                    proof.step = 4; return;
                }
                if (proof.step === 4) {
                    if (Native.Bridge.requests.length < 4) return;
                    const request = Native.Bridge.requests[3].value;
                    proof.check(request.value.accountId === "11" && request.value.cursor === null,
                                "interior model replacement refetches range");
                    Native.Bridge.success(3, proof.page("11", proof.firstRows(), null, "a".repeat(64)));
                    proof.check(proof.mapLength(range.rowsByIndex, 0) === 2,
                                "native id collision reanchors after interior replacement");
                    historyModel.setMiddleDelayed(true);
                    proof.check(range.nativeKeyAt(1) === null
                                && Object.keys(range.rowsByIndex).length === 0,
                                "delayed override fails closed and hides stale ghosts");
                    historyModel.setMiddleDelayed(false);
                    proof.step = 5; return;
                }
                if (proof.step === 5) {
                    if (Native.Bridge.requests.length < 5) return;
                    Native.Bridge.success(4, proof.page("11", proof.firstRows(), null, "a".repeat(64)));
                    proof.check(proof.mapLength(range.rowsByIndex, 0) === 2,
                                "ordinary timestamp resumes inline placement");
                    range.requestPage(null, "refresh");
                    proof.step = 6; return;
                }
                if (proof.step === 6) {
                    if (Native.Bridge.requests.length < 6) return;
                    proof.account = "99";
                    proof.check(Object.keys(range.rowsByIndex).length === 0
                                && Object.keys(range.rowsBeforeIndex).length === 0,
                                "account switch removes old bubbles immediately");
                    Native.Bridge.success(5, proof.page("11", proof.firstRows(), null, "a".repeat(64)));
                    proof.check(Object.keys(range.rowsByIndex).length === 0,
                                "late old-account reply discarded");
                    proof.step = 7; return;
                }
                if (proof.step === 7) {
                    if (Native.Bridge.requests.length < 7) return;
                    proof.check(Native.Bridge.requests[6].value.value.accountId === "99",
                                "new account request isolated");
                    Native.Bridge.success(6, proof.page("99",
                        [proof.row("111", String(keyAt(2) + 100), "new account synthetic row")], null,
                        "b".repeat(64)));
                    proof.check(range.cachedRows.length === 1
                                && proof.mapLength(range.rowsByIndex, 2) === 1
                                && range.rowsByIndex["2"][0].messageId === "111",
                                "only selected-account bubble visible");
                    proof.finished = true; run.stop();
                }
            } catch (error) {
                proof.failure = String(error);
                proof.finished = true; run.stop();
            }
        }
    }
}
