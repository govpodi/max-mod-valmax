pragma Singleton
import QtQml
import ValMAX.Native 1.0

// Local explicit support actions only. No HTTP, upload or raw snapshot forwarding.
// abort suppresses UI delivery; an accepted file export may still finish.
QtObject {
    id: transport
    property var pending: ({})
    property var waiting: []
    readonly property int maximumRequests: 8
    function numericVersion(value) {
        return typeof value === "string" && /^[0-9]{1,5}(\.[0-9]{1,5}){1,3}$/.test(value) ? value : null;
    }
    function privacyStatus(value) {
        if (!value || value.schema !== 1) return null;
        if (value.state === "verified" && value.buildVerified === true
                && value.currentProcessVerified === true && typeof value.serviceProcessVerified === "boolean"
                && value.error === "") return true;
        if (value.state === "unavailable" && value.buildVerified === false
                && value.currentProcessVerified === false && value.serviceProcessVerified === false) return false;
        return null;
    }
    function currentContext() {
        return {versions: {valmax: null, max: numericVersion(Qt.application.version), windows: null, qt: null},
            startup: null, privacy: {verified: privacyStatus(Bridge.privacyReport)}};
    }
    function createRequest() {
        const request = {
            readyState: 0, status: 0, responseText: "", onreadystatechange: function() {},
            method: "", requestId: "", sent: false, aborted: false, file: null,
            open: function(method) {
                if (this.sent || this.aborted || ["GET", "POST"].indexOf(method) < 0) throw new Error("invalid_method");
                this.method = method; this.readyState = 1;
            },
            abort: function() {
                this.aborted = true;
                if (this.requestId.length) delete transport.pending[this.requestId];
                transport.waiting = transport.waiting.filter(function(value) { return value !== request; });
                this.file = null; this.onreadystatechange = function() {};
            },
            send: function(body) {
                if (this.sent || this.aborted || !this.method.length) throw new Error("invalid_state");
                this.sent = true;
                try {
                    if (this.method === "POST") {
                        if (typeof body !== "string" || body.length > 65536) throw new Error("invalid_body");
                        const value = JSON.parse(body);
                        if (!value || Object.keys(value).sort().join(",") !== "file,schema" || value.schema !== 1
                                || typeof value.file !== "string" || !value.file.length || value.file.length > 32768) throw new Error("invalid_body");
                        this.file = value.file;
                    } else if (body !== undefined && body !== null && body !== "") throw new Error("invalid_body");
                } catch (_) {
                    this.file = null;
                    Qt.callLater(function() { transport.finish(request, 400, {schema: 1, error: "invalid_request"}); }); return;
                }
                if (Object.keys(transport.pending).length + transport.waiting.length >= transport.maximumRequests) {
                    this.file = null;
                    Qt.callLater(function() { transport.finish(request, 503, {schema: 1, error: "io_error"}); }); return;
                }
                if (Bridge.runtimeState === "opening") transport.waiting.push(this);
                else transport.dispatch(this);
            }
        };
        return request;
    }
    function finish(request, status, value) {
        if (request.aborted || request.readyState === 4) return;
        request.file = null; request.readyState = 4; request.status = status;
        request.responseText = JSON.stringify(value);
        const callback = request.onreadystatechange; request.onreadystatechange = function() {};
        callback();
    }
    function dispatch(request) {
        if (request.aborted) return;
        if (!Bridge.runtimeAvailable) {
            request.file = null;
            Qt.callLater(function() { transport.finish(request, 503, {schema: 1, error: "io_error"}); }); return;
        }
        // Build a fresh minimal context for every preview/export, including work
        // queued while opening. Never export the earlier displayed report.
        let envelope = {operation: request.method === "GET" ? "support.preview" : "support.export", context: currentContext()};
        if (request.method === "POST") envelope.file = request.file;
        const result = Bridge.invoke(JSON.stringify(envelope));
        request.file = null; envelope = null;
        if (!result.accepted) {
            Qt.callLater(function() { transport.finish(request, 503, {schema: 1, error: "io_error"}); }); return;
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
                if (value.ok) {
                    if (!value.result || value.result.schema !== 1 || (request.method === "GET"
                            ? !value.result.report || value.result.report.schema !== 2
                            : value.result.saved !== true)) throw new Error("invalid_response");
                    transport.finish(request, 200, value.result);
                } else {
                    const errors = ["invalid_request", "invalid_report", "unsafe_path", "file_exists", "io_error"];
                    const code = errors.indexOf(value.error) >= 0 ? value.error : "io_error";
                    transport.finish(request, code === "file_exists" ? 409 : code === "io_error" ? 503 : 400, {schema: 1, error: code});
                }
            } catch (_) { transport.finish(request, 503, {schema: 1, error: "io_error"}); }
        }
    }
}
