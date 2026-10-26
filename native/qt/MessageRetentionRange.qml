// Read retained text only for the selected chat's visible native history range.
// This never modifies the MAX model and never sends message text over a network.
import QtQuick
import ValMAX.Native 1.0 as Native
import QmlSingletonesModule

Item {
    id: range
    objectName: "valmaxRetentionRange"
    property var view: null
    property string accountId: ""
    property string chatId: ""
    property bool active: false
    property bool hasMoreBackward: true
    property bool hasMoreForward: true
    readonly property bool backendReady: Native.RetentionTransport.lookupAvailable
    readonly property int pageSize: 20
    readonly property int maxRangeRows: 200
    readonly property int maxNativeSpan: 128
    readonly property int maximumResponseCharacters: 8 * 1024 * 1024
    readonly property string maximumOrderKey: "9223372036854775807"
    readonly property bool hasMore: nextCursor !== null && rangeRows.length < maxRangeRows
    readonly property bool hasNextWindow: windowIndex + 1 < windowCursors.length
        || nextCursor !== null && rangeRows.length >= maxRangeRows
    readonly property bool hasPreviousWindow: windowIndex > 0
    property bool completed: false
    property int generation: 0
    property var pendingRequest: null
    property var currentBounds: null
    property string queryToken: ""
    property var rangeRows: []
    property var firstPageRows: []
    property var firstNextCursor: null
    property var cachedRows: []
    property var rowsByIndex: ({})
    property var rowsBeforeIndex: ({})
    property var nextCursor: null
    // Only opaque page cursors survive a window change; retained text remains
    // bounded to the currently displayed 200-row window.
    property var windowCursors: [null]
    property int windowIndex: 0
    property string revision: ""
    property string storeRevision: ""
    property int staleRestarts: 0
    property int invalidRetries: 0
    property bool failed: false
    property int pagingAnchorIndex: -1
    property bool pagingBefore: false
    property bool fallbackLatched: false
    // If original-order placement cannot be read, the chat footer can still
    // reach every retained row through the same bounded local page API. Keep
    // fallback visible during a retry until a valid range reply is accepted.
    readonly property bool fallbackToFooter: completed && active && backendReady &&
        (fallbackLatched || currentBounds === null && invalidRetries >= 3 || failed)
    width: 0; height: 0; visible: false

    function compareIds(first, second) {
        return Native.RetentionTransport.comparePositiveIds(first, second);
    }
    function comparePair(firstKey, firstId, secondKey, secondId) {
        const keyOrder = compareIds(firstKey, secondKey);
        return keyOrder || compareIds(firstId, secondId);
    }
    function messageTimeMs(key) {
        if (typeof key !== "string" || !/^[1-9][0-9]*$/.test(key)) return -1;
        const millis = Number(key);
        // The pinned MAX storage `time` and QmlUtils.toDateTime use epoch ms.
        // A positive order key alone is not proof of a displayable date.
        if (!Number.isSafeInteger(millis) || millis < 946684800000
                || millis >= 4102444800000) return -1;
        return new Date(millis).getTime() === millis ? millis : -1;
    }
    function calendarDay(key) {
        const millis = messageTimeMs(key);
        if (millis < 0) return "";
        const date = new Date(millis);
        return [date.getFullYear(), date.getMonth() + 1, date.getDate()].join("-");
    }
    function nativeKeyAt(index) {
        try {
            if (!view || !view.model || index < 0 || index >= view.count
                    || typeof view.model.index !== "function"
                    || typeof view.model.data !== "function") return null;
            const modelIndex = view.model.index(index, 0);
            const context = view.model.data(modelIndex, 0x100);
            if (!context || typeof context.get !== "function") return null;
            const message = context.get();
            if (!message) return null;
            // A delayed message exposes an override time rather than the
            // original storage time used by the sidecar order key.
            if (message.isDelayed !== false) return null;
            const messageId = String(QmlUtils.messageIdString(message.messageId));
            if (!Native.RetentionTransport.validId(messageId, true)) return null;
            const raw = message.time;
            let key = null;
            if (typeof raw === "string") key = raw;
            else if (typeof raw === "number" && Number.isSafeInteger(raw) && raw > 0) key = String(raw);
            else if (typeof raw === "bigint") key = raw.toString();
            else {
                // The pinned client exposes MeTypes::TimeStamp through QmlUtils.
                // Its toDateTime conversion is fromMSecsSinceEpoch without scaling.
                const converted = QmlUtils.toDateTime(raw);
                const millis = converted && typeof converted.getTime === "function"
                    ? converted.getTime() : NaN;
                if (Number.isSafeInteger(millis) && millis > 0) key = String(millis);
            }
            if (!Native.RetentionTransport.validId(key, true)) return null;
            // The stock section uses messageDayStartTime. Role 0x101 is not a
            // verified day role; derive the local day from the same storage
            // time that was validated for ordering instead.
            return {index: index, key: key, day: calendarDay(key), messageId: messageId};
        } catch (_) { return null; }
    }
    function visibleBounds() {
        if (!view || view.count < 1 || !active) return null;
        const count = view.count;
        let first = view.firstVisibleIndex(), last = view.lastVisibleIndex();
        if (!Number.isInteger(first) || first < 0 || first >= count
                || !Number.isInteger(last) || last < first || last >= count) return null;
        const start = Math.max(0, first - 2);
        const end = Math.min(count - 1, last + 2, start + maxNativeSpan - 1);
        const keys = [];
        for (let index = start; index <= end; index += 1) {
            const value = nativeKeyAt(index);
            if (!value || keys.length && comparePair(keys[keys.length - 1].key,
                    keys[keys.length - 1].messageId, value.key, value.messageId) >= 0)
                return null;
            keys.push(value);
        }
        if (!keys.length) return null;
        const beforeStart = start === 0 && !hasMoreBackward;
        const from = beforeStart ? "1" : keys[0].key;
        const to = end === count - 1 && !hasMoreForward
            ? maximumOrderKey : keys[keys.length - 1].key;
        if (compareIds(from, to) > 0) return null;
        const fingerprint = keys.map(function(value) {
            return [value.index, value.key, value.messageId, value.day].join(":");
        }).join("|");
        return {start: start, end: end, keys: keys, from: from, to: to,
                fingerprint: fingerprint, beforeStart: beforeStart,
                afterEnd: end === count - 1 && !hasMoreForward};
    }
    function validReply(value, account, chat, from, to, limit) {
        if (!value || typeof value !== "object" || Array.isArray(value)
                || Object.keys(value).sort().join("|") !== "accountId|chatId|fromOrderKey|items|nextCursor|revision|schema|toOrderKey"
                || value.schema !== 1 || value.accountId !== account || value.chatId !== chat
                || value.fromOrderKey !== from || value.toOrderKey !== to
                || typeof value.revision !== "string" || !/^[0-9a-f]{64}$/.test(value.revision)
                || !(value.nextCursor === null || typeof value.nextCursor === "string"
                    && /^[A-Za-z0-9_-]{1,256}$/.test(value.nextCursor))
                 || !Array.isArray(value.items) || value.items.length > limit) return false;
        let previous = null;
        for (const row of value.items) {
            if (!row || typeof row !== "object" || Array.isArray(row)
                    || Object.keys(row).sort().join("|") !== "deletedAtMs|messageId|orderKey|receivedAtMs|sentAtMs|text"
                    || !Native.RetentionTransport.validId(row.messageId, true)
                    || !Native.RetentionTransport.validId(row.orderKey, true)
                    || compareIds(row.orderKey, from) < 0 || compareIds(row.orderKey, to) > 0
                    || row.sentAtMs !== null
                    || !Number.isSafeInteger(row.receivedAtMs) || row.receivedAtMs < 0
                    || !Number.isSafeInteger(row.deletedAtMs) || row.deletedAtMs < 0
                    || typeof row.text !== "string" || row.text.length < 1 || row.text.length > 65536)
                return false;
            if (previous && (compareIds(previous.orderKey, row.orderKey) > 0
                    || previous.orderKey === row.orderKey
                       && compareIds(previous.messageId, row.messageId) >= 0)) return false;
            previous = row;
        }
        return true;
    }
    function sameRows(first, second) {
        if (first.length !== second.length) return false;
        for (let index = 0; index < first.length; index += 1) {
            const a = first[index], b = second[index];
            if (a.messageId !== b.messageId || a.orderKey !== b.orderKey
                    || a.text !== b.text || a.deletedAtMs !== b.deletedAtMs
                    || a.receivedAtMs !== b.receivedAtMs) return false;
        }
        return true;
    }
    function anchorFor(row, bounds) {
        const keys = bounds.keys, key = row.orderKey;
        // A retained marker can arrive before the ordinary MAX row vanishes.
        // Keep that row and its existing basket badge until the model removes it.
        for (const nativeRow of keys) if (nativeRow.messageId === row.messageId) return null;
        if (bounds.afterEnd && comparePair(key, row.messageId,
                keys[keys.length - 1].key, keys[keys.length - 1].messageId) > 0)
            return {index: keys[keys.length - 1].index, crossDay: false,
                    dayUnknown: !calendarDay(key), afterEnd: true, beforeStart: false,
                    tieUnknown: key === keys[keys.length - 1].key};
        let low = 0, high = keys.length;
        while (low < high) {
            const middle = Math.floor((low + high) / 2);
            if (comparePair(keys[middle].key, keys[middle].messageId,
                    key, row.messageId) < 0) low = middle + 1;
            else high = middle;
        }
        const predecessor = low - 1;
        if (predecessor < 0) return bounds.beforeStart
            ? {index: keys[0].index, crossDay: false, dayUnknown: !calendarDay(key),
               afterEnd: false, beforeStart: true,
               tieUnknown: key === keys[0].key} : null;
        if (predecessor + 1 >= keys.length || comparePair(key, row.messageId,
                keys[predecessor + 1].key, keys[predecessor + 1].messageId) >= 0) return null;
        const previousDay = keys[predecessor].day, nextDay = keys[predecessor + 1].day;
        const messageDay = calendarDay(key);
        return {index: keys[predecessor].index,
                crossDay: !!messageDay && !!previousDay && !!nextDay && previousDay !== nextDay,
                dayUnknown: !messageDay || !previousDay || !nextDay,
                afterEnd: false, beforeStart: false,
                tieUnknown: key === keys[predecessor].key
                    || key === keys[predecessor + 1].key};
    }
    function rebuildMap() {
        const map = {}, before = {};
        for (const row of cachedRows) {
            const key = String(row.anchorIndex);
            const target = row.beforeStart ? before : map;
            if (!target[key]) target[key] = [];
            target[key].push(row);
        }
        for (const target of [map, before]) for (const key of Object.keys(target))
            target[key].sort(function(a, b) {
                return comparePair(a.orderKey, a.messageId, b.orderKey, b.messageId);
            });
        rowsByIndex = map; rowsBeforeIndex = before;
        if (view) Qt.callLater(function() { if (range.view) range.view.forceLayout(); });
    }
    function cachePage(items, bounds) {
        const updated = cachedRows.slice();
        for (const row of items) {
            const anchor = anchorFor(row, bounds);
            if (!anchor) continue;
            const copy = {messageId: row.messageId, orderKey: row.orderKey,
                text: row.text, deletedAtMs: row.deletedAtMs,
                messageTimeMs: messageTimeMs(row.orderKey),
                anchorIndex: anchor.index, crossDay: anchor.crossDay,
                dayUnknown: anchor.dayUnknown, afterEnd: anchor.afterEnd,
                beforeStart: anchor.beforeStart, tieUnknown: anchor.tieUnknown};
            const previous = updated.findIndex(function(value) { return value.messageId === row.messageId; });
            if (previous >= 0) updated.splice(previous, 1);
            updated.push(copy);
        }
        cachedRows = updated.slice(-maxRangeRows);
        rebuildMap();
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
        generation += 1; abortPending(); scanDebounce.stop();
        invalidRetry.stop(); invalidRetries = 0;
        currentBounds = null; queryToken = ""; rangeRows = []; firstPageRows = []; firstNextCursor = null;
        cachedRows = []; rowsByIndex = ({}); rowsBeforeIndex = ({}); nextCursor = null;
        windowCursors = [null]; windowIndex = 0;
        revision = ""; storeRevision = ""; staleRestarts = 0;
        failed = false; pagingAnchorIndex = -1; pagingBefore = false;
        fallbackLatched = false;
        if (completed && active && backendReady) scanDebounce.restart();
    }
    function restartAfterStale() {
        if (staleRestarts >= 2) { failed = true; nextCursor = null; return; }
        staleRestarts += 1; generation += 1; abortPending();
        rangeRows = []; firstPageRows = []; firstNextCursor = null;
        cachedRows = []; rowsByIndex = ({}); rowsBeforeIndex = ({});
        windowCursors = [null]; windowIndex = 0;
        nextCursor = null; revision = "";
        requestPage(null, "first");
    }
    function scheduleScan() {
        if (completed && active && backendReady) scanDebounce.restart();
    }
    function invalidateVisibleRange() {
        if (!queryToken.length && !pendingRequest
                && Object.keys(rowsByIndex).length === 0
                && Object.keys(rowsBeforeIndex).length === 0) return;
        generation += 1; abortPending();
        currentBounds = null; queryToken = ""; rangeRows = []; firstPageRows = [];
        firstNextCursor = null; cachedRows = [];
        rowsByIndex = ({}); rowsBeforeIndex = ({});
        windowCursors = [null]; windowIndex = 0;
        nextCursor = null; revision = ""; storeRevision = "";
        failed = false; pagingAnchorIndex = -1; pagingBefore = false;
    }
    function scanVisible(forceRefresh) {
        if (!completed || !active || !backendReady
                || !Native.RetentionTransport.validId(accountId, true)
                || !Native.RetentionTransport.validId(chatId, false)) return;
        const bounds = visibleBounds();
        if (!bounds) {
            invalidateVisibleRange();
            if (invalidRetries < 3 && !invalidRetry.running) {
                invalidRetries += 1; invalidRetry.restart();
            }
            if (invalidRetries >= 3) fallbackLatched = true;
            return;
        }
        invalidRetry.stop(); invalidRetries = 0;
        const token = [bounds.start, bounds.end, bounds.from, bounds.to,
            bounds.fingerprint].join("|");
        if (token !== queryToken) {
            generation += 1; abortPending();
            if (currentBounds && currentBounds.start === bounds.start
                    && currentBounds.end === bounds.end
                    && currentBounds.fingerprint !== bounds.fingerprint) {
                cachedRows = []; rowsByIndex = ({}); rowsBeforeIndex = ({});
            }
            currentBounds = bounds; queryToken = token;
            rangeRows = []; firstPageRows = []; firstNextCursor = null;
            windowCursors = [null]; windowIndex = 0;
            nextCursor = null; revision = "";
            failed = false; pagingAnchorIndex = bounds.end; pagingBefore = false;
            requestPage(null, "first");
        } else if (forceRefresh && !pendingRequest)
            requestPage(windowCursors[windowIndex], "refresh");
    }
    function requestPage(cursor, direction) {
        if (!completed || !active || !backendReady || pendingRequest || !currentBounds) return;
        const limit = direction === "next"
            ? Math.min(pageSize, maxRangeRows - rangeRows.length) : pageSize;
        if (limit < 1) return;
        const account = accountId, chat = chatId, bounds = currentBounds;
        const requestGeneration = generation, requestedToken = queryToken;
        let request;
        try { request = Native.RetentionTransport.createRequest("range"); request.open("POST"); }
        catch (_) { failed = true; return; }
        pendingRequest = request; requestWatchdog.restart();
        request.onreadystatechange = function() {
            if (request.readyState !== 4 || pendingRequest !== request) return;
            pendingRequest = null; requestWatchdog.stop();
            if (!completed || !active || generation !== requestGeneration
                    || queryToken !== requestedToken || accountId !== account || chatId !== chat) return;
            try {
                if (request.status === 409) {
                    const error = JSON.parse(request.responseText);
                    if (error && error.error === "stale_cursor") { restartAfterStale(); return; }
                }
                if (request.status !== 200 || typeof request.responseText !== "string"
                        || request.responseText.length > maximumResponseCharacters) throw new Error("response");
                const value = JSON.parse(request.responseText);
                 if (!validReply(value, account, chat, bounds.from, bounds.to, limit)) throw new Error("schema");
                 if ((cursor !== null || direction === "window-previous")
                         && revision.length && revision !== value.revision) {
                    restartAfterStale(); return;
                }
                if (direction === "refresh" && revision === value.revision
                        && firstNextCursor === value.nextCursor && sameRows(firstPageRows, value.items)) {
                    failed = false; fallbackLatched = false; return;
                }
                 if (storeRevision.length && storeRevision !== value.revision) {
                    cachedRows = []; rowsByIndex = ({}); rowsBeforeIndex = ({});
                     windowCursors = [null]; windowIndex = 0;
                }
                storeRevision = value.revision;
                if (direction === "next") {
                    if (rangeRows.length && value.items.length) {
                        const last = rangeRows[rangeRows.length - 1], first = value.items[0];
                        if (compareIds(last.orderKey, first.orderKey) > 0
                                || last.orderKey === first.orderKey
                                   && compareIds(last.messageId, first.messageId) >= 0)
                            throw new Error("page_order");
                    }
                     rangeRows = rangeRows.concat(value.items);
                } else {
                     if (direction === "window-next") {
                         if (rangeRows.length && value.items.length) {
                             const last = rangeRows[rangeRows.length - 1], first = value.items[0];
                             if (comparePair(last.orderKey, last.messageId,
                                     first.orderKey, first.messageId) >= 0)
                                 throw new Error("window_order");
                         }
                         if (windowIndex + 1 === windowCursors.length)
                             windowCursors = windowCursors.concat([cursor]);
                         windowIndex += 1;
                     } else if (direction === "window-previous") {
                         windowIndex -= 1;
                     } else if (direction === "first") {
                         windowCursors = [null]; windowIndex = 0;
                     }
                     if (direction === "window-next" || direction === "window-previous") {
                         cachedRows = []; rowsByIndex = ({}); rowsBeforeIndex = ({});
                     }
                    rangeRows = value.items.slice(); firstPageRows = value.items.slice();
                    firstNextCursor = value.nextCursor;
                }
                revision = value.revision; nextCursor = value.nextCursor;
                cachePage(value.items, bounds);
                const last = rangeRows.length ? rangeRows[rangeRows.length - 1] : null;
                pagingBefore = !!last && bounds.beforeStart &&
                    comparePair(last.orderKey, last.messageId,
                        bounds.keys[0].key, bounds.keys[0].messageId) < 0;
                pagingAnchorIndex = pagingBefore ? bounds.start : bounds.end;
                failed = false;
                fallbackLatched = false;
                if (cursor === null) staleRestarts = 0;
             } catch (_) {
                 failed = true;
                 // Keep the last accepted cursor available for a retry.
                 if (direction === "first")
                     nextCursor = null;
             }
        };
        try {
            request.send(JSON.stringify({schema: 1, accountId: account,
                chatId: chat, fromOrderKey: bounds.from, toOrderKey: bounds.to,
                 cursor: cursor, limit: limit}));
         } catch (_) {
             abortPending(); failed = true;
             if (direction === "first")
                 nextCursor = null;
         }
    }
    function loadMore() {
        if (hasMore && !pendingRequest) requestPage(nextCursor, "next");
    }
    function nextWindow() {
        if (!hasNextWindow || pendingRequest) return;
        const cursor = windowIndex + 1 < windowCursors.length
            ? windowCursors[windowIndex + 1] : nextCursor;
        if (cursor !== null) requestPage(cursor, "window-next");
    }
    function previousWindow() {
        if (hasPreviousWindow && !pendingRequest)
            requestPage(windowCursors[windowIndex - 1], "window-previous");
    }
    onAccountIdChanged: if (completed) resetScope()
    onChatIdChanged: if (completed) resetScope()
    onViewChanged: if (completed) resetScope()
    onBackendReadyChanged: if (completed) resetScope()
    onFailedChanged: if (failed) fallbackLatched = true
    onHasMoreBackwardChanged: scheduleScan()
    onHasMoreForwardChanged: scheduleScan()
    onActiveChanged: {
        if (!completed) return;
        if (!active) { generation += 1; abortPending(); scanDebounce.stop(); }
        else scheduleScan();
    }
    Component.onCompleted: { completed = true; resetScope(); }
    Component.onDestruction: { completed = false; generation += 1; abortPending(); }
    Timer {
        id: scanDebounce
        interval: 120
        onTriggered: range.scanVisible(false)
    }
    Timer {
        id: invalidRetry
        interval: 700
        onTriggered: range.scanVisible(false)
    }
    Timer {
        interval: 5000; repeat: true
        running: range.completed && range.active && range.backendReady
        onTriggered: range.scanVisible(true)
    }
    Timer {
        id: requestWatchdog
        interval: 10000
        onTriggered: { range.abortPending(); range.failed = true; }
    }
    Connections {
        target: range.view
        ignoreUnknownSignals: true
        function onContentYChanged() { range.scheduleScan(); }
        function onHeightChanged() { range.scheduleScan(); }
        function onCountChanged() { range.resetScope(); }
        function onModelChanged() { range.resetScope(); }
    }
    Connections {
        target: range.view ? range.view.model : null
        ignoreUnknownSignals: true
        function onModelReset() { range.resetScope(); }
        function onRowsInserted() { range.resetScope(); }
        function onRowsRemoved() { range.resetScope(); }
        function onDataChanged() { range.invalidateVisibleRange(); range.scheduleScan(); }
    }
}
