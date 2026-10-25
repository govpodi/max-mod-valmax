pragma Singleton
import QtQml
import ValMAX.Native 1.0

// Native retention status and bounded, selected-chat archive reads only.
QtObject {
    id: transport
    // A lookup can serve durable local rows even while new capture is disabled.
    // Bridge admission still fails closed until the runtime actually opens.
    readonly property bool lookupAvailable: Bridge.runtimeAvailable
    readonly property int maximumRequests: 8
    property var pending: ({})
    property var waiting: []

    function validId(value, positiveOnly) {
        if (typeof value !== "string" || !/^-?[1-9][0-9]{0,18}$/.test(value)) return false;
        const negative = value.charAt(0) === "-";
        if (positiveOnly && negative) return false;
        const magnitude = negative ? value.slice(1) : value;
        const maximum = negative ? "9223372036854775808" : "9223372036854775807";
        return magnitude.length < maximum.length || magnitude.length === maximum.length && magnitude <= maximum;
    }
    function comparePositiveIds(first, second) {
        if (!validId(first, true) || !validId(second, true)) throw new Error("invalid_id");
        return first.length === second.length ? (first < second ? -1 : first > second ? 1 : 0)
            : first.length < second.length ? -1 : 1;
    }

    function finish(request, status, value) {
        if (request.aborted || request.readyState === 4) return;
        request.payload = null;
        request.status = status;
        request.responseText = value === null ? "" : JSON.stringify(value);
        request.readyState = 4;
        const callback = request.onreadystatechange;
        request.onreadystatechange = function() {};
        callback();
    }
    function failLater(request, status, code) {
        Qt.callLater(function() { transport.finish(request, status, {schema: 2, error: code}); });
    }
    function createRequest(kind) {
        if (["settings", "lookup", "list", "inline", "range", "marker"].indexOf(kind) < 0) throw new Error("invalid_kind");
        const request = {
            readyState: 0, status: 0, responseText: "", onreadystatechange: function() {},
            method: "", requestId: "", sent: false, aborted: false, payload: null,
            open: function(method) {
                if (this.sent || this.aborted || ["GET", "POST"].indexOf(method) < 0 || kind !== "settings" && method !== "POST")
                    throw new Error("invalid_method");
                this.method = method; this.readyState = 1;
            },
            abort: function() {
                this.aborted = true;
                if (this.requestId.length) delete transport.pending[this.requestId];
                transport.waiting = transport.waiting.filter(function(value) { return value !== request; });
                this.payload = null; this.onreadystatechange = function() {};
            },
            send: function(body) {
                if (this.sent || this.aborted || !this.method.length) throw new Error("invalid_state");
                this.sent = true;
                let payload = {operation: "retention.get"};
                try {
                    if (this.method === "POST") {
                        if (typeof body !== "string" || body.length > 32768) throw new Error("invalid_body");
                        const value = JSON.parse(body);
                        if (!value || Array.isArray(value) || value.schema !== 1) throw new Error("invalid_body");
                        if (kind === "lookup") {
                            if (Object.keys(value).sort().join("|") !== "accountId|chatId|messageIds|schema"
                                    || !transport.validId(value.accountId, true) || !transport.validId(value.chatId, false)
                                    || !Array.isArray(value.messageIds) || value.messageIds.length < 1 || value.messageIds.length > 256)
                                throw new Error("invalid_body");
                            const seen = {};
                            for (const id of value.messageIds) {
                                if (!transport.validId(id, true) || seen[id]) throw new Error("invalid_body");
                                seen[id] = true;
                            }
                            payload = {operation: "retention.lookup", value: value};
                        } else if (kind === "list" || kind === "inline") {
                            if (Object.keys(value).sort().join("|") !== "accountId|chatId|cursor|limit|schema"
                                    || !transport.validId(value.accountId, true) || !transport.validId(value.chatId, false)
                                    || !(value.cursor === null || typeof value.cursor === "string" && value.cursor.length <= 256)
                                    || !Number.isSafeInteger(value.limit) || value.limit < 1 || value.limit > 100)
                                throw new Error("invalid_body");
                            payload = {operation: kind === "inline" ? "retention.inline" : "retention.list", value: value};
                        } else if (kind === "range") {
                            if (Object.keys(value).sort().join("|") !== "accountId|chatId|cursor|fromOrderKey|limit|schema|toOrderKey"
                                    || !transport.validId(value.accountId, true) || !transport.validId(value.chatId, false)
                                    || !transport.validId(value.fromOrderKey, true) || !transport.validId(value.toOrderKey, true)
                                    || transport.comparePositiveIds(value.fromOrderKey, value.toOrderKey) > 0
                                    || !(value.cursor === null || typeof value.cursor === "string"
                                        && /^[A-Za-z0-9_-]{1,256}$/.test(value.cursor))
                                    || !Number.isSafeInteger(value.limit) || value.limit < 1 || value.limit > 100)
                                throw new Error("invalid_body");
                            payload = {operation: "retention.range", value: value};
                        } else if (kind === "marker") {
                            if (Object.keys(value).sort().join("|") !== "accountId|chatId|revision|schema"
                                    || !transport.validId(value.accountId, true) || !transport.validId(value.chatId, false)
                                    || typeof value.revision !== "string" || !/^[0-9a-f]{64}$/.test(value.revision))
                                throw new Error("invalid_body");
                            payload = {operation: "retention.marker-summary", value: value};
                        } else {
                            if (Object.keys(value).sort().join("|") !== "enabled|revision|schema"
                                    || typeof value.enabled !== "boolean" || typeof value.revision !== "string"
                                    || !/^[0-9a-f]{64}$/.test(value.revision)) throw new Error("invalid_body");
                            payload = {operation: "retention.set", revision: value.revision, enabled: value.enabled};
                        }
                    } else if (body !== undefined && body !== "") throw new Error("invalid_body");
                } catch (_) { transport.failLater(request, 400, "invalid_request"); return; }
                if (Object.keys(transport.pending).length + transport.waiting.length >= transport.maximumRequests) {
                    transport.failLater(request, 503, "busy"); return;
                }
                this.payload = JSON.stringify(payload);
                if (Bridge.runtimeState === "opening") transport.waiting.push(this);
                else transport.dispatch(this);
            }
        };
        return request;
    }
    function dispatch(request) {
        if (request.aborted) return;
        if (!Bridge.runtimeAvailable) { failLater(request, 503, "unavailable"); return; }
        let result;
        try { result = Bridge.invoke(request.payload); }
        catch (_) { request.payload = null; failLater(request, 503, "unavailable"); return; }
        request.payload = null;
        // A synchronous callback can dispose the page during invoke().
        if (request.aborted) return;
        if (!result || result.accepted !== true || typeof result.requestId !== "string"
                || !/^[A-Za-z0-9_-]{1,64}$/.test(result.requestId) || Object.prototype.hasOwnProperty.call(pending, result.requestId)) {
            failLater(request, 503, "busy"); return;
        }
        request.requestId = result.requestId;
        pending[result.requestId] = request;
    }
    property Connections bridgeConnection: Connections {
        target: Bridge
        function onRuntimeStateChanged() {
            if (Bridge.runtimeState === "opening") return;
            const queued = transport.waiting; transport.waiting = [];
            for (const request of queued) transport.dispatch(request);
        }
        function onOperationFinished(id, response) {
            const request = transport.pending[id];
            if (!request) return;
            delete transport.pending[id];
            try {
                const value = JSON.parse(response);
                if (!value || value.schema !== 1 || typeof value.ok !== "boolean") throw new Error("response");
                if (value.ok) transport.finish(request, 200, value.result);
                else {
                    const code = ["revision_conflict", "invalid_request", "invalid_revision", "invalid_settings",
                        "invalid_store", "unsafe_path", "store_busy", "io_error", "no_space",
                        "retention_unavailable", "invalid_cursor", "stale_cursor"].indexOf(value.error) >= 0 ? value.error : "unavailable";
                    transport.finish(request, ["revision_conflict", "stale_cursor"].indexOf(code) >= 0 ? 409 :
                        ["invalid_request", "invalid_revision", "invalid_settings"].indexOf(code) >= 0 ? 400 : 503,
                        {schema: 2, error: code});
                }
            } catch (_) { transport.finish(request, 503, {schema: 2, error: "invalid_response"}); }
        }
    }
}
