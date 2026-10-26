// Approximate retained bubbles inside the selected chat's native history row.
// This component never changes the client's history model or its indices.
import QtQuick
import QtQuick.Controls
import ValMAX.Native 1.0 as Native
import "." as ValMAX

Item {
    id: inline
    objectName: "valmaxRetentionInline"
    property string accountId: ""
    property string chatId: ""
    property bool active: false
    property int nativeRowCount: 0
    property bool orderedFallback: false
    property var embeddedOrderedRows: []
    property var chatAppearance: null
    readonly property bool backendReady: Native.RetentionTransport.lookupAvailable
    readonly property int pageSize: 20
    readonly property int maximumResponseCharacters: 8 * 1024 * 1024
    readonly property int displayedCount: savedRows.count
    property bool completed: false
    property int generation: 0
    property var pendingRequest: null
    property var currentCursor: null
    property var nextCursor: null
    property var previousCursors: []
    property string revision: ""
    property int staleRestarts: 0
    property bool failed: false

    width: parent ? parent.width : 0
    implicitHeight: content.visible ? content.implicitHeight : 0
    height: implicitHeight
    ListModel { id: savedRows }

    function verifiedMessageTimeMs(key) {
        if (typeof key !== "string" || !/^[1-9][0-9]*$/.test(key)) return -1;
        const millis = Number(key);
        if (!Number.isSafeInteger(millis) || millis < 946684800000
                || millis >= 4102444800000) return -1;
        return new Date(millis).getTime() === millis ? millis : -1;
    }

    function validReply(value, requestedAccount, requestedChat) {
        if (!value || typeof value !== "object" || Array.isArray(value)
                || Object.keys(value).sort().join("|") !== "accountId|chatId|items|nextCursor|revision|schema"
                || value.schema !== 1 || value.accountId !== requestedAccount || value.chatId !== requestedChat
                || typeof value.revision !== "string" || !/^[0-9a-f]{64}$/.test(value.revision)
                || !(value.nextCursor === null || typeof value.nextCursor === "string"
                    && value.nextCursor.length > 0 && value.nextCursor.length <= 256)
                || !Array.isArray(value.items) || value.items.length > pageSize) return false;
        const seen = {};
        for (const row of value.items) {
            if (!row || typeof row !== "object" || Array.isArray(row)
                    || Object.keys(row).sort().join("|") !== "deletedAtMs|messageId|orderKey|receivedAtMs|sentAtMs|text"
                    || !Native.RetentionTransport.validId(row.messageId, true) || seen[row.messageId]
                    || !(row.orderKey === null || Native.RetentionTransport.validId(row.orderKey, true))
                    || !Number.isSafeInteger(row.receivedAtMs) || row.receivedAtMs < 0
                    || !Number.isSafeInteger(row.deletedAtMs) || row.deletedAtMs < 0
                    || !(row.sentAtMs === null || Number.isSafeInteger(row.sentAtMs) && row.sentAtMs >= 0)
                    || typeof row.text !== "string" || row.text.length < 1 || row.text.length > 65536)
                return false;
            seen[row.messageId] = true;
        }
        return true;
    }
    function sameRows(items) {
        if (savedRows.count !== items.length) return false;
        for (let index = 0; index < items.length; index += 1) {
            const old = savedRows.get(index), row = items[index];
            if (old.messageId !== row.messageId || old.text !== row.text
                    || old.receivedAtMs !== row.receivedAtMs
                    || old.deletedAtMs !== row.deletedAtMs
                    || old.messageTimeMs !== verifiedMessageTimeMs(row.orderKey)) return false;
        }
        return true;
    }
    function abortPending() {
        requestWatchdog.stop();
        const request = pendingRequest;
        pendingRequest = null;
        if (request) {
            request.onreadystatechange = function() {};
            try { request.abort(); } catch (_) {}
        }
    }
    function resetScope() {
        generation += 1;
        abortPending(); savedRows.clear();
        currentCursor = null; nextCursor = null; previousCursors = [];
        revision = ""; staleRestarts = 0; failed = false;
        if (completed && active && backendReady
                && Native.RetentionTransport.validId(accountId, true)
                && Native.RetentionTransport.validId(chatId, false)) requestPage(null, "first");
    }
    function restartAfterStale() {
        if (staleRestarts >= 2) {
            savedRows.clear(); nextCursor = null; failed = true; return;
        }
        staleRestarts += 1; generation += 1;
        abortPending(); savedRows.clear();
        currentCursor = null; nextCursor = null; previousCursors = []; revision = "";
        requestPage(null, "first");
    }
    function requestPage(cursor, direction) {
        if (!completed || !active || !backendReady || pendingRequest
                || !Native.RetentionTransport.validId(accountId, true)
                || !Native.RetentionTransport.validId(chatId, false)) return;
        const requestedAccount = accountId, requestedChat = chatId, requestGeneration = generation;
        const legacyOnly = nativeRowCount > 0 && !orderedFallback;
        const oldCursor = currentCursor, oldPrevious = previousCursors;
        let request;
        try { request = Native.RetentionTransport.createRequest("inline"); request.open("POST"); }
        catch (_) { failed = true; return; }
        pendingRequest = request;
        requestWatchdog.restart();
        request.onreadystatechange = function() {
            if (request.readyState !== 4 || pendingRequest !== request) return;
            pendingRequest = null; requestWatchdog.stop();
            if (!completed || !active || generation !== requestGeneration
                    || accountId !== requestedAccount || chatId !== requestedChat) return;
            try {
                if (request.status === 409) {
                    const error = JSON.parse(request.responseText);
                    if (error && error.error === "stale_cursor") { restartAfterStale(); return; }
                }
                if (request.status !== 200 || typeof request.responseText !== "string"
                        || request.responseText.length > maximumResponseCharacters) throw new Error("response");
                const value = JSON.parse(request.responseText);
                if (!validReply(value, requestedAccount, requestedChat)) throw new Error("schema");
                if (legacyOnly && value.items.some(function(row) { return row.orderKey !== null; }))
                    throw new Error("ordered_row_in_legacy_page");
                const embeddedIds = {};
                if (!legacyOnly && nativeRowCount > 0 && Array.isArray(embeddedOrderedRows))
                    for (const row of embeddedOrderedRows)
                        if (row && typeof row.messageId === "string")
                            embeddedIds[row.messageId] = true;
                const approximateRows = value.items.filter(function(row) {
                    return !embeddedIds[row.messageId];
                });
                if (cursor !== null && revision.length && revision !== value.revision) {
                    restartAfterStale(); return;
                }
                // Polling an unchanged first page must not rebuild bubbles:
                // that would collapse expanded text and move the user's scroll.
                if ((direction === "refresh" || direction === "resume")
                        && currentCursor === cursor
                        && nextCursor === value.nextCursor && sameRows(approximateRows)) {
                    revision = value.revision; failed = false; staleRestarts = 0;
                    return;
                }
                if (direction === "next") {
                    previousCursors = oldPrevious.concat([oldCursor]); currentCursor = cursor;
                } else if (direction === "previous") {
                    previousCursors = oldPrevious.slice(0, -1); currentCursor = cursor;
                } else if (direction === "first") {
                    previousCursors = []; currentCursor = null;
                }
                revision = value.revision; nextCursor = value.nextCursor;
                savedRows.clear();
                for (const row of approximateRows) savedRows.append({
                    messageId: row.messageId, text: row.text,
                    receivedAtMs: row.receivedAtMs, deletedAtMs: row.deletedAtMs,
                    messageTimeMs: verifiedMessageTimeMs(row.orderKey)
                });
                failed = false;
                if (cursor === null) staleRestarts = 0;
            } catch (_) {
                savedRows.clear(); nextCursor = null; failed = true;
            }
        };
        try {
            request.send(JSON.stringify({schema: 1, accountId: requestedAccount,
                chatId: requestedChat, cursor: cursor, limit: pageSize, legacyOnly: legacyOnly}));
        } catch (_) { abortPending(); savedRows.clear(); nextCursor = null; failed = true; }
    }
    function nextPage() {
        if (!pendingRequest && nextCursor !== null) requestPage(nextCursor, "next");
    }
    function previousPage() {
        if (!pendingRequest && previousCursors.length > 0)
            requestPage(previousCursors[previousCursors.length - 1], "previous");
    }
    function refresh() {
        if (!completed) return;
        generation += 1; abortPending();
        currentCursor = null; previousCursors = []; staleRestarts = 0;
        requestPage(null, "refresh");
    }
    onAccountIdChanged: if (completed) resetScope()
    onChatIdChanged: if (completed) resetScope()
    onNativeRowCountChanged: if (completed) resetScope()
    onOrderedFallbackChanged: if (completed) resetScope()
    onEmbeddedOrderedRowsChanged: if (completed && orderedFallback) resetScope()
    onActiveChanged: {
        if (!completed) return;
        if (!active) { generation += 1; abortPending(); }
        else if (backendReady) requestPage(currentCursor,
            currentCursor === null ? "refresh" : "resume");
    }
    onBackendReadyChanged: if (completed) resetScope()
    Component.onCompleted: { completed = true; resetScope(); }
    Component.onDestruction: { completed = false; generation += 1; abortPending(); savedRows.clear(); }

    Timer {
        id: refreshTimer
        interval: 5000; repeat: true
        running: inline.completed && inline.active && inline.backendReady
        onTriggered: {
            if (inline.pendingRequest) return;
            if (inline.failed) inline.refresh();
            else if (inline.currentCursor === null) inline.requestPage(null, "refresh");
        }
    }
    Timer {
        id: requestWatchdog
        interval: 10000
        onTriggered: {
            inline.abortPending(); savedRows.clear();
            inline.nextCursor = null; inline.failed = true;
        }
    }

    Column {
        id: content
        objectName: "valmaxRetentionInlineContent"
        width: inline.width
        spacing: 4
        // Marker-only pages can still expose the next local page.
        visible: savedRows.count > 0 || inline.nextCursor !== null
                 || inline.previousCursors.length > 0

        Repeater {
            model: savedRows
            delegate: ValMAX.MessageRetentionBubble {
                required property var model
                width: content.width
                messageId: model.messageId
                messageText: model.text
                deletedAtMs: model.deletedAtMs
                messageTimeMs: model.messageTimeMs
                approximatePlacement: true
                chatAppearance: inline.chatAppearance
            }
        }
        // Pagination stays in the chat flow and only appears when needed.
        Row {
            x: 16; spacing: 8
            visible: inline.previousCursors.length > 0 || inline.nextCursor !== null
            Button {
                objectName: "valmaxRetentionInlinePrevious"
                visible: inline.previousCursors.length > 0
                text: "Назад"
                enabled: !inline.pendingRequest
                onClicked: inline.previousPage()
            }
            Button {
                objectName: "valmaxRetentionInlineNext"
                visible: inline.nextCursor !== null
                text: "Показать ещё"
                enabled: !inline.pendingRequest
                onClicked: inline.nextPage()
            }
        }
    }
}
