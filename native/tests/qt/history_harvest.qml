import QtQuick
import QtQml.Models
import ValMAX.Native 1.0 as Native

Item {
    id: test
    property int checks: 0
    property string failure: ""
    property var limits: ({maxPageMessages: 2, maxPageBytes: 2097152, maxMessages: 1000, maxAttachments: 1000})
    property var sentIds: ({})
    property int messageCount: 0
    property int attachmentCount: 0
    property int omissionCount: 0
    property int sequence: 0
    property string stopReason: ""
    property string phase: "history"
    property string jobId: "a".repeat(32)
    property string jobToken: "b".repeat(64)
    property string statusText: ""
    property var pendingMessages: []
    property var mediaQueue: []
    property var admitted: null
    property var acknowledgedIds: []
    property int nextMediaCalls: 0
    property string dtoAction: ""
    property string attachmentAction: ""
    property var dtoCalls: []
    property var ownedWrappers: []
    property var testClock: ({now: function() { return 100000; }})
    property var actualCollector: null
    property var actualHistoryStep: null
    property double clockNow: 100000
    property int clockStep: 0
    property string commitAction: ""
    property int modelDataChanges: 0
    property var history: fakeHistory
    property var pendingRequest: null
    property int pagesFetched: 0
    property bool awaitingHistory: false
    property bool sawHistoryChange: false
    property double waitStarted: 100000
    property double changedAt: 99000
    property int noProgress: 0
    property bool initialHistoryReady: false
    property string direction: "backward"
    property bool moreBackward: true
    property bool moreForward: true
    property bool backwardEnd: false
    property bool forwardEnd: false
    property double proofStarted: 0
    property int historyRevision: 0
    property int requestRevision: 0
    property int fetchCalls: 0
    property int proofCalls: 0
    property int finishCalls: 0

    ListModel { id: messages; dynamicRoles: true }
    ListModel { id: replacement; dynamicRoles: true }
    QtObject {
        id: fakeHistory
        function fetchBackward() { ++test.fetchCalls; }
        function fetchForward() { ++test.fetchCalls; }
    }
    Connections {
        target: messages
        function onDataChanged() { ++test.modelDataChanges; }
    }
    Native.HistoryModelCursor { id: historyCursor; roleName: "messageContext" }
    Connections {
        target: historyCursor
        function onStateChanged() {
            if (test.commitAction === "" || historyCursor.leasedCount !== 0) return;
            const action = test.commitAction;
            test.commitAction = "";
            if (action === "reset") historyCursor.reset();
            else if (action === "cancel") test.phase = "cancelled";
            else if (action === "job") test.jobToken = "c".repeat(64);
        }
    }
    Component {
        id: wrapperFactory
        QtObject {
            id: wrapper
            property string messageId: ""
            property string text: "synthetic"
            property bool isPending: false
            property bool isDelayed: false
            property bool ready: true
            function get() { return ready ? wrapper : null; }
        }
    }

    function check(value, message) {
        ++checks;
        if (!value) throw new Error(message);
    }
    function wrap(id, text, pending, delayed) {
        const wrapper = wrapperFactory.createObject(test, {
            messageId: String(id), text: text || "synthetic",
            isPending: pending === true, isDelayed: delayed === true
        });
        ownedWrappers.push(wrapper);
        return wrapper;
    }
    function add(id, text, pending, delayed) {
        const wrapper = wrap(id, text, pending, delayed);
        messages.append({messageContext: wrapper});
        return wrapper;
    }
    function reset() {
        historyCursor.model = null;
        messages.clear(); replacement.clear();
        for (const wrapper of ownedWrappers) wrapper.destroy();
        ownedWrappers = [];
        limits = {maxPageMessages: 2, maxPageBytes: 2097152, maxMessages: 1000, maxAttachments: 1000};
        sentIds = ({}); messageCount = 0; attachmentCount = 0; omissionCount = 0;
        sequence = 0; stopReason = ""; phase = "history"; statusText = "";
        pendingMessages = []; mediaQueue = []; admitted = null; acknowledgedIds = [];
        nextMediaCalls = 0; dtoAction = ""; attachmentAction = ""; dtoCalls = [];
        commitAction = ""; jobId = "a".repeat(32); jobToken = "b".repeat(64);
        modelDataChanges = 0;
        pendingRequest = null; pagesFetched = 0; awaitingHistory = false; sawHistoryChange = false;
        waitStarted = 100000; changedAt = 99000; noProgress = 0; initialHistoryReady = false;
        direction = "backward"; moreBackward = true; moreForward = true;
        backwardEnd = false; forwardEnd = false; proofStarted = 0; historyRevision = 0; requestRevision = 0;
        fetchCalls = 0; proofCalls = 0; finishCalls = 0; clockNow = 100000;
        testClock.now = function() { return test.clockNow; };
    }
    function bind() {
        historyCursor.model = messages;
        check(historyCursor.error === "" && historyCursor.rowCount === messages.count,
              "real C++ cursor binds the QML ListModel");
    }
    function getContext(wrapper) { return wrapper && wrapper.get ? wrapper.get() : null; }
    function canonicalId(value, kind) { return typeof value === "string" ? value : ""; }
    function utf8Length(value) {
        let bytes = 0;
        for (const ch of value) {
            const code = ch.codePointAt(0);
            bytes += code <= 0x7f ? 1 : code <= 0x7ff ? 2 : code <= 0xffff ? 3 : 4;
        }
        return bytes;
    }
    function messageDTO(context) {
        const message = getContext(context);
        dtoCalls.push(message.messageId);
        if (dtoAction === "throw") throw new Error("synthetic_dto_failure");
        if (dtoAction === "null") return null;
        if (dtoAction === "replace") {
            dtoAction = "";
            historyCursor.model = replacement;
        }
        if (dtoAction.indexOf("arm-") === 0) {
            commitAction = dtoAction.slice(4);
            dtoAction = "";
        }
        return {id: message.messageId, text: message.text, attachments: []};
    }
    function prepareAttachments(context, dto) {
        if (attachmentAction === "throw") throw new Error("synthetic_attachment_failure");
        if (attachmentAction === "two") dto.attachments = [{id: "a"}, {id: "b"}];
    }
    function jobBody(extra) { return Object.assign({schema: 1, jobId: jobId, jobToken: jobToken}, extra); }
    function request(route, body, callback) {
        check(admitted === null && route === "page", "only one synthetic native page ACK can be outstanding");
        admitted = {body: body, callback: callback};
    }
    function nextMedia() { ++nextMediaCalls; phase = "history"; }
    function pollHistoryProof() { ++proofCalls; }
    function finishExport(reason) { ++finishCalls; stopReason = reason; phase = "partial"; }
    function ack() {
        check(admitted !== null, "generated collector submits a page to the native writer adapter");
        const page = admitted;
        admitted = null;
        for (const record of page.body.messages) acknowledgedIds.push(record.id);
        page.callback({sequence: page.body.sequence, media: []});
    }
    function runUntilDrained() {
        let turns = 0;
        while (historyCursor.pendingCount > 0 && turns++ < 100) {
            const worked = harvestPage();
            check(worked, "queued eligible rows make progress before a new history fetch");
            if (admitted) ack();
        }
        check(turns < 100 && historyCursor.pendingCount === 0 && historyCursor.leasedCount === 0,
              "all local rows drained without a leaked native lease");
    }

    function collectorWithClock(Date) {
        // The actual generated function remains byte for byte unchanged.
        // A local Date parameter makes its budget clock deterministic without
        // changing Qt's shared built-in objects or relying on intrinsic caches.
    // GENERATED_HARVEST_FUNCTION
        return harvestPage;
    }
    function harvestPage() { return actualCollector(); }
    function callerWithClock(Date) {
    // GENERATED_HISTORY_STEP
        return historyStep;
    }
    function historyStep() { return actualHistoryStep(); }

    function scenarios() {
        reset();
        const first = add("1");
        bind();
        let lease = historyCursor.reserve(1);
        check(lease.ok && lease.contexts.length === 1 && lease.contexts[0].get() === first,
              "QVariant role preserves the QObject wrapper and get identity in real QML");
        check(historyCursor.release(lease.ticket), "fixture returns inspected row to the real cursor");

        // A deterministic clock isolates the byte boundary from the 2 ms yield.
        reset();
        limits.maxPageBytes = 2000;
        add("11", "x".repeat(600)); add("12", "y".repeat(600));
        bind();
        const dtoBytes = utf8Length(JSON.stringify({id: "11", text: "x".repeat(600), attachments: []})) + 1;
        check(dtoBytes + 512 <= limits.maxPageBytes && 512 + dtoBytes * 2 > limits.maxPageBytes - 1024,
              "fixture reaches the generated multi-record byte boundary");
        check(harvestPage() && admitted.body.messages.length === 1 && admitted.body.messages[0].id === "11",
              "first byte-limited page contains exactly its consumed prefix");
        check(historyCursor.leasedCount === 0 && historyCursor.pendingCount === 1 && !sentIds["11"],
              "unconsumed byte-boundary row is queued and sent IDs await native ACK");
        ack();
        check(sequence === 1 && messageCount === 1 && sentIds["11"], "native ACK commits the first exported DTO");
        check(harvestPage() && admitted.body.messages[0].id === "12", "next page recovers the unconsumed boundary row");
        ack();
        check(acknowledgedIds.join(",") === "11,12" && historyCursor.pendingCount === 0,
              "byte boundary neither loses nor duplicates a message");
        check(!harvestPage() && stopReason === "" && phase === "history",
              "empty local queue does not assert successful server EOF");

        reset();
        add("21"); add("22"); add("23"); add("24"); add("25");
        sentIds = {"21": true, "22": true, "23": true, "24": true};
        bind();
        check(harvestPage() && admitted === null && historyCursor.pendingCount === 3,
              "duplicate-only first batch reports work so caller must drain before paging");
        check(harvestPage() && admitted === null && historyCursor.pendingCount === 1,
              "duplicate-only second batch cannot hide a queued fresh row");
        check(harvestPage() && admitted.body.messages.length === 1 && admitted.body.messages[0].id === "25",
              "fresh row behind duplicate batches reaches the writer");
        ack();
        check(dtoCalls.join(",") === "25", "duplicate IDs avoid DTO and attachment reconstruction");

        reset();
        const initializing = add("28"); initializing.ready = false; bind();
        check(!harvestPage() && admitted === null && historyCursor.pendingCount === 1 && historyCursor.leasedCount === 0,
              "null wrapper get retains the uninitialized row instead of consuming it");
        check(modelDataChanges === 0 && dtoCalls.length === 0, "null wrapper was not passed to DTO extraction");
        initializing.ready = true;
        check(modelDataChanges === 0 && historyCursor.pendingCount === 1,
              "nested wrapper initialization needs no QAbstractItemModel dataChanged notification");
        runUntilDrained();
        check(acknowledgedIds.join(",") === "28", "initialized wrapper exports from its retained native position");

        reset();
        add("31", "pending", true, false); add("32", "delayed", false, true);
        bind();
        check(!harvestPage() && admitted === null && historyCursor.leasedCount === 0,
              "pending and delayed rows are skipped and their lease is consumed");
        check(sentIds["pending:31"] && sentIds["pending:32"] && !sentIds["31"] && !sentIds["32"],
              "pending IDs do not suppress a later sent version");
        messages.setProperty(0, "messageContext", wrap("31", "now sent", false, false));
        messages.setProperty(1, "messageContext", wrap("32", "now sent too", false, false));
        check(historyCursor.pendingCount === 2, "real QML ListModel dataChanged requeues sent versions");
        runUntilDrained();
        check(acknowledgedIds.join(",") === "31,32", "both changed rows export after pending/delayed flags clear");

        reset(); add("41"); bind(); dtoAction = "null";
        check(!harvestPage() && admitted === null && historyCursor.leasedCount === 0 && historyCursor.pendingCount === 1,
              "null DTO leaves its unconsumed row retryable without leaking a lease");
        dtoAction = ""; runUntilDrained();
        check(acknowledgedIds.join(",") === "41", "row survives a transient null DTO");

        for (const kind of ["dto", "attachment"]) {
            reset(); add("51"); add("52"); bind();
            dtoAction = kind === "dto" ? "throw" : "";
            attachmentAction = kind === "attachment" ? "throw" : "";
            let error = "";
            try { harvestPage(); } catch (caught) { error = String(caught); }
            check(error.indexOf("synthetic_") >= 0 && admitted === null && historyCursor.leasedCount === 0,
                  kind + " exception propagates and releases the native lease");
            check(historyCursor.pendingCount === 2, kind + " exception preserves all unacknowledged rows");
            dtoAction = ""; attachmentAction = ""; runUntilDrained();
            check(acknowledgedIds.join(",") === "51,52", kind + " exception recovery loses no message");
        }

        reset(); add("61"); bind();
        replacement.append({messageContext: wrap("62")}); dtoAction = "replace";
        let swapError = "";
        try { harvestPage(); } catch (caught) { swapError = String(caught); }
        check(swapError.indexOf("history_cursor_changed") >= 0 && admitted === null,
              "model replacement invalidates the generated function's old ticket");
        check(historyCursor.model === replacement && historyCursor.pendingCount === 1 && historyCursor.leasedCount === 0,
              "cleanup cannot consume or release rows of the replacement model");
        runUntilDrained();
        check(acknowledgedIds.join(",") === "62", "replacement model starts with its own valid queue");

        for (const action of ["reset", "cancel", "job"]) {
            reset(); add("65"); bind(); dtoAction = "arm-" + action;
            let reentrantError = "";
            try { harvestPage(); } catch (caught) { reentrantError = String(caught); }
            check(reentrantError.indexOf("history_cursor_changed") >= 0 && admitted === null,
                  "synchronous commit notification " + action + " prevents stale writer submission");
            check(historyCursor.leasedCount === 0 && !sentIds["65"] && messageCount === 0,
                  "commit notification " + action + " leaves no lease or false acknowledged message");
            if (action === "reset") {
                check(historyCursor.pendingCount === 1, "synchronous reset retains the row in its new generation");
                runUntilDrained();
            } else {
                check(action === "cancel" ? phase === "cancelled" : jobToken === "c".repeat(64),
                      "old collector does not overwrite changed job or cancellation state");
            }
        }

        reset(); add("71"); bind();
        const stale = historyCursor.reserve(1);
        historyCursor.reset();
        const fresh = historyCursor.reserve(1);
        check(stale.ok && fresh.ok && stale.ticket !== fresh.ticket, "real native ticket changes after reset");
        check(!historyCursor.commit(stale.ticket, 1) && !historyCursor.release(stale.ticket),
              "stale ticket cannot acknowledge or release the current native lease");
        check(historyCursor.leasedCount === 1 && historyCursor.release(fresh.ticket), "fresh lease survives stale cleanup");
        runUntilDrained();

        reset(); add("81"); add("82"); add("83"); limits.maxPageMessages = 3; bind();
        clockStep = 0;
        testClock.now = function() { return 100000 + test.clockStep++ * 2; };
        const timeWorked = harvestPage();
        check(timeWorked && admitted.body.messages.length === 1 && historyCursor.pendingCount === 2,
              "2 ms yielding returns the remaining prefix to the native queue: " +
              JSON.stringify({worked: timeWorked, messages: admitted ? admitted.body.messages.length : -1,
                              pending: historyCursor.pendingCount, clockCalls: clockStep}));
        check(clockStep === 2, "actual generated budget check uses the controlled lexical clock");
        testClock.now = function() { return 100000; };
        ack(); runUntilDrained();
        check(acknowledgedIds.join(",") === "81,82,83", "time budget yields without losing a row");

        reset(); add("91", "z".repeat(4096)); bind(); limits.maxPageBytes = 2000;
        check(!harvestPage() && stopReason === "message_too_large" && historyCursor.pendingCount === 1 && historyCursor.leasedCount === 0,
              "single oversize DTO fails explicitly and does not leak a cursor lease");
        check(admitted === null, "oversize record never reaches writer as a successful page");
        historyCursor.model = null;
        check(historyCursor.pendingCount === 0 && historyCursor.leasedCount === 0,
              "explicit detach clears the owned native queue on cancellation");

        reset();
        const callerInitializing = add("101"); callerInitializing.ready = false; bind();
        clockNow = 100600;
        historyStep();
        check(historyCursor.pendingCount === 1 && historyCursor.leasedCount === 0 && admitted === null,
              "actual historyStep retains an uninitialized local row after 500 ms");
        check(fetchCalls === 0 && proofCalls === 0 && finishCalls === 0,
              "readiness wait cannot trigger another page fetch or history proof");
        clockNow = 129999;
        historyStep();
        check(historyCursor.pendingCount === 1 && fetchCalls === 0 && proofCalls === 0 && finishCalls === 0,
              "actual caller keeps a bounded readiness wait below its existing 30 second timeout");
        callerInitializing.ready = true;
        check(modelDataChanges === 0, "caller wrapper becomes ready without model dataChanged");
        historyStep();
        check(admitted !== null && admitted.body.messages[0].id === "101" && fetchCalls === 0 && proofCalls === 0,
              "ready local row reaches the writer before any paging attempt");
        ack();
        historyStep();
        check(historyCursor.pendingCount === 0 && fetchCalls === 1 && pagesFetched === 1 && awaitingHistory,
              "actual caller permits paging only after the local queue is empty");
        moreBackward = false; moreForward = false; awaitingHistory = false;
        changedAt = clockNow - 1000;
        historyStep();
        check(backwardEnd && direction === "forward" && proofCalls === 0,
              "empty local queue may advance direction without claiming server completion");
        historyStep();
        check(forwardEnd && phase === "history-proof" && proofCalls === 1 && finishCalls === 0,
              "empty queue permits a separate history-proof request rather than a complete-export claim");

        reset(); add("102"); bind(); dtoAction = "null"; clockNow = 100600;
        historyStep();
        check(historyCursor.pendingCount === 1 && historyCursor.leasedCount === 0 && fetchCalls === 0 && proofCalls === 0,
              "actual caller also waits for an unready DTO without fetching past its row");
        dtoAction = ""; clockNow = 100700;
        historyStep(); ack();
        check(acknowledgedIds.join(",") === "102" && fetchCalls === 0 && proofCalls === 0,
              "DTO readiness retry exports the retained position");

        reset(); const neverReady = add("103"); neverReady.ready = false; bind(); clockNow = 130001;
        historyStep();
        check(finishCalls === 1 && stopReason === "timeout" && phase === "partial",
              "actual caller bounds permanent readiness wait with its honest timeout result");
        check(fetchCalls === 0 && proofCalls === 0 && admitted === null && historyCursor.leasedCount === 0,
              "readiness timeout cannot fetch or report complete history");

        reset(); add("104"); bind(); dtoAction = "arm-cancel"; clockNow = 100600;
        let callerCancelled = "";
        try { historyStep(); } catch (caught) { callerCancelled = String(caught); }
        check(callerCancelled.indexOf("history_cursor_changed") >= 0 && phase === "cancelled",
              "actual caller propagates synchronous cancellation during cursor commit");
        check(admitted === null && fetchCalls === 0 && proofCalls === 0 && historyCursor.leasedCount === 0,
              "cancelled caller submits no stale page or pagination request");
    }

    Timer {
        interval: 1; running: true
        onTriggered: {
            try {
                test.actualCollector = test.collectorWithClock(test.testClock);
                test.actualHistoryStep = test.callerWithClock(test.testClock);
                test.scenarios();
                Qt.exit(0);
            } catch (error) {
                test.failure = String(error);
                console.error("history-harvest-fixture:", test.failure);
                Qt.exit(1);
            }
        }
    }
}
