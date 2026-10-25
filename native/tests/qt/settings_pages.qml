import QtQuick
import ValMAX.Native 1.0 as Native

Item {
    id: test
    property int checks: 0
    property string failure: ""
    property string phase: "@PHASE@"
    property int step: 0
    property string staleRevision: ""
    property int cancelledCallbacks: 0
    property int overflow: 0
    property int completed: 0
    property bool startedOverflow: false
    OptimizationPage { id: optimization }
    GhostPage { id: ghost }
    AccountsPage { id: accounts }
    function check(value, message) {
        ++checks;
        if (!value) { failure = message; Qt.exit(1); return false; }
        return true;
    }
    function finish() { Qt.exit(0); }
    Timer {
        interval: 5; running: true; repeat: true
        onTriggered: {
            if (test.failure.length) return;
            if (test.step === 0) {
                if (!optimization.loaded || !ghost.loaded || !accounts.loaded) return;
                if (!test.check(Native.Bridge.runtimeAvailable && Native.Bridge.runtimeState === "ready", "runtime opened")) return;
                if (!test.check(typeof Native.Bridge.configureRuntime === "undefined", "QML cannot set store paths")) return;
                if (!test.check(optimization.validResponse({schema: 1, available: optimization.available, revision: optimization.revision, settings: optimization.savedSettings, appliedSettings: optimization.appliedSettings, restartRequired: optimization.restartRequired}), "actual optimization page accepted Rust response")) return;
                if (!test.check(ghost.runtimeTrusted && !ghost.canConfigure && !ghost.confirmedActive && ghost.runtime.state === "unavailable", "ghost waits for hook acknowledgement")) return;
                if (!test.check(Native.Bridge.ghostRuntimeState === "unavailable", "unsupported native host never acknowledges hooks")) return;
                if (!test.check(!accounts.hasError && accounts.validResponse({schema: 1, revision: accounts.revision, accounts: accounts.accounts}), "actual account page accepted Rust response")) return;
                if (test.phase === "write") {
                    if (!test.check(optimization.canConfigure && optimization.editorSettings.enabled, "initial optimization configuration")) return;
                    test.staleRevision = optimization.revision;
                    optimization.setFlag("smoothWheel", false); optimization.setFlag("enabled", false); optimization.save();
                    accounts.editorName = "Работа 🛠"; accounts.editorColor = "#8b5cf6"; accounts.dirty = true; accounts.save();
                    // Store roundtrip only. The page's visible save action stays
                    // disabled until native hooks acknowledge their policy.
                    ghost.request("POST", JSON.stringify({schema: 1, revision: ghost.revision,
                        settings: {enabled: false, hideTyping: false, hideRead: true, hideOnline: false, hideStories: false}}), false);
                    test.step = 1;
                } else {
                    if (!test.check(!optimization.editorSettings.enabled && !optimization.editorSettings.smoothWheel, "optimization settings survived process restart")) return;
                    if (!test.check(accounts.editorName === "Работа 🛠" && accounts.editorColor === "#8b5cf6", "Unicode account preferences survived process restart")) return;
                    if (!test.check(!ghost.savedSettings.hideTyping && !ghost.confirmedActive, "ghost preference survived without claiming protection")) return;
                    const existing = test.phase === "existing";
                    if (!test.check(optimization.restartRequired === existing && optimization.appliedSettings.enabled === existing, "active settings fixed for existing client and refreshed only on restart")) return;
                    test.finish();
                }
            } else if (test.step === 1) {
                if (optimization.pendingRequest !== null || ghost.pendingRequest !== null || accounts.pendingRequest !== null) return;
                if (!test.check(!optimization.hasError && !optimization.dirty && optimization.clientNeedsRestart, "optimization save and restart indication")) return;
                if (!test.check(!accounts.hasError && !accounts.dirty && accounts.editorName === "Работа 🛠", "account page save")) return;
                if (!test.check(!ghost.hasError && !ghost.savedSettings.hideTyping && !ghost.canConfigure, "ghost store save stays unavailable")) return;
                optimization.request("POST", JSON.stringify({schema: 1, revision: test.staleRevision, settings: optimization.savedSettings}), false);
                test.step = 2;
            } else if (test.step === 2) {
                if (optimization.pendingRequest !== null) return;
                if (!test.check(optimization.needsRefresh && optimization.hasError && !optimization.trusted, "revision conflict reaches actual page")) return;
                optimization.request("GET", "", false);
                const cancelled = Native.SettingsTransport.createRequest("accounts");
                cancelled.onreadystatechange = function() { ++test.cancelledCallbacks; };
                cancelled.open("GET"); cancelled.send(""); cancelled.abort();
                test.step = 3;
            } else if (test.step === 3) {
                if (optimization.pendingRequest !== null || Native.Bridge.pendingOperations !== 0) return;
                if (!test.check(!optimization.needsRefresh && optimization.trusted && !optimization.hasError, "refresh recovers revision conflict")) return;
                if (!test.check(test.cancelledCallbacks === 0 && Object.keys(Native.SettingsTransport.pending).length === 0, "aborted requests cannot update closed page")) return;
                if (!test.startedOverflow) {
                    test.startedOverflow = true;
                    for (let i = 0; i < 10; ++i) {
                        const request = Native.SettingsTransport.createRequest("accounts");
                        request.onreadystatechange = function() {
                            if (request.readyState === 4) { ++test.completed; if (request.status === 503) ++test.overflow; }
                        };
                        request.open("GET"); request.send("");
                    }
                    test.step = 4;
                }
            } else if (test.step === 4 && test.completed === 10) {
                if (!test.check(test.overflow === 2, "bounded native transport queue")) return;
                if (!test.check(Object.keys(Native.SettingsTransport.pending).length === 0, "completed requests released")) return;
                test.finish();
            }
        }
    }
}
