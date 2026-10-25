pragma Singleton
import QtQml
QtObject {
    property string runtimeState: "ready"
    readonly property bool runtimeAvailable: runtimeState === "ready"
    property bool accept: true
    property bool throwOnInvoke: false
    property string forcedId: ""
    property int sequence: 0
    property var requests: []
    property var duringInvoke: null
    signal operationFinished(string id, string response)
    function invoke(raw) {
        const value = JSON.parse(raw);
        const id = forcedId || "op-" + (++sequence);
        // Record only shape and fixed test predicates, never the synthetic password.
        requests.push({id: id, keys: Object.keys(value).sort().join(","), operation: value.operation,
            fileMatches: value.file === "file:///C:/synthetic/selected.mdata",
            passwordMatches: value.password === "synthetic-password"});
        value.password = ""; value.file = ""; raw = "";
        if (duringInvoke) { const callback = duringInvoke; duringInvoke = null; callback(); }
        if (throwOnInvoke) throw new Error("SYNTHETIC-PRIVATE");
        return {accepted: accept, requestId: id, error: accept ? "" : "queue_full"};
    }
}
