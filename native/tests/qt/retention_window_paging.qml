// Synthetic 202-row in-chat window traversal with real range, bubble and transport QML.
import QtQuick
import QtQuick.Controls
import ValMAX.Native 1.0 as Native
import ValMAX 1.0 as ValMAX

Window {
    id: proof
    objectName: "retentionWindowPagingWindow"
    width: 440; height: 900; visible: true
    property string account: "11"
    property string chat: "21"
    property bool finished: false
    property string failure: ""
    property int checks: 0
    property int step: 0
    property int ticks: 0
    property int handledRequests: 0
    property bool sawTailLimit: false
    property var allRows: []
    readonly property var rangeObject: rangeLoader.item

    QtObject {
        id: appearance
        property color colorBubblesIncomingBackgroundSurfaceSecondary: "#d6e2f0"
        property color colorBubblesIncomingStrokeNeutralSecondary: "#57718e"
        property color colorBubblesIncomingTextBodySecondary: "#334e6a"
        property color colorBubblesIncomingTextBody: "#142c44"
    }
    ListView {
        id: listView
        anchors.fill: parent
        model: historyModel
        cacheBuffer: 50000
        function firstVisibleIndex() { return count > 0 ? 0 : -1; }
        function lastVisibleIndex() { return count > 0 ? count - 1 : -1; }
        Loader {
            id: rangeLoader
            active: true
            sourceComponent: Component {
                ValMAX.MessageRetentionRange {
                    view: listView
                    accountId: proof.account
                    chatId: proof.chat
                    active: true
                    hasMoreBackward: false
                    hasMoreForward: false
                }
            }
        }
        delegate: Item {
            id: historyRow
            required property int index
            required property var messageContext
            width: listView.width
            height: nativeItem.height + ghostAfter.implicitHeight
            Control {
                id: nativeItem
                width: parent.width; height: 40
                background: Rectangle { color: "#fafafa" }
            }
            Column {
                id: ghostAfter
                width: historyRow.width
                y: nativeItem.height
                spacing: 4
                Button {
                    objectName: "retentionWindowPrevious"
                    visible: !!rangeLoader.item && !rangeLoader.item.pagingBefore
                             && rangeLoader.item.hasPreviousWindow
                             && rangeLoader.item.pagingAnchorIndex === historyRow.index
                    text: "Предыдущая часть сохранённых сообщений"
                    onClicked: rangeLoader.item.previousWindow()
                    onVisibleChanged: ghostAfter.forceLayout()
                }
                Repeater {
                    model: rangeLoader.item
                        ? rangeLoader.item.rowsByIndex[String(historyRow.index)] || [] : []
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
                    objectName: "retentionWindowMore"
                    visible: !!rangeLoader.item && !rangeLoader.item.pagingBefore
                             && rangeLoader.item.hasMore
                             && rangeLoader.item.pagingAnchorIndex === historyRow.index
                    text: "Показать ещё сохранённые сообщения"
                    onClicked: rangeLoader.item.loadMore()
                    onVisibleChanged: ghostAfter.forceLayout()
                }
                Button {
                    objectName: "retentionWindowNext"
                    visible: !!rangeLoader.item && !rangeLoader.item.pagingBefore
                             && rangeLoader.item.hasNextWindow
                             && rangeLoader.item.pagingAnchorIndex === historyRow.index
                    text: "Следующая часть сохранённых сообщений"
                    onClicked: rangeLoader.item.nextWindow()
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
    function countNamed(item, name) {
        if (!item) return 0;
        let count = item.objectName === name ? 1 : 0;
        for (const child of item.children) count += countNamed(child, name);
        return count;
    }
    function makeRow(messageId, orderKey, text) {
        return {messageId: messageId, orderKey: orderKey, sentAtMs: null,
                receivedAtMs: Number(orderKey) + 10,
                deletedAtMs: Number(orderKey) + 1000, text: text};
    }
    function prepareRows() {
        const base = Number(rangeLoader.item.currentBounds.keys[1].key);
        const rows = [];
        for (let index = 0; index < 202; index += 1)
            rows.push(makeRow(String(1000 + index), String(base + 1000 + index * 1000),
                              "synthetic retained row " + index));
        allRows = rows;
    }
    function selectedRows(accountId) {
        if (accountId === "11") return allRows;
        if (accountId === "99") return [makeRow("9000", allRows[5].orderKey,
                                                 "synthetic other account")];
        throw new Error("unexpected account");
    }
    function nextRequest() {
        if (Native.Bridge.requests.length <= handledRequests) return null;
        const index = handledRequests++;
        const outer = Native.Bridge.requests[index].value;
        check(outer.operation === "retention.range", "only selected-chat range operation");
        return {index: index, body: outer.value};
    }
    function page(accountId, items, nextCursor) {
        return {schema: 1, accountId: accountId, chatId: chat,
                fromOrderKey: "1", toOrderKey: "9223372036854775807",
                items: items, nextCursor: nextCursor,
                revision: accountId === "11" ? "a".repeat(64) : "b".repeat(64)};
    }
    function respondPage(request) {
        const body = request.body;
        check(body.schema === 1 && body.chatId === "21"
              && body.fromOrderKey === "1"
              && body.toOrderKey === "9223372036854775807"
              && body.limit >= 1 && body.limit <= 20,
              "bounded selected-chat request");
        const rows = selectedRows(body.accountId);
        let start = 0;
        if (body.cursor !== null) {
            check(/^p[0-9]+$/.test(body.cursor), "opaque fixture cursor");
            start = Number(body.cursor.slice(1));
        }
        check(start >= 0 && start <= rows.length, "cursor remains in fixture range");
        if (body.limit === 1 && start === 199) sawTailLimit = true;
        const count = start === 0 && body.accountId === "11"
            ? Math.min(19, body.limit) : Math.min(body.limit, rows.length - start);
        const end = start + count;
        Native.Bridge.success(request.index,
            page(body.accountId, rows.slice(start, end), end < rows.length ? "p" + end : null));
    }
    function mappedRows() {
        const range = rangeLoader.item;
        return range && range.rowsByIndex["1"] ? range.rowsByIndex["1"] : [];
    }
    function mappedIds() { return mappedRows().map(function(row) { return row.messageId; }); }
    Timer {
        id: run
        interval: 12; repeat: true; running: true
        onTriggered: {
            try {
                if (++proof.ticks > 2500) throw new Error("synthetic Qt deadline");
                const range = rangeLoader.item;
                if (proof.step === 0) {
                    if (!range || Native.Bridge.requests.length < 1) return;
                    proof.prepareRows();
                    const request = proof.nextRequest();
                    proof.check(request.body.accountId === "11" && request.body.cursor === null
                                && request.body.limit === 20, "initial account window");
                    proof.respondPage(request);
                    proof.step = 15; return;
                }
                if (proof.step === 15) {
                    proof.check(range.rangeRows.length === 19 && range.hasMore
                                && range.nextCursor === "p19", "partial page has a next cursor");
                    range.scanVisible(true); proof.step = 16; return;
                }
                if (proof.step === 16) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.cursor === null, "partial-page refresh starts at window cursor");
                    Native.Bridge.failure(request.index, "store_busy");
                    proof.check(range.failed && range.hasMore && range.nextCursor === "p19",
                                "transient refresh failure preserves load-more cursor");
                    range.scanVisible(true); proof.step = 17; return;
                }
                if (proof.step === 17) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.respondPage(request);
                    proof.check(!range.failed && range.hasMore && range.nextCursor === "p19",
                                "unchanged refresh retry restores load-more control");
                    proof.step = 1; return;
                }
                if (proof.step === 1) {
                    const request = proof.nextRequest();
                    if (request) proof.respondPage(request);
                    if (range.rangeRows.length < 200 && !range.pendingRequest) range.loadMore();
                    if (range.rangeRows.length < 200) return;
                    const ids = proof.mappedIds();
                    proof.check(proof.sawTailLimit && range.rangeRows.length === 200
                                && range.cachedRows.length === 200 && ids.length === 200,
                                "partial first page and one-row tail preserve all 200 bubbles");
                    proof.check(ids[0] === "1000" && ids[199] === "1199"
                                && ids.every(function(id, index) { return id === String(1000 + index); }),
                                "first window preserves original key/id order");
                    proof.check(range.hasNextWindow && !range.hasMore && range.windowIndex === 0,
                                "in-chat next-window control replaces the 200-row stop");
                    const nativeRow = listView.itemAtIndex(1);
                    proof.check(nativeRow && proof.countNamed(nativeRow, "valmaxRetainedInlineBubble") === 200,
                                "200 actual in-chat bubble components instantiated");
                    range.scanVisible(true); proof.step = 18; return;
                }
                if (proof.step === 18) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.cursor === null, "full-window refresh starts at window cursor");
                    Native.Bridge.failure(request.index, "store_busy");
                    proof.check(range.failed && range.hasNextWindow && range.nextCursor === "p200",
                                "transient refresh failure preserves next-window cursor");
                    range.scanVisible(true); proof.step = 19; return;
                }
                if (proof.step === 19) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.respondPage(request);
                    proof.check(!range.failed && range.hasNextWindow && range.nextCursor === "p200"
                                && !range.hasMore, "unchanged refresh retry preserves next-window control");
                    range.nextWindow(); proof.step = 2; return;
                }
                if (proof.step === 2) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.cursor === "p200" && request.body.limit === 20,
                                "next window starts after exact 200-row boundary");
                    proof.respondPage(request); proof.step = 3; return;
                }
                if (proof.step === 3) {
                    if (range.windowIndex !== 1) return;
                    proof.check(range.windowCursors.length === 2 && range.windowCursors[1] === "p200"
                                && range.cachedRows.length === 2 && proof.mappedIds().join(",") === "1200,1201",
                                "new window replaces old bubble text without a gap or duplicate");
                    proof.check(range.hasPreviousWindow && !range.hasNextWindow,
                                "previous-window control is available in the chat");
                    const control = proof.findNamed(listView.itemAtIndex(3), "retentionWindowPrevious");
                    proof.check(control && control.visible, "previous in-chat button rendered");
                    range.previousWindow(); proof.step = 4; return;
                }
                if (proof.step === 4) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.cursor === null && request.body.limit === 20,
                                "previous window reopens at its saved cursor");
                    proof.respondPage(request); proof.step = 5; return;
                }
                if (proof.step === 5) {
                    if (range.windowIndex !== 0) return;
                    proof.check(range.cachedRows.length === 19 && proof.mappedIds()[0] === "1000"
                                && proof.mappedIds()[18] === "1018", "older bubbles restored from range API");
                    proof.check(range.hasMore && range.hasNextWindow && !range.hasPreviousWindow,
                                "known next window remains navigable without refilling 200 rows");
                    range.nextWindow(); proof.step = 6; return;
                }
                if (proof.step === 6) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.cursor === "p200", "forward return uses saved boundary");
                    proof.respondPage(request); proof.step = 7; return;
                }
                if (proof.step === 7) {
                    if (range.windowIndex !== 1) return;
                    proof.check(proof.mappedIds().join(",") === "1200,1201"
                                && range.cachedRows.length <= 200, "forward return keeps bounded newest bubbles");
                    range.scanVisible(true); proof.step = 70; return;
                }
                if (proof.step === 70) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.cursor === "p200",
                                "periodic refresh keeps the visible window cursor");
                    proof.respondPage(request);
                    proof.check(range.windowIndex === 1
                                && proof.mappedIds().join(",") === "1200,1201",
                                "periodic refresh does not jump to oldest bubbles");
                    range.previousWindow(); proof.step = 8; return;
                }
                if (proof.step === 8) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    Native.Bridge.failure(request.index, "store_busy");
                    proof.check(range.failed && range.windowIndex === 1
                                && proof.mappedIds().join(",") === "1200,1201",
                                "failed navigation preserves displayed window");
                    range.previousWindow(); proof.step = 9; return;
                }
                if (proof.step === 9) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.respondPage(request);
                    proof.check(range.windowIndex === 0 && !range.failed
                                && proof.mappedIds()[0] === "1000", "failed navigation retries safely");
                    range.nextWindow(); proof.step = 90; return;
                }
                if (proof.step === 90) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.cursor === "p200", "known cursor reopens later window");
                    proof.respondPage(request);
                    range.scanVisible(true); proof.step = 91; return;
                }
                if (proof.step === 91) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.cursor === "p200", "stale refresh targets active window");
                    Native.Bridge.failure(request.index, "stale_cursor");
                    proof.check(range.windowIndex === 0 && range.windowCursors.length === 1
                                && proof.mappedRows().length === 0,
                                "stale cursor clears prior windows and visible bubbles");
                    proof.step = 92; return;
                }
                if (proof.step === 92) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.cursor === null && request.body.accountId === "11",
                                "stale cursor restarts at account-scoped first page");
                    proof.respondPage(request);
                    proof.check(range.windowIndex === 0 && range.windowCursors.length === 1
                                && proof.mappedIds()[0] === "1000", "stale restart restores first window");
                    range.loadMore(); proof.step = 10; return;
                }
                if (proof.step === 10) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.accountId === "11" && request.body.cursor === "p19",
                                "old-account page is pending at switch");
                    proof.account = "99";
                    proof.check(proof.mappedRows().length === 0 && rangeLoader.item.windowIndex === 0
                                && rangeLoader.item.windowCursors.length === 1,
                                "account switch clears bubbles and all window cursors");
                    proof.respondPage(request);
                    proof.check(proof.mappedRows().length === 0,
                                "late old-account response cannot repopulate switched chat");
                    proof.step = 11; return;
                }
                if (proof.step === 11) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.accountId === "99" && request.body.cursor === null,
                                "new account begins at its own first cursor");
                    proof.respondPage(request); proof.step = 12; return;
                }
                if (proof.step === 12) {
                    proof.check(proof.mappedIds().join(",") === "9000"
                                && range.cachedRows.length === 1 && !range.hasPreviousWindow,
                                "only new-account bubble appears in the chat");
                    rangeLoader.active = false;
                    proof.check(rangeLoader.item === null, "UI range destroyed for restart");
                    rangeLoader.active = true;
                    proof.step = 13; return;
                }
                if (proof.step === 13) {
                    const request = proof.nextRequest();
                    if (!request) return;
                    proof.check(request.body.accountId === "99" && request.body.cursor === null,
                                "restart reloads selected account from durable interface");
                    proof.respondPage(request); proof.step = 14; return;
                }
                if (proof.step === 14) {
                    const fresh = rangeLoader.item;
                    proof.check(fresh && fresh.windowIndex === 0 && fresh.windowCursors.length === 1
                                && fresh.cachedRows.length === 1 && proof.mappedIds().join(",") === "9000",
                                "restart never resurrects other-account window or bubble");
                    proof.check(proof.countNamed(listView.itemAtIndex(1), "valmaxRetainedInlineBubble") === 1,
                                "one actual in-chat bubble rendered after restart");
                    proof.finished = true; run.stop();
                }
            } catch (error) {
                proof.failure = String(error);
                proof.finished = true; run.stop();
            }
        }
    }
}
