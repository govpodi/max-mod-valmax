pragma Singleton
import QtQml
import ValMAX.Native 1.0

// Selected-chat commands only. Messages stay in short-lived request buffers;
// this adapter has no socket, filesystem access or account/history discovery.
QtObject {
    id: transport
    property var pending: Object.create(null)
    property var waiting: []
    property var detached: Object.create(null)
    property int epoch: 0
    readonly property int maximumRequests: 8
    readonly property var routes: ({
        start: ["schema", "accountId", "chat", "directory"],
        "api2.history.preview": ["schema", "ownId", "chatId"],
        "api2-next": ["schema", "jobId", "jobToken"],
        page: ["schema", "jobId", "jobToken", "sequence", "messages"],
        "media-start": ["schema", "jobId", "jobToken", "messageId", "attachmentId"],
        "media-chunk": ["schema", "jobId", "jobToken", "messageId", "attachmentId", "offset", "data"],
        media: ["schema", "jobId", "jobToken", "messageId", "attachmentId", "status", "reason"],
        finish: ["schema", "jobId", "jobToken", "completion"],
        status: ["schema", "jobId", "jobToken"],
        cancel: ["schema", "jobId", "jobToken"],
        history: ["schema", "jobId", "jobToken"]
    })
    readonly property var errors: ["invalid_request", "invalid_state", "sequence_conflict", "limit_reached",
        "cancelled", "unsafe_path", "io_error", "insufficient_space", "cleanup_failed", "busy", "not_found",
        "unsupported_operation", "unavailable", "internal_error"]

    function exact(value, keys) {
        return value !== null && typeof value === "object" && !Array.isArray(value)
            && Object.keys(value).sort().join(",") === keys.slice().sort().join(",");
    }
    function utf8Length(value) { return encodeURIComponent(value).replace(/%[0-9a-f]{2}|./gi, "x").length; }
    function validJob(value) {
        return value && typeof value.jobId === "string" && /^[0-9a-f]{32}$/.test(value.jobId)
            && typeof value.jobToken === "string" && /^[0-9a-f]{64}$/.test(value.jobToken);
    }
    // Only fixed stage/category codes from the native preview are forwarded.
    // Server text, request values and identifiers never become UI errors.
    function validPreviewError(code) {
        return typeof code === "string" && /^api2_(?:connect_(?:trust|tls|timeout|cancelled|io)|init_(?:rejected|limit|protocol|timeout|cancelled|io)|credential_(?:invalid|unavailable)|login_(?:request_(?:token|protocol)|frame_(?:rejected|limit|protocol|timeout|cancelled|io)|decompress_(?:limit|protocol)|preflight_(?:limit|protocol)|msgpack_protocol|trailing_protocol|profile_missing|contact_missing|id_invalid|bind_(?:account|protocol))|history_(?:rejected|scope|clock|limit|protocol|timeout|cancelled|io))$/.test(code);
    }
    function validPreview(value) {
        const keys = ["schema", "pages", "firstCount", "secondCount", "boundaryOverlapCount",
            "firstAdvanced", "secondAdvanced", "emptyFollowupWindow", "cursorFieldSeen",
            "hasMoreFieldSeen", "totalFieldSeen", "complete"];
        if (!exact(value, keys) || value.schema !== 1 || value.complete !== false
                || (value.pages !== 1 && value.pages !== 2)) return false;
        for (const key of ["firstCount", "secondCount", "boundaryOverlapCount"])
            if (!Number.isSafeInteger(value[key]) || value[key] < 0 || value[key] > 3) return false;
        for (const key of ["firstAdvanced", "secondAdvanced", "emptyFollowupWindow",
                "cursorFieldSeen", "hasMoreFieldSeen", "totalFieldSeen"])
            if (typeof value[key] !== "boolean") return false;
        return value.boundaryOverlapCount <= Math.min(value.firstCount, value.secondCount)
            && (value.pages === 2 || (value.secondCount === 0 && !value.secondAdvanced && !value.emptyFollowupWindow))
            && (!value.emptyFollowupWindow || (value.pages === 2 && value.secondCount === 0));
    }
    function validApi2Receipt(value) {
        if (!value || value.schema !== 1 || typeof value.jobId !== "string"
                || !/^[0-9a-f]{32}$/.test(value.jobId) || value.downloadsPending !== false
                || !exact(value.counts, ["messages", "attachments", "ready", "unavailable", "mediaBytes"])
                || !exact(value.api2, ["windowsFetched", "mediaProcessed", "mediaTotal", "phase", "done"])) return false;
        for (const key of ["messages", "attachments", "ready", "unavailable", "mediaBytes"])
            if (!Number.isSafeInteger(value.counts[key]) || value.counts[key] < 0) return false;
        for (const key of ["windowsFetched", "mediaProcessed", "mediaTotal"])
            if (!Number.isSafeInteger(value.api2[key]) || value.api2[key] < 0) return false;
        if (value.api2.windowsFetched > 1000 || value.api2.mediaTotal > 20000
                || value.api2.mediaProcessed > value.api2.mediaTotal
                || ["history", "media", "done"].indexOf(value.api2.phase) < 0
                || typeof value.api2.done !== "boolean" || value.api2.done !== (value.api2.phase === "done")
                || value.counts.ready + value.counts.unavailable
                    + value.api2.mediaTotal - value.api2.mediaProcessed !== value.counts.attachments) return false;
        const allowed = ["api2", "counts", "downloadsPending", "error", "htmlFileURL", "htmlPath", "jobId", "schema", "state"];
        if (Object.keys(value).some(function(key) { return allowed.indexOf(key) < 0; })) return false;
        if (value.error !== undefined && (typeof value.error !== "string" || !/^[a-z_]{1,64}$/.test(value.error))) return false;
        if (value.state === "exporting") return !value.api2.done
            && (value.api2.phase === "history" || value.api2.phase === "media" && value.api2.mediaTotal > 0)
            && value.htmlPath === undefined && value.htmlFileURL === undefined && value.error === undefined;
        if (!value.api2.done || value.api2.mediaProcessed !== value.api2.mediaTotal) return false;
        if (value.state === "partial") return value.error === undefined
            && typeof value.htmlPath === "string" && value.htmlPath.length > 0 && value.htmlPath.length < 32768
            && !/[\u0000-\u001f]/.test(value.htmlPath) && typeof value.htmlFileURL === "string"
            && value.htmlFileURL.length < 32768 && /^file:\/\/\/[A-Za-z]:\//.test(value.htmlFileURL)
            && !/[\u0000-\u001f]/.test(value.htmlFileURL);
        return (value.state === "failed" || value.state === "cancelled")
            && value.htmlPath === undefined && value.htmlFileURL === undefined;
    }
    function createRequest() {
        const request = {
            readyState: 0, status: 0, responseText: "", onreadystatechange: function() {},
            route: "", requestId: "", sent: false, aborted: false, invoking: false, payload: null, job: null, media: null,
            epoch: transport.epoch,
            open: function(method, route) {
                if (this.sent || this.aborted || this.epoch !== transport.epoch) throw new Error("invalid_state");
                this.route = method === "POST" && (Object.prototype.hasOwnProperty.call(transport.routes, route)
                    || route === "media-start") ? route : "";
                this.readyState = 1;
            },
            abort: function() {
                this.aborted = true; this.payload = null; this.onreadystatechange = function() {};
                transport.waiting = transport.waiting.filter(function(value) { return value !== request; });
                // Keep an admitted request until completion. A late start can
                // create a job even after its dialog disappears; cancel that
                // exact job instead of losing its token and blocking all starts.
                if (!this.requestId.length && !this.invoking) this.job = null;
            },
            send: function(body) {
                if (this.sent || this.aborted || this.readyState !== 1 || this.epoch !== transport.epoch) throw new Error("invalid_state");
                this.sent = true;
                if (!Object.prototype.hasOwnProperty.call(transport.routes, this.route)) {
                    transport.failLater(request, 501, "unsupported_operation"); return;
                }
                let value = null;
                try {
                    const limit = this.route === "page" ? 2097152 + 8192 : 98304;
                    if (typeof body !== "string" || body.length > limit || utf8Length(body) > limit) throw new Error("invalid_body");
                    value = JSON.parse(body);
                    const preview = this.route === "api2.history.preview";
                    if (!transport.exact(value, transport.routes[this.route]) || value.schema !== 1
                            || (!preview && this.route !== "start" && !transport.validJob(value))) throw new Error("invalid_body");
                    if (!preview && this.route !== "start") this.job = {jobId: value.jobId, jobToken: value.jobToken};
                    if (this.route === "media-start") this.media = {messageId: value.messageId, attachmentId: value.attachmentId};
                    this.payload = JSON.stringify({operation: preview ? this.route : "export." + this.route, value: value});
                } catch (_) { this.job = null; transport.failLater(request, 400, "invalid_request"); return; }
                finally { body = ""; value = null; }
                if (Object.keys(transport.pending).length + transport.waiting.length >= transport.maximumRequests) {
                    transport.failLater(request, 503, "busy"); return;
                }
                if (Bridge.runtimeState === "opening") transport.waiting.push(request);
                else transport.dispatch(request);
            }
        };
        return request;
    }
    function failLater(request, status, code) {
        request.payload = null;
        Qt.callLater(function() { transport.finish(request, status, {schema: 1, error: code}); });
    }
    function finish(request, status, value) {
        request.payload = null; request.job = null; request.media = null;
        if (request.aborted || request.epoch !== epoch || request.readyState === 4) return;
        request.readyState = 4; request.status = status; request.responseText = JSON.stringify(value);
        const callback = request.onreadystatechange; request.onreadystatechange = function() {};
        callback();
    }
    function dispatch(request) {
        if (request.aborted || request.epoch !== epoch) return;
        if (!Bridge.runtimeAvailable) { failLater(request, 503, "unavailable"); return; }
        let admission = null;
        request.invoking = true;
        try { admission = Bridge.invoke(request.payload); }
        catch (_) { failLater(request, 503, "unavailable"); return; }
        finally { request.payload = null; request.invoking = false; }
        if (!admission || admission.accepted !== true || typeof admission.requestId !== "string"
                || !admission.requestId.length || pending[admission.requestId]) {
            failLater(request, 503, admission && admission.error === "queue_full" ? "busy" : "unavailable"); return;
        }
        // Even an abort reentered from invoke retains completion ownership.
        request.requestId = admission.requestId; pending[admission.requestId] = request;
    }
    function cancelDetached(job) {
        if (!Bridge.runtimeAvailable || !validJob(job)) return;
        let task = detached[job.jobId];
        if (!task) {
            if (Object.keys(detached).length >= maximumRequests) return;
            task = {job: job, media: null, busy: false}; detached[job.jobId] = task;
        }
        drainDetached(task);
    }
    function abandonMedia(job, media) {
        // No SDK call has been delivered to an aborted dialog. Its lease can
        // receive a terminal cancelled ACK before cancelling the whole job.
        if (!Bridge.runtimeAvailable || !validJob(job) || !media) return;
        let task = detached[job.jobId];
        if (!task) {
            if (Object.keys(detached).length >= maximumRequests) return;
            task = {job: job, media: media, busy: false}; detached[job.jobId] = task;
        } else task.media = media;
        drainDetached(task);
    }
    function drainDetached(task) {
        if (task.busy || !Bridge.runtimeAvailable || detached[task.job.jobId] !== task) return;
        task.busy = true;
        const media = task.media;
        const done = createRequest(); done.open("POST", media ? "media" : "cancel");
        done.onreadystatechange = function() {
            task.busy = false;
            if (detached[task.job.jobId] !== task) return;
            let error = ""; try { error = JSON.parse(done.responseText).error || ""; } catch (_) {}
            if (done.status === 200) {
                if (media && task.media === media) task.media = null;
                else if (!media && !task.media) { delete detached[task.job.jobId]; return; }
                drainDetached(task);
            } else if (error === "not_found") delete detached[task.job.jobId];
            else detachedRetry.start(); // Fixed-size ownership survives a busy worker/queue.
        };
        const body = {schema: 1, jobId: task.job.jobId, jobToken: task.job.jobToken};
        if (media) Object.assign(body, {messageId: media.messageId, attachmentId: media.attachmentId, status: "cancelled", reason: "cancelled"});
        done.send(JSON.stringify(body));
    }
    property Timer detachedRetry: Timer {
        interval: 250; repeat: false
        onTriggered: {
            if (!Bridge.runtimeAvailable) { transport.detached = Object.create(null); return; }
            for (const id of Object.keys(transport.detached)) transport.drainDetached(transport.detached[id]);
        }
    }
    function close() {
        ++epoch;
        const queued = waiting; waiting = [];
        for (const request of queued) request.abort();
        for (const id of Object.keys(pending)) pending[id].abort();
    }
    Component.onDestruction: close()
    property Connections bridgeConnection: Connections {
        target: Bridge
        function onRuntimeStateChanged() {
            if (Bridge.runtimeState === "opening") return;
            const queued = transport.waiting; transport.waiting = [];
            for (const request of queued) transport.dispatch(request);
            if (!Bridge.runtimeAvailable) {
                const active = transport.pending; transport.pending = Object.create(null);
                for (const id of Object.keys(active)) transport.failLater(active[id], 503, "unavailable");
            }
        }
        function onOperationFinished(id, response) {
            const request = transport.pending[id]; if (!request) return;
            delete transport.pending[id];
            let value = null;
            try {
                if (typeof response !== "string" || response.length > 8388608) throw new Error("invalid_response");
                value = JSON.parse(response); response = "";
                if (!value || value.schema !== 1 || typeof value.ok !== "boolean") throw new Error("invalid_response");
                if (value.ok) {
                    const result = value.result;
                    if (request.route === "api2.history.preview") {
                        if (!transport.validPreview(result)) throw new Error("invalid_response");
                    } else if (!result || result.schema !== 1 || typeof result.jobId !== "string"
                            || !/^[0-9a-f]{32}$/.test(result.jobId)
                            || (request.job && request.job.jobId !== result.jobId)
                            || (request.route === "start" && !transport.validJob(result))) throw new Error("invalid_response");
                    if (request.route === "api2-next" && !transport.validApi2Receipt(result)) throw new Error("invalid_response");
                    if ((request.aborted || request.epoch !== transport.epoch)
                            && request.route !== "cancel" && request.route !== "api2.history.preview") {
                        if (request.route === "media-start") transport.abandonMedia(request.job, request.media);
                        else transport.cancelDetached(request.route === "start" ? result : request.job);
                    }
                    transport.finish(request, 200, result);
                } else {
                    const code = transport.errors.indexOf(value.error) >= 0
                        || (request.route === "api2.history.preview" && transport.validPreviewError(value.error))
                        ? value.error : "internal_error";
                    if ((request.aborted || request.epoch !== transport.epoch)
                            && request.route !== "cancel" && request.route !== "api2.history.preview") transport.cancelDetached(request.job);
                    transport.finish(request, code === "unsupported_operation" ? 501 : ["busy", "invalid_state", "sequence_conflict"].indexOf(code) >= 0 ? 409
                        : ["invalid_request", "unsafe_path", "limit_reached", "not_found"].indexOf(code) >= 0 ? 400 : 503,
                        {schema: 1, error: code});
                }
            } catch (_) { transport.finish(request, 503, {schema: 1, error: "internal_error"}); }
            finally { response = ""; value = null; }
        }
    }
}
