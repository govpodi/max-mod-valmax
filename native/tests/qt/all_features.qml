import QtQuick
import ValMAX.Native 1.0 as Native
Item {
    id: test
    property int checks: 0
    property string failure: ""
    property int phase: 0
    property string request: ""
    function check(value, message) {
        ++checks;
        if (!value) { failure = message; Qt.exit(1); throw new Error(message); }
    }
    function invoke(value) {
        const result = Native.Bridge.invoke(JSON.stringify(value));
        check(result.accepted, "worker accepted request"); request = result.requestId;
    }
    Timer {
        interval: 10; repeat: true; running: test.phase === 0
        onTriggered: {
            if (!Native.Bridge.runtimeAvailable || Native.Bridge.privacyReport.state === "checking") return;
            test.check(Native.Bridge.servicesAvailable, "real core ABI loaded");
            test.check(Native.Bridge.ghostRuntimeState === "unavailable", "non-MAX host cannot enable ghost");
            test.check(Native.Bridge.notificationRuntimeState === "unavailable", "non-MAX host cannot enable notifications");
            test.check(Native.Bridge.privacyReport.state === "unavailable" && !Native.Bridge.privacyReport.currentProcessVerified, "non-MAX host cannot claim privacy");
            test.check(typeof Native.Bridge.configureRuntime === "undefined" && typeof Native.Bridge.notificationsAttach === "undefined" && typeof Native.Bridge.refreshPrivacy === "undefined", "host setup unavailable to QML");
            test.phase = 1; test.invoke({operation: "version"});
        }
    }
    Connections {
        target: Native.Bridge
        function onOperationFinished(id, responseJson) {
            if (id !== test.request) return;
            const value = JSON.parse(responseJson);
            if (test.phase === 1) {
                test.check(value.ok && value.result.runtime === "rust", "version traversed native ABI");
                test.check(value.result.features.indexOf("chat_lock") >= 0 && value.result.features.indexOf("quick_replies") >= 0, "runtime features retained");
                test.phase = 2; test.invoke({operation: "ghost.get"});
            } else if (test.phase === 2) {
                test.check(value.ok && value.result.runtime.state === "unavailable" && !value.result.runtime.applied, "no fabricated hook ACK");
                test.phase = 3;
                test.invoke({operation:"chat_lock.execute", action:"set", value:{schema:1,accountId:"101",chatId:"202",pin:"426819",confirmPin:"426819"}});
            } else if (test.phase === 3) {
                test.check(!value.ok && value.error === "notification_unavailable", "PIN creation fails closed without trusted hook lease");
                test.phase = 4; test.invoke({operation:"chat_lock.catalog"});
            } else {
                test.check(value.ok && value.result.protected.length === 0, "failed PIN transaction leaves empty catalogue");
                test.check(Native.Bridge.pendingOperations === 0, "completed queue drained");
                // Destroying the QQmlEngine below must drain/cancel pending work.
                test.request = "";
                for (let i=0;i<8;++i) test.check(Native.Bridge.invoke('{"operation":"version"}').accepted, "pending work before teardown admitted");
                Qt.exit(0);
            }
        }
    }
}
