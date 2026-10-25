import QtQuick
import ValMAX.Native 1.0 as Native
Item {
    id: test
    property int checks: 0
    property string failure: ""
    function check(value, message) {
        ++checks;
        if (!value) { failure = message; Qt.exit(1); return false; }
        return true;
    }
    Component.onCompleted: {
        if (!check(Native.Bridge.servicesAvailable, "core loaded without configured profile")) return;
        if (!check(!Native.Bridge.runtimeAvailable, "no ready runtime before valid host configuration")) return;
        if (!check(typeof Native.Bridge.configureRuntime === "undefined", "no QML path configuration")) return;
        const request = Native.SettingsTransport.createRequest("accounts");
        request.onreadystatechange = function() {
            if (!test.check(request.readyState === 4 && request.status === 503, "unavailable propagated")) return;
            if (!test.check(Native.Bridge.runtimeState === "@STATE@", "finite unavailable state")) return;
            if (!test.check(Object.keys(Native.SettingsTransport.pending).length === 0 && Native.SettingsTransport.waiting.length === 0, "request references released")) return;
            Qt.exit(0);
        };
        request.open("GET"); request.send("");
    }
}
