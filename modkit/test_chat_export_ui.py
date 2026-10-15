"""Native-source anchors and production adapter JS against synthetic models."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import unittest
import tempfile
import importlib.util

import chat_export_ui as target

NATIVE = Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi/ContactMoreMenu.qml")
WINDOW = NATIVE.with_name("MeWindow.qml")
PANEL = Path(__file__).with_name("chat_export_panel.qml")


def functions():
    source = PANEL.read_text(encoding="utf8")
    return "\n".join(re.findall(r"^        function [^\n]+\n.*?^        \}\n", source, flags=re.M | re.S))


HARNESS = r'''
const vm=require('vm'), assert=require('assert'), fs=require('fs');
const source=JSON.parse(fs.readFileSync(0,'utf8')), scenario=process.argv[1];
function setup(options={}) {
 const requests=[], acks=[], gets=[];
 const raw=Uint8Array.from({length:options.size || 100001},(_,i)=>(i*17)%256);
 let aborted=false;
 const context={
   console, Uint8Array, Number, Object, JSON, Date, String, Math,
   jobId:'a'.repeat(32),jobToken:'b'.repeat(64),
   limits:{maxAudioBytes:67108864,maxChunkBytes:49152},
   currentMedia:{reservation:{messageId:'9007199254740993',attachmentId:'voice-message',nativePathFileURL:'file:///D:/synthetic/voice.ogg'},task:{kind:'voice'}},
   pendingRequest:null,nativeDownloadRequest:null,audioBuffer:null,audioOffset:0,terminalResult:null,activeNative:false,cancelRequested:false,closedByUser:false,
   mediaCount:0,omissionCount:0,phase:'media',mediaQueue:[],nativeTasks:{},
   requestWatchdog:{stop(){},restart(){}},mediaWatchdog:{stop(){},restart(){}},stepTimer:{stop(){}},renderPoll:{stop(){}},historyProofTimer:{stop(){},restart(){}},
   filesModel:{model:null},previewContext:{previews:[]},failureText:'',
   QmlUtils:{messageIdString:v=>String(v),contactIdString:v=>String(v),toDateTime:v=>new Date(v)},
   XMLHttpRequest:class {
    static HEADERS_RECEIVED=2;static DONE=4;
    open(method,url){gets.push({method,url});this.method=method;this.url=url;}
    send(){
     this.status=options.status || 200;this.readyState=2;this.onreadystatechange();
     if(aborted)return;
     this.response=raw.buffer;this.readyState=4;this.onreadystatechange();
    }
    abort(){aborted=true;}
    getResponseHeader(name){return name==='Content-Length'?(options.length === undefined?String(raw.length):options.length):options.encoding || '';}
   }
 };
 vm.createContext(context);vm.runInContext(source,context,{timeout:1000});
 context.request=function(route,body,callback){
  requests.push({route,body});
  if(route==='media-chunk')callback({schema:1,jobId:context.jobId,offset:body.offset+Buffer.from(body.data,'base64').length});
  else if(route==='media'){acks.push(body);callback({schema:1,jobId:context.jobId});}
  else assert.fail('Unexpected route');
 };
 context.productionNextMedia=context.nextMedia;context.nextMedia=function(){};
 return {context,requests,acks,gets,raw,aborted:()=>aborted};
}
if(scenario==='voice'){
 const h=setup();h.context.downloadAudio('https://cdn.example.test/voice.ogg?opaque=synthetic');
 assert.deepEqual(h.gets,[{method:'GET',url:'https://cdn.example.test/voice.ogg?opaque=synthetic'}]);
 const chunks=h.requests.filter(v=>v.route==='media-chunk');assert.equal(chunks.length,3);
 assert.deepEqual(chunks.map(x=>x.body.offset),[0,49152,98304]);
 assert(chunks.every(x=>x.body.data.length<=65536));
 assert(Buffer.concat(chunks.map(x=>Buffer.from(x.body.data,'base64'))).equals(Buffer.from(h.raw)));
 assert.equal(h.acks[0].status,'ready');assert.equal(h.context.audioBuffer,null);
 assert(h.requests.every(x=>!JSON.stringify(x.body).includes('cdn.example')&&!('url' in x.body)&&!('headers' in x.body)));
} else if(scenario==='voice-deny'){
 for(const opts of [{length:''},{length:'67108865'},{length:'1e6'},{encoding:'gzip'},{status:403},{length:'2'}]) {
  const h=setup(opts);h.context.downloadAudio('https://cdn.example.test/voice.ogg');
  assert(!h.requests.some(x=>x.route==='media-chunk'));assert.notEqual(h.acks[0].status,'ready');
 }
 for(const url of ['http://cdn.example.test/a','file:///D:/private.ogg','https://user:pass@cdn.example.test/a','data:audio/ogg;base64,AAAA']){
  const h=setup();h.context.downloadAudio(url);assert.equal(h.gets.length,0);assert.equal(h.acks[0].status,'unavailable');
 }
} else if(scenario==='projection'){
 const h=setup();h.context.commonDependency={contactProperties:{createDisplayNameContext:()=>({get:()=>({displayText:'Synthetic Author'})})}};
 const message={messageId:'9007199254740993',senderId:'9007199254740995',time:1600000000123,text:'<script>synthetic</script>',isOutgoing:true,hasReply:false};
 const dto=h.context.messageDTO({isNull:false,get:()=>message});
 assert.equal(dto.id,'9007199254740993');assert.equal(dto.author.id,'9007199254740995');assert.equal(dto.timestampMs,1600000000123);assert.equal(dto.text,message.text);
 assert.equal(h.context.canonicalId('9007199254740995','contact'),'9007199254740995');
 assert.equal(h.context.utf8Length('abcЖ😀'),9);
 assert.equal(h.context.base64Chunk(Uint8Array.from([0,1,2,255])),'AAEC/w==');
 assert.equal(h.context.nativeFilePath('file:///D:/Synthetic%20Directory/voice.ogg'),'D:\\Synthetic Directory\\voice.ogg');
 assert.throws(()=>h.context.nativeFilePath('https://example.test/a'));
 message.text='x'.repeat(65537);assert.equal(h.context.messageDTO({get:()=>message}),null);assert.equal(h.context.stopReason,'message_too_large');
} else if(scenario==='eof-honesty'){
 for(const proof of [{ready:false,backward:false,forward:false},{ready:true,backward:true,forward:false},{ready:true,backward:true,forward:true}]) {
  const h=setup();Object.assign(h.context,{jobId:'a'.repeat(32),phase:'history',history:{hasMoreBackward:false,hasMoreForward:false},moreBackward:false,moreForward:false,historyModel:{items:{count:1}},messageCount:1,attachmentCount:0,pagesFetched:1,limits:{maxMessages:1000,maxAttachments:1000,maxPages:100,maxPageMessages:200},pendingRequest:null,waitStarted:Date.now(),changedAt:0,awaitingHistory:false,initialHistoryReady:true,direction:'forward',historicalEndProven:false,forwardEnd:false,backwardEnd:true,stopReason:'',noProgress:0,historyProofTimer:{restart(){}}});
  h.context.harvestPage=()=>false;let completion;
  h.context.finishExport=reason=>completion=reason;
  h.context.request=(route,body,callback)=>{assert.equal(route,'history');h.context.proofStarted=Date.now()-3000;callback({schema:1,jobId:h.context.jobId,history:proof});};
  h.context.historyStep();assert.equal(completion,proof.ready&&proof.backward&&proof.forward?'end_of_history':'unsupported_history');
  assert.equal(h.context.historicalEndProven,proof.ready&&proof.backward&&proof.forward);
 }
} else if(scenario==='cancel-native'){
 const h=setup();Object.assign(h.context,{busy:true,activeNative:true,phase:'media',closedByUser:false});
 let disposed=false,ack;
 h.context.dispose=()=>{assert(!h.context.activeNative);disposed=true;};
 h.context.request=(route,body,callback)=>{
  h.requests.push({route,body});
  if(route==='cancel')callback({schema:1,jobId:h.context.jobId,state:'cancelled',cleanupPending:true});
  else {assert.equal(route,'media');ack=callback;}
 };
 const current=h.context.currentMedia;
 h.context.cancelExport();assert.equal(h.context.phase,'cancelled');assert(h.context.activeNative);assert.strictEqual(h.context.currentMedia,current);assert(!disposed);
 h.context.closedByUser=true;h.context.mediaFinished('ready',null);
 assert.equal(h.requests[1].body.status,'cancelled');assert(h.context.activeNative);assert(!disposed);
 ack({schema:1,jobId:h.context.jobId});assert(!h.context.activeNative);assert.equal(h.context.currentMedia,null);assert(disposed);
} else if(scenario==='cancel-before-native'){
 const h=setup();Object.assign(h.context,{busy:true,currentMedia:null,mediaQueue:[{messageId:'1',attachmentId:'photo',nativePathFileURL:'file:///D:/synthetic/photo.jpg'}],nativeTasks:{'1|photo':{kind:'image'}}});
 let startCallback,invoked=false,ackStatus;
 h.context.invokeNative=()=>{invoked=true;};
 h.context.request=(route,body,callback)=>{
  if(route==='media-start'){h.context.pendingRequest={};startCallback=callback;}
  else if(route==='media'){ackStatus=body.status;callback({schema:1,jobId:h.context.jobId});}
  else if(route==='cancel')callback({schema:1,jobId:h.context.jobId,state:'cancelled'});
  else assert.fail(route);
 };
 h.context.productionNextMedia();h.context.cancelExport();assert(!invoked);assert(h.context.cancelRequested);
 h.context.pendingRequest=null;startCallback({schema:1,jobId:h.context.jobId});
 assert(!invoked);assert.equal(ackStatus,'cancelled');assert(!h.context.activeNative);
} else if(scenario==='photo-callback'){
 for(const [path,expected] of [['D:\\synthetic\\photo.jpg','ready'],['file:///D:/synthetic/photo.jpg','ready'],['','error'],['D:\\another\\photo.jpg','error'],[true,'error']]) {
  const h=setup();let callback,result;
  const reservation={messageId:'1',attachmentId:'photo',nativePathFileURL:'file:///D:/synthetic/photo.jpg'},task={kind:'image',url:'https://synthetic.example.test/photo'};
  Object.assign(h.context,{currentMedia:{reservation,task},MediaDownloaderPhotoType:{Photo:0,Gif:1},downloader:{downloadImageWithJSCallback(...args){callback=args[5];}}});
  h.context.mediaFinished=(status,reason)=>{result={status,reason};};
  h.context.invokeNative(reservation,task);callback(path);assert.equal(result.status,expected);
 }
}
process.stdout.write(JSON.stringify({scenario,passed:true}));
'''


class ChatExportTransformTests(unittest.TestCase):
    def source(self):
        if not NATIVE.is_file(): self.skipTest("Pinned native menu fixture absent")
        return NATIVE.read_text(encoding="utf8")

    def test_native_menu_entry_is_self_contained_and_preserves_actions(self):
        source = self.source(); result = target.transform(source)
        self.assertEqual(result.count(target.MARKER), 1)
        self.assertIn('text: "Скачать чат"', result)
        self.assertIn('iconSource: "qrc:/valmax/icons/chat-export.svg"', result)
        self.assertIn("!root.chatPropertiesContext.get().isPreventCopyingEnabled", result)
        for value in ("root.context.clearHistory(", "root.contactBlocked()", "root.stopBot()", "deleteDialog.createObject("):
            self.assertEqual(result.count(value), source.count(value))
        self.assertIn('Overlay.overlay.Window.window', result)

    def test_changed_anchor_double_transform_missing_resource_rejected(self):
        source = self.source()
        for value in (None, "", source.replace("    signal finished\n", "    signal done\n"), target.transform(source)):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(ValueError): target.transform(value)
        with self.assertRaises(ValueError): target.transform_sources({"Other.qml": "unchanged"})
        result = target.transform_sources({"ContactMoreMenu.qml": source, "MeWindow.qml": WINDOW.read_text(encoding="utf8"), "Other.qml": "unchanged"})
        self.assertEqual(result["Other.qml"], "unchanged")
        self.assertIn('setSource("qrc:/valmax/ChatExportPanel.qml", properties)', result['MeWindow.qml'])
        self.assertNotIn('Qt.createQmlObject', result['ContactMoreMenu.qml'])
        with self.assertRaises(ValueError): target.transform_window(result['MeWindow.qml'])

    def test_no_read_receipt_or_auth_actions_in_adapter(self):
        source = PANEL.read_text(encoding="utf8")
        for value in ("setIsActive(", "setLastVisibleIndex(", "readCurrentReactionIfNeeded(", "openOnLastReaction(", "CredRead", "access_token", "SessionInit", "WebSocket", "saveServerUrl", "requestCode"):
            self.assertNotIn(value, source)
        self.assertIn("createOnMessage(nativeChatId, message.messageTimeId)", source)
        self.assertIn("history.fetchBackward()", source)
        self.assertIn("history.fetchForward()", source)
        self.assertIn('"X-Max-Mod-Local-Write", "chat-export-v1"', source)

    @unittest.skipUnless(shutil.which("node"), "Node VM unavailable")
    def test_production_adapter_functions_with_synthetic_data(self):
        for scenario in ("voice", "voice-deny", "projection", "eof-honesty", "cancel-native", "cancel-before-native", "photo-callback"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([shutil.which("node"), "-e", HARNESS, scenario], input=json.dumps(functions()), capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)["passed"])

    @unittest.skipUnless(importlib.util.find_spec("PySide6"), "Qt synthetic runtime unavailable")
    def test_qml_compile_and_stable_host_lifetime(self):
        import sys
        root = Path('D:/ValMAX/analysis/chat-export-v1')
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='synthetic-test-', dir=root) as temporary:
            result = subprocess.run([sys.executable, str(PANEL.with_name('chat_export_qa.py')), '--output', str(Path(temporary)/'proof')], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report['delegateRoleAccess'])
            self.assertTrue(report['creationSurvivesCallerDestruction'])
            self.assertFalse(report['nativeClientStarted'])
            self.assertEqual(report['warnings'], [])


if __name__ == "__main__": unittest.main()
