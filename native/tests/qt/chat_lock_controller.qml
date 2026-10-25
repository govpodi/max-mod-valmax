import QtQuick
import ValMAX.Native 1.0 as Native

Item {
    id: test
    property int checks: 0
    property string failure: ""
    property int step: 0
    property int completed: 0
    property int overflow: 0
    property int cancelled: 0
    PinController { id: locks }
    function check(value, message) {
        ++checks; if (!value) { failure = message; Qt.exit(1); return false; } return true;
    }
    Timer {
        interval: 5; repeat: true; running: true
        onTriggered: {
            if (test.failure.length) return;
            if (test.step === 0) {
                if (!locks.ready || locks.lockAllPending) return;
                if (!test.check(Native.Bridge.runtimeAvailable && Native.Bridge.notificationRuntimeState === "unavailable", "unsupported host cannot confirm notifications")) return;
                if (!test.check(typeof Native.Bridge.notificationsAttach === "undefined" && typeof Native.Bridge.configureRuntime === "undefined", "no QML host registration or path setter")) return;
                if (!test.check(locks.key("101", "-9223372036854775808") !== "" && locks.key("01", "202") === "", "exact controller scope validation")) return;
                if (!test.check(!locks.isProtected("101", "202") && !locks.isLocked("101", "202"), "unprotected chat remains accessible without hooks")) return;
                locks.setCurrentScope("101", "202");
                if (!test.check(locks.openSettings("101", "202", "Synthetic"), "native PIN settings controller opens")) return;
                locks.testSetSecrets("12", "12"); locks.submitPin();
                if (!test.check(!locks.busy && locks.testSecretsClear() && locks.errorText.length > 0, "invalid PIN clears both fields")) return;
                locks.testSetSecrets("426819", "426819"); locks.submitPin();
                if (!test.check(locks.busy && locks.testSecretsClear(), "valid PIN buffers cleared immediately")) return;
                test.step = 1;
            } else if (test.step === 1) {
                if (locks.busy || locks.mutationRequest !== null) return;
                if (!test.check(locks.ready && locks.errorText === locks.errorFor("notification_unavailable"), "native failure reaches original user-facing explanation")) return;
                if (!test.check(!locks.isProtected("101", "202") && !locks.isLocked("101", "202"), "failed set creates no lock or grant")) return;
                locks.refreshCatalog(); test.step = 2;
            } else if (test.step === 2) {
                if (locks.catalogPending) return;
                if (!test.check(locks.ready && !locks.hasLocks, "persisted catalogue remains empty")) return;
                const request = Native.ChatLockTransport.createRequest();
                request.onreadystatechange = function() { ++test.cancelled; };
                request.open("GET", ""); request.send(""); request.abort();
                if (!test.check(request.payload === null, "cancel drops request payload")) return;
                locks.request("POST", "unlock", {schema: 1, accountId: "101", chatId: "202", pin: "426819"}, function(ok, value) {
                    test.check(!ok && value.error === "not_protected", "native error code survives transport"); ++test.completed;
                });
                test.step = 3;
            } else if (test.step === 3) {
                if (test.completed !== 1 || Native.Bridge.pendingOperations !== 0) return;
                if (!test.check(test.cancelled === 0 && Object.keys(Native.ChatLockTransport.pending).length === 0, "closed controller cannot receive callbacks")) return;
                test.completed = 0;
                for (let i = 0; i < 10; ++i) {
                    const request = Native.ChatLockTransport.createRequest();
                    request.onreadystatechange = function() { ++test.completed; if (request.status === 503) ++test.overflow; };
                    request.open("GET", ""); request.send("");
                }
                test.step = 4;
            } else if (test.step === 4 && test.completed === 10) {
                if (!test.check(test.overflow === 2 && Native.ChatLockTransport.waiting.length === 0, "bounded PIN queue")) return;
                if (!test.check(Object.keys(Native.ChatLockTransport.pending).length === 0, "PIN request references released")) return;
                Qt.exit(0);
            }
        }
    }
}
