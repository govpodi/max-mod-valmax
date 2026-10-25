pragma Singleton
import QtQml
QtObject {
    property QtObject exportDownloader: QtObject {
        readonly property bool available:true
        property var requests:[]
        property var cancelled:[]
        signal completed(string id,bool success,string error)
        function start(job,url) {
            const id=(requests.length+1).toString(16).padStart(32,"0");
            requests.push({id:id,job:JSON.parse(job),url:url});
            return {accepted:true,requestId:id};
        }
        function cancel(id) {cancelled.push(id);}
    }
    property string runtimeState: "ready"
    readonly property bool runtimeAvailable: runtimeState === "ready"
    property bool accept: true
    property bool throwOnInvoke: false
    property int sequence: 0
    property var requests: []
    property var duringInvoke: null
    signal operationFinished(string id, string response)
    function invoke(raw) {
        const id = "op-" + (++sequence);
        requests.push({id: id, value: JSON.parse(raw)}); // Synthetic test data only.
        if (duringInvoke) { const callback = duringInvoke; duringInvoke = null; callback(); }
        if (throwOnInvoke) throw new Error("synthetic-private-error");
        return {accepted: accept, requestId: id, error: accept ? "" : "queue_full"};
    }
}
