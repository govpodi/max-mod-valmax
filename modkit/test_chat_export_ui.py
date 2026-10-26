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
   pendingRequest:null,pendingPreviewRequest:null,previewBusy:false,previewRequested:false,previewStatusText:'',nativeHistoryPreviewAvailable:false,
   nativeApi2ExportAvailable:false,exportMode:'client',
   nativeDownloadRequest:null,audioBuffer:null,audioOffset:0,terminalResult:null,activeNative:false,cancelRequested:false,closedByUser:false,
   mediaCount:0,omissionCount:0,phase:'media',mediaQueue:[],nativeTasks:{},
   requestWatchdog:{stop(){},restart(){}},previewWatchdog:{stop(){},restart(){}},mediaWatchdog:{stop(){},restart(){}},stepTimer:{stop(){}},renderPoll:{stop(){}},historyProofTimer:{stop(){},restart(){}},api2PaceTimer:{stop(){},restart(){}},
   filesModel:{model:null},previewContext:{previews:[]},failureText:'',
    QmlUtils:{messageIdString:v=>String(v),contactIdString:v=>String(v),chatIdString:v=>String(v),toDateTime:v=>new Date(v)},
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
 context.productionRequest=context.request;
 context.request=function(route,body,callback){
  requests.push({route,body});
  if(route==='media-chunk')callback({schema:1,jobId:context.jobId,offset:body.offset+Buffer.from(body.data,'base64').length});
  else if(route==='media'){acks.push(body);callback({schema:1,jobId:context.jobId});}
  else assert.fail('Unexpected route');
 };
 context.productionNextMedia=context.nextMedia;context.nextMedia=function(){};
 return {context,requests,acks,gets,raw,aborted:()=>aborted};
}
if(scenario==='accounts'){
 const h=setup();
 const ids=['9007199254740993','9007199254740994','9007199254740995','9007199254740996'];
 const deps=ids.map(id=>({commonDependency:{selfInfo:{id}}}));
 h.context.accountSources={count:4,itemAt:i=>({dependency:deps[i]})};
 h.context.commonDependency={serverParameters:{maxAccountsCount:4}};
 h.context.ownId=ids[3];
 assert.strictEqual(h.context.dependencyForAccount(),deps[3]);
 h.context.commonDependency.serverParameters.maxAccountsCount=3;
 assert.strictEqual(h.context.dependencyForAccount(),deps[3]);
 h.context.commonDependency.serverParameters.maxAccountsCount='4';
 assert.strictEqual(h.context.dependencyForAccount(),deps[3]);
 h.context.commonDependency.serverParameters.maxAccountsCount=4;
 deps[1].commonDependency.selfInfo.id=ids[3];
 assert.strictEqual(h.context.dependencyForAccount(),null);
 deps[1].commonDependency.selfInfo.id=ids[1];
 h.context.accountSources.count=33;
 h.context.commonDependency.serverParameters.maxAccountsCount=33;
 assert.strictEqual(h.context.dependencyForAccount(),null);
 h.context.accountSources.count=4.5;
 assert.strictEqual(h.context.dependencyForAccount(),null);
} else if(scenario==='selected-scope'){
  const h=setup(),id='9007199254740993',chat='-9007199254740997';
  function fresh(){
   const owner={selfInfo:{id,accountId:'account-A'}};
   const properties={chatId:chat,dialogContactId:'9007199254740999',isPreventCopyingEnabled:false};
   const wrapper={get:()=>properties};
   const model={currentAccountId:'account-A',activeAccountDependency:{commonDependency:owner}};
   Object.assign(h.context,{commonDependency:owner,nativeChatId:chat,nativeChatProperties:wrapper,chatTitle:'Synthetic chat',
    ownId:id,chatIdText:chat,
    selectedScope:{owner,propertiesContext:wrapper,accountKey:'account-A',ownId:id,nativeChatId:chat,chatId:chat,contactId:'9007199254740999',title:'Synthetic chat'},
    application:{accountModel:model},scopeInvalidated:false});
   return {owner,properties,wrapper,model};
  }
  let s=fresh();assert(h.context.validSelectedScope());
  h.context.closedByUser=true;assert(!h.context.validSelectedScope());h.context.closedByUser=false;
  s.model.currentAccountId='account-B';assert(!h.context.validSelectedScope());
  s.model.currentAccountId='account-A';h.context.scopeInvalidated=true;assert(!h.context.validSelectedScope());
  s=fresh();s.model.activeAccountDependency.commonDependency={selfInfo:{id,accountId:'account-A'}};assert(!h.context.validSelectedScope());
  s=fresh();h.context.commonDependency={selfInfo:{id,accountId:'account-A'}};assert(!h.context.validSelectedScope());
  s=fresh();s.properties.chatId='-9007199254740998';assert(!h.context.validSelectedScope());
  s=fresh();s.properties.dialogContactId='9007199254740998';assert(!h.context.validSelectedScope());
  s=fresh();h.context.nativeChatId='-9007199254740998';assert(!h.context.validSelectedScope());
  s=fresh();s.properties.isPreventCopyingEnabled=true;assert(!h.context.validSelectedScope());
  s=fresh();h.context.chatTitle='Different chat';assert(!h.context.validSelectedScope());
  s=fresh();h.context.selectedScope.ownId='9007199254740994';assert(!h.context.validSelectedScope());
  s=fresh();s.model.currentAccountId='account-B';let invalidated=0,requests=0;
  const productionInvalidate=h.context.invalidateSelectedScope;
  h.context.invalidateSelectedScope=()=>{invalidated++};h.context.request=()=>{requests++};h.context.busy=false;
  h.context.startExport('file:///D:/synthetic');assert.equal(invalidated,1);assert.equal(requests,0);
  s=fresh();const submitted=[];
  h.context.invalidateSelectedScope=productionInvalidate;
  h.context.destinationDialog={close(){}};h.context.close=()=>{};
  h.context.busy=false;
  h.context.accountSources={count:1,itemAt:()=>({dependency:{commonDependency:s.owner,historyPanelDependency:{}}})};
  h.context.request=(route,body)=>{submitted.push({route,body});return true;};
  h.context.startExport('file:///D:/synthetic');
  assert.equal(submitted.length,1);assert.equal(submitted[0].route,'start');
  assert.equal(submitted[0].body.accountId,id);assert.equal(submitted[0].body.chat.id,chat);
  assert.equal(submitted[0].body.directory,'file:///D:/synthetic');
  s=fresh();s.properties.chatId='-9007199254740998';const beforeChatRebind=submitted.length;
  h.context.startExport('file:///D:/synthetic');assert.equal(submitted.length,beforeChatRebind);
  assert(h.context.scopeInvalidated);
  s=fresh();let closed=0,cancelled=0,pickerClosed=0;
  h.context.destinationDialog={close(){pickerClosed++}};h.context.close=()=>{closed++};h.context.cancelExport=()=>{cancelled++};
  h.context.busy=true;h.context.cancelRequested=false;h.context.invalidateSelectedScope=productionInvalidate;
  h.context.invalidateSelectedScope();h.context.invalidateSelectedScope();
  assert(h.context.scopeInvalidated);assert.equal(closed,1);assert.equal(cancelled,1);assert.equal(pickerClosed,1);
} else if(scenario==='api2-preview'){
  const id='9007199254740993',chat='-9007199254740997',contact='9007199254740999';
  const response={schema:1,pages:2,firstCount:3,secondCount:3,boundaryOverlapCount:1,
   firstAdvanced:true,secondAdvanced:true,emptyFollowupWindow:false,cursorFieldSeen:false,
   hasMoreFieldSeen:false,totalFieldSeen:false,complete:false};
  function scope(h){
   const owner={selfInfo:{id,accountId:'session-17'}};
   const properties={chatId:chat,dialogContactId:contact,isPreventCopyingEnabled:false};
   const wrapper={get:()=>properties};
   const model={currentAccountId:'session-17',activeAccountDependency:{commonDependency:owner}};
   Object.assign(h.context,{commonDependency:owner,nativeChatId:chat,nativeChatProperties:wrapper,
    chatTitle:'Synthetic chat',selectedScope:{owner,propertiesContext:wrapper,accountKey:'session-17',
     ownId:id,nativeChatId:chat,chatId:chat,contactId:contact,title:'Synthetic chat'},
    application:{accountModel:model},scopeInvalidated:false,closedByUser:false,
    nativeHistoryPreviewAvailable:true,previewRequested:false,previewBusy:false,
    pendingPreviewRequest:null,pendingRequest:null,busy:false,phase:'idle',previewStatusText:'',
    destinationDialog:{close(){}},close(){}});
   return {model,properties};
  }
  function fake(h,mutate){
   const sent=[];
   h.context.XMLHttpRequest=class {
    open(method,route){assert.equal(method,'POST');assert.equal(route,'api2.history.preview');if(mutate)mutate();}
    send(raw){sent.push({body:JSON.parse(raw),request:this});}
    abort(){this.aborted=true;}
    finish(value,status=200){this.status=status;this.responseText=JSON.stringify(value);this.readyState=4;this.onreadystatechange();}
   };
   return sent;
  }
  const h=setup();scope(h);const sent=fake(h);
  h.context.previewHistory();
  assert.equal(sent.length,1);assert.deepEqual(sent[0].body,{schema:1,ownId:id,chatId:chat});
  assert.equal(h.requests.length,0);assert(h.context.previewRequested);
  sent[0].request.finish(response);
  assert(!h.context.previewBusy);assert.equal(h.context.pendingPreviewRequest,null);
  assert(h.context.previewStatusText.includes('Полнота истории ещё не подтверждена'));
  h.context.previewHistory();assert.equal(sent.length,1);
  const failed=setup();scope(failed);const failedRequests=fake(failed);
  failed.context.previewHistory();
  failedRequests[0].request.finish({schema:1,error:'api2_login_preflight_protocol',
   serverBody:'synthetic-private'},503);
  assert(failed.context.previewStatusText.includes('Этап: вход'));
  assert(failed.context.previewStatusText.includes('api2_login_preflight_protocol'));
  assert(!failed.context.previewStatusText.includes('synthetic-private'));
  assert(!failed.context.previewStatusText.includes(chat));
  const unknown=setup();scope(unknown);const unknownRequests=fake(unknown);
  unknown.context.previewHistory();
  unknownRequests[0].request.finish({schema:1,error:'api2_login_preflight_protocol_secret'},503);
  assert.equal(unknown.context.previewStatusText,'Не удалось проверить историю этого чата.');
  for(const mutation of ['account','chat','contact']){
   const test=setup(),state=scope(test);
   const attempts=fake(test,()=>{if(mutation==='account')state.model.currentAccountId='session-18';
    else if(mutation==='chat')state.properties.chatId='-9007199254740998';
    else state.properties.dialogContactId='9007199254740998';});
   test.context.previewHistory();
   assert.equal(attempts.length,0);assert(test.context.scopeInvalidated);
   assert.equal(test.requests.length,0);
  }
  const delayed=setup(),state=scope(delayed),pending=fake(delayed);
  delayed.context.previewHistory();state.model.currentAccountId='session-18';
  pending[0].request.finish(response);
  assert(delayed.context.scopeInvalidated);
  assert(!delayed.context.previewStatusText.includes('Проверены страницы'));
  assert.equal(delayed.requests.length,0);
} else if(scenario==='api2-pacing'){
 const h=setup();let starts=0,stops=0,calls=0;
 Object.assign(h.context,{phase:'api2-next',cancelRequested:false,pendingRequest:null,
   api2Pacing:false,api2PauseRemainingMs:0,
   api2HistoryContext:{isNull:false,get:()=>({})},
   api2PaceTimer:{restart(){starts++},stop(){stops++}},
   validSelectedScope:()=>true,
   request:(route,body)=>{assert.equal(route,'api2-next');assert.equal(body.jobId,'a'.repeat(32));calls++;return true;}});
 const before=Date.now();h.context.queueApi2Window();
 assert.equal(starts,1);assert(h.context.api2Pacing);assert.equal(h.context.api2PauseRemainingMs,500);
 assert(h.context.api2PauseUntil>=before+500);
 h.context.nextApi2Window();assert.equal(calls,0);
 h.context.api2Pacing=false;h.context.nextApi2Window();assert.equal(calls,1);
 h.context.cancelRequested=true;h.context.queueApi2Window();h.context.nextApi2Window();
 assert.equal(starts,1);assert.equal(calls,1);assert.equal(stops,0);
} else if(scenario==='request-watchdog'){
 const h=setup(), sent=[];let starts=0;
 h.context.pendingRequest=null;
 h.context.requestWatchdog={interval:0,stop(){},restart(){starts++}};
 h.context.XMLHttpRequest=class {
  static DONE=4;
  open(method,url){this.method=method;this.url=url;}
  setRequestHeader(){}
  send(body){sent.push({method:this.method,url:this.url,body});}
  abort(){}
 };
 const intervals=[['api2-next',14700000],['media-chunk',10000],['start',15000],['status',15000],['cancel',15000]];
 for(const [route,expected] of intervals){
  h.context.pendingRequest=null;
  assert.strictEqual(h.context.productionRequest(route,{schema:1},()=>{}),true);
  assert.equal(h.context.requestWatchdog.interval,expected,route);
 }
 assert.equal(starts,intervals.length);
 assert.equal(sent.length,intervals.length);
 assert(sent.every(x=>x.method==='POST'&&x.body==='{"schema":1}'));
 h.context.pendingRequest={hold:true};
 assert.strictEqual(h.context.productionRequest('api2-next',{schema:1},()=>{}),false);
 assert.equal(h.context.requestWatchdog.interval,15000);
} else if(scenario==='voice'){
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

MENU_SCOPE_HARNESS = r'''
const vm=require('vm'),assert=require('assert'),fs=require('fs');
const source=JSON.parse(fs.readFileSync(0,'utf8'));
const id='9007199254740993',chat='-9007199254740997',contact='9007199254740999';
const owner={selfInfo:{id,accountId:'account-A'}};
const properties={chatId:chat,dialogContactId:contact,isPreventCopyingEnabled:false};
const wrapper={get:()=>properties};
const root={commonDependency:owner,chatPropertiesContext:wrapper,chatId:chat,contactId:contact,
 chatType:1,chatName:'Synthetic chat'};
const model={currentAccountId:'account-A',activeAccountDependency:{commonDependency:owner}};
const context={root,application:{accountModel:model},Chat:{Type:{Dialog:1}},QmlUtils:{
 contactIdString:v=>String(v),chatIdString:v=>String(v)}};
vm.createContext(context);vm.runInContext(source,context,{timeout:1000});
let scope=context.valmaxExportScope();
assert(scope);assert.strictEqual(scope.owner,owner);assert.strictEqual(scope.propertiesContext,wrapper);
assert.strictEqual(scope.accountKey,'account-A');assert.strictEqual(scope.ownId,id);
assert.strictEqual(scope.nativeChatId,chat);assert.strictEqual(scope.chatId,chat);
assert.strictEqual(scope.contactId,contact);
assert.strictEqual(scope.title,'Synthetic chat');
model.currentAccountId='account-B';assert.strictEqual(context.valmaxExportScope(),null);model.currentAccountId='account-A';
model.activeAccountDependency.commonDependency={selfInfo:{id,accountId:'account-A'}};
assert.strictEqual(context.valmaxExportScope(),null);model.activeAccountDependency.commonDependency=owner;
root.chatId='-9007199254740998';assert.strictEqual(context.valmaxExportScope(),null);root.chatId=chat;
properties.chatId='-9007199254740998';assert.strictEqual(context.valmaxExportScope(),null);properties.chatId=chat;
properties.dialogContactId='9007199254740998';assert.strictEqual(context.valmaxExportScope(),null);properties.dialogContactId=contact;
properties.isPreventCopyingEnabled=true;assert.strictEqual(context.valmaxExportScope(),null);properties.isPreventCopyingEnabled=false;
root.chatType=2;assert.strictEqual(context.valmaxExportScope(),null);root.chatType=1;
root.commonDependency={selfInfo:{id,accountId:'account-A'}};assert.strictEqual(context.valmaxExportScope(),null);
root.commonDependency=owner;root.chatPropertiesContext={get:()=>null};assert.strictEqual(context.valmaxExportScope(),null);
process.stdout.write('passed');
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
        self.assertIn("selectedScope: scope", result)
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
        for scenario in ("accounts", "selected-scope", "api2-preview", "api2-pacing", "request-watchdog", "voice", "voice-deny", "projection", "eof-honesty", "cancel-native", "cancel-before-native", "photo-callback"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([shutil.which("node"), "-e", HARNESS, scenario], input=json.dumps(functions()), capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)["passed"])

    @unittest.skipUnless(shutil.which("node"), "Node VM unavailable")
    def test_menu_scope_is_captured_from_one_owner_and_fails_closed(self):
        transformed = target.transform(self.source())
        start = transformed.index("    function valmaxExportScope() {")
        end = transformed.index("    UiKit.ActionMenuItem {", start)
        result = subprocess.run([shutil.which("node"), "-e", MENU_SCOPE_HARNESS],
                                input=json.dumps(transformed[start:end]), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "passed")

    def test_folder_picker_and_start_both_check_scope(self):
        source = PANEL.read_text(encoding="utf8")
        self.assertIn('else if (validSelectedScope()) destinationDialog.open();', source)
        self.assertIn('onAccepted: exportDialog.startExport(selectedFolder.toString())', source)
        start = source.index('function startExport(directory) {')
        self.assertLess(source.index('if (!validSelectedScope()) { invalidateSelectedScope(); return; }', start),
                        source.index('request("start",', start))

    @unittest.skipUnless(importlib.util.find_spec("PySide6"), "Qt synthetic runtime unavailable")
    def test_qml_compile_and_stable_host_lifetime(self):
        import sys
        root = Path('E:/ValMAXBuild/tmp')
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='synthetic-test-', dir=root) as temporary:
            result = subprocess.run([sys.executable, str(PANEL.with_name('chat_export_qa.py')), '--output', str(Path(temporary)/'proof')], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report['delegateRoleAccess'])
            self.assertTrue(report['creationSurvivesCallerDestruction'])
            self.assertTrue(report['selectedScopeValid'])
            self.assertTrue(report['scopeInvalidatedOnAccountSwitch'])
            self.assertFalse(report['nativeClientStarted'])
            self.assertEqual(report['warnings'], [])


if __name__ == "__main__": unittest.main()
