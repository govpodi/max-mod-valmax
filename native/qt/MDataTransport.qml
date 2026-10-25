pragma Singleton
import QtQml
import ValMAX.Native 1.0

// Compact-archive preview/export. Import UI uses the standalone native helper.
// abort/close discard delivery; native work already admitted may still finish.
// Clearing references limits secret lifetime, but JS strings cannot be wiped.
QtObject {
    id: transport
    property var pending: Object.create(null)
    property var waiting: []
    property int epoch: 0
    // Only a validated, finite completion summary; no paths, accounts or secrets.
    property var lastExport: null
    // Opaque generation objects are never reused and contain no request data.
    property var exportGeneration: null
    readonly property int maximumRequests: 8
    readonly property int done: 4

    function exact(value, keys) {
        return value !== null && typeof value === "object" && !Array.isArray(value)
                && Object.keys(value).sort().join(",") === keys.slice().sort().join(",");
    }
    function utf8Length(value) {
        return encodeURIComponent(value).replace(/%[0-9a-f]{2}|./gi, "x").length;
    }
    function scalarLength(value) {
        // QJSEngine Array.from iterates UTF-16 units in the pinned runtime.
        let count = 0;
        for (let i = 0; i < value.length; ++i) {
            const unit = value.charCodeAt(i);
            if (unit >= 0xd800 && unit <= 0xdbff) {
                const next = value.charCodeAt(++i);
                if (!(next >= 0xdc00 && next <= 0xdfff)) return -1;
            } else if (unit >= 0xdc00 && unit <= 0xdfff) return -1;
            ++count;
        }
        return count;
    }
    function validAccountIds(value) {
        if (!Array.isArray(value) || value.length < 1 || value.length > 3) return false;
        const seen = {};
        for (const id of value) {
            if (typeof id !== "string" || !/^[1-9][0-9]{0,18}$/.test(id)
                    || (id.length === 19 && id > "9223372036854775807") || seen[id]) return false;
            seen[id] = true;
        }
        return true;
    }
    function validExport(value) {
        return exact(value, ["schema", "state", "action", "accepted", "result", "profiles", "archiveBytes"])
                && value.schema === 1 && value.state === "done" && value.action === "export"
                && value.accepted === true && value.result === "exported"
                && Number.isInteger(value.profiles) && value.profiles >= 1 && value.profiles <= 3
                && Number.isInteger(value.archiveBytes) && value.archiveBytes > 0 && value.archiveBytes <= 2097152;
    }
    function statusValue() {
        const value = {schema: 1, available: typeof TransferLauncher !== "undefined" && TransferLauncher.available,
            exportAvailable: Bridge.runtimeAvailable,
            state: lastExport ? lastExport.state : "idle", action: lastExport ? "export" : "",
            error: lastExport ? (lastExport.error || "") : (Bridge.runtimeAvailable ? "" : "unavailable"),
            result: lastExport && lastExport.state === "done" ? "exported" : null,
            profiles: lastExport && lastExport.state === "done" ? lastExport.profiles : 0, backupAvailable: false};
        if (lastExport && lastExport.state === "done") value.archiveBytes = lastExport.archiveBytes;
        return value;
    }
    function errorValue(request, code) {
        return request.kind === "export" ? {schema: 1, state: "error", action: "export", error: code}
                                         : {schema: 1, error: code};
    }
    function validPreview(value) {
        if (!exact(value, ["schema", "kind", "profiles", "accounts", "fingerprint"])
                || value.schema !== 1 || value.kind !== "login" || !Array.isArray(value.accounts)
                || !Number.isInteger(value.profiles) || value.profiles < 1 || value.profiles > 3
                || value.accounts.length !== value.profiles || typeof value.fingerprint !== "string"
                || !/^[0-9a-f]{64}$/.test(value.fingerprint)) return false;
        const seen = {};
        for (let i = 0; i < value.accounts.length; ++i) {
            const row = value.accounts[i];
            if (!exact(row, ["accountId", "displayName", "shortName"]) || typeof row.accountId !== "string"
                    || !/^[1-9][0-9]{0,18}$/.test(row.accountId)
                    || (row.accountId.length === 19 && row.accountId > "9223372036854775807")
                    || seen[row.accountId] || typeof row.displayName !== "string" || typeof row.shortName !== "string"
                    || row.displayName.length > 512 || row.shortName.length > 64) return false;
            seen[row.accountId] = true;
            const nameLength = scalarLength(row.displayName), shortLength = scalarLength(row.shortName);
            if (nameLength < 0 || nameLength > 256 || shortLength < 0 || shortLength > 32
                    || /[\u0000-\u001f\u007f-\u009f\u2028-\u202e\u2066-\u2069]/.test(row.displayName + row.shortName)) return false;
        }
        return true;
    }
    function createRequest() {
        const request = {
            DONE: 4, readyState: 0, status: 0, responseText: "", onreadystatechange: function() {},
            method: "", route: "", kind: "", requestId: "", sent: false, aborted: false, payload: null,
            generation: null, epoch: transport.epoch,
            open: function(method, route) {
                if (this.sent || this.aborted || this.epoch !== transport.epoch) throw new Error("invalid_state");
                // Keep only recognized routes/methods, never an arbitrary URL.
                this.method = method === "GET" || method === "POST" ? method : "";
                this.route = route === "/mdata" || route === "/mdata/inspect" || route === "/mdata/export-selected" ? route : "";
                this.kind = this.method === "POST" ? (this.route === "/mdata/inspect" ? "inspect"
                    : this.route === "/mdata" || this.route === "/mdata/export-selected" ? "export" : "") : "";
                this.readyState = 1;
            },
            abort: function() {
                if (this.kind === "export" && this.generation !== null && this.generation === transport.exportGeneration
                        && this.readyState !== 4) transport.lastExport = {state: "error", error: "outcome_unknown"};
                this.aborted = true; this.payload = null;
                if (this.requestId.length && transport.pending[this.requestId] === request) delete transport.pending[this.requestId];
                transport.waiting = transport.waiting.filter(function(value) { return value !== request; });
                this.onreadystatechange = function() {};
            },
            close: function() { this.abort(); },
            send: function(body) {
                if (this.sent || this.aborted || this.epoch !== transport.epoch || this.readyState !== 1) throw new Error("invalid_state");
                this.sent = true;
                if (this.method === "GET" && this.route === "/mdata") {
                    const empty = body === undefined || body === null || body === ""; body = "";
                    if (!empty) { transport.failLater(request, 400, "invalid_request"); return; }
                    Qt.callLater(function() { transport.finish(request, 200, transport.statusValue()); });
                    return;
                }
                if (this.method !== "POST" || !this.kind.length) {
                    body = ""; transport.failLater(request, 501, "unsupported_operation"); return;
                }
                let value = null;
                try {
                    if (typeof body !== "string" || body.length > 65536 || utf8Length(body) > 65536) throw new Error("invalid_body");
                    value = JSON.parse(body); body = "";
                    const selected = this.route === "/mdata/export-selected";
                    const keys = this.kind === "inspect" ? ["schema", "file", "password"]
                        : selected ? ["schema", "file", "password", "accountIds"] : ["schema", "action", "file", "password", "mode"];
                    if (!exact(value, keys) || value.schema !== 1
                            || typeof value.file !== "string" || !value.file.length || value.file.length > 32768
                            || value.file.indexOf("\u0000") >= 0 || typeof value.password !== "string"
                            || value.password.length > 1024 || value.password.indexOf("\u0000") >= 0
                            || utf8Length(value.password) < 12 || utf8Length(value.password) > 1024) throw new Error("invalid_body");
                    if (this.kind === "export" && (selected ? !validAccountIds(value.accountIds)
                            : value.action !== "export" || value.mode !== "restore")) throw new Error("invalid_body");
                    const operation = this.kind === "inspect" ? "mdata.inspect" : selected ? "mdata.export-selected" : "mdata.export";
                    this.payload = JSON.stringify(selected ? {operation: operation, file: value.file, password: value.password, accounts: value.accountIds}
                        : {operation: operation, file: value.file, password: value.password});
                } catch (_) {
                    this.payload = null; transport.failLater(request, 400, "invalid_request"); return;
                } finally {
                    body = ""; if (value && typeof value === "object") { value.password = ""; value.file = ""; value.accountIds = null; } value = null;
                }
                if (Object.keys(transport.pending).length + transport.waiting.length >= transport.maximumRequests) {
                    this.payload = null; transport.failLater(request, 503, "busy"); return;
                }
                if (Bridge.runtimeState === "opening") transport.waiting.push(request);
                else transport.dispatch(request);
            }
        };
        return request;
    }
    function failLater(request, status, code) {
        request.payload = null;
        Qt.callLater(function() { transport.finish(request, status, transport.errorValue(request, code)); });
    }
    function finish(request, status, value) {
        request.payload = null;
        if (request.aborted || request.epoch !== epoch || request.readyState === 4) return;
        if (request.kind === "export" && request.generation !== null && request.generation === exportGeneration) {
            // Copy only finite public fields before invoking arbitrary UI code.
            lastExport = value.state === "done" ? {state: "done", profiles: value.profiles, archiveBytes: value.archiveBytes}
                : {state: "error", error: value.error};
        }
        request.readyState = 4; request.status = status; request.responseText = JSON.stringify(value);
        const callback = request.onreadystatechange; request.onreadystatechange = function() {};
        callback();
    }
    function dispatch(request) {
        if (request.aborted || request.epoch !== epoch) { request.payload = null; return; }
        if (!Bridge.runtimeAvailable) { failLater(request, 503, "unavailable"); return; }
        let raw = request.payload; request.payload = null;
        let result = null;
        if (request.kind === "export") {
            // Invalidate the previous result before invoke can emit a signal
            // that aborts/closes the panel or starts a newer export reentrantly.
            request.generation = {}; exportGeneration = request.generation;
            lastExport = {state: "error", error: "outcome_unknown"};
        }
        try { result = Bridge.invoke(raw); }
        catch (_) { failLater(request, 503, "unavailable"); return; }
        finally { raw = ""; }
        // Native invoke can emit synchronous admission signals. Respect a
        // reentrant abort/close before registering its queued completion.
        if (request.aborted || request.epoch !== epoch) return;
        if (!result || result.accepted !== true || typeof result.requestId !== "string"
                || !/^[A-Za-z0-9_-]{1,64}$/.test(result.requestId)
                || Object.prototype.hasOwnProperty.call(pending, result.requestId)) {
            failLater(request, 503, result && result.error === "queue_full" ? "busy" : "unavailable"); return;
        }
        request.requestId = result.requestId; pending[result.requestId] = request;
        if (request.kind === "export" && request.generation === exportGeneration) lastExport = {state: "working"};
    }
    function close() {
        // Runtime shutdown may already have detached an admitted request and
        // queued its failure. Closing suppresses that callback too, so settle
        // the summary synchronously instead of leaving it working forever.
        if (lastExport && lastExport.state === "working") lastExport = {state: "error", error: "outcome_unknown"};
        ++epoch;
        const queued = waiting, active = pending; waiting = []; pending = Object.create(null);
        for (let i = 0; i < queued.length; ++i) queued[i].abort();
        for (const id of Object.keys(active)) active[id].abort();
    }
    Component.onDestruction: close()
    property Connections bridgeConnection: Connections {
        target: Bridge
        function onRuntimeStateChanged() {
            if (Bridge.runtimeState === "opening") return;
            const queued = transport.waiting; transport.waiting = [];
            for (let i = 0; i < queued.length; ++i) transport.dispatch(queued[i]);
            if (!Bridge.runtimeAvailable) {
                // A closing/failed runtime can still finish previously admitted
                // work. Detach it before notifying callers, so no stale result
                // from the old context can be published after unavailability.
                const active = transport.pending; transport.pending = Object.create(null);
                for (const id of Object.keys(active)) transport.failLater(active[id], 503, "unavailable");
            }
        }
        function onOperationFinished(id, response) {
            const request = transport.pending[id]; if (!request) return;
            delete transport.pending[id];
            let value = null;
            try {
                if (typeof response !== "string" || response.length > 16384) throw new Error("invalid_response");
                value = JSON.parse(response); response = "";
                if (!value || value.schema !== 1 || typeof value.ok !== "boolean") throw new Error("invalid_response");
                if (value.ok) {
                    if (!(request.kind === "export" ? transport.validExport(value.result) : transport.validPreview(value.result))) throw new Error("invalid_response");
                    transport.finish(request, 200, value.result);
                } else {
                    const errors = ["invalid_request", "authentication_failed", "invalid_archive", "invalid_payload",
                        "unsupported_version", "policy_violation", "invalid_password", "unsafe_path", "unsupported_path",
                        "io_error", "crypto_unavailable", "too_large", "account_limit", "no_accounts", "busy",
                        "unavailable", "unsupported_operation", "file_exists", "invalid_profile", "credential_io",
                        "insufficient_space", "profile_changed", "recovery_required", "cancelled"];
                    const code = errors.indexOf(value.error) >= 0 ? value.error : "io_error";
                    transport.finish(request, request.kind === "export" && ["busy", "file_exists", "account_limit", "recovery_required"].indexOf(code) >= 0 ? 409
                        : ["io_error", "crypto_unavailable", "busy", "unavailable", "credential_io", "cancelled"].indexOf(code) >= 0 ? 503
                        : code === "unsupported_operation" ? 501 : 400, transport.errorValue(request, code));
                }
            } catch (_) { transport.finish(request, 503, transport.errorValue(request, "io_error")); }
            finally { response = ""; value = null; }
        }
    }
}
