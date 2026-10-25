pragma Singleton
import QtQml
QtObject {
    property string runtimeState: "ready"
    readonly property bool runtimeAvailable: runtimeState === "ready"
    property bool accept: true
    property int sequence: 0
    property var requests: []
    property var privacyReport: ({schema: 1, state: "unavailable", buildVerified: false,
        currentProcessVerified: false, serviceProcessVerified: false, error: "synthetic",
        secret: "SYNTHETIC-PRIVATE", groups: [{secret: "SYNTHETIC-PRIVATE"}]})
    signal operationFinished(string id, string response)
    function invoke(raw) {
        const id = "s" + (++sequence);
        requests.push({id: id, value: JSON.parse(raw)});
        return {accepted: accept, requestId: id, error: accept ? "" : "synthetic"};
    }
}
