import QtQuick
import ValMAX.Native 1.0 as Native
Item {
    id: test
    property int checks: 0
    property string failure: ""
    property string phase: "@PHASE@"
    property string outputUrl: "@OUTPUT_URL@"
    property int step: 0
    property bool settingDone: false
    property int cancelled: 0
    SupportPage { id: support }
    function check(value, message) {
        ++checks;
        if (!value) { failure = message; Qt.exit(1); return false; }
        return true;
    }
    function setSmoothWheel(enabled) {
        settingDone = false;
        const get = Native.SettingsTransport.createRequest("optimization");
        get.onreadystatechange = function() {
            if (!test.check(get.status === 200, "read synthetic optimization")) return;
            const value = JSON.parse(get.responseText); value.settings.smoothWheel = enabled;
            const post = Native.SettingsTransport.createRequest("optimization");
            post.onreadystatechange = function() {
                if (!test.check(post.status === 200, "change synthetic optimization")) return;
                test.settingDone = true;
            };
            post.open("POST"); post.send(JSON.stringify({schema: 1, revision: value.revision, settings: value.settings}));
        };
        get.open("GET"); get.send();
    }
    Timer {
        interval: 5; running: true; repeat: true
        onTriggered: {
            if (test.failure.length || support.pendingRequest !== null) return;
            if (test.step === 0) {
                if (test.phase === "unconfigured") {
                    if (!test.check(support.hasError && support.report === null && !Native.Bridge.runtimeAvailable, "unconfigured native host exposes no report")) return;
                    if (!test.check(Object.keys(Native.SupportTransport.pending).length === 0, "failed request released")) return;
                    Qt.exit(0); return;
                }
                if (!test.check(Native.Bridge.runtimeAvailable && !support.hasError && support.report !== null, "actual support controller receives native report")) return;
                if (!test.check(support.report.schema === 2 && support.report.runtime.kind === "native" && support.report.runtime.core === "0.1.0", "native schema and actual core version")) return;
                if (!test.check(support.report.versions.valmax === null && support.report.versions.windows === null && support.report.runtime.qt === null, "unknown versions remain null")) return;
                if (!test.check(support.report.startup === null && support.report.counters.observation.seconds === null && support.report.errors.hook_failed === null, "no fabricated observer data")) return;
                if (!test.check(support.report.flags.verified === false, "plain synthetic host has unavailable privacy verification")) return;
                const raw = JSON.stringify(support.report);
                if (!test.check(raw.indexOf("accountId") < 0 && raw.indexOf("file://") < 0 && raw.indexOf("python") < 0 && raw.indexOf("groups") < 0, "report contains allowlisted fields only")) return;
                if (test.phase === "preview") { Qt.exit(0); return; }
                if (!test.check(support.report.flags.optimization.smoothWheel === true, "initial preview matches initial settings")) return;
                test.setSmoothWheel(false); test.step = 1;
            } else if (test.step === 1 && test.settingDone) {
                if (!test.check(support.report.flags.optimization.smoothWheel === true, "displayed preview remains earlier snapshot")) return;
                support.request("POST", test.outputUrl); test.step = 2;
            } else if (test.step === 2) {
                if (!test.check(!support.hasError && support.statusText.indexOf("Отчёт сохранён") === 0, "explicit chosen new file saved through actual controller")) return;
                test.setSmoothWheel(true); test.step = 3;
            } else if (test.step === 3 && test.settingDone) {
                support.request("POST", test.outputUrl); test.step = 4;
            } else if (test.step === 4) {
                if (!test.check(support.hasError && support.statusText.indexOf("Файл уже существует") === 0, "existing file cannot be overwritten with newer report")) return;
                support.request("POST", "https://example.invalid/report.json"); test.step = 5;
            } else if (test.step === 5) {
                if (!test.check(support.hasError && support.statusText.indexOf("Выбери новый локальный файл") === 0, "unsafe file maps to local selection advice")) return;
                support.request("GET", ""); test.step = 6;
            } else if (test.step === 6) {
                if (!test.check(!support.hasError && support.report.flags.optimization.smoothWheel === true, "refresh uses latest settings after export")) return;
                const request = Native.SupportTransport.createRequest();
                request.onreadystatechange = function() { ++test.cancelled; };
                request.open("GET"); request.send(); request.abort(); test.step = 7;
            } else if (test.step === 7 && Native.Bridge.pendingOperations === 0) {
                if (!test.check(test.cancelled === 0 && Object.keys(Native.SupportTransport.pending).length === 0, "aborted preview suppresses late delivery")) return;
                Qt.exit(0);
            }
        }
    }
}
