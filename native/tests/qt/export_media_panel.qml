import QtQuick
import ValMAX.Native 1.0 as ValMAXNative
Item {
    id: test
    property int checks: 0
    property string failure: ""
    property string phase: "media"
    property var pendingRequest: null
    property var currentMedia: null
    property bool cancelRequested: false
    property bool activeNative: false
    property var nativeTasks: ({})
    property var mediaQueue: []
    property var admitted: null
    property var sdkCall: null
    property var mediaResult: null
    property string requestedAudio: ""
    property string jobId: "a".repeat(32)
    property string jobToken: "b".repeat(64)
    property int mediaCount:0
    property int omissionCount:0
    property bool closedByUser:false
    property var terminalResult:null
    property var statusResult:null
    property var heldCallback:null
    property int testStep:0
    readonly property var downloader: sdk
    Timer { id: mediaWatchdog; interval: 100000 }
    QtObject {
        id: sdk
        signal videoDownloadSucceed(string path)
        signal videoDownloadFailed()
        function downloadImageWithJSCallback(chat, message, attachment, url, path, callback, photoType) {
            test.sdkCall = {kind:"image", chat:chat, message:message, attachment:attachment, url:url, path:path, callback:callback, photoType:photoType};
        }
        function downloadVideo(chat, time, attachment, path) { test.sdkCall = {kind:"video", chat:chat, time:time, attachment:attachment, path:path}; }
    }
    QtObject {
        id: fileSdk
        signal fileSaveAsSucceed(string path)
        signal fileSaveAsFailed()
        signal confirmRequested()
        function saveAs(value) { test.sdkCall={kind:"file",url:String(value)}; }
        function rejectDownloadUnsafeFile() { test.sdkCall.rejected=true; }
    }
    function check(value, message) { ++checks; if (!value) { failure=message; Qt.exit(1); return false; } return true; }
    function localFileURL(value) { return typeof value === "string" && /^file:\/\/\/[A-Za-z]:\//.test(value); }
    function nativeFilePath(value) { if (!localFileURL(value)) throw new Error("local_path"); return decodeURIComponent(value.slice(8)).replace(/\//g,"\\"); }
    function getContext(wrapper) { return wrapper && wrapper.get ? wrapper.get() : null; }
    function jobBody(extra) { return Object.assign({schema:1,jobId:"a".repeat(32),jobToken:"b".repeat(64)},extra); }
    function request(route, body, callback) { admitted = {route:route,body:body,callback:callback}; }
    function cancelExport() { phase="cancelled"; }
    function downloadAudio(url) { requestedAudio=url; }
    function mediaFinished(status, reason) { mediaResult={status:status,reason:reason}; }
    function applyStatus(value) { statusResult=value; phase=value.state; }
    function dispose() {}
    function acknowledge(state) {
        const pending=ValMAXNative.Bridge.requests[ValMAXNative.Bridge.requests.length - 1];
        ValMAXNative.Bridge.operationFinished(pending.id,JSON.stringify({schema:1,ok:true,result:{schema:1,jobId:jobId,state:state || "exporting",downloadsPending:false}}));
    }
    function networkCall() {return ValMAXNative.Bridge.exportDownloader.requests[ValMAXNative.Bridge.exportDownloader.requests.length - 1];}
    function completeNetwork(success,error) {const call=networkCall();ValMAXNative.Bridge.exportDownloader.completed(call.id,success,error || "");}
    function sameNativePath(path) { return currentMedia && path===nativeFilePath(currentMedia.reservation.nativePathFileURL); }
    function prepare(kind) {
        phase="media"; cancelRequested=false; activeNative=false; currentMedia=null;
        admitted=null; sdkCall=null; mediaResult=null; requestedAudio=""; mediaCount=0; omissionCount=0; statusResult=null;
        const media={messageId:"1",attachmentId:"asset",source:"media/"+"c".repeat(32)+".png"};
        mediaQueue=[media];
        nativeTasks={}; nativeTasks["1|asset"]={kind:kind,chatId:"42",messageId:"1",messageTimeId:"time",attachId:"attachment",url:"https://synthetic.invalid/owned",isGif:false};
        return media;
    }
    // GENERATED_EXPORT_FUNCTIONS
    Component.onCompleted: {
        const url="file:///D:/selected/.valmax-download-synthetic/attachment.png";
        let media=prepare("image"); nextMedia();
        if (!check(admitted && admitted.route==="media-start" && sdkCall===null && !activeNative,"SDK does not run before native lease receipt")) return;
        admitted.callback({schema:1,nativePathFileURL:url});
        if (!check(activeNative && networkCall().job.messageId==="1" && networkCall().job.attachmentId==="asset"
                && networkCall().url==="https://synthetic.invalid/owned" && !Object.prototype.hasOwnProperty.call(networkCall().job,"path"),"image uses authorized job sink without path")) return;
        completeNetwork(true,"");
        if (!check(ValMAXNative.ExportMediaOwner.pending && mediaCount===0,"native owner retains terminal task until core ACK")) return;
        acknowledge();
        if (!check(!ValMAXNative.ExportMediaOwner.pending && mediaCount===1,"exact image callback ACK updates dialog after cleanup")) return;
        media=prepare("image"); nextMedia(); admitted.callback({schema:1,nativePathFileURL:url});
        ValMAXNative.Bridge.exportDownloader.completed("f".repeat(32),true,"");
        if (!check(ValMAXNative.ExportMediaOwner.pending && mediaCount===0,"unrelated network task cannot mark selected asset ready")) return;
        completeNetwork(false,"permission_denied"); acknowledge();
        if (!check(omissionCount===1 && mediaCount===0,"correlated image failure acknowledged")) return;
        media=prepare("video"); nextMedia(); admitted.callback({schema:1,nativePathFileURL:url});
        if (!check(ValMAXNative.ExportMediaOwner.pending && networkCall().job.messageId==="1" && sdkCall===null,"video uses per-task native network stream")) return;
        sdk.videoDownloadSucceed("D:\\foreign.mp4"); sdk.videoDownloadFailed();
        if (!check(ValMAXNative.ExportMediaOwner.pending,"uncorrelated video failure/success cannot settle selected lease")) return;
        completeNetwork(true,""); acknowledge();
        if (!check(!ValMAXNative.ExportMediaOwner.pending && mediaCount===1,"path-correlated video success settled")) return;
        media=prepare("file");
        nativeTasks["1|asset"].fileContext={get:function(){ return fileSdk; }};
        nextMedia(); admitted.callback({schema:1,nativePathFileURL:url});
        if (!check(sdkCall && sdkCall.kind==="file" && sdkCall.url===url,"file saveAs receives QUrl for exact lease")) return;
        fileSdk.fileSaveAsSucceed("D:\\foreign.bin");
        if (!check(ValMAXNative.ExportMediaOwner.pending,"foreign path cannot settle scoped file task")) return;
        fileSdk.fileSaveAsSucceed(nativeFilePath(url)); acknowledge();
        if (!check(!ValMAXNative.ExportMediaOwner.pending && mediaCount===1,"file exact success settled")) return;
        media=prepare("file");nativeTasks["1|asset"].fileContext={get:function(){return fileSdk;}};
        nextMedia();admitted.callback({schema:1,nativePathFileURL:url});fileSdk.fileSaveAsFailed();acknowledge();
        if (!check(!ValMAXNative.ExportMediaOwner.pending && omissionCount===1,"dedicated file context failure releases lease after core ACK")) return;
        media=prepare("file");nativeTasks["1|asset"].fileContext={get:function(){return fileSdk;}};
        nextMedia();admitted.callback({schema:1,nativePathFileURL:url});fileSdk.confirmRequested();acknowledge();
        if (!check(!ValMAXNative.ExportMediaOwner.pending && omissionCount===1 && sdkCall.rejected,"unsafe file request rejected on dedicated context")) return;
        media=prepare("image"); nextMedia(); cancelRequested=true; admitted.callback({schema:1,nativePathFileURL:url});
        if (!check(!activeNative && sdkCall===null && mediaResult.status==="cancelled","cancel before SDK invocation returns terminal ACK without native task")) return;
        media=prepare("image"); nextMedia(); let threw=false;
        try { admitted.callback({nativePathFileURL:"https://unselected.invalid/file"}); } catch (_) { threw=true; }
        if (!check(threw && sdkCall===null && !activeNative,"invalid lease receipt rejected before SDK invocation")) return;
        media=prepare("voice"); nextMedia();
        if (!check(admitted && admitted.route==="media-start" && requestedAudio==="","voice admits owned native lease instead of whole-file XHR buffer")) return;
        admitted.callback({schema:1,nativePathFileURL:url});
        ValMAXNative.ExportMediaOwner.cancelForJob(jobId);
        if (!check(ValMAXNative.Bridge.exportDownloader.cancelled.indexOf(networkCall().id)>=0,"cancel targets exact native network request")) return;
        completeNetwork(false,"cancelled");acknowledge("cancelled");
        media=prepare("image"); nextMedia(); admitted.callback({schema:1,nativePathFileURL:url});
        // Simulate destruction of the page callback. Singleton still owns the
        // SDK completion and its terminal ACK even if page delivery throws.
        ValMAXNative.ExportMediaOwner.active.callback=function() { throw new Error("synthetic-destroyed-page"); };
        completeNetwork(true,"");
        const pending=ValMAXNative.Bridge.requests[ValMAXNative.Bridge.requests.length - 1];
        ValMAXNative.Bridge.operationFinished(pending.id,JSON.stringify({schema:1,ok:false,error:"busy"}));
        if (!check(ValMAXNative.ExportMediaOwner.pending,"busy terminal ACK retains engine-owned task")) return;
        testStep=1;
    }
    Timer {
        interval:300; repeat:true; running:test.testStep>0
        onTriggered: {
            if (test.testStep===1) {
                test.acknowledge("cancelled");
                if (!test.check(!ValMAXNative.ExportMediaOwner.pending,"retry ACK settles after page callback destruction")) return;
                test.testStep=0; Qt.exit(0);
            }
        }
    }
}
