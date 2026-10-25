import QtQuick
import ValMAX.Native 1.0 as Native

Item {
    id: test
    property int checks: 0
    property string failure: ""
    property var tasks: []
    property var request: null
    property var first: null
    property int callbacks: 0
    property int before: 0
    property int admissions: 0
    property string abortedId: ""

    function check(value, label) { ++checks; if (!value) throw new Error(label); }
    function add(action) { tasks.push(action); }
    function create() {
        const xhr = Native.SettingsTransport.createRequest("accounts");
        xhr.onreadystatechange = function() { if (xhr.readyState === 4) ++test.callbacks; };
        xhr.open("GET"); return xhr;
    }
    function send() { const xhr = create(); xhr.send(""); return xhr; }
    function ack(id, value) { Native.Bridge.operationFinished(id, JSON.stringify({schema: 1, ok: true, result: value})); }
    function noLeaks() {
        check(Object.keys(Native.SettingsTransport.pending).length === 0, "no pending SettingsTransport entry");
        check(Native.SettingsTransport.waiting.length === 0, "no waiting SettingsTransport entry");
    }
    function prepare() {
        add(function() {
            request = create(); before = callbacks;
            Native.Bridge.duringInvoke = function() { test.request.abort(); };
            request.send("");
            check(request.aborted && request.payload === null, "synchronous invoke abort clears payload");
            check(request.requestId === "" && callbacks === before, "aborted admission never becomes pending or calls back");
            const id = Native.Bridge.requests[Native.Bridge.requests.length - 1].id;
            ack(id, {unexpected: true});
            check(callbacks === before && request.readyState !== 4, "completion of reentrantly aborted admission is ignored");
            noLeaks();
            Native.Bridge.throwOnInvoke = true; request = send(); Native.Bridge.throwOnInvoke = false;
        });
        add(function() {
            check(request.readyState === 4 && request.status === 503 && request.payload === null,
                  "invoke exception completes unavailable and clears payload");
            check(callbacks === before + 1, "invoke exception delivers one callback"); noLeaks();
        });
        for (const invalidId of ["", "space id", "path/id", "x".repeat(65), 42]) {
            add(function() {
                Native.Bridge.forcedId = invalidId; before = callbacks;
                request = send(); Native.Bridge.forcedId = null;
            });
            add(function() {
                check(request.readyState === 4 && request.status === 503 && request.requestId === "" && request.payload === null,
                      "invalid request ID rejected without retaining payload or ID");
                check(callbacks === before + 1, "invalid ID response finishes once"); noLeaks();
            });
        }
        add(function() {
            first = send(); before = callbacks;
            Native.Bridge.forcedId = first.requestId; request = send(); Native.Bridge.forcedId = null;
            check(Native.SettingsTransport.pending[first.requestId] === first, "duplicate admission preserves original pending object");
            check(Object.keys(Native.SettingsTransport.pending).length === 1, "duplicate admission cannot allocate a second pending entry");
        });
        add(function() {
            check(request.readyState === 4 && request.status === 503 && request.payload === null,
                  "duplicate ID response completes unavailable");
            check(callbacks === before + 1 && first.readyState !== 4, "duplicate response does not finish original request");
            ack(first.requestId, {accounts: ["synthetic-account"]});
            check(first.readyState === 4 && first.status === 200 && JSON.parse(first.responseText).accounts[0] === "synthetic-account",
                  "normal original completion retains correct payload");
            check(callbacks === before + 2, "normal completion finishes once");
            ack(first.requestId, {accounts: ["stale"]});
            check(callbacks === before + 2 && JSON.parse(first.responseText).accounts[0] === "synthetic-account",
                  "second completion cannot replace delivered result"); noLeaks();
            request = send(); abortedId = request.requestId; before = callbacks;
            request.abort(); ack(abortedId, {unexpected: true});
        });
        add(function() {
            check(request.aborted && request.payload === null && request.readyState !== 4,
                  "aborted accepted request stays aborted after stale completion");
            check(callbacks === before, "stale completion after abort cannot call dead callback"); noLeaks();
            Native.Bridge.runtimeState = "opening"; admissions = Native.Bridge.requests.length;
            request = send(); before = callbacks;
            check(Native.SettingsTransport.waiting.length === 1 && request.requestId === "",
                  "opening request waits without a native admission");
            check(Native.Bridge.requests.length === admissions, "opening queue does not invoke prematurely");
            request.abort(); Native.Bridge.runtimeState = "ready";
        });
        add(function() {
            check(request.aborted && request.payload === null && Native.Bridge.requests.length === admissions,
                  "cancelled opening request never dispatches when ready");
            check(callbacks === before, "cancelled opening request never calls back"); noLeaks();
            request = send();
            check(Object.keys(Native.SettingsTransport.pending).length === 1, "adapter accepts ordinary work after cancelled opening queue");
            ack(request.requestId, {recovered: true});
            check(request.readyState === 4 && request.status === 200 && JSON.parse(request.responseText).recovered,
                  "adapter completes subsequent ordinary work"); noLeaks();
        });
    }
    Component.onCompleted: prepare()
    Timer {
        id: run; interval: 5; repeat: true; running: true
        property int ticks: 0
        onTriggered: {
            try {
                if (++ticks > 1000) throw new Error("SettingsTransport regression deadline");
                if (!test.tasks.length) { stop(); Qt.exit(0); return; }
                test.tasks.shift()();
            } catch (error) {
                test.failure = String(error); console.error("settings-transport-regression:", test.failure);
                stop(); Qt.exit(1);
            }
        }
    }
}
