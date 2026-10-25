import QtQuick
import ValMAX.Native 1.0 as Native
Item {
    id: test
    property int checks: 0
    property string failure: ""
    property int callbacks: 0
    property int step: 0
    property int retryBefore:0
    property var pendingTests: []
    readonly property string jobId: "a".repeat(32)
    readonly property string token: "b".repeat(64)
    function check(value, message) {
        ++checks; if (!value) { failure = message; Qt.exit(1); return false; } return true;
    }
    function body(extra) { return Object.assign({schema: 1, jobId: jobId, jobToken: token}, extra || {}); }
    function send(route, value) {
        const request = Native.ExportTransport.createRequest();
        request.onreadystatechange = function() { ++test.callbacks; };
        request.open("POST", route); request.send(typeof value === "string" ? value : JSON.stringify(value)); return request;
    }
    function complete(request, value) { Native.Bridge.operationFinished(request.requestId, JSON.stringify(value)); }
    function result(extra) { return {schema: 1, ok: true, result: Object.assign({schema: 1, jobId: jobId}, extra || {})}; }
    function clearDetached() {
        for (let pass = 0; pass < 3 && Object.keys(Native.ExportTransport.pending).length; ++pass)
            for (const id of Object.keys(Native.ExportTransport.pending))
                Native.Bridge.operationFinished(id, JSON.stringify(result({state: "cancelled"})));
    }
    Component.onCompleted: {
        const start = send("start", {schema: 1, accountId: "9007199254740993", chat: {id: "-9223372036854775808", title: "Синтетический чат", timezoneOffsetMinutes: 180}, directory: "file:///D:/owned/output"});
        const native = Native.Bridge.requests[0].value;
        if (!check(native.operation === "export.start" && Object.keys(native).sort().join(",") === "operation,value", "direct operation envelope")) return;
        if (!check(native.value.accountId === "9007199254740993" && native.value.chat.id === "-9223372036854775808", "IDs stay exact strings")) return;
        if (!check(start.payload === null && callbacks === 0, "no retained dispatched page / asynchronous completion")) return;
        complete(start, result({jobToken: token, state: "exporting"}));
        if (!check(start.status === 200 && callbacks === 1, "native start success decoded")) return;
        complete(start, result({jobToken: token}));
        if (!check(callbacks === 1, "completion once")) return;
        const previewBody = {schema: 1, ownId: "9007199254740993", chatId: "-9007199254740995"};
        const previewResult = {schema: 1, pages: 2, firstCount: 3, secondCount: 3,
            boundaryOverlapCount: 1, firstAdvanced: true, secondAdvanced: true,
            emptyFollowupWindow: false, cursorFieldSeen: false, hasMoreFieldSeen: false,
            totalFieldSeen: false, complete: false};
        const preview = send("api2.history.preview", previewBody);
        const previewEnvelope = Native.Bridge.requests[Native.Bridge.requests.length - 1].value;
        if (!check(previewEnvelope.operation === "api2.history.preview"
                && previewEnvelope.value.ownId === previewBody.ownId
                && previewEnvelope.value.chatId === previewBody.chatId,
                "preview carries exact selected string IDs to native worker")) return;
        complete(preview, {schema: 1, ok: true, result: previewResult});
        if (!check(preview.status === 200 && JSON.parse(preview.responseText).boundaryOverlapCount === 1
                && preview.responseText.indexOf(previewBody.chatId) < 0,
                "preview returns only bounded structural evidence")) return;
        const stageFailure = send("api2.history.preview", previewBody);
        complete(stageFailure, {schema: 1, ok: false, error: "api2_login_preflight_protocol",
            serverBody: "synthetic-private"});
        if (!check(stageFailure.status === 503
                && JSON.parse(stageFailure.responseText).error === "api2_login_preflight_protocol"
                && stageFailure.responseText.indexOf("synthetic-private") < 0
                && stageFailure.responseText.indexOf(previewBody.chatId) < 0,
                "preview forwards only whitelisted stage/category code")) return;
        const unknownFailure = send("api2.history.preview", previewBody);
        complete(unknownFailure, {schema: 1, ok: false, error: "api2_login_preflight_protocol_secret"});
        if (!check(unknownFailure.status === 503
                && JSON.parse(unknownFailure.responseText).error === "internal_error",
                "preview rejects non-whitelisted diagnostic text")) return;
        const leakingPreview = send("api2.history.preview", previewBody);
        complete(leakingPreview, {schema: 1, ok: true,
            result: Object.assign({}, previewResult, {messageText: "synthetic-private"})});
        if (!check(leakingPreview.status === 503 && leakingPreview.responseText.indexOf("synthetic-private") < 0,
                "preview rejects extra content fields")) return;
        const abortedPreview = send("api2.history.preview", previewBody);
        const requestsBeforeAbort = Native.Bridge.requests.length;
        const callbacksBeforeAbort = callbacks;
        abortedPreview.abort(); complete(abortedPreview, {schema: 1, ok: true, result: previewResult});
        if (!check(Native.Bridge.requests.length === requestsBeforeAbort && callbacks === callbacksBeforeAbort,
                "aborted preview suppresses result without detached export cleanup")) return;
        const api2Body = body();
        const api2 = send("api2-next", api2Body);
        const api2Envelope = Native.Bridge.requests[Native.Bridge.requests.length - 1].value;
        if (!check(api2Envelope.operation === "export.api2-next"
                && Object.keys(api2Envelope.value).sort().join(",") === "jobId,jobToken,schema"
                && api2Envelope.value.jobId === jobId && api2Envelope.value.jobToken === token,
                "one API2 window receives only the exact owned job capability")) return;
        const api2Counts = {messages: 100, attachments: 3, ready: 0, unavailable: 3, mediaBytes: 0};
        const api2Progress = {schema: 1, jobId: jobId, state: "exporting", counts: api2Counts,
            downloadsPending: false, api2: {windowsFetched: 1, mediaProcessed: 0,
                mediaTotal: 0, phase: "history", done: false}};
        complete(api2, {schema: 1, ok: true, result: api2Progress});
        if (!check(api2.status === 200 && JSON.parse(api2.responseText).api2.windowsFetched === 1,
                "bounded API2 progress receipt delivered without messages or path")) return;
        const api2Terminal = send("api2-next", body());
        complete(api2Terminal, {schema: 1, ok: true, result: Object.assign({}, api2Progress,
            {state: "partial", api2: {windowsFetched: 2, mediaProcessed: 0,
                mediaTotal: 0, phase: "done", done: true},
                htmlPath: "D:\\owned\\output\\index.html", htmlFileURL: "file:///D:/owned/output/index.html"})});
        if (!check(api2Terminal.status === 200 && JSON.parse(api2Terminal.responseText).state === "partial",
                "only terminal partial receipt carries the published local file")) return;
        const api2Leak = send("api2-next", body());
        complete(api2Leak, {schema: 1, ok: true, result: Object.assign({}, api2Progress, {messageText: "synthetic-private"})});
        if (!check(api2Leak.status === 503 && api2Leak.responseText.indexOf("synthetic-private") < 0,
                "API2 receipt rejects extra message content")) return;
        const api2Aborted = send("api2-next", body());
        const beforeApi2Abort = callbacks; api2Aborted.abort();
        complete(api2Aborted, {schema: 1, ok: true, result: api2Progress});
        if (!check(callbacks === beforeApi2Abort
                && Native.Bridge.requests[Native.Bridge.requests.length - 1].value.operation === "export.cancel",
                "aborted admitted API2 window cancels its exact job")) return;
        clearDetached();
        const routes = [
            ["page", body({sequence: 0, messages: [{id: "1", text: "synthetic-only"}]})],
            ["media-start", body({messageId: "1", attachmentId: "image"})],
            ["media-chunk", body({messageId: "1", attachmentId: "audio", offset: 0, data: "T2dnUw=="})],
            ["media", body({messageId: "1", attachmentId: "audio", status: "ready", reason: null})],
            ["finish", body({completion: {historyComplete: false, stopReason: "unsupported_history", pagesFetched: 1}})],
            ["status", body()], ["history", body()], ["cancel", body()]
        ];
        for (const item of routes) {
            const request = send(item[0], item[1]);
            if (!check(Native.Bridge.requests[Native.Bridge.requests.length - 1].value.operation === "export." + item[0], "all supported operations direct")) return;
            if (!check(request.payload === null, "message/token payload reference cleared after dispatch")) return;
            complete(request, result({state: "partial"}));
            if (!check(request.status === 200, "result forwarded")) return;
        }
        for (const item of [["insufficient_space", 503], ["cleanup_failed", 503], ["busy", 409], ["unsupported_operation", 501], ["invalid_request", 400], ["synthetic-private-error", 503]]) {
            const request = send("status", body()); complete(request, {schema: 1, ok: false, error: item[0]});
            if (!check(request.status === item[1] && request.responseText.indexOf("synthetic-private") < 0, "finite mapped error only")) return;
        }
        const mismatch = send("status", body());
        complete(mismatch, {schema: 1, ok: true, result: {schema: 1, jobId: "c".repeat(32)}});
        if (!check(mismatch.status === 503, "job identity mismatch rejected")) return;
        const orphan = send("start", {schema: 1, accountId: "42", chat: {}, directory: "file:///D:/owned"});
        const before = callbacks; orphan.abort(); complete(orphan, result({jobToken: token}));
        if (!check(callbacks === before && Native.Bridge.requests[Native.Bridge.requests.length - 1].value.operation === "export.cancel", "destroyed dialog late start cancelled without delivery")) return;
        clearDetached();
        const abandonedMedia = send("media-start", body({messageId: "1", attachmentId: "image"}));
        const beforeAbandon = callbacks; abandonedMedia.abort();
        complete(abandonedMedia, result({nativePathFileURL: "file:///D:/owned/.valmax-download-synthetic/attachment.png"}));
        const terminal = Native.Bridge.requests[Native.Bridge.requests.length - 1];
        if (!check(callbacks === beforeAbandon && terminal.value.operation === "export.media"
                && terminal.value.value.messageId === "1" && terminal.value.value.attachmentId === "image"
                && terminal.value.value.status === "cancelled", "aborted media admission receives terminal lease acknowledgement")) return;
        Native.Bridge.operationFinished(terminal.id, JSON.stringify(result({state: "exporting"})));
        if (!check(Native.Bridge.requests[Native.Bridge.requests.length - 1].value.operation === "export.cancel", "unstarted lease settled before whole job cancellation")) return;
        clearDetached();
        const active = send("page", body({sequence: 1, messages: []})); active.abort();
        complete(active, result({sequence: 1, media: []}));
        if (!check(Native.Bridge.requests[Native.Bridge.requests.length - 1].value.operation === "export.cancel", "aborted admitted page cancels its exact job after completion")) return;
        clearDetached();
        let reentrant = Native.ExportTransport.createRequest();
        reentrant.open("POST", "status"); Native.Bridge.duringInvoke = function() { reentrant.abort(); };
        reentrant.send(JSON.stringify(body())); complete(reentrant, result({state: "exporting"}));
        if (!check(Native.Bridge.requests[Native.Bridge.requests.length - 1].value.operation === "export.cancel", "reentrant abort retains cleanup identity")) return;
        clearDetached();
        const count = Native.Bridge.requests.length;
        pendingTests = [send("media-start", body({messageId: "1", attachmentId: "image", path: "C:/unselected"})),
            send("status", body({unexpected: true})), send("__proto__", body()), send("status", "{"),
            send("status", body({jobToken: "x"})), send("page", "x".repeat(2097152 + 8193)),
            send("status", JSON.stringify(body()) + " ".repeat(98305)), send("api2-next", body({unexpected: true}))];
        if (!check(Native.Bridge.requests.length === count, "unsupported/malformed requests never reach core")) return;
    }
    Timer {
        interval: 5; running: true; repeat: true
        onTriggered: {
            if (failure.length) return;
            if (step === 0) {
                if (pendingTests.some(function(value) { return value.readyState !== 4; })) return;
                if (!check(pendingTests[2].status === 501, "unknown command finite failure")) return;
                if (!check(pendingTests.filter(function(_, i) { return i !== 2; }).every(function(value) { return value.status === 400; }), "invalid commands and caller download path fail")) return;
                Native.Bridge.runtimeState = "opening"; pendingTests = [];
                for (let i = 0; i < 9; ++i) pendingTests.push(send("status", body()));
                if (!check(Native.ExportTransport.waiting.length === 8, "opening queue bounded")) return;
                pendingTests[0].abort(); step = 1;
            } else if (step === 1) {
                if (pendingTests[8].readyState !== 4) return;
                if (!check(pendingTests[8].status === 503 && Native.ExportTransport.waiting.length === 7, "queue overflow and abort finite")) return;
                Native.Bridge.runtimeState = "ready";
                for (let i = 1; i < 8; ++i) complete(pendingTests[i], result({state: "partial"}));
                if (!check(Object.keys(Native.ExportTransport.pending).length === 0, "opening queue drained")) return;
                Native.Bridge.accept = false; pendingTests = [send("status", body())]; step = 2;
            } else if (step === 2) {
                if (pendingTests[0].readyState !== 4) return;
                if (!check(pendingTests[0].status === 503, "native admission rejection finite")) return;
                Native.Bridge.accept = true; Native.Bridge.throwOnInvoke = true;
                pendingTests = [send("status", body())]; step = 3;
            } else if (step === 3) {
                if (pendingTests[0].readyState !== 4) return;
                if (!check(pendingTests[0].status === 503 && pendingTests[0].responseText.indexOf("synthetic-private") < 0, "invoke exception sanitized")) return;
                Native.Bridge.throwOnInvoke = false;
                pendingTests = [send("status", body())]; Native.Bridge.runtimeState = "closed"; step = 4;
            } else if (step === 4) {
                if (pendingTests[0].readyState !== 4) return;
                if (!check(pendingTests[0].status === 503 && Object.keys(Native.ExportTransport.pending).length === 0, "runtime shutdown settles pending")) return;
                Native.Bridge.runtimeState = "ready";
                const request = send("status", body()); const before = callbacks;
                Native.ExportTransport.close(); complete(request, result({state: "exporting"}));
                if (!check(callbacks === before, "close suppresses stale delivery")) return;
                clearDetached();
                const media = send("media-start",body({messageId:"1",attachmentId:"image"})); media.abort();
                complete(media,result({nativePathFileURL:"file:///D:/owned/.valmax-download-synthetic/attachment.png"}));
                const ack=Native.Bridge.requests[Native.Bridge.requests.length - 1];
                Native.Bridge.operationFinished(ack.id,JSON.stringify({schema:1,ok:false,error:"busy"}));
                retryBefore=Native.Bridge.requests.length; step=5;
            } else if (step === 5) {
                if (Native.Bridge.requests.length === retryBefore) return;
                const ack=Native.Bridge.requests[Native.Bridge.requests.length - 1];
                if (!check(ack.value.operation==="export.media" && ack.value.value.status==="cancelled","busy detached lease ACK retries before cancel")) return;
                Native.Bridge.operationFinished(ack.id,JSON.stringify(result({state:"cancelled",downloadsPending:false})));
                const cancel=Native.Bridge.requests[Native.Bridge.requests.length - 1];
                if (!check(cancel.value.operation==="export.cancel","detached cancel follows successful terminal ACK")) return;
                Native.Bridge.operationFinished(cancel.id,JSON.stringify(result({state:"cancelled"})));
                if (!check(Object.keys(Native.ExportTransport.detached).length===0,"successful detached lifecycle releases token/ownership")) return;
                Qt.exit(0);
            }
        }
    }
}
