import QtQuick
import ValMAX.Native 1.0 as Native

Item {
    id: test
    property int checks: 0
    property string failure: ""
    property var tasks: []
    property var page: null
    property var controller: null
    property var request: null
    property var orphan: null
    property var requests: []
    property int callbacks: 0
    property int before: 0
    property var savedCallback: null
    property int disposalPolls: 0
    property bool awaitingDisposal: false
    Component { id: pageFactory; RetentionPage {} }
    Component { id: controllerFactory; RetentionController {} }

    function check(value, label) { ++checks; if (!value) throw new Error(label); }
    function add(action) { tasks.push(action); }
    function status(enabled) {
        return {schema: 2, revision: "a".repeat(64), enabled: enabled === true,
            capabilities: {capture: false, lookup: false, enable: false, disable: true},
            runtime: {ready: false, applied: false, enabled: false, historySyncVerified: false,
                error: "retention_not_integrated", retainedEvents: null, retainedMessages: null}};
    }
    function copy(value) { return JSON.parse(JSON.stringify(value)); }
    function send(family, method, body) {
        const xhr = Native.RetentionTransport.createRequest(family);
        xhr.onreadystatechange = function() { if (xhr.readyState === 4) ++test.callbacks; };
        xhr.open(method); xhr.send(body || ""); return xhr;
    }
    function ack(xhr, value) {
        check(typeof xhr.requestId === "string" && xhr.requestId.length > 0, "native operation admission has a request ID");
        Native.Bridge.operationFinished(xhr.requestId, JSON.stringify({schema: 1, ok: true, result: value}));
    }
    function reject(xhr, code) {
        Native.Bridge.operationFinished(xhr.requestId, JSON.stringify({schema: 1, ok: false, error: code}));
    }
    function last() { return Native.Bridge.requests[Native.Bridge.requests.length - 1]; }
    function noLeaks() {
        check(Object.keys(Native.RetentionTransport.pending).length === 0 && Native.RetentionTransport.waiting.length === 0,
              "completed and aborted requests leave no adapter entries");
    }
    function prepareSuite() {
        add(function() {
            check(Native.RetentionTransport.lookupAvailable === false, "native adapter advertises no archive lookup backend");
            let invalid = false; try { Native.RetentionTransport.createRequest("capture"); } catch (_) { invalid = true; }
            check(invalid, "unsupported request family is rejected");
            request = send("settings", "GET", "");
            check(last().value.operation === "retention.get" && Object.keys(last().value).join(",") === "operation",
                  "settings GET maps to native preference retrieval only");
            ack(request, status(true));
        });
        add(function() {
            check(request.readyState === 4 && request.status === 200 && JSON.parse(request.responseText).enabled,
                  "stored legacy enabled preference remains visible without claiming capture readiness");
            before = callbacks;
            Native.Bridge.operationFinished(request.requestId, JSON.stringify({schema: 1, ok: true, result: status(false)}));
            check(callbacks === before && JSON.parse(request.responseText).enabled,
                  "stale completion cannot replace an already-delivered preference result");
            request = send("settings", "POST", JSON.stringify({schema: 1, revision: "a".repeat(64), enabled: false}));
            check(last().value.operation === "retention.set" && last().value.enabled === false
                  && last().value.revision === "a".repeat(64), "disable POST maps to native compare-and-swap preference update");
            ack(request, status(false));
        });
        add(function() {
            check(request.status === 200 && JSON.parse(request.responseText).enabled === false,
                  "disabling an old preference is acknowledged");
            request = send("settings", "POST", JSON.stringify({schema: 1, revision: "a".repeat(64), enabled: true}));
            reject(request, "retention_unavailable");
        });
        add(function() {
            check(request.readyState === 4 && request.status === 503,
                  "native enabling rejection is published as unavailable, never success");
            before = Native.Bridge.requests.length;
            request = send("lookup", "POST", JSON.stringify({schema: 1, accountId: "1", chatId: "2", messageIds: ["3"]}));
        });
        add(function() {
            check(request.readyState === 4 && request.status === 503 && Native.Bridge.requests.length === before,
                  "lookup remains explicitly unavailable without dispatching archive work");
            const reply = JSON.parse(request.responseText);
            check(typeof reply.error === "string" && !Object.prototype.hasOwnProperty.call(reply, "messages"),
                  "unavailable lookup is an error, not an empty successful archive");
            before = Native.Bridge.requests.length;
            request = send("settings", "POST", JSON.stringify({schema: 2, revision: "a".repeat(64), enabled: false}));
        });
        add(function() {
            check(request.readyState === 4 && request.status === 400 && Native.Bridge.requests.length === before,
                  "settings POST keeps the exact legacy schema-1 mutation contract");
            request = send("settings", "GET", ""); before = callbacks;
            request.abort(); reject(request, "retention_unavailable");
            check(callbacks === before, "aborted native preference response cannot invoke a dead page callback");
            noLeaks();
            Native.Bridge.runtimeState = "opening";
            requests = [];
            before = Native.Bridge.requests.length;
            for (let i = 0; i < 9; ++i) requests.push(send("settings", "GET", ""));
        });
        add(function() {
            check(Native.Bridge.requests.length === before && requests[8].readyState === 4 && requests[8].status === 503,
                  "opening runtime defers work and total request capacity remains eight");
            check(Native.RetentionTransport.waiting.length === 8, "only eight requests wait on readiness");
            requests[0].abort();
            Native.Bridge.runtimeState = "ready";
            check(Native.Bridge.requests.length === before + 7, "aborted opening request never dispatches after readiness");
            for (let i = 1; i < 8; ++i) ack(requests[i], status(false));
        });
        add(function() {
            for (let i = 1; i < 8; ++i) check(requests[i].readyState === 4 && requests[i].status === 200,
                                            "queued preference request completes after native readiness");
            noLeaks();
            request = Native.RetentionTransport.createRequest("settings"); request.open("GET");
            Native.Bridge.duringInvoke = function() { test.request.abort(); };
            before = callbacks; request.send("");
            check(request.aborted, "reentrant abort during Bridge admission is observed");
            noLeaks();
            Native.Bridge.throwOnInvoke = true;
            request = send("settings", "GET", "");
            Native.Bridge.throwOnInvoke = false;
        });
        add(function() {
            check(request.readyState === 4 && request.status === 503 && request.payload === null,
                  "Bridge admission exception becomes a finite unavailable response");
            noLeaks();
            orphan = send("settings", "GET", "");
            Native.Bridge.forcedId = orphan.requestId;
            request = send("settings", "GET", "");
            Native.Bridge.forcedId = "";
            ack(orphan, status(false));
        });
        add(function() {
            check(orphan.status === 200 && request.status === 503,
                  "duplicate native request ID cannot replace the first pending request");
            noLeaks();
            Native.Bridge.runtimeState = "error";
            request = send("settings", "GET", "");
        });
        add(function() {
            check(request.readyState === 4 && request.status === 503, "failed runtime cannot appear as a preference success");
            Native.Bridge.runtimeState = "ready";
            request = Native.SettingsTransport.createRequest("accounts"); request.open("GET");
            Native.Bridge.duringInvoke = function() { test.request.abort(); };
            request.send("");
            check(request.aborted && Object.keys(Native.SettingsTransport.pending).length === 0,
                  "actual ordinary SettingsTransport must not register a synchronously aborted request");
            Native.Bridge.throwOnInvoke = true;
            request = Native.SettingsTransport.createRequest("accounts"); request.open("GET"); request.send("");
            Native.Bridge.throwOnInvoke = false;
        });
        add(function() {
            check(request.readyState === 4 && request.status === 503 && Object.keys(Native.SettingsTransport.pending).length === 0,
                  "ordinary settings invocation exception is finite and leaves no pending entry");
            page = pageFactory.createObject(test);
            check(page !== null && page.pendingRequest !== null, "actual generated page starts native settings retrieval");
            ack(page.pendingRequest, status(true));
        });
        add(function() {
            check(page.loaded && page.runtimeTrusted && page.savedEnabled && page.editorEnabled,
                  "actual page preserves the old saved preference");
            check(!page.confirmedActive && page.runtime.retainedEvents === null && page.runtime.retainedMessages === null,
                  "saved true is not active capture and unavailable counts stay unknown");
            check(page.testToggleEnabled && !page.testSaveEnabled,
                  "actual switch binding permits disabling the old preference without inventing capture capability");
            check(page.validResponse(status(false)), "schema-2 native unavailable status passes the actual validator");
            for (const mutate of [function(v) { v.schema = 1; }, function(v) { v.extra = true; },
                function(v) { v.capabilities.enable = true; }, function(v) { v.capabilities.lookup = "true"; },
                function(v) { v.capabilities.capture = "true"; }, function(v) { v.capabilities.disable = "true"; },
                function(v) { v.runtime.retainedEvents = -1; }, function(v) { v.runtime.retainedMessages = 0.5; },
                function(v) { v.runtime.ready = true; v.capabilities.capture = true; v.runtime.error = null; },
                function(v) { v.runtime.applied = true; }, function(v) { v.runtime.enabled = true; },
                function(v) { v.runtime.extra = "synthetic"; }, function(v) { v.revision = "A".repeat(64); }]) {
                const value = status(false); mutate(value);
                check(!page.validResponse(value), "actual preference validator rejects invalid types/keys/dependencies/counts");
            }
            const future = status(false);
            future.capabilities.capture = true; future.capabilities.lookup = true;
            future.runtime.retainedEvents = 0; future.runtime.retainedMessages = 0;
            check(page.validResponse(future), "consistent future capabilities and known counts need no fake active status");
            page.editorEnabled = false;
            check(page.dirty && page.testSaveEnabled, "actual save binding allows removal of legacy enabled preference");
            page.save();
            check(page.pendingRequest !== null && last().value.operation === "retention.set" && last().value.enabled === false,
                  "actual page save submits only the authorized disable action");
            ack(page.pendingRequest, status(false));
        });
        add(function() {
            check(page.loaded && !page.savedEnabled && !page.editorEnabled && !page.testToggleEnabled && !page.testSaveEnabled,
                  "disabled native preference cannot be re-enabled while capture is unavailable");
            page.editorEnabled = true; before = Native.Bridge.requests.length; page.save();
            check(Native.Bridge.requests.length === before && page.pendingRequest === null,
                  "programmatic editor mutation cannot bypass the actual save capability guard");
            page.editorEnabled = false;
            page.request("GET", "", false); orphan = page.pendingRequest;
            awaitingDisposal = true; disposalPolls = 0;
            page.destroy(); page = null;
        });
        add(function() {
            check(orphan.aborted && orphan.payload === null,
                  "actual page destruction aborts pending callback delivery: " +
                  JSON.stringify({aborted: orphan.aborted, payloadCleared: orphan.payload === null, polls: disposalPolls}));
            ack(orphan, status(true)); noLeaks();
            controller = controllerFactory.createObject(test, {accountId: "1", chatId: "2", active: true});
            check(controller !== null, "actual generated marker controller creates without an archive service");
            before = Native.Bridge.requests.length;
            controller.register("3"); controller.startCycle();
            check(controller.pendingRequest === null && !controller.ready && Native.Bridge.requests.length === before,
                  "unavailable native lookup never launches a controller request or claims an empty archive");
            let pollCount = 0;
            for (const object of controller.resources) {
                if (object.interval === 2000) { ++pollCount; check(!object.running, "lookup polling stops when backend is unavailable"); }
            }
            check(pollCount === 1, "actual controller poll timer is inspected");
            controller.destroy(); controller = null;
        });
        add(function() { noLeaks(); });
    }

    Component.onCompleted: prepareSuite()
    Timer {
        interval: 1; repeat: true; running: true
        onTriggered: {
            try {
                if (!test.tasks.length) { stop(); Qt.exit(0); return; }
                // QObject deferred deletion can run after another ready timer.
                // Wait only for the actual aborted request, with a hard bound.
                if (test.awaitingDisposal && !test.orphan.aborted && test.disposalPolls < 100) {
                    ++test.disposalPolls;
                    return;
                }
                if (test.awaitingDisposal && test.orphan.aborted) test.awaitingDisposal = false;
                const action = test.tasks.shift(); action();
            } catch (error) {
                test.failure = String(error); console.error("retention-preferences-fixture:", test.failure);
                stop(); Qt.exit(1);
            }
        }
    }
}
