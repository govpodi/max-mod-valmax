import QtQuick
import QmlSingletonesModule
import ValMAX.Native 1.0 as ValMAXNative
Item {
    id: exportDialog
    property int checks: 0
    property string failure: ""
    property int step: 0
    property bool nativeApi2ExportAvailable: true
    property string exportMode: "api2-text"
    property string phase: "idle"
    property bool cancelRequested: false
    property bool api2StopPending: false
    property bool closedByUser: false
    property bool hasError: false
    property string statusText: ""
    property string failureText: ""
    property string jobId: ""
    property string jobToken: ""
    property string htmlPath: ""
    property string htmlFileURL: ""
    property int messageCount: 0
    property int attachmentCount: 0
    property int mediaCount: 0
    property int omissionCount: 0
    property int api2WindowsFetched: 0
    property int api2MediaProcessed: 0
    property int api2MediaTotal: 0
    property string api2Stage: "history"
    property bool api2Pacing: false
    property double api2PauseUntil: 0
    property int api2PauseRemainingMs: 0
    property double exportStartedAt: 0
    property int elapsedSeconds: 0
    property var pendingRequest: null
    property var admitted: null
    property var accountDependency: null
    property var api2HistoryContext: null
    property var seedContext: null
    readonly property var seedHistory: getContext(seedContext)
    property double waitStarted: 0
    property var nativeChatId: ({synthetic: "selected-chat"})
    property string chatIdText: "42"
    property string ownId: "73"
    property string chatTitle: "Синтетический чат"
    property var commonDependency: owner
    property bool scopeLive: true
    property bool viewAvailable: true
    property int seedCreates: 0
    property int viewCreates: 0
    property int scopeChecks: 0
    property int invalidations: 0
    property var boundChat: null
    property var factoryChat: null
    property var factoryMessageTime: null
    readonly property bool busy: ["api2-start", "api2-seed", "api2-next", "cancelling"].indexOf(phase) >= 0
    readonly property string firstId: "a".repeat(32)
    readonly property string firstToken: "b".repeat(64)
    QtObject { id: owner }
    QtObject { id: selectedView; property string valmaxSyntheticType: "history-view" }
    QtObject { id: seedHistoryObject; objectName: "synthetic-chat-history"; property string valmaxSyntheticType: "chat-history" }
    QtObject { id: seedMessage; property var chatId: exportDialog.nativeChatId; property var messageTimeId: ({synthetic: "seed-time"}) }
    QtObject { id: seedModel; property var items: ({count: 0, get: function(index) {
        return {model: {messageContext: {isNull: false, get: function() { return seedMessage; }}}};
    }}) }
    Timer { id: requestWatchdog; interval: 100000 }
    Timer { id: stepTimer; interval: 100000 }
    Timer { id: renderPoll; interval: 100000 }
    Timer { id: historyProofTimer; interval: 100000 }
    Timer { id: api2PaceTimer; interval: 500; onTriggered: {
        exportDialog.api2Pacing = false;
        exportDialog.api2PauseRemainingMs = 0;
        exportDialog.nextApi2Window();
    } }
    function check(value, message) {
        ++checks;
        if (!value) { failure = message; Qt.exit(1); return false; }
        return true;
    }
    function localFileURL(value) { return typeof value === "string" && /^file:\/\/\/[A-Za-z]:\//.test(value); }
    function plain(value, limit) { return String(value).slice(0, limit); }
    function validSelectedScope() { ++scopeChecks; return scopeLive; }
    function invalidateSelectedScope() {
        ++invalidations;
        if (phase === "api2-seed" || phase === "api2-next")
            stopApi2Export("Выбранный чат изменился. Остановка запрошена.", true);
    }
    function getContext(wrapper) { return wrapper && !wrapper.isNull && wrapper.get ? wrapper.get() : null; }
    function dependencyForAccount() {
        return {commonDependency: owner, historyPanelDependency: {openedChatsContext: {
            getOrCreateChatHistory: function(chat) {
                ++exportDialog.seedCreates;
                exportDialog.boundChat = chat;
                return {isNull: false, get: function() { return seedHistoryObject; }};
            }
        }, historyViewFactory: {
            createOnMessage: function(chat, time) {
                ++exportDialog.viewCreates;
                exportDialog.factoryChat = chat;
                exportDialog.factoryMessageTime = time;
                return exportDialog.viewAvailable ? {isNull: false, get: function() { return selectedView; }} : null;
            }
        }}};
    }
    function jobBody(extra) { return Object.assign({schema: 1, jobId: jobId, jobToken: jobToken}, extra || {}); }
    function request(route, body, callback) {
        if (pendingRequest) return false;
        const xhr = {aborted: false, abort: function() { this.aborted = true; }};
        pendingRequest = xhr;
        admitted = {route: route, body: body, callback: callback, xhr: xhr};
        return true;
    }
    function respond(value) {
        const active = admitted;
        admitted = null;
        pendingRequest = null;
        if (!active) throw new Error("missing_synthetic_request");
        try { active.callback(value); }
        catch (_) { fail("history_error", "Ответ задачи экспорта не прошёл проверку."); }
    }
    function fail(reason, text) {
        if (nativeApi2ExportAvailable && exportMode === "api2-text"
                && ["api2-start", "api2-seed", "api2-next"].indexOf(phase) >= 0)
            stopApi2Export(text + " Остановка запрошена; её завершение может произойти в фоне.", true);
    }
    function dispose() {}
    // GENERATED_API2_FUNCTIONS
    Component.onCompleted: {
        startApi2Export("https://synthetic.invalid/output");
        if (!check(admitted === null && viewCreates === 0, "folder must be a local file URL")) return;
        startApi2Export("file:///D:/synthetic-output");
        if (!check(admitted && admitted.route === "start" && admitted.body.accountId === ownId
                && admitted.body.chat.id === chatIdText && viewCreates === 0,
                "start uses only selected account/chat after explicit folder")) return;
        respond({schema: 1, jobId: firstId, jobToken: firstToken, state: "exporting",
            counts: {messages: 0, attachments: 0, ready: 0, unavailable: 0, mediaBytes: 0},
            downloadsPending: false});
        if (!check(phase === "api2-seed" && seedCreates === 1 && viewCreates === 0
                && seedContext !== null && api2HistoryContext === null && admitted === null
                && ValMAXNative.Bridge.beginCalls.length === 0 && ValMAXNative.Bridge.bindCalls.length === 0,
                "selected chat history context is a seed, not a bindable View")) return;
        seedModel.items.count = 1;
        api2SeedStep();
        if (!check(ValMAXNative.Bridge.beginCalls.length === 1 && ValMAXNative.Bridge.bindCalls.length === 1
                && ValMAXNative.Bridge.bindCalls[0].view === selectedView
                && !ValMAXNative.Bridge.isHistoryView(seedHistoryObject, firstId)
                && ValMAXNative.Bridge.isHistoryView(selectedView, firstId)
                && boundChat === nativeChatId && factoryChat === nativeChatId
                && factoryMessageTime === seedMessage.messageTimeId && viewCreates === 1
                && seedContext !== null && api2HistoryContext !== null,
                "only selected chat View from its seed message is bound before first API2 window")) return;
        if (!check(admitted === null && htmlFileURL === "", "no API2 read or output before bind")) return;
        step = 1;
    }
    Timer {
        interval: 25; repeat: true; running: exportDialog.step > 0
        onTriggered: {
            if (exportDialog.step === 1) {
                if (!exportDialog.check(exportDialog.admitted && exportDialog.admitted.route === "api2-next"
                        && Object.keys(exportDialog.admitted.body).sort().join(",") === "jobId,jobToken,schema"
                        && exportDialog.admitted.body.jobId === exportDialog.firstId
                        && exportDialog.admitted.body.jobToken === exportDialog.firstToken
                        && ValMAXNative.Bridge.boundView === selectedView,
                        "first window exact capability and live host view")) return;
                exportDialog.respond({schema: 1, jobId: exportDialog.firstId, state: "exporting",
                    counts: {messages: 100, attachments: 3, ready: 0, unavailable: 3, mediaBytes: 0},
                    downloadsPending: false, api2: {windowsFetched: 1, mediaProcessed: 0,
                        mediaTotal: 0, phase: "history", done: false}});
                if (!exportDialog.check(exportDialog.messageCount === 100 && exportDialog.api2WindowsFetched === 1
                        && exportDialog.htmlFileURL === "" && exportDialog.api2HistoryContext !== null,
                        "progress is counts only and retains selected view")) return;
                exportDialog.step = 2;
            } else if (exportDialog.step === 2) {
                if (exportDialog.api2Pacing) {
                    if (!exportDialog.check(exportDialog.admitted === null
                            && exportDialog.api2PauseRemainingMs === 500,
                            "pacing waits before admitting the next window")) return;
                    return;
                }
                if (!exportDialog.check(exportDialog.admitted && exportDialog.admitted.route === "api2-next"
                        && exportDialog.admitted.body.jobId === exportDialog.firstId,
                        "exactly one next window scheduled after receipt")) return;
                exportDialog.respond({schema: 1, jobId: exportDialog.firstId, state: "exporting",
                    counts: {messages: 101, attachments: 4, ready: 0, unavailable: 3, mediaBytes: 0},
                    downloadsPending: false, api2: {windowsFetched: 2, mediaProcessed: 0,
                        mediaTotal: 1, phase: "media", done: false}});
                if (!exportDialog.check(exportDialog.api2Stage === "media"
                        && exportDialog.api2MediaTotal === 1 && exportDialog.api2MediaProcessed === 0
                        && exportDialog.mediaCount === 0 && exportDialog.omissionCount === 3
                        && exportDialog.htmlFileURL === "", "media queue progress is visible before publication")) return;
                exportDialog.step = 21;
            } else if (exportDialog.step === 21) {
                if (exportDialog.api2Pacing) return;
                if (!exportDialog.check(exportDialog.admitted && exportDialog.admitted.route === "api2-next",
                        "one paced media task follows the history phase")) return;
                exportDialog.respond({schema: 1, jobId: exportDialog.firstId, state: "partial",
                    counts: {messages: 101, attachments: 4, ready: 1, unavailable: 3, mediaBytes: 15},
                    downloadsPending: false, api2: {windowsFetched: 2, mediaProcessed: 1,
                        mediaTotal: 1, phase: "done", done: true},
                    htmlPath: "D:\\synthetic-output\\index.html",
                    htmlFileURL: "file:///D:/synthetic-output/index.html"});
                if (!exportDialog.check(exportDialog.phase === "partial"
                        && exportDialog.htmlFileURL === "file:///D:/synthetic-output/index.html"
                        && exportDialog.mediaCount === 1 && exportDialog.api2MediaProcessed === 1
                        && ValMAXNative.Bridge.releaseCalls.length === 1
                        && exportDialog.api2HistoryContext === null && exportDialog.jobId === "",
                        "only terminal Partial publishes URL and releases host view")) return;
                exportDialog.startApi2Export("file:///D:/synthetic-output");
                exportDialog.respond({schema: 1, jobId: "c".repeat(32), jobToken: "d".repeat(64),
                    state: "exporting", counts: {messages: 0, attachments: 0, ready: 0,
                        unavailable: 0, mediaBytes: 0}, downloadsPending: false});
                exportDialog.api2SeedStep();
                exportDialog.step = 3;
            } else if (exportDialog.step === 3) {
                if (!exportDialog.check(exportDialog.admitted && exportDialog.admitted.route === "api2-next",
                        "second job reaches first window only after bind")) return;
                const old = exportDialog.admitted;
                exportDialog.stopApi2Export("Остановка запрошена; завершение может произойти в фоне.", false);
                if (!exportDialog.check(old.xhr.aborted && exportDialog.phase === "cancelling"
                        && exportDialog.api2StopPending && exportDialog.htmlFileURL === ""
                        && exportDialog.api2HistoryContext === null
                        && ValMAXNative.Bridge.releaseCalls.length === 2
                        && ValMAXNative.Bridge.requests[ValMAXNative.Bridge.requests.length - 1].value.operation === "export.cancel",
                        "stop aborts window, releases view and requests detached cancel without completion claim")) return;
                old.callback({schema: 1, jobId: "c".repeat(32), state: "partial",
                    counts: {messages: 1, attachments: 0, ready: 0, unavailable: 0, mediaBytes: 0},
                    downloadsPending: false, api2: {windowsFetched: 1, mediaProcessed: 0,
                        mediaTotal: 0, phase: "done", done: true},
                    htmlPath: "D:\\synthetic-output\\index.html", htmlFileURL: "file:///D:/synthetic-output/index.html"});
                if (!exportDialog.check(exportDialog.htmlFileURL === "" && exportDialog.phase === "cancelling",
                        "late receipt after stop cannot open archive")) return;
                exportDialog.phase = "idle"; exportDialog.api2StopPending = false; exportDialog.admitted = null;
                ValMAXNative.Bridge.allowBind = false;
                exportDialog.startApi2Export("file:///D:/synthetic-output");
                exportDialog.respond({schema: 1, jobId: "e".repeat(32), jobToken: "f".repeat(64),
                    state: "exporting", counts: {messages: 0, attachments: 0, ready: 0,
                        unavailable: 0, mediaBytes: 0}, downloadsPending: false});
                exportDialog.api2SeedStep();
                if (!exportDialog.check(exportDialog.phase === "cancelling" && exportDialog.admitted === null
                        && exportDialog.api2HistoryContext === null
                        && ValMAXNative.Bridge.requests[ValMAXNative.Bridge.requests.length - 1].value.operation === "export.cancel",
                        "bind rejection fails closed before API2 request")) return;
                exportDialog.step = 4;
            } else if (exportDialog.step === 4) {
                exportDialog.phase = "idle"; exportDialog.api2StopPending = false; exportDialog.admitted = null;
                ValMAXNative.Bridge.allowBind = true; exportDialog.viewAvailable = false;
                exportDialog.startApi2Export("file:///D:/synthetic-output");
                exportDialog.respond({schema: 1, jobId: "1".repeat(32), jobToken: "2".repeat(64),
                    state: "exporting", counts: {messages: 0, attachments: 0, ready: 0,
                        unavailable: 0, mediaBytes: 0}, downloadsPending: false});
                exportDialog.api2SeedStep();
                if (!exportDialog.check(exportDialog.phase === "cancelling" && exportDialog.admitted === null
                        && exportDialog.api2HistoryContext === null
                        && ValMAXNative.Bridge.requests[ValMAXNative.Bridge.requests.length - 1].value.operation === "export.cancel",
                        "missing native View fails closed before API2 request")) return;
                exportDialog.phase = "idle"; exportDialog.api2StopPending = false; exportDialog.admitted = null;
                exportDialog.viewAvailable = true;
                seedMessage.chatId = {synthetic: "other-chat"};
                const beginBeforeWrongChat = ValMAXNative.Bridge.beginCalls.length;
                exportDialog.startApi2Export("file:///D:/synthetic-output");
                exportDialog.respond({schema: 1, jobId: "7".repeat(32), jobToken: "8".repeat(64),
                    state: "exporting", counts: {messages: 0, attachments: 0, ready: 0,
                        unavailable: 0, mediaBytes: 0}, downloadsPending: false});
                exportDialog.api2SeedStep();
                if (!exportDialog.check(exportDialog.phase === "cancelling" && exportDialog.admitted === null
                        && ValMAXNative.Bridge.beginCalls.length === beginBeforeWrongChat
                        && ValMAXNative.Bridge.requests[ValMAXNative.Bridge.requests.length - 1].value.operation === "export.cancel",
                        "message from another chat cannot begin a history lease")) return;
                seedMessage.chatId = exportDialog.nativeChatId;
                exportDialog.step = 5;
            } else if (exportDialog.step === 5) {
                exportDialog.phase = "idle"; exportDialog.api2StopPending = false; exportDialog.admitted = null;
                exportDialog.viewAvailable = true;
                exportDialog.startApi2Export("file:///D:/synthetic-output");
                exportDialog.respond({schema: 1, jobId: "3".repeat(32), jobToken: "4".repeat(64),
                    state: "exporting", counts: {messages: 1, attachments: 0, ready: 0,
                        unavailable: 0, mediaBytes: 0}, downloadsPending: false});
                if (!exportDialog.check(exportDialog.phase === "cancelling" && exportDialog.admitted === null
                        && ValMAXNative.Bridge.requests[ValMAXNative.Bridge.requests.length - 1].value.operation === "export.cancel",
                        "malformed start with valid job token still receives detached cancel")) return;
                exportDialog.step = 6;
            } else if (exportDialog.step === 6) {
                exportDialog.phase = "idle"; exportDialog.api2StopPending = false; exportDialog.admitted = null;
                exportDialog.startApi2Export("file:///D:/synthetic-output");
                exportDialog.respond({schema: 1, jobId: "5".repeat(32), jobToken: "6".repeat(64),
                    state: "exporting", counts: {messages: 0, attachments: 0, ready: 0,
                        unavailable: 0, mediaBytes: 0}, downloadsPending: false});
                exportDialog.api2SeedStep();
                exportDialog.step = 7;
            } else if (exportDialog.step === 7) {
                if (!exportDialog.check(exportDialog.admitted && exportDialog.admitted.route === "api2-next",
                        "scoped job can begin its first window")) return;
                exportDialog.respond({schema: 1, jobId: "5".repeat(32), state: "exporting",
                    counts: {messages: 100, attachments: 0, ready: 0, unavailable: 0, mediaBytes: 0},
                    downloadsPending: false, api2: {windowsFetched: 1, mediaProcessed: 0,
                        mediaTotal: 0, phase: "history", done: false}});
                exportDialog.scopeLive = false;
                exportDialog.step = 8;
            } else if (exportDialog.step === 8) {
                if (exportDialog.api2Pacing) return;
                if (!exportDialog.check(exportDialog.admitted === null && exportDialog.invalidations === 1
                        && exportDialog.phase === "cancelling" && exportDialog.htmlFileURL === ""
                        && ValMAXNative.Bridge.requests[ValMAXNative.Bridge.requests.length - 1].value.operation === "export.cancel",
                        "scope change before subsequent window cancels instead of reading")) return;
                exportDialog.scopeLive = true; exportDialog.phase = "idle";
                exportDialog.api2StopPending = false; exportDialog.admitted = null;
                seedModel.items.count = 0;
                exportDialog.startApi2Export("file:///D:/synthetic-output");
                exportDialog.respond({schema: 1, jobId: "9".repeat(32), jobToken: "a".repeat(64),
                    state: "exporting", counts: {messages: 0, attachments: 0, ready: 0,
                        unavailable: 0, mediaBytes: 0}, downloadsPending: false});
                const beginBeforeNoSeed = ValMAXNative.Bridge.beginCalls.length;
                exportDialog.api2SeedStep();
                if (!exportDialog.check(exportDialog.phase === "api2-seed" && exportDialog.admitted === null
                        && ValMAXNative.Bridge.beginCalls.length === beginBeforeNoSeed,
                        "no seed row means no lease and no API2 history request")) return;
                exportDialog.waitStarted = Date.now() - 30001;
                exportDialog.api2SeedStep();
                if (!exportDialog.check(exportDialog.phase === "cancelling" && exportDialog.seedContext === null
                        && exportDialog.api2HistoryContext === null && exportDialog.admitted === null
                        && ValMAXNative.Bridge.beginCalls.length === beginBeforeNoSeed,
                        "bounded missing-seed timeout cancels without client or API2 history")) return;
                exportDialog.phase = "idle"; exportDialog.api2StopPending = false;
                seedModel.items.count = 1; ValMAXNative.Bridge.allowBegin = false;
                exportDialog.startApi2Export("file:///D:/synthetic-output");
                exportDialog.respond({schema: 1, jobId: "b".repeat(32), jobToken: "c".repeat(64),
                    state: "exporting", counts: {messages: 0, attachments: 0, ready: 0,
                        unavailable: 0, mediaBytes: 0}, downloadsPending: false});
                const viewsBeforeRejectedBegin = exportDialog.viewCreates;
                exportDialog.api2SeedStep();
                if (!exportDialog.check(exportDialog.phase === "cancelling" && exportDialog.admitted === null
                        && exportDialog.viewCreates === viewsBeforeRejectedBegin
                        && exportDialog.api2HistoryContext === null,
                        "begin rejection prevents View construction and API2 request")) return;
                exportDialog.phase = "idle"; exportDialog.api2StopPending = false;
                ValMAXNative.Bridge.allowBegin = true;
                exportDialog.startApi2Export("file:///D:/synthetic-output");
                exportDialog.respond({schema: 1, jobId: "d".repeat(32), jobToken: "e".repeat(64),
                    state: "exporting", counts: {messages: 0, attachments: 0, ready: 0,
                        unavailable: 0, mediaBytes: 0}, downloadsPending: false});
                exportDialog.api2SeedStep();
                if (!exportDialog.check(exportDialog.phase === "api2-next" && exportDialog.api2HistoryContext !== null
                        && exportDialog.admitted === null, "new View must bind before first request")) return;
                exportDialog.api2HistoryContext.isNull = true;
                exportDialog.nextApi2Window();
                if (!exportDialog.check(exportDialog.phase === "cancelling" && exportDialog.admitted === null
                        && exportDialog.api2HistoryContext === null
                        && ValMAXNative.Bridge.requests[ValMAXNative.Bridge.requests.length - 1].value.operation === "export.cancel",
                        "lost View stops before API2 history request")) return;
                exportDialog.step = 0;
                Qt.exit(0);
            }
        }
    }
}
