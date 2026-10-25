pragma Singleton
import QtQml
import ValMAX.Native 1.0

// Compatibility with the settings pages' existing completion contract.
// No URL, socket, credentials, or configurable filesystem path crosses QML.
QtObject {
    id: transport
    property var pending: ({})
    property var waiting: []
    readonly property int maximumRequests: 8
    function createRequest(family) {
        if (["optimization", "ghost", "accounts", "quick_replies"].indexOf(family) < 0) throw new Error("invalid_family");
        const request = {
            readyState: 0, status: 0, responseText: "", onreadystatechange: function() {},
            method: "", requestId: "", sent: false, aborted: false, payload: null,
            open: function(method) {
                if (this.sent || ["GET", "POST"].indexOf(method) < 0) throw new Error("invalid_method");
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
                let payload = {operation: family + (this.method === "GET" ? ".get" : ".set")};
                try {
                    if (this.method === "POST") {
                        const bodyLimit = family === "quick_replies" ? 262144 : 65536;
                        if (typeof body !== "string" || body.length > bodyLimit) throw new Error("invalid_body");
                        if (family === "quick_replies" && encodeURIComponent(body).replace(/%[0-9a-f]{2}|./gi, "x").length > bodyLimit) throw new Error("invalid_body");
                        const value = JSON.parse(body);
                        const dataKey = family === "accounts" ? "accounts" : family === "quick_replies" ? "replies" : "settings";
                        if (!value || value.schema !== 1 || Object.keys(value).sort().join(",") !== ["schema", "revision", dataKey].sort().join(",")) throw new Error("invalid_body");
                        payload.revision = value.revision; payload[dataKey] = value[dataKey];
                    } else if (body) throw new Error("invalid_body");
                } catch (_) { Qt.callLater(function() { transport.finish(request, 400, ""); }); return; }
                this.payload = JSON.stringify(payload);
                if (Object.keys(transport.pending).length + transport.waiting.length >= transport.maximumRequests) {
                    Qt.callLater(function() { transport.finish(request, 503, ""); }); return;
                }
                if (Bridge.runtimeState === "opening") transport.waiting.push(this);
                else transport.dispatch(this);
            }
        };
        return request;
    }
    function finish(request, status, text) {
        if (request.aborted || request.readyState === 4) return;
        request.payload = null; request.readyState = 4;
        request.status = status; request.responseText = text;
        const callback = request.onreadystatechange;
        request.onreadystatechange = function() {};
        callback();
    }
    function dispatch(request) {
        if (request.aborted) return;
        if (!Bridge.runtimeAvailable) { Qt.callLater(function() { transport.finish(request, 503, ""); }); return; }
        let result;
        try { result = Bridge.invoke(request.payload); }
        catch (_) { request.payload = null; Qt.callLater(function() { transport.finish(request, 503, ""); }); return; }
        request.payload = null;
        if (request.aborted) return;
        if (!result || result.accepted !== true || typeof result.requestId !== "string"
                || !/^[A-Za-z0-9_-]{1,64}$/.test(result.requestId)
                || Object.prototype.hasOwnProperty.call(pending, result.requestId)) {
            Qt.callLater(function() { transport.finish(request, 503, ""); }); return;
        }
        request.requestId = result.requestId;
        pending[result.requestId] = request;
    }
    property Connections bridgeConnection: Connections {
        target: Bridge
        function onRuntimeStateChanged() {
            if (Bridge.runtimeState === "opening") return;
            const queued = transport.waiting; transport.waiting = [];
            for (let i = 0; i < queued.length; ++i) transport.dispatch(queued[i]);
        }
        function onOperationFinished(id, response) {
            const request = transport.pending[id];
            if (!request) return;
            delete transport.pending[id];
            try {
                const value = JSON.parse(response);
                if (value.schema !== 1 || typeof value.ok !== "boolean") throw new Error("invalid_response");
                if (value.ok) transport.finish(request, 200, JSON.stringify(value.result));
                else transport.finish(request, value.error === "revision_conflict" ? 409 :
                    ["invalid_request", "invalid_revision", "invalid_settings", "invalid_accounts", "invalid_replies"].indexOf(value.error) >= 0 ? 400 : 503, "");
            } catch (_) { transport.finish(request, 503, ""); }
        }
    }
}
