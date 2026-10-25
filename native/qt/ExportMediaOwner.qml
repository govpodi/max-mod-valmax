pragma Singleton
import QtQml

// QQmlEngine-owned task, independent of an export dialog/loader lifetime.
// Only explicitly selected model objects and a core-issued lease enter here.
QtObject {
    id: owner
    property var active: null
    readonly property bool pending: active !== null
    function localPath(url) {
        if (typeof url !== "string" || url.length >= 32768 || !/^file:\/\/\/[A-Za-z]:\//.test(url) || /[\u0000-\u001f]/.test(url)) throw new Error("invalid_lease");
        return decodeURIComponent(url.slice(8)).replace(/\//g,"\\");
    }
    function samePath(task, path) {
        if (typeof path !== "string") return false;
        try { return (path.startsWith("file:///") ? localPath(path) : path.replace(/\//g,"\\")).toLowerCase() === localPath(task.fileURL).toLowerCase(); }
        catch (_) { return false; }
    }
    function context(wrapper) {
        try { return wrapper && !wrapper.isNull ? wrapper.get() : null; } catch (_) { return null; }
    }
    function start(job, media, task, downloader, callback) {
        if (active || !job || !media || !task || !/^[0-9a-f]{32}$/.test(job.jobId) || !/^[0-9a-f]{64}$/.test(job.jobToken)) return false;
        let path;
        try { path = localPath(media.nativePathFileURL); } catch (_) { return false; }
        if (["image","video","voice","audio","file"].indexOf(task.kind) < 0) return false;
        const record = {jobId:job.jobId,jobToken:job.jobToken,messageId:media.messageId,attachmentId:media.attachmentId,
            fileURL:media.nativePathFileURL,task:task,downloader:downloader,callback:callback,
            terminal:null,acknowledging:false,networkId:""};
        active = record;
        try {
            if (task.kind !== "file") {
                const admission=Bridge.exportDownloader.start(JSON.stringify({jobId:record.jobId,jobToken:record.jobToken,
                    messageId:record.messageId,attachmentId:record.attachmentId}),String(task.url || ""));
                if (!admission || admission.accepted !== true || typeof admission.requestId !== "string" || !/^[0-9a-f]{32}$/.test(admission.requestId))
                    terminal(record,"error","download_failed");
                else record.networkId=admission.requestId;
            } else {
                const file = context(task.fileContext);
                if (!file) terminal(record,"unavailable","not_available");
                else file.saveAs(Qt.url(record.fileURL));
            }
        } catch (_) { terminal(record,"error","download_failed"); }
        return true;
    }
    function cancelForJob(jobId) {
        const record=active;
        if (record && record.jobId===jobId && record.networkId.length && !record.terminal)
            Bridge.exportDownloader.cancel(record.networkId);
    }
    function terminal(record,status,reason) {
        if (active !== record || record.terminal) return;
        record.terminal={status:status,reason:reason}; acknowledge();
    }
    function acknowledge() {
        const record=active;
        if (!record || !record.terminal || record.acknowledging) return;
        record.acknowledging=true;
        const request=ExportTransport.createRequest(); request.open("POST","media");
        request.onreadystatechange=function() {
            if (owner.active !== record) return;
            record.acknowledging=false;
            if (request.status !== 200) {
                // A worker may still hold the job, or an SDK writer can close
                // shortly after its terminal signal. Preserve the one task and
                // its exact result until the core acknowledges cleanup.
                let code=""; try { code=JSON.parse(request.responseText).error || ""; } catch (_) {}
                if (["busy","unavailable"].indexOf(code) < 0 && record.terminal.status === "ready")
                    record.terminal={status:"error",reason:"download_failed"};
                retry.start(); return;
            }
            let response=null; try { response=JSON.parse(request.responseText); } catch (_) { retry.start(); return; }
            const result=record.terminal, callback=record.callback;
            owner.active=null;
            record.jobToken=""; record.task=null; record.downloader=null; record.callback=null;
            try { if (callback) callback(result.status,result.reason,response); } catch (_) {}
        };
        request.send(JSON.stringify({schema:1,jobId:record.jobId,jobToken:record.jobToken,
            messageId:record.messageId,attachmentId:record.attachmentId,status:record.terminal.status,reason:record.terminal.reason}));
    }
    property Timer retry: Timer { interval:250; repeat:false; onTriggered: owner.acknowledge() }
    property Connections networkSignals: Connections {
        target: Bridge.exportDownloader
        ignoreUnknownSignals:true
        function onCompleted(id,success,error) {
            const record=owner.active;
            if (!record || record.networkId!==id) return;
            const reason=["not_available","permission_denied","download_failed","timeout","unsupported","cancelled"].indexOf(error)>=0 ? error : "download_failed";
            owner.terminal(record,success ? "ready" : reason==="cancelled" ? "cancelled" : "error",success ? null : reason);
        }
    }
    property Connections fileSignals: Connections {
        target: owner.active && owner.active.task.kind === "file" ? owner.context(owner.active.task.fileContext) : null
        ignoreUnknownSignals:true
        function onFileSaveAsSucceed(path) {
            const record=owner.active;
            if (record && record.task.kind === "file" && owner.samePath(record,path)) owner.terminal(record,"ready",null);
        }
        function onFileSaveAsFailed() {
            const record=owner.active;
            if (record && record.task.kind === "file") owner.terminal(record,"error","download_failed");
        }
        function onConfirmRequested() {
            const record=owner.active;
            if (!record || record.task.kind !== "file") return;
            const file=owner.context(record.task.fileContext);
            if (file) file.rejectDownloadUnsafeFile();
            owner.terminal(record,"unavailable","permission_denied");
        }
        // The pinned stock factory creates a fresh Context+Presenter per call
        // (create->c5a5b0, fresh Context at c5a6e8). The export retains that one
        // instance and invokes saveAs once. These are that instance's signals,
        // unlike MediaDownloader's process-wide failure without a request ID.
    }
}
