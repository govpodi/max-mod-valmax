import QtQuick
import ValMAX.Native 1.0 as Native
Item {
    id: test
    property int checks: 0
    property string failure: ""
    property int callbacks: 0
    property int step: 0
    property var deferred: []
    property var queued: []
    property var active: []
    property var closedDeferred: null
    function check(value, message) {
        ++checks; if (!value) { failure = message; Qt.exit(1); return false; } return true;
    }
    function body() { return JSON.stringify({schema: 1, file: "file:///C:/synthetic/selected.mdata", password: "synthetic-password"}); }
    function exportBody(accounts) {
        const value = JSON.parse(body());
        if (accounts === undefined) { value.action = "export"; value.mode = "restore"; }
        else value.accountIds = accounts;
        return JSON.stringify(value);
    }
    function exportRequest(accounts) { return send("POST", accounts === undefined ? "/mdata" : "/mdata/export-selected", exportBody(accounts)); }
    function exported() { return {schema: 1, state: "done", action: "export", accepted: true, result: "exported", profiles: 2, archiveBytes: 1234}; }
    function preview() { return {schema: 1, kind: "login", profiles: 1, fingerprint: "a".repeat(64),
        accounts: [{accountId: "9007199254740993", displayName: "Алиса 😀", shortName: "AE"}]}; }
    function send(method, route, payload) {
        const request = Native.MDataTransport.createRequest();
        request.onreadystatechange = function() { ++test.callbacks; };
        request.open(method, route); request.send(payload); return request;
    }
    function inspect() { return send("POST", "/mdata/inspect", body()); }
    function complete(request, value) { Native.Bridge.operationFinished(request.requestId, JSON.stringify(value)); }
    function errorLater(request, code, status) { deferred.push({request: request, code: code, status: status}); }
    function verifyDeferred() {
        if (deferred.some(function(value) { return value.request.readyState !== 4; })) return false;
        for (const value of deferred) {
            if (!check(value.request.status === value.status && JSON.parse(value.request.responseText).error === value.code,
                       "finite deferred status " + value.code)) return false;
            if (!check(value.request.payload === null && value.request.responseText.indexOf("SYNTHETIC-PRIVATE") < 0,
                       "deferred result has no retained request or raw exception")) return false;
            if (value.request.kind === "export" && !check(JSON.parse(value.request.responseText).state === "error"
                    && JSON.parse(value.request.responseText).action === "export", "deferred export error keeps panel contract")) return false;
        }
        deferred = []; return true;
    }
    Component.onCompleted: {
        const request = inspect(), call = Native.Bridge.requests[0];
        if (!check(request.DONE === 4 && Native.MDataTransport.done === 4 && request.payload === null, "DONE and dispatched secret reference released")) return;
        if (!check(call.keys === "file,operation,password" && call.operation === "mdata.inspect" && call.fileMatches && call.passwordMatches, "native operation shape")) return;
        Native.Bridge.operationFinished("unknown", "{}"); Native.Bridge.operationFinished("__proto__", "{}");
        if (!check(callbacks === 0 && Object.keys(Native.MDataTransport.pending).length === 1, "unrelated completion ignored")) return;
        complete(request, {schema: 1, ok: true, result: preview()});
        if (!check(request.status === 200 && request.readyState === 4 && callbacks === 1 && JSON.parse(request.responseText).accounts[0].accountId === "9007199254740993", "authenticated preview envelope preserves exact string ID")) return;
        complete(request, {schema: 1, ok: false, error: "SYNTHETIC-PRIVATE"});
        if (!check(callbacks === 1, "completion once")) return;
        const all = exportRequest(), allCall = Native.Bridge.requests[Native.Bridge.requests.length - 1];
        if (!check(allCall.keys === "file,operation,password" && allCall.operation === "mdata.export"
                && allCall.fileMatches && allCall.passwordMatches && all.payload === null, "legacy all export maps only exact native fields")) return;
        complete(all, {schema: 1, ok: true, result: exported()});
        if (!check(all.status === 200 && JSON.stringify(JSON.parse(all.responseText)) === JSON.stringify(exported()), "validated export acknowledgement delivered")) return;
        Native.Bridge.runtimeState = "opening";
        const selected = exportRequest(["9223372036854775807", "9007199254740993", "1"]);
        const selectedPayload = JSON.parse(selected.payload);
        if (!check(Object.keys(selectedPayload).sort().join(",") === "accounts,file,operation,password"
                && selectedPayload.operation === "mdata.export-selected"
                && JSON.stringify(selectedPayload.accounts) === '["9223372036854775807","9007199254740993","1"]',
                "selected IDs preserve exact strings and map accountIds to accounts")) return;
        selectedPayload.password = ""; selectedPayload.file = ""; selectedPayload.accounts = null;
        Native.Bridge.runtimeState = "ready";
        const selectedCall = Native.Bridge.requests[Native.Bridge.requests.length - 1];
        if (!check(selected.payload === null && selectedCall.keys === "accounts,file,operation,password"
                && selectedCall.operation === "mdata.export-selected", "selected export dispatch clears queued secret")) return;
        const selectedResult = exported(); selectedResult.profiles = 3; selectedResult.archiveBytes = 2097152;
        complete(selected, {schema: 1, ok: true, result: selectedResult});
        if (!check(selected.status === 200 && JSON.parse(selected.responseText).archiveBytes === 2097152, "export size and profile upper bounds accepted")) return;
        for (const mutate of [function(v) { v.schema = true; }, function(v) { v.state = "working"; },
            function(v) { v.action = "import"; }, function(v) { v.accepted = 1; }, function(v) { v.result = "restored"; },
            function(v) { v.profiles = 0; }, function(v) { v.profiles = 4; }, function(v) { v.profiles = 1.5; },
            function(v) { v.archiveBytes = 0; }, function(v) { v.archiveBytes = 2097153; }, function(v) { v.archiveBytes = 3.5; },
            function(v) { v.archiveBytes = "1234"; }, function(v) { v.file = "SYNTHETIC-PRIVATE"; }]) {
            const value = exported(); mutate(value); const bad = exportRequest();
            complete(bad, {schema: 1, ok: true, result: value});
            if (!check(bad.status === 503 && JSON.parse(bad.responseText).state === "error"
                    && JSON.parse(bad.responseText).action === "export" && bad.responseText.indexOf("SYNTHETIC-PRIVATE") < 0,
                    "invalid export acknowledgement never published")) return;
        }
        const wrongKind = exportRequest(); complete(wrongKind, {schema: 1, ok: true, result: preview()});
        if (!check(wrongKind.status === 503, "preview cannot acknowledge export")) return;
        const wrongPreview = inspect(); complete(wrongPreview, {schema: 1, ok: true, result: exported()});
        if (!check(wrongPreview.status === 503, "export cannot acknowledge preview")) return;
        for (const pair of [["file_exists", 409], ["busy", 409], ["account_limit", 409], ["recovery_required", 409],
            ["unsafe_path", 400], ["profile_changed", 400], ["invalid_profile", 400], ["insufficient_space", 400],
            ["credential_io", 503], ["cancelled", 503], ["SYNTHETIC-PRIVATE", 503]]) {
            const failed = exportRequest(); complete(failed, {schema: 1, ok: false, error: pair[0]});
            const value = JSON.parse(failed.responseText);
            if (!check(failed.status === pair[1] && Object.keys(value).sort().join(",") === "action,error,schema,state"
                    && value.state === "error" && value.action === "export"
                    && value.error === (pair[0] === "SYNTHETIC-PRIVATE" ? "io_error" : pair[0]), "finite export error preserves panel shape")) return;
        }
        const emoji = preview(); emoji.accounts[0].displayName = "😀".repeat(256); emoji.accounts[0].shortName = "😀".repeat(32);
        if (!check(Native.MDataTransport.validPreview(emoji), "valid scalar limits preserve astral names")) return;
        for (const mutate of [function(v) { v.profiles = 2; }, function(v) { v.accounts[0].accountId = "9223372036854775808"; },
            function(v) { v.accounts[0].accountId = 9007199254740993; }, function(v) { v.accounts[0].displayName = "\ud800"; },
            function(v) { v.accounts[0].displayName = "x\u202e"; }, function(v) { v.accounts[0].extra = "SYNTHETIC-PRIVATE"; },
            function(v) { v.extra = "SYNTHETIC-PRIVATE"; }, function(v) { v.fingerprint = "A".repeat(64); },
            function(v) { v.accounts[0].shortName = "😀".repeat(33); }]) {
            const value = preview(); mutate(value); const bad = inspect();
            complete(bad, {schema: 1, ok: true, result: value});
            if (!check(bad.status === 503 && JSON.parse(bad.responseText).error === "io_error", "invalid preview rejected")) return;
        }
        for (const raw of ["{", "x".repeat(16385), JSON.stringify({schema: 1, ok: false, error: "SYNTHETIC-PRIVATE"}), JSON.stringify({schema: 1, ok: 1})]) {
            const bad = inspect(); Native.Bridge.operationFinished(bad.requestId, raw);
            if (!check(bad.status === 503 && bad.responseText.indexOf("SYNTHETIC-PRIVATE") < 0, "bad response sanitized")) return;
        }
        for (const code of ["authentication_failed", "unsupported_version", "account_limit", "unsafe_path", "too_large"]) {
            const error = inspect(); complete(error, {schema: 1, ok: false, error: code});
            if (!check(error.status === 400 && JSON.parse(error.responseText).error === code, "known native error preserved")) return;
        }
        const aborted = inspect(), beforeAbort = callbacks; aborted.abort();
        complete(aborted, {schema: 1, ok: true, result: preview()});
        if (!check(callbacks === beforeAbort && aborted.payload === null && Object.keys(Native.MDataTransport.pending).length === 0, "abort only discards delivery")) return;
        const reentrant = Native.MDataTransport.createRequest(); reentrant.open("POST", "/mdata/inspect");
        Native.Bridge.duringInvoke = function() { reentrant.abort(); }; reentrant.send(body());
        if (!check(reentrant.aborted && reentrant.payload === null && Object.keys(Native.MDataTransport.pending).length === 0, "reentrant admission abort not registered")) return;
        const closeDuring = Native.MDataTransport.createRequest(); closeDuring.open("POST", "/mdata/inspect");
        Native.Bridge.duringInvoke = function() { Native.MDataTransport.close(); }; closeDuring.send(body());
        if (!check(closeDuring.payload === null && Object.keys(Native.MDataTransport.pending).length === 0, "reentrant admission close not registered")) return;
        const first = inspect(); Native.Bridge.forcedId = first.requestId;
        errorLater(inspect(), "unavailable", 503); Native.Bridge.forcedId = "";
        if (!check(Native.MDataTransport.pending[first.requestId] === first, "duplicate request ID never replaces first")) return;
        complete(first, {schema: 1, ok: true, result: preview()});
        Native.Bridge.throwOnInvoke = true; errorLater(inspect(), "unavailable", 503); Native.Bridge.throwOnInvoke = false;
        Native.Bridge.accept = false; errorLater(inspect(), "busy", 503); Native.Bridge.accept = true;
        const beforeUnsupported = Native.Bridge.requests.length;
        for (const pair of [["POST", "/mdata/import-confirmed"], ["GET", "/mdata/export-selected"],
            ["GET", "/mdata/inspect"], ["PUT", "/mdata/inspect"], ["POST", "http://127.0.0.1:18973/mdata/inspect"]])
            errorLater(send(pair[0], pair[1], body()), "unsupported_operation", 501);
        if (!check(Native.Bridge.requests.length === beforeUnsupported, "unsupported operations never invoke native work or HTTP")) return;
        for (const invalid of ["{", "{}", "x".repeat(65537), JSON.stringify({schema: 1, file: "x", password: "short"}),
            JSON.stringify({schema: 1, file: "x", password: "x".repeat(1025)}), JSON.stringify({schema: 1, file: "x", password: "\ud800".repeat(20)}),
            JSON.stringify({schema: 1, file: "x", password: "synthetic-password", extra: 1}), JSON.stringify({schema: 1, file: "x\u0000", password: "synthetic-password"})])
            errorLater(send("POST", "/mdata/inspect", invalid), "invalid_request", 400);
        errorLater(send("GET", "/mdata", "unexpected"), "invalid_request", 400);
        const beforeBadExport = Native.Bridge.requests.length;
        for (const mutate of [function(v) { delete v.mode; }, function(v) { v.mode = "add"; },
            function(v) { v.action = "import"; }, function(v) { v.schema = true; }, function(v) { v.extra = 1; },
            function(v) { v.accountIds = ["1"]; }, function(v) { v.password = "short"; }, function(v) { v.file = null; }]) {
            const value = JSON.parse(exportBody()); mutate(value);
            errorLater(send("POST", "/mdata", JSON.stringify(value)), "invalid_request", 400);
        }
        for (const ids of [[], ["1", "2", "3", "4"], ["1", "1"], [1], [true], ["0"], ["01"], ["-1"],
            ["9223372036854775808"], ["9007199254740993", 2], "1", null])
            errorLater(exportRequest(ids), "invalid_request", 400);
        const nativeShape = JSON.parse(body()); nativeShape.accounts = ["1"];
        errorLater(send("POST", "/mdata/export-selected", JSON.stringify(nativeShape)), "invalid_request", 400);
        if (!check(Native.Bridge.requests.length === beforeBadExport, "invalid export forms never reach native runtime")) return;
    }
    Timer {
        interval: 5; running: true; repeat: true
        onTriggered: {
            if (test.failure.length) return;
            if (test.step === 0) {
                if (!test.verifyDeferred()) return;
                Native.Bridge.runtimeState = "opening";
                for (let i = 0; i < 9; ++i) test.queued.push(test.inspect());
                if (!test.check(Native.MDataTransport.waiting.length === 8 && test.queued[0].payload.indexOf("synthetic-password") >= 0, "eight opening requests bounded and awaiting dispatch")) return;
                test.errorLater(test.queued[8], "busy", 503); test.queued[0].close();
                if (!test.check(test.queued[0].payload === null && Native.MDataTransport.waiting.length === 7, "close clears queued secret")) return;
                const count = Native.Bridge.requests.length;
                Native.Bridge.runtimeState = "ready";
                if (!test.check(Native.Bridge.requests.length === count + 7 && Native.MDataTransport.waiting.length === 0 && Object.keys(Native.MDataTransport.pending).length === 7, "opening drains once")) return;
                for (let i = 1; i < 8; ++i) if (!test.check(test.queued[i].payload === null, "dispatched queued secret reference released")) return;
                test.active.push(test.inspect()); Native.Bridge.runtimeState = "opening";
                test.errorLater(test.inspect(), "busy", 503);
                if (!test.check(Native.MDataTransport.waiting.length === 0 && Object.keys(Native.MDataTransport.pending).length === 8, "pending and opening share the same cap")) return;
                const before = test.callbacks; Native.MDataTransport.close();
                for (let i = 1; i < 8; ++i) test.complete(test.queued[i], {schema: 1, ok: true, result: test.preview()});
                if (!test.check(test.callbacks === before && Object.keys(Native.MDataTransport.pending).length === 0, "transport close drops all admitted deliveries")) return;
                // close intentionally invalidates deferred failures too.
                test.deferred = []; test.queued = [];
                for (let i = 0; i < 3; ++i) test.queued.push(test.inspect());
                Native.MDataTransport.close();
                if (!test.check(test.queued.every(function(value) { return value.payload === null && value.aborted; }) && Native.MDataTransport.waiting.length === 0, "transport close clears all waiting secrets")) return;
                test.queued = [test.inspect()]; Native.Bridge.runtimeState = "unconfigured";
                test.errorLater(test.queued[0], "unavailable", 503); test.step = 1;
            } else if (test.step === 1) {
                if (!test.verifyDeferred()) return;
                const status = test.send("GET", "/mdata", ""); test.active = [status]; test.step = 2;
            } else if (test.step === 2) {
                if (test.active[0].readyState !== 4) return;
                const status = JSON.parse(test.active[0].responseText);
                if (!test.check(test.active[0].status === 200 && status.available === false && status.exportAvailable === false
                        && status.state === "error" && status.action === "export" && status.error === "io_error", "GET retains finite native export status without claiming unavailable runtime readiness")) return;
                Native.Bridge.runtimeState = "ready";
                test.closedDeferred = test.send("POST", "/mdata", test.body());
                const unopened = Native.MDataTransport.createRequest(); Native.MDataTransport.close();
                let staleRejected = false; try { unopened.open("POST", "/mdata/inspect"); } catch (_) { staleRejected = true; }
                if (!test.check(staleRejected, "closed epoch cannot retain a new secret")) return;
                test.active = [test.inspect()];
                test.complete(test.active[0], {schema: 1, ok: true, result: test.preview()}); test.step = 3;
            } else if (test.step === 3) {
                if (!test.check(test.closedDeferred.readyState !== 4 && test.closedDeferred.payload === null, "close suppresses already scheduled failure")) return;
                if (!test.check(test.active[0].status === 200 && Object.keys(Native.MDataTransport.pending).length === 0, "fresh request works after close")) return;
                const pending = test.inspect(); Native.Bridge.runtimeState = "opening";
                const waiting = test.inspect();
                if (!test.check(Object.keys(Native.MDataTransport.pending).length + Native.MDataTransport.waiting.length === 2,
                                "mixed pending and waiting tracked together")) return;
                Native.Bridge.runtimeState = "closed";
                const stale = test.preview(); stale.accounts[0].displayName = "SYNTHETIC-PRIVATE";
                test.complete(pending, {schema: 1, ok: true, result: stale});
                if (!test.check(Object.keys(Native.MDataTransport.pending).length === 0 && Native.MDataTransport.waiting.length === 0
                                && pending.payload === null && waiting.payload === null, "runtime close clears pending and queued references")) return;
                test.errorLater(pending, "unavailable", 503); test.errorLater(waiting, "unavailable", 503); test.step = 4;
            } else if (test.step === 4) {
                if (!test.verifyDeferred()) return;
                Native.Bridge.runtimeState = "ready";
                const completed = test.exportRequest();
                test.complete(completed, {schema: 1, ok: true, result: test.exported()});
                const previewOnly = test.inspect(); test.complete(previewOnly, {schema: 1, ok: true, result: test.preview()});
                Native.MDataTransport.close();
                test.active = [test.send("GET", "/mdata", "")]; test.step = 5;
            } else if (test.step === 5) {
                if (test.active[0].readyState !== 4) return;
                const status = JSON.parse(test.active[0].responseText);
                if (!test.check(status.available === false && status.exportAvailable === true && status.state === "done"
                        && status.action === "export" && status.result === "exported" && status.profiles === 2 && status.archiveBytes === 1234
                        && status.error === "" && status.backupAvailable === false, "GET preserves export result across inspect and panel close")) return;
                if (!test.check(Object.keys(status).sort().join(",") === "action,archiveBytes,available,backupAvailable,error,exportAvailable,profiles,result,schema,state"
                        && JSON.stringify(Native.MDataTransport.lastExport).indexOf("synthetic") < 0, "status retains only finite non-secret metadata")) return;
                const before = test.callbacks, aborted = test.exportRequest(), count = Native.Bridge.requests.length;
                if (!test.check(Native.MDataTransport.statusValue().state === "working"
                        && Native.MDataTransport.statusValue().result === null, "accepted new export invalidates previous success")) return;
                aborted.abort(); test.complete(aborted, {schema: 1, ok: true, result: test.exported()});
                if (!test.check(test.callbacks === before && Native.Bridge.requests.length === count && aborted.payload === null,
                        "export abort suppresses delivery and never sends a native cancellation")) return;
                test.active = [test.send("GET", "/mdata", "")]; test.step = 6;
            } else if (test.step === 6) {
                if (test.active[0].readyState !== 4) return;
                const status = JSON.parse(test.active[0].responseText);
                if (!test.check(status.state === "error" && status.action === "export" && status.error === "outcome_unknown"
                        && status.result === null && status.profiles === 0 && !Object.prototype.hasOwnProperty.call(status, "archiveBytes"),
                        "success A then aborted B never presents A as B success")) return;
                const previous = test.exportRequest(); test.complete(previous, {schema: 1, ok: true, result: test.exported()});
                const closing = test.exportRequest(), count = Native.Bridge.requests.length;
                Native.MDataTransport.close(); test.complete(closing, {schema: 1, ok: true, result: test.exported()});
                if (!test.check(Native.Bridge.requests.length === count, "panel close does not cancel native export")) return;
                test.active = [test.send("GET", "/mdata", "")]; test.step = 7;
            } else if (test.step === 7) {
                if (test.active[0].readyState !== 4) return;
                const status = JSON.parse(test.active[0].responseText);
                if (!test.check(status.state === "error" && status.error === "outcome_unknown" && status.result === null,
                        "close and late completion cannot restore previous success")) return;
                const previous = test.exportRequest(); test.complete(previous, {schema: 1, ok: true, result: test.exported()});
                const reentrant = Native.MDataTransport.createRequest(); reentrant.open("POST", "/mdata");
                Native.Bridge.duringInvoke = function() { reentrant.abort(); }; reentrant.send(test.exportBody());
                if (!test.check(reentrant.aborted && reentrant.payload === null && Object.keys(Native.MDataTransport.pending).length === 0,
                        "reentrant export abort clears request before registration")) return;
                test.active = [test.send("GET", "/mdata", "")]; test.step = 8;
            } else if (test.step === 8) {
                if (test.active[0].readyState !== 4) return;
                const status = JSON.parse(test.active[0].responseText);
                if (!test.check(status.state === "error" && status.error === "outcome_unknown" && status.result === null,
                        "invoke abort cannot leave previous success or working state")) return;
                const previous = test.exportRequest(); test.complete(previous, {schema: 1, ok: true, result: test.exported()});
                Native.Bridge.duringInvoke = function() { Native.MDataTransport.close(); };
                const reentrant = test.exportRequest();
                if (!test.check(reentrant.payload === null && Object.keys(Native.MDataTransport.pending).length === 0,
                        "reentrant close does not register in-flight export")) return;
                test.active = [test.send("GET", "/mdata", "")]; test.step = 9;
            } else if (test.step === 9) {
                if (test.active[0].readyState !== 4) return;
                const status = JSON.parse(test.active[0].responseText);
                if (!test.check(status.state === "error" && status.error === "outcome_unknown" && status.result === null,
                        "invoke close keeps finite unknown outcome")) return;
                const older = test.exportRequest(), newer = test.exportRequest(), result = test.exported(); result.profiles = 3;
                test.complete(newer, {schema: 1, ok: true, result: result});
                test.complete(older, {schema: 1, ok: false, error: "unsafe_path"}); older.abort();
                test.active = [test.send("GET", "/mdata", "")]; test.step = 10;
            } else if (test.step === 10) {
                if (test.active[0].readyState !== 4) return;
                const status = JSON.parse(test.active[0].responseText);
                if (!test.check(status.state === "done" && status.result === "exported" && status.profiles === 3,
                        "older completion and abort cannot replace newer generation result")) return;
                const failed = test.exportRequest(); test.complete(failed, {schema: 1, ok: false, error: "unsafe_path"});
                test.active = [test.send("GET", "/mdata", "")]; test.step = 11;
            } else if (test.step === 11) {
                if (test.active[0].readyState !== 4) return;
                const status = JSON.parse(test.active[0].responseText);
                if (!test.check(status.state === "error" && status.action === "export" && status.error === "unsafe_path"
                        && status.result === null && status.profiles === 0 && !Object.prototype.hasOwnProperty.call(status, "archiveBytes"),
                        "new native export error clears prior success fields")) return;
                Native.Bridge.runtimeState = "closed";
                test.active = [test.send("GET", "/mdata", "")]; test.step = 12;
            } else if (test.step === 12) {
                if (test.active[0].readyState !== 4) return;
                const status = JSON.parse(test.active[0].responseText);
                if (!test.check(status.available === false && status.exportAvailable === false && status.state === "error"
                        && status.error === "unsafe_path", "runtime availability remains live in safe status")) return;
                Native.Bridge.runtimeState = "ready";
                const accepted = test.exportRequest();
                accepted.onreadystatechange = function() { test.check(false, "detached runtime failure delivered after close"); };
                if (!test.check(accepted.requestId.length > 0 && Native.MDataTransport.lastExport.state === "working",
                        "export admitted before runtime detach")) return;
                Native.Bridge.runtimeState = "closed";
                if (!test.check(Object.keys(Native.MDataTransport.pending).length === 0 && accepted.readyState !== 4,
                        "runtime detaches request before queued unavailable callback")) return;
                Native.MDataTransport.close();
                test.active = [test.send("GET", "/mdata", ""), accepted]; test.step = 13;
            } else {
                if (test.active[0].readyState !== 4) return;
                const status = JSON.parse(test.active[0].responseText);
                if (!test.check(status.state === "error" && status.error === "outcome_unknown" && status.action === "export"
                        && status.result === null && status.profiles === 0 && status.exportAvailable === false,
                        "close settles detached working export before deferred failure is discarded")) return;
                if (!test.check(test.active[1].readyState !== 4 && test.active[1].payload === null,
                        "closed epoch suppresses detached callback and keeps no secret")) return;
                Qt.exit(0);
            }
        }
    }
}
