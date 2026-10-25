pragma Singleton
import QtQml
QtObject {
    property string runtimeState: "ready"
    readonly property bool runtimeAvailable: runtimeState === "ready"
    property int sequence: 0
    property var requests: []
    property var beginCalls: []
    property var bindCalls: []
    property var releaseCalls: []
    property var boundView: null
    property bool allowBegin: true
    property bool allowBind: true
    signal operationFinished(string id, string response)
    function invoke(raw) {
        const id = "op-" + (++sequence);
        requests.push({id: id, value: JSON.parse(raw)});
        return {accepted: true, requestId: id};
    }
    function beginExportHistory(jobId, jobToken) {
        beginCalls.push({jobId: jobId, jobToken: jobToken});
        return allowBegin;
    }
    function isHistoryView(view, jobId) {
        return view && view.valmaxSyntheticType === "history-view"
            && view.objectName === "valmax-export-" + jobId;
    }
    function bindExportHistory(view, jobId, jobToken) {
        bindCalls.push({view: view, jobId: jobId, jobToken: jobToken});
        if (!allowBind || !isHistoryView(view, jobId)) return false;
        boundView = view;
        return true;
    }
    function releaseExportHistory(jobId, jobToken) {
        releaseCalls.push({jobId: jobId, jobToken: jobToken});
        boundView = null;
    }
}
