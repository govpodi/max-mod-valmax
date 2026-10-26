// Synthetic visual and transport exercise; no MAX profile or message data.
import QtQuick
import QtQuick.Controls
import ValMAX.Native 1.0 as Native

Item {
    id: proof
    width: 220; height: 400
    property string account: "11"
    property string chat: "21"
    property bool viewerActive: true
    property bool finished: false
    property string failure: ""
    property int checks: 0
    property int step: 0
    property int ticks: 0
    property int baseRequestIndex: -1
    property bool sawRetained: false
    property bool sawBridgeRequest: false
    readonly property int requestCount: Native.Bridge.requests.length
    property var originalRow: null
    property real originalHeight: 0
    readonly property string longText: "<b>literal</b> " + "x".repeat(1500)
    property var retained: null
    property var hostRef: null
    property bool orderedFallback: false
    property var embeddedOrderedRows: []

    QtObject {
        id: appearance
        property color colorBubblesIncomingBackgroundSurfaceSecondary: "#d6e2f0"
        property color colorBubblesIncomingStrokeNeutralSecondary: "#57718e"
        property color colorBubblesIncomingTextBodySecondary: "#334e6a"
        property color colorBubblesIncomingTextBody: "#142c44"
    }
    ListView {
        id: listView
        objectName: "retentionInlineTestHistoryListView"
        width: proof.width; height: proof.height
        model: ListModel { ListElement { title: "ordinary row" } }
        delegate: Item {
            id: nativeRow
            objectName: "retentionInlineTestOrdinaryRow"
            required property int index
            width: listView.width
            height: 40 + retainedLoader.height
            Component.onCompleted: proof.hostRef = nativeRow
            Rectangle { width: parent.width; height: 40; color: "#fff" }
            Loader {
                id: retainedLoader
                objectName: "retentionInlineTestLoader"
                y: 40
                width: parent.width
                height: item ? item.implicitHeight : 0
                active: nativeRow.index === listView.count - 1
                source: "MessageRetentionInline.qml"
                onLoaded: {
                    proof.retained = item;
                    item.accountId = Qt.binding(function() { return proof.account; });
                    item.chatId = Qt.binding(function() { return proof.chat; });
                    item.nativeRowCount = Qt.binding(function() { return listView.count; });
                    item.chatAppearance = Qt.binding(function() { return appearance; });
                    item.orderedFallback = Qt.binding(function() { return proof.orderedFallback; });
                    item.embeddedOrderedRows = Qt.binding(function() { return proof.embeddedOrderedRows; });
                    item.active = Qt.binding(function() { return proof.viewerActive; });
                }
            }
        }
        footer: Item { height: 24 }
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
    function reply(accountId, chatId, items, nextCursor, revision) {
        return {schema: 1, accountId: accountId, chatId: chatId,
                items: items, nextCursor: nextCursor, revision: revision};
    }
    function request(index) { return Native.Bridge.requests[baseRequestIndex + index]; }
    function hasRequest(count) { return Native.Bridge.requests.length >= baseRequestIndex + count; }
    function success(index, value) { Native.Bridge.success(baseRequestIndex + index, value); }
    function originalPage() {
        return reply("11", "21", [{messageId: "9223372036854775807",
            orderKey: null, sentAtMs: null, receivedAtMs: 1720000000000,
            deletedAtMs: 1720000001000, text: longText}],
            "later", "a".repeat(64));
    }
    function twentyRows() {
        const rows = [];
        for (let number = 2; number < 22; number += 1) rows.push({
            messageId: String(number), orderKey: null, sentAtMs: null,
            receivedAtMs: 1720000000000 + number,
            deletedAtMs: 1720000001000 + number,
            text: "synthetic page row " + number
        });
        return rows;
    }
    function mixedRows() {
        return [{messageId: "25", orderKey: "1720000000025", sentAtMs: null,
            receivedAtMs: 1720000000025, deletedAtMs: 1720000001025,
            text: "ordered synthetic text"},
            {messageId: "24", orderKey: null, sentAtMs: null,
             receivedAtMs: 1720000000024, deletedAtMs: 1720000001024,
             text: "legacy synthetic text"}];
    }
    Timer {
        id: run
        interval: 5; repeat: true; running: true
        onTriggered: {
            try {
                if (++proof.ticks > 1000) throw new Error("synthetic Qt deadline");
                proof.sawRetained = !!proof.retained;
                proof.sawBridgeRequest = Native.Bridge.requests.length > 0;
                if (!proof.retained) return;
                if (proof.step === 0) {
                    if (Native.Bridge.requests.length < 1) return;
                    const pending = proof.retained.pendingRequest;
                    if (!pending || !pending.requestId) return;
                    proof.baseRequestIndex = Native.Bridge.requests.findIndex(function(value) {
                        return value.id === pending.requestId;
                    });
                    if (proof.baseRequestIndex < 0) return;
                    const first = proof.request(0).value;
                    proof.check(first.operation === "retention.inline", "real transport operation");
                    proof.check(first.value.accountId === "11" && first.value.chatId === "21"
                                && first.value.cursor === null && first.value.limit === 20
                                && first.value.legacyOnly === true,
                                "selected legacy-only scope and page limit");
                    proof.check(listView.count === 1 && proof.hostRef.parent !== null,
                                "stock list model and native row retained");
                    proof.success(0, proof.originalPage());
                    proof.step = 1; return;
                }
                if (proof.step === 1) {
                    proof.check(proof.retained.displayedCount === 1, "inline row shown");
                    const row = proof.findNamed(proof.retained, "valmaxRetainedInlineBubbleRow");
                    const bubble = proof.findNamed(proof.retained, "valmaxRetainedInlineBubble");
                    const icon = proof.findNamed(proof.retained, "valmaxRetainedInlineTrashIcon");
                    const text = proof.findNamed(proof.retained, "valmaxRetainedInlineText");
                    const body = proof.findNamed(proof.retained, "valmaxRetainedInlineBodyScroll");
                    const stamp = proof.findNamed(proof.retained, "valmaxRetainedInlineDeletedAt");
                    const placement = proof.findNamed(proof.retained, "valmaxRetainedInlinePlacementLabel");
                    proof.check(row && bubble && icon && text && body && stamp && placement,
                                "rendered bubble and trash icon objects");
                    proof.check(row.messageId === "9223372036854775807", "64-bit ID remains a string");
                    proof.check(row.approximatePlacement && placement.visible
                                && placement.text.indexOf("Положение в истории приблизительное") === 0
                                && placement.text.indexOf("дата сообщения не подтверждена") > 0,
                                "unplaced bubble states approximate position and unverified date");
                    proof.check(icon.source.toString() === "qrc:/icon_pack/delete_fill.svg",
                                "client trash icon URL");
                    proof.check(text.textFormat === Text.PlainText && text.text.indexOf("<b>literal</b>") === 0,
                                "message text is literal plain text");
                    proof.check(text.color.toString() === appearance.colorBubblesIncomingTextBody.toString(),
                                "incoming theme body color");
                    proof.check(bubble.x === 16 && bubble.width <= 480 && bubble.width <= proof.width - 32,
                                "left aligned and narrow width bounded");
                    proof.check(body.height <= 420 && bubble.height < 560
                                && stamp.width <= bubble.width - 20,
                                "long body and deletion time fit the bubble");
                    proof.check(proof.hostRef.height === 40 + proof.retained.implicitHeight
                                && listView.contentHeight >= proof.hostRef.height,
                                "bubble is inside the native history row");
                    row.expanded = true;
                    proof.originalRow = row;
                    proof.originalHeight = proof.hostRef.height;
                    proof.retained.requestPage(null, "refresh");
                    proof.step = 2; return;
                }
                if (proof.step === 2) {
                    if (!proof.hasRequest(2)) return;
                    proof.success(1, proof.originalPage());
                    proof.check(proof.findNamed(proof.retained, "valmaxRetainedInlineBubbleRow") === proof.originalRow
                                && proof.originalRow.expanded && proof.hostRef.height === proof.originalHeight,
                                "unchanged refresh preserves row and height");
                    proof.viewerActive = false;
                    proof.check(proof.retained.displayedCount === 1 && proof.hostRef.height === proof.originalHeight,
                                "inactive window keeps inline scroll geometry");
                    proof.viewerActive = true;
                    proof.step = 3; return;
                }
                if (proof.step === 3) {
                    if (!proof.hasRequest(3)) return;
                    proof.success(2, proof.originalPage());
                    proof.check(proof.findNamed(proof.retained, "valmaxRetainedInlineBubbleRow") === proof.originalRow
                                && proof.originalRow.expanded && proof.hostRef.height === proof.originalHeight,
                                "restore with same data preserves expanded bubble");
                    proof.retained.requestPage(null, "refresh");
                    proof.step = 4; return;
                }
                if (proof.step === 4) {
                    if (!proof.hasRequest(4)) return;
                    proof.chat = "22";
                    proof.check(proof.retained.displayedCount === 0 && proof.retained.implicitHeight === 0,
                                "chat switch clears previous text immediately");
                    proof.check(proof.findNamed(proof.retained, "valmaxRetainedInlineBubbleRow") === null,
                                "old-chat bubble removed from native row");
                    proof.success(3, proof.originalPage());
                    proof.check(proof.retained.displayedCount === 0,
                                "late prior-chat reply is discarded");
                    proof.step = 5; return;
                }
                if (proof.step === 5) {
                    if (!proof.hasRequest(5)) return;
                    const scoped = proof.request(4).value;
                    proof.check(scoped.value.accountId === "11" && scoped.value.chatId === "22",
                                "next request uses selected chat");
                    proof.success(4, proof.reply("11", "22", [], "skip", "b".repeat(64)));
                    proof.check(proof.retained.displayedCount === 0
                                && proof.retained.nextCursor === "skip",
                                "empty marker-only page still exposes next cursor");
                    proof.retained.nextPage();
                    proof.step = 6; return;
                }
                if (proof.step === 6) {
                    if (!proof.hasRequest(6)) return;
                    proof.check(proof.request(5).value.value.cursor === "skip",
                                "next page uses opaque cursor");
                    proof.success(5, proof.reply("11", "22", proof.twentyRows(), null, "b".repeat(64)));
                    proof.check(proof.retained.displayedCount === 20 && listView.count === 1,
                                "20 local bubbles leave stock row index intact");
                    proof.retained.previousPage();
                    proof.step = 7; return;
                }
                if (proof.step === 7) {
                    if (!proof.hasRequest(7)) return;
                    proof.check(proof.request(6).value.value.cursor === null,
                                "back navigation reaches first page");
                    proof.success(6, proof.reply("11", "22", [], "skip", "b".repeat(64)));
                    proof.check(proof.retained.displayedCount === 0, "empty first page restored");
                    proof.retained.refresh();
                    proof.step = 8; return;
                }
                if (proof.step === 8) {
                    if (!proof.hasRequest(8)) return;
                    proof.success(7, proof.reply("99", "22", [], null, "b".repeat(64)));
                    proof.check(proof.retained.displayedCount === 0 && proof.retained.failed,
                                "wrong-account reply rejected");
                    proof.retained.refresh();
                    proof.step = 9; return;
                }
                if (proof.step === 9) {
                    if (!proof.hasRequest(9)) return;
                    proof.success(8, proof.reply("11", "22", [{
                        messageId: "23", orderKey: null, sentAtMs: null,
                        receivedAtMs: 1720000000023, deletedAtMs: 1720000001023,
                        text: "old account synthetic text"
                    }], null, "c".repeat(64)));
                    proof.check(proof.retained.displayedCount === 1,
                                "old account row visible before account switch");
                    proof.retained.requestPage(null, "refresh");
                    proof.step = 10; return;
                }
                if (proof.step === 10) {
                    if (!proof.hasRequest(10)) return;
                    proof.account = "99";
                    proof.check(proof.retained.displayedCount === 0 && proof.retained.implicitHeight === 0,
                                "account switch clears old account bubble immediately");
                    proof.check(proof.findNamed(proof.retained, "valmaxRetainedInlineBubbleRow") === null,
                                "old-account bubble removed from native row");
                    proof.success(9, proof.reply("11", "22", [{
                        messageId: "23", orderKey: null, sentAtMs: null,
                        receivedAtMs: 1720000000023, deletedAtMs: 1720000001023,
                        text: "late old account synthetic text"
                    }], null, "c".repeat(64)));
                    proof.check(proof.retained.displayedCount === 0,
                                "late prior-account reply is discarded");
                    proof.step = 11; return;
                }
                if (proof.step === 11) {
                    if (!proof.hasRequest(11)) return;
                    proof.check(proof.request(10).value.value.accountId === "99"
                                && proof.request(10).value.value.chatId === "22",
                                "new account request has exact scope");
                    proof.success(10, proof.reply("99", "22", [{
                        messageId: "24", orderKey: null, sentAtMs: null,
                        receivedAtMs: 1720000000024, deletedAtMs: 1720000001024,
                        text: "new account synthetic text"
                    }], null, "d".repeat(64)));
                    const row = proof.findNamed(proof.retained, "valmaxRetainedInlineBubbleRow");
                    proof.check(proof.retained.displayedCount === 1 && row.messageId === "24"
                                && row.messageText === "new account synthetic text",
                                "only new account bubble shown");
                    proof.embeddedOrderedRows = [{messageId: "25"}];
                    proof.orderedFallback = true;
                    proof.check(proof.retained.displayedCount === 0,
                                "fallback switch clears prior page");
                    proof.step = 12; return;
                }
                if (proof.step === 12) {
                    if (!proof.hasRequest(12)) return;
                    proof.check(proof.request(11).value.value.legacyOnly === false,
                                "fallback requests all retained rows");
                    proof.success(11, proof.reply("99", "22", proof.mixedRows(), null, "e".repeat(64)));
                    const row = proof.findNamed(proof.retained, "valmaxRetainedInlineBubbleRow");
                    proof.check(proof.retained.displayedCount === 1 && row.messageId === "24",
                                "already embedded ordered row has no duplicate");
                    proof.embeddedOrderedRows = [];
                    proof.step = 13; return;
                }
                if (proof.step === 13) {
                    if (!proof.hasRequest(13)) return;
                    proof.check(proof.request(12).value.value.legacyOnly === false,
                                "fallback stays all-rows after range cache changes");
                    proof.success(12, proof.reply("99", "22", proof.mixedRows(), null, "e".repeat(64)));
                    proof.check(proof.retained.displayedCount === 2,
                                "unplaced ordered row appears as an approximate chat bubble");
                    proof.orderedFallback = false;
                    proof.step = 14; return;
                }
                if (proof.step === 14) {
                    if (!proof.hasRequest(14)) return;
                    proof.check(proof.request(13).value.value.legacyOnly === true,
                                "recovered range returns approximate bubbles to legacy only");
                    proof.success(13, proof.reply("99", "22", [proof.mixedRows()[1]],
                                                       null, "e".repeat(64)));
                    proof.check(proof.retained.displayedCount === 1,
                                "recovery hides approximate ordered duplicate");
                    proof.retained.refresh();
                    proof.step = 15; return;
                }
                if (proof.step === 15) {
                    if (!proof.hasRequest(15)) return;
                    proof.success(14, proof.reply("99", "22", proof.twentyRows(),
                                                       "page21", "f".repeat(64)));
                    proof.check(proof.retained.displayedCount === 20,
                                "first 20 fallback bubbles remain bounded");
                    proof.retained.nextPage();
                    proof.step = 16; return;
                }
                if (proof.step === 16) {
                    if (!proof.hasRequest(16)) return;
                    proof.check(proof.request(15).value.value.cursor === "page21",
                                "21st bubble uses a local page cursor");
                    proof.success(15, proof.reply("99", "22", [{
                        messageId: "22", orderKey: null, sentAtMs: null,
                        receivedAtMs: 1720000000022, deletedAtMs: 1720000001022,
                        text: "synthetic row beyond first page"
                    }], null, "f".repeat(64)));
                    const row = proof.findNamed(proof.retained, "valmaxRetainedInlineBubbleRow");
                    proof.check(proof.retained.displayedCount === 1 && row.messageId === "22"
                                && row.approximatePlacement,
                                "21st retained message is still an inline trash bubble");
                    proof.retained.previousPage();
                    proof.step = 17; return;
                }
                if (proof.step === 17) {
                    if (!proof.hasRequest(17)) return;
                    proof.check(proof.request(16).value.value.cursor === null,
                                "previous page returns to first 20 bubbles");
                    proof.success(16, proof.reply("99", "22", proof.twentyRows(),
                                                       "page21", "f".repeat(64)));
                    proof.check(proof.retained.displayedCount === 20,
                                "first 20 bubbles restored without stock model changes");
                    proof.finished = true; run.stop();
                }
            } catch (error) {
                proof.failure = String(error);
                proof.finished = true; run.stop();
            }
        }
    }
}
