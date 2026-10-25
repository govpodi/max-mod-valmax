pragma Singleton
import QtQml
import ValMAX.Native 1.0

// PIN request transport only. The host notification lease is not exposed here.
QtObject {
    id: transport
    property var pending: ({})
    property var waiting: []
    readonly property int maximumRequests: 8
    function createRequest() {
        const request = {
            readyState: 0, status: 0, responseText: "", onreadystatechange: function() {},
            method: "", action: "", requestId: "", sent: false, aborted: false, payload: null,
            open: function(method, action) {
                if (this.sent || ["GET", "POST"].indexOf(method) < 0 || typeof action !== "string") throw new Error("invalid_method");
                if (method === "GET" ? action !== "" : ["status", "set", "unlock", "remove", "lock", "lock-all"].indexOf(action) < 0) throw new Error("invalid_action");
                this.method = method; this.action = action; this.readyState = 1;
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
                let envelope = {operation: "chat_lock.catalog"};
                try {
                    if (this.method === "POST") {
                        if (typeof body !== "string" || body.length > 4096) throw new Error("invalid_body");
                        const value = JSON.parse(body);
                        if (!value || value.schema !== 1) throw new Error("invalid_body");
                        envelope = {operation: "chat_lock.execute", action: this.action, value: value};
                    } else if (body) throw new Error("invalid_body");
                    this.payload = JSON.stringify(envelope);
                } catch (_) { Qt.callLater(function() { transport.finish(request, 400, {schema: 1, error: "invalid_request"}); }); return; }
                finally { body = null; envelope = null; }
                if (Object.keys(transport.pending).length + transport.waiting.length >= transport.maximumRequests) {
                    this.payload = null;
                    Qt.callLater(function() { transport.finish(request, 503, {schema: 1, error: "store_busy"}); }); return;
                }
                if (Bridge.runtimeState === "opening") transport.waiting.push(this);
                else transport.dispatch(this);
            }
        };
        return request;
    }
    function finish(request, status, value) {
        if (request.aborted || request.readyState === 4) return;
        request.payload = null; request.readyState = 4; request.status = status;
        request.responseText = JSON.stringify(value);
        const callback = request.onreadystatechange; request.onreadystatechange = function() {};
        callback();
    }
    function dispatch(request) {
        if (request.aborted) return;
        if (!Bridge.runtimeAvailable) {
            request.payload = null;
            Qt.callLater(function() { transport.finish(request, 503, {schema: 1, error: "notification_unavailable"}); }); return;
        }
        const result = Bridge.invoke(request.payload); request.payload = null;
        if (!result.accepted) {
            Qt.callLater(function() { transport.finish(request, 503, {schema: 1, error: "store_busy"}); }); return;
        }
        request.requestId = result.requestId; pending[result.requestId] = request;
    }
    property Connections bridgeConnection: Connections {
        target: Bridge
        function onRuntimeStateChanged() {
            if (Bridge.runtimeState === "opening") return;
            const queued = transport.waiting; transport.waiting = [];
            for (let i = 0; i < queued.length; ++i) transport.dispatch(queued[i]);
        }
        function onOperationFinished(id, response) {
            const request = transport.pending[id]; if (!request) return;
            delete transport.pending[id];
            try {
                const value = JSON.parse(response);
                if (value.schema !== 1 || typeof value.ok !== "boolean") throw new Error("invalid_response");
                if (value.ok) transport.finish(request, 200, value.result);
                else {
                    const errors = ["invalid_request", "invalid_pin", "wrong_pin", "rate_limited", "not_protected", "already_protected",
                        "limit_reached", "unsafe_path", "store_busy", "io_error", "invalid_store", "protector_unavailable", "notification_unavailable"];
                    const code = errors.indexOf(value.error) >= 0 ? value.error : "notification_unavailable";
                    transport.finish(request, ["invalid_request", "invalid_pin", "wrong_pin"].indexOf(code) >= 0 ? 400 : 503, {schema: 1, error: code});
                }
            } catch (_) { transport.finish(request, 503, {schema: 1, error: "notification_unavailable"}); }
        }
    }
}
