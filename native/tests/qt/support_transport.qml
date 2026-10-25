import QtQuick
import ValMAX.Native 1.0 as Native
Item {
    id: test
    property int checks: 0
    property string failure: ""
    property int callbacks: 0
    property int overflow: 0
    property int step: 0
    property var requests: []
    function check(value, message) {
        ++checks; if (!value) { failure = message; Qt.exit(1); return false; } return true;
    }
    function send(method, body) {
        const request = Native.SupportTransport.createRequest();
        request.onreadystatechange = function() { ++test.callbacks; if (request.status === 503) ++test.overflow; };
        request.open(method); request.send(body); return request;
    }
    function complete(request, value) { Native.Bridge.operationFinished(request.requestId, JSON.stringify(value)); }
    Component.onCompleted: {
        if (!check(Native.SupportTransport.numericVersion("26.35.1.80662") === "26.35.1.80662", "numeric application version accepted")) return;
        for (const version of ["8.8.4 Beta", "1.2\n", "SYNTHETIC-PRIVATE", "１２.３", "999999.1", null, 123])
            if (!check(Native.SupportTransport.numericVersion(version) === null, "non-numeric/unknown version omitted")) return;
        const request = send("GET");
        const envelope = Native.Bridge.requests[0].value;
        if (!check(Object.keys(envelope.context).sort().join(",") === "privacy,startup,versions" && envelope.context.startup === null, "minimal context schema")) return;
        if (!check(envelope.context.privacy.verified === false && Object.keys(envelope.context.privacy).length === 1 && JSON.stringify(envelope).indexOf("SYNTHETIC-PRIVATE") < 0, "raw host snapshot never forwarded")) return;
        Native.Bridge.operationFinished("unknown", "{}");
        if (!check(callbacks === 0 && Object.keys(Native.SupportTransport.pending).length === 1, "unrelated completion ignored")) return;
        complete(request, {schema: 1, ok: true, result: {schema: 1, report: {schema: 2}}});
        if (!check(request.status === 200 && request.readyState === 4 && callbacks === 1, "native report envelope translated")) return;
        complete(request, {schema: 1, ok: false, error: "SYNTHETIC-PRIVATE"});
        if (!check(callbacks === 1, "completion delivered once")) return;
        Native.Bridge.privacyReport = {schema: 1, state: "verified", buildVerified: true, currentProcessVerified: true, serviceProcessVerified: false, error: "", secret: "SYNTHETIC-PRIVATE"};
        const post = send("POST", JSON.stringify({schema: 1, file: "file:///C:/synthetic/report.json"}));
        if (!check(Native.Bridge.requests[1].value.context.privacy.verified === true && Native.Bridge.requests[1].value.operation === "support.export", "export collects fresh minimal context")) return;
        complete(post, {schema: 1, ok: false, error: "file_exists"});
        if (!check(post.status === 409 && JSON.parse(post.responseText).error === "file_exists", "existing destination error preserved")) return;
        for (const response of ["{", JSON.stringify({schema: 1, ok: true, result: {schema: 1, report: {schema: 1}}}), JSON.stringify({schema: 1, ok: false, error: "SYNTHETIC-PRIVATE"})]) {
            const bad = send("GET"); Native.Bridge.operationFinished(bad.requestId, response);
            if (!check(bad.status === 503 && bad.responseText.indexOf("SYNTHETIC-PRIVATE") < 0, "malformed response or unknown code cannot leak")) return;
        }
        const aborted = send("GET"); const before = callbacks; aborted.abort();
        complete(aborted, {schema: 1, ok: true, result: {schema: 1, report: {schema: 2}}});
        if (!check(callbacks === before && Object.keys(Native.SupportTransport.pending).length === 0, "aborted response ignored")) return;
        Native.Bridge.runtimeState = "opening";
        for (let i = 0; i < 9; ++i) requests.push(send("GET"));
        if (!check(Native.SupportTransport.waiting.length === 8, "opening queue limited to eight")) return;
        requests[0].abort();
        if (!check(Native.SupportTransport.waiting.length === 7, "abort removes queued request")) return;
    }
    Timer {
        interval: 5; running: true; repeat: true
        onTriggered: {
            if (test.failure.length) return;
            if (test.step === 0) {
                if (test.requests[8].readyState !== 4) return;
                if (!test.check(test.requests[8].status === 503, "overflow completed without dispatch")) return;
                Native.Bridge.privacyReport = {schema: 1, state: "verified", buildVerified: false, currentProcessVerified: true, serviceProcessVerified: false, error: ""};
                const before = Native.Bridge.requests.length;
                Native.Bridge.runtimeState = "ready";
                if (!test.check(Native.Bridge.requests.length === before + 7 && Native.SupportTransport.waiting.length === 0, "ready drains bounded queue")) return;
                for (let i = 1; i < 8; ++i) {
                    const latest = Native.Bridge.requests[before + i - 1].value;
                    if (!test.check(latest.context.privacy.verified === null, "queued context built at dispatch; contradictory verification is unknown")) return;
                    test.complete(test.requests[i], {schema: 1, ok: true, result: {schema: 1, report: {schema: 2}}});
                }
                if (!test.check(Object.keys(Native.SupportTransport.pending).length === 0, "pending map drained")) return;
                test.requests = [];
                for (const body of ["{", "{}", JSON.stringify({schema: 1, file: ""}), JSON.stringify({schema: 1, file: "file:///C:/synthetic/a.json", extra: "private"}), "x".repeat(65537)])
                    test.requests.push(test.send("POST", body));
                test.requests.push(test.send("GET", "unexpected"));
                test.step = 1;
            } else if (test.step === 1) {
                if (test.requests.some(function(request) { return request.readyState !== 4; })) return;
                if (!test.check(test.requests.every(function(request) { return request.status === 400; }), "malformed bodies rejected before native dispatch")) return;
                Native.Bridge.runtimeState = "unconfigured";
                test.requests = [test.send("GET")]; test.step = 2;
            } else if (test.step === 2) {
                if (test.requests[0].readyState !== 4) return;
                if (!test.check(test.requests[0].status === 503, "unavailable runtime fails finitely")) return;
                Native.Bridge.runtimeState = "ready"; Native.Bridge.accept = false;
                test.requests = [test.send("GET")]; test.step = 3;
            } else if (test.step === 3) {
                if (test.requests[0].readyState !== 4) return;
                if (!test.check(test.requests[0].status === 503 && Object.keys(Native.SupportTransport.pending).length === 0, "rejected bridge admission releases request")) return;
                Qt.exit(0);
            }
        }
    }
}
