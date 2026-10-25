import QtQml
import ValMAX.Native 1.0

QtObject {
    id: test
    property int checks: 0
    property string failure: ""
    property int phase: 0
    property int replies: 0
    property int ticks: 0
    property int encryptStartTicks: 0
    property string requestId: ""
    property string archive: ""
    property var payload: ({schema: 1, sourceClient: "MAX Desktop", sourceVersion: "26.35.1",
                            credentials: [{name: "synthetic-key", blob: "dGVzdA=="}], files: []})
    property var policy: ({credential_names: ["synthetic-key"]})
    function check(value, description) {
        if (!value) { failure = description; Qt.exit(1); throw new Error(description) }
        checks++
    }
    function invoke(value) {
        const reply = Bridge.invoke(JSON.stringify(value))
        check(reply.accepted && reply.requestId !== "", "native operation admitted")
        requestId = reply.requestId
    }
    property Timer pulse: Timer { interval: 1; repeat: true; running: true; onTriggered: test.ticks++ }
    property Timer start: Timer {
        interval: 1; running: true
        onTriggered: {
            test.check(Bridge.servicesAvailable, "Rust core ABI available")
            test.check(Bridge.invoke("").error === "invalid_request", "empty request rejected")
            for (let i = 0; i < 8; ++i)
                test.check(Bridge.invoke('{"operation":"version"}').accepted, "bounded worker queue admission")
            test.check(Bridge.invoke('{"operation":"version"}').error === "queue_full", "pending bound enforced")
            test.check(test.replies === 0, "responses asynchronous")
        }
    }
    property Connections signals: Connections {
        target: Bridge
        function onOperationFinished(id, responseJson) {
            const response = JSON.parse(responseJson)
            test.check(response.schema === 1, "versioned Rust response")
            if (test.phase === 0) {
                test.check(response.ok && response.result.runtime === "rust" && response.result.abi === 1, "real Rust call")
                test.replies++
                if (test.replies === 8) {
                    test.check(Bridge.pendingOperations === 0, "worker replies drained")
                    test.phase = 1
                    test.invoke({operation: "session.validate", payload: test.payload, policy: test.policy})
                }
            } else if (test.phase === 1) {
                test.check(id === test.requestId && response.ok, "payload validated")
                test.phase = 2
                test.encryptStartTicks = test.ticks
                test.invoke({operation: "session.encrypt", payload: test.payload, policy: test.policy, password: "synthetic пароль 🔐"})
            } else if (test.phase === 2) {
                test.check(response.ok && response.result.archive.length > 100, "Rust encrypted archive")
                test.check(test.ticks > test.encryptStartTicks, "QML loop responsive during crypto")
                test.archive = response.result.archive
                test.phase = 3
                test.invoke({operation: "session.decrypt", archive: test.archive, policy: test.policy, password: "synthetic пароль 🔐"})
            } else if (test.phase === 3) {
                test.check(response.ok && response.result.payload.credentials[0].blob === "dGVzdA==", "Rust decrypted synthetic record")
                test.phase = 4
                test.invoke({operation: "session.decrypt", archive: test.archive, policy: test.policy, password: "incorrect synthetic password"})
            } else {
                test.check(!response.ok && response.error === "authentication_failed", "wrong password rejected")
                test.check(Bridge.pendingOperations === 0, "all operations completed")
                test.archive = ""
                Qt.exit(0)
            }
        }
    }
}
