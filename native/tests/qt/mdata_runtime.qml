import QtQuick
import ValMAX.Native 1.0 as Native

// Runner substitutes JSON literals, including quotes, into these placeholders.
// All inputs are owned synthetic fixtures; no real profile or credential data.
Item {
    id: test
    property string selectedFile: @FILE@
    property string missingFile: @MISSING_FILE@
    property string invalidFile: @INVALID_FILE@
    property string exportFile: @EXPORT_FILE@
    property string secret: @PASSWORD@
    property var expectedPreview: @EXPECTED_PREVIEW@
    property int checks: 0
    property string failure: ""
    property int step: 0
    property var pending: null
    property var reply: null
    property int status: 0
    property int cancelledDeliveries: 0
    property int closedDeliveries: 0
    property int nativeBeforeUnsupported: 0
    function check(value, message) {
        ++checks;
        if (!value) { failure = message; Qt.exit(1); return false; }
        return true;
    }
    function requestBody(method, route, value) {
        const xhr = Native.MDataTransport.createRequest(); pending = xhr; reply = null; status = 0;
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== xhr.DONE || test.pending !== xhr) return;
            test.pending = null; test.status = xhr.status;
            if (!test.check(xhr.payload === null, "request reference released after native dispatch")) return;
            if (!test.check(xhr.responseText.indexOf(test.secret) < 0, "native public response never echoes synthetic password")) return;
            try { test.reply = JSON.parse(xhr.responseText); }
            catch (_) { test.check(false, "finite native JSON response"); }
        };
        xhr.open(method, route);
        let body = value === null ? "" : JSON.stringify(value);
        if (value !== null) { value.password = ""; value.file = ""; }
        value = null; xhr.send(body); body = "";
        return xhr;
    }
    function request(method, route, file, password) {
        return requestBody(method, route, method === "POST"
                           ? {schema: 1, file: file, password: password} : null);
    }
    function errorIs(code, httpStatus) {
        return check(status === httpStatus && reply && reply.schema === 1 && reply.error === code
                     && Object.keys(reply).sort().join(",") === "error,schema", "native error " + code);
    }
    function exportErrorIs(code) {
        return check(status === 400 && reply && reply.schema === 1 && reply.state === "error"
                     && reply.action === "export" && reply.error === code
                     && Object.keys(reply).sort().join(",") === "action,error,schema,state",
                     "native export rejected before credential access: " + code)
                && check(reply.file === undefined && reply.password === undefined
                         && reply.accounts === undefined && reply.accountIds === undefined,
                         "export error contains no input path, password or account selection");
    }
    Timer {
        interval: 5; running: true; repeat: true
        onTriggered: {
            if (test.failure.length || test.pending !== null) return;
            if (test.step === 0) {
                if (Native.Bridge.runtimeState === "opening") return;
                if (!test.check(Native.Bridge.runtimeAvailable, "actual native runtime opened")) return;
                if (!test.check(test.secret.length >= 12 && test.secret.length < 1000, "bounded synthetic fixture password")) return;
                test.request("POST", "/mdata/inspect", test.selectedFile, test.secret); test.step = 1;
            } else if (test.step === 1) {
                if (!test.check(test.status === 200 && Native.MDataTransport.validPreview(test.reply), "actual authenticated native preview returned")) return;
                const value = test.reply, expected = test.expectedPreview;
                if (!test.check(value.profiles === expected.profiles && value.fingerprint === expected.fingerprint
                                && value.accounts.length === expected.accounts.length, "preview count and encrypted fingerprint match fixture")) return;
                for (let i = 0; i < expected.accounts.length; ++i) {
                    const actual = value.accounts[i], wanted = expected.accounts[i];
                    if (!test.check(actual.accountId === wanted.accountId && actual.displayName === wanted.displayName
                                    && actual.shortName === wanted.shortName, "authenticated account ID and metadata match fixture")) return;
                }
                test.request("POST", "/mdata/inspect", test.selectedFile, test.secret + "-wrong"); test.step = 2;
            } else if (test.step === 2) {
                if (!test.errorIs("authentication_failed", 400)) return;
                test.request("POST", "/mdata/inspect", test.missingFile, test.secret); test.step = 3;
            } else if (test.step === 3) {
                if (!test.errorIs("io_error", 503)) return;
                test.request("POST", "/mdata/inspect", test.invalidFile, test.secret); test.step = 4;
            } else if (test.step === 4) {
                if (!test.errorIs("invalid_archive", 400)) return;
                test.request("POST", "/mdata/inspect", "https://example.invalid/no-network.mdata", test.secret); test.step = 5;
            } else if (test.step === 5) {
                if (!test.errorIs("unsafe_path", 400)) return;
                test.nativeBeforeUnsupported = Native.Bridge.pendingOperations;
                test.request("POST", "/mdata/import-confirmed", test.selectedFile, test.secret); test.step = 6;
            } else if (test.step === 6) {
                if (!test.errorIs("unsupported_operation", 501)) return;
                if (!test.check(Native.Bridge.pendingOperations === test.nativeBeforeUnsupported, "unsupported import never admitted to native work")) return;
                test.request("GET", "/mdata", "", ""); test.step = 7;
            } else if (test.step === 7) {
                if (!test.check(test.status === 200 && test.reply.available === false && test.reply.state === "idle"
                                && test.reply.exportAvailable === true
                                && (test.reply.error === "" || test.reply.error === "unavailable"),
                                "export available while import remains explicitly unavailable")) return;
                const ordinary = test.requestBody("POST", "/mdata", {schema: 1, action: "export",
                    file: test.exportFile, password: test.secret, mode: "restore"});
                if (!test.check(ordinary.requestId.length > 0, "ordinary export admitted to actual native runtime")) return;
                test.step = 8;
            } else if (test.step === 8) {
                if (!test.exportErrorIs("unsafe_path")) return;
                const selected = test.requestBody("POST", "/mdata/export-selected", {schema: 1,
                    file: test.exportFile, password: test.secret, accountIds: ["42", "9007199254740993"]});
                if (!test.check(selected.requestId.length > 0, "selected export admitted with lossless account IDs")) return;
                test.step = 9;
            } else if (test.step === 9) {
                if (!test.exportErrorIs("unsafe_path")) return;
                test.request("GET", "/mdata", "", ""); test.step = 10;
            } else if (test.step === 10) {
                if (!test.check(test.status === 200 && test.reply.available === false && test.reply.exportAvailable === true
                                && test.reply.state === "error" && test.reply.action === "export"
                                && test.reply.error === "unsafe_path" && test.reply.profiles === 0,
                                "read-only status preserves finite export outcome without enabling import")) return;
                if (!test.check(test.reply.file === undefined && test.reply.password === undefined
                                && test.reply.accounts === undefined && test.reply.accountIds === undefined,
                                "status excludes private request values")) return;
                const cancelled = Native.MDataTransport.createRequest();
                cancelled.onreadystatechange = function() { ++test.cancelledDeliveries; };
                cancelled.open("POST", "/mdata/inspect");
                let raw = JSON.stringify({schema: 1, file: test.selectedFile, password: test.secret});
                cancelled.send(raw); raw = ""; cancelled.abort();
                if (!test.check(cancelled.payload === null, "abort releases its secret reference")) return;
                const closed = Native.MDataTransport.createRequest();
                closed.onreadystatechange = function() { ++test.closedDeliveries; };
                closed.open("POST", "/mdata/inspect");
                raw = JSON.stringify({schema: 1, file: test.selectedFile, password: test.secret});
                closed.send(raw); raw = "";
                Native.MDataTransport.close();
                if (!test.check(closed.payload === null, "transport close releases outstanding secret reference")) return;
                test.step = 11;
            } else if (test.step === 11 && Native.Bridge.pendingOperations === 0) {
                if (!test.check(test.cancelledDeliveries === 0 && test.closedDeliveries === 0
                                && Object.keys(Native.MDataTransport.pending).length === 0
                                && Native.MDataTransport.waiting.length === 0, "late native completion suppressed and transport drained")) return;
                test.secret = ""; test.reply = null; test.expectedPreview = null;
                Native.MDataTransport.close(); Qt.exit(0);
            }
        }
    }
    Component.onDestruction: {
        secret = "";
        if (pending !== null) { pending.abort(); pending = null; }
    }
}
