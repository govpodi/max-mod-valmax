import QtQuick
import ValMAX.Native 1.0 as Native
Item {
    id: test
    property int checks: 0
    property int notifications: 0
    property string failure: ""
    PrivacyController { id: page }
    Connections { target: Native.Bridge; function onPrivacyReportChanged() { ++test.notifications; } }
    function check(value, message) {
        ++checks;
        if (!value) { failure = message; console.error("privacy_controller_failed:" + message); Qt.exit(1); return false; }
        return true;
    }
    function good(serviceVerified, serviceError) {
        if (typeof serviceError === "undefined") serviceError = serviceVerified === true ? "" : "service_not_running";
        var ids = ["tracer_main", "tracer_service", "ui_statistics", "mytracker", "log_masking", "error_logs"];
        var rows = [];
        for (var i = 0; i < ids.length; ++i) rows.push({ id: ids[i], state: (i === 1 || i === 3) && !serviceVerified ? "verified_package" : "verified_loaded" });
        return { schema: 1, state: "verified", buildVerified: true, currentProcessVerified: true,
            serviceProcessVerified: serviceVerified === true, serviceError: serviceError, error: "", groups: rows };
    }
    Timer {
        interval: 800; running: true
        onTriggered: {
            if (!test.check(Native.Bridge.runtimeState === "unconfigured", "privacy does not depend on Rust profile configuration")) return;
            if (!test.check(test.notifications > 0, "native verifier result signal reached QML")) return;
            if (!test.check(page.report.error === "io_error" && page.report.state === "unavailable", "wrong host cannot claim verified MAX")) return;
            if (!test.check(!page.verified && !page.report.currentProcessVerified && !page.report.serviceProcessVerified, "unavailable never green")) return;
            if (!test.check(page.report.groups.length === 6 && page.groupState("tracer_main") === "unavailable", "six unavailable groups")) return;
            if (!test.check(page.groupState("unknown") === "unavailable", "unknown group unavailable")) return;
            if (!test.check(typeof Native.Bridge.configureRuntime === "undefined" && typeof Native.Bridge.refreshPrivacy === "undefined", "no QML path, role or reverify control")) return;
            if (!test.check(typeof page.report.observation === "undefined" && typeof page.report.manifest_sha256 === "undefined", "no packet counts or hashes in UI report")) return;
            if (!test.check(page.validateReport(test.good()), "exact valid report accepted")) return;
            if (!test.check(page.validateReport(test.good(true)), "service proof and six loaded groups accepted")) return;
            for (var i = 0; i < 14; ++i) {
                var value = test.good();
                if (i === 0) value.schema = 2;
                if (i === 1) value.state = "unavailable";
                if (i === 2) value.buildVerified = false;
                if (i === 3) value.currentProcessVerified = false;
                if (i === 4) value.serviceProcessVerified = true;
                if (i === 5) value.error = "memory_mismatch";
                if (i === 6) value.groups.pop();
                if (i === 7) value.groups[1].state = "verified_loaded";
                if (i === 8) value.groups[2].id = "unknown";
                if (i === 9) value.groups[5].state = "unavailable";
                if (i === 10) value = null;
                if (i === 11) value.groups[0] = null;
                if (i === 12) value.serviceError = "unknown";
                if (i === 13) value.serviceError = undefined;
                if (!test.check(!page.validateReport(value), "partial or malformed report rejected " + i)) return;
            }
            var serviceCases = [
                {code: "service_not_running", summary: "не найдена", row: "служба не найдена"},
                {code: "module_not_loaded", summary: "не загружен", row: "модуль службы не загружен"},
                {code: "io_error", summary: "Не удалось получить доступ", row: "проверка службы недоступна"},
                {code: "memory_mismatch", summary: "не совпал", row: "код службы не совпал"}
            ];
            for (var j = 0; j < serviceCases.length; ++j) {
                var scenario = serviceCases[j];
                Native.Bridge.privacyReport = test.good(false, scenario.code);
                if (!test.check(page.verified && !page.serviceVerified && page.groupState("tracer_service") === "verified_package",
                                "service failure stays package-only " + scenario.code)) return;
                if (!test.check(page.verificationText.indexOf(scenario.summary) >= 0
                                && page.stateLabel("verified_package").indexOf(scenario.row) >= 0,
                                "service failure has precise message " + scenario.code)) return;
                if (!test.check(String(page.verificationColor) === String(page.warningColor),
                                "service failure is yellow " + scenario.code)) return;
            }
            Native.Bridge.privacyReport = test.good(true);
            if (!test.check(page.verified && page.serviceVerified && page.groupState("tracer_service") === "verified_loaded",
                            "service success shows loaded proof")) return;
            if (!test.check(page.verificationText.indexOf("проверены в работающих процессах") >= 0
                            && String(page.verificationColor) === String(page.successColor),
                            "service success is green")) return;
            if (!test.check(page.stateLabel("unknown") === "Проверка недоступна", "unknown state label")) return;
            Qt.exit(0);
        }
    }
}
