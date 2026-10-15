// Marker-only local lookup for currently instantiated history delegates.
import QtQuick

Item {
    id: retentionController
    visible: false
    width: 0
    height: 0

    property string accountId: ""
    property string chatId: ""
    property bool active: false
    enabled: false
    property bool ready: false
    property string revision: ""
    property var markers: ({})
    property var registrations: ({})
    property int scopeGeneration: 0
    property int registrationCount: 0
    property var pendingRequest: null
    property var cycleIds: []
    property int cycleOffset: 0
    property int cycleScope: -1
    property bool rescanNeeded: false
    property bool completed: false
    property bool closing: false
    readonly property int maxRegisteredIds: 4096
    readonly property int maxBatchIds: 256

    function validId(value, positiveOnly) {
        if (typeof value !== "string" || !/^-?[1-9][0-9]{0,18}$/.test(value)) return false;
        const negative = value.charAt(0) === "-";
        if (positiveOnly && negative) return false;
        const magnitude = negative ? value.slice(1) : value;
        const maximum = negative ? "9223372036854775808" : "9223372036854775807";
        return magnitude.length < maximum.length || (magnitude.length === maximum.length && magnitude <= maximum);
    }
    function copyMap(value) {
        const next = {};
        for (const key of Object.keys(value)) next[key] = value[key];
        return next;
    }
    function contains(messageId) {
        return ready && validId(messageId, true) && markers[messageId] === true;
    }
    function register(messageId) {
        if (closing || !validId(messageId, true)) return -1;
        const previous = registrations[messageId] || 0;
        if (!previous && registrationCount >= maxRegisteredIds) return -1;
        if (previous >= maxRegisteredIds) return -1;
        const next = copyMap(registrations);
        next[messageId] = previous + 1;
        registrations = next;
        if (!previous) { registrationCount += 1; schedule(); }
        return scopeGeneration;
    }
    function unregister(messageId, generation) {
        if (closing || generation !== scopeGeneration || !validId(messageId, true) || !registrations[messageId]) return;
        const next = copyMap(registrations);
        if (next[messageId] > 1) next[messageId] -= 1;
        else {
            delete next[messageId];
            registrationCount -= 1;
            if (markers[messageId]) { const clean = copyMap(markers); delete clean[messageId]; markers = clean; }
        }
        registrations = next;
        if (registrationCount === 0) {
            abortPending(); cycleIds = []; cycleOffset = 0; rescanNeeded = false;
            debounceTimer.stop(); ready = false; enabled = false; markers = ({});
        } else schedule();
    }
    function abortPending() {
        requestWatchdog.stop();
        const xhr = pendingRequest;
        pendingRequest = null;
        if (xhr !== null) { xhr.onreadystatechange = function() {}; try { xhr.abort(); } catch (_) {} }
    }
    function resetScope() {
        abortPending(); debounceTimer.stop();
        cycleIds = []; cycleOffset = 0; cycleScope = -1; rescanNeeded = false;
        markers = ({}); enabled = false; ready = false; revision = "";
        registrations = ({}); registrationCount = 0;
        scopeGeneration += 1;
        schedule();
    }
    function handleActiveChanged() {
        if (!active) {
            abortPending(); debounceTimer.stop();
            cycleIds = []; cycleOffset = 0; cycleScope = -1; rescanNeeded = false;
            markers = ({}); enabled = false; ready = false;
        } else schedule();
    }
    function schedule() {
        if (!completed || closing || !active) return;
        rescanNeeded = true;
        debounceTimer.restart();
    }
    function startCycle() {
        if (!completed || closing || !active || !validId(accountId, true) || !validId(chatId, false)) return;
        if (pendingRequest !== null || cycleIds.length > 0) { rescanNeeded = true; return; }
        const ids = Object.keys(registrations).filter(function(id) { return registrations[id] > 0; }).sort();
        if (ids.length === 0) return;
        cycleIds = ids; cycleOffset = 0; cycleScope = scopeGeneration; rescanNeeded = false;
        requestNext();
    }
    function validReply(value, ids, requestedAccount, requestedChat) {
        if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join("|") !== "accountId|chatId|deletedIds|enabled|ready|revision|schema") return false;
        if (value.schema !== 1 || value.accountId !== requestedAccount || value.chatId !== requestedChat || typeof value.enabled !== "boolean" || typeof value.ready !== "boolean" || typeof value.revision !== "string" || !/^[0-9a-f]{64}$/.test(value.revision) || !Array.isArray(value.deletedIds) || value.deletedIds.length > ids.length) return false;
        const allowed = {}, seen = {};
        for (const id of ids) allowed[id] = true;
        for (const id of value.deletedIds) {
            if (!validId(id, true) || !allowed[id] || seen[id]) return false;
            seen[id] = true;
        }
        return true;
    }
    function sameMarkers(value) {
        const keys = Object.keys(value);
        if (keys.length !== Object.keys(markers).length) return false;
        for (const key of keys) if (value[key] !== markers[key]) return false;
        return true;
    }
    function applyReply(value, ids) {
        let next = revision && revision !== value.revision ? {} : copyMap(markers);
        for (const id of ids) delete next[id];
        if (value.ready) {
            for (const id of value.deletedIds) if (registrations[id] > 0) next[id] = true;
        } else next = {};
        revision = value.revision; enabled = value.enabled; ready = value.ready;
        if (!sameMarkers(next)) markers = next;
    }
    function unavailable() {
        ready = false; enabled = false; markers = ({});
        cycleIds = []; cycleOffset = 0; cycleScope = -1;
    }
    function requestNext() {
        if (closing || !active || pendingRequest !== null || cycleScope !== scopeGeneration) return;
        let ids = [];
        while (cycleOffset < cycleIds.length && ids.length === 0) {
            ids = cycleIds.slice(cycleOffset, cycleOffset + maxBatchIds).filter(function(id) { return registrations[id] > 0; });
            cycleOffset += maxBatchIds;
        }
        if (ids.length === 0) {
            cycleIds = []; cycleOffset = 0; cycleScope = -1;
            if (rescanNeeded) debounceTimer.restart();
            return;
        }
        const requestedAccount = accountId, requestedChat = chatId, generation = scopeGeneration;
        let xhr;
        try { xhr = new XMLHttpRequest(); } catch (_) { unavailable(); return; }
        pendingRequest = xhr; requestWatchdog.restart();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE || pendingRequest !== xhr) return;
            pendingRequest = null; requestWatchdog.stop();
            if (closing || !active || generation !== scopeGeneration || requestedAccount !== accountId || requestedChat !== chatId) return;
            try {
                if (xhr.status !== 200 || typeof xhr.responseText !== "string" || xhr.responseText.length > 32768) throw new Error("response");
                const value = JSON.parse(xhr.responseText);
                if (!validReply(value, ids, requestedAccount, requestedChat)) throw new Error("schema");
                applyReply(value, ids);
            } catch (_) { unavailable(); return; }
            Qt.callLater(requestNext);
        };
        try {
            xhr.open("POST", "http://127.0.0.1:18973/message-retention/lookup");
            xhr.setRequestHeader("Content-Type", "application/json");
            xhr.setRequestHeader("X-Max-Mod-Local-Write", "message-retention-v1");
            xhr.send(JSON.stringify({schema: 1, accountId: requestedAccount, chatId: requestedChat, messageIds: ids}));
        } catch (_) { abortPending(); unavailable(); }
    }
    onAccountIdChanged: if (completed) resetScope()
    onChatIdChanged: if (completed) resetScope()
    onActiveChanged: if (completed) handleActiveChanged()
    Component.onCompleted: { completed = true; resetScope(); }
    Component.onDestruction: { closing = true; completed = false; abortPending(); debounceTimer.stop(); pollTimer.stop(); }
    Timer {
        id: debounceTimer
        interval: 120
        onTriggered: retentionController.startCycle()
    }
    Timer {
        id: pollTimer
        interval: 2000
        repeat: true
        running: retentionController.completed && retentionController.active && !retentionController.closing
        onTriggered: retentionController.startCycle()
    }
    Timer {
        id: requestWatchdog
        interval: 7000
        onTriggered: { retentionController.abortPending(); retentionController.unavailable(); }
    }
}
