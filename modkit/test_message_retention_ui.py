"""Exact native transforms and marker-only controller with synthetic data."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import message_retention_ui as target
import history_cache_ui
import scroll_performance_ui

NATIVE = Path('D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi')
CONTROLLER = Path(__file__).with_name('message_retention_controller.qml')
SETTINGS = CONTROLLER.with_name('message_retention_settings_panel.qml')


def controller_functions():
    source = CONTROLLER.read_text('utf8')
    return '\n'.join(re.findall(r'^    function [^\n]+\n.*?^    \}\n', source, re.M | re.S))


def settings_functions():
    source = SETTINGS.read_text('utf8')
    return '\n'.join(re.findall(r'^        function [^\n]+\n.*?^        \}\n', source, re.M | re.S))


HARNESS = r'''
const vm=require('vm'),assert=require('assert'),fs=require('fs');
const source=JSON.parse(fs.readFileSync(0,'utf8')), scenario=process.argv[1];
const ACCOUNT='9007199254740993', CHAT='-9007199254740995', MESSAGE='9007199254740997';
function setup() {
 const xhrs=[], queue=[], timers={};
 function timer(name){return timers[name]={running:false,restarts:0,stop(){this.running=false;},restart(){this.running=true;this.restarts++;}};}
 const c={String,Object,Array,JSON,Number,Math,Date,
   accountId:ACCOUNT,chatId:CHAT,active:true,enabled:false,ready:false,revision:'',markers:{},registrations:{},scopeGeneration:1,registrationCount:0,
   pendingRequest:null,cycleIds:[],cycleOffset:0,cycleScope:-1,rescanNeeded:false,completed:true,closing:false,maxRegisteredIds:4096,maxBatchIds:256,
   debounceTimer:timer('debounce'),requestWatchdog:timer('watchdog'),pollTimer:timer('poll'),
   Qt:{callLater(fn){queue.push(fn);}},
   XMLHttpRequest:class {
    static DONE=4;
    constructor(){this.headers={};this.aborted=false;this.readyState=0;xhrs.push(this);}
    open(method,url){this.method=method;this.url=url;}
    setRequestHeader(k,v){this.headers[k]=v;}
    send(body){this.body=JSON.parse(body);}
    abort(){this.aborted=true;}
    respond(value,status=200){this.status=status;this.responseText=typeof value==='string'?value:JSON.stringify(value);this.readyState=4;this.onreadystatechange();}
   }
 };
 vm.createContext(c);vm.runInContext(source,c,{timeout:1000});
 function drain(){let steps=0;while(queue.length){assert(++steps<100);queue.shift()();}}
 function reply(x,deleted=[],extra={}){return Object.assign({schema:1,accountId:x.body.accountId,chatId:x.body.chatId,deletedIds:deleted,enabled:true,ready:true,revision:'a'.repeat(64)},extra);}
 return {c,xhrs,queue,timers,drain,reply};
}
if(scenario==='ids') {
 const {c}=setup();
 for(const id of ['1',MESSAGE,'9223372036854775807'])assert(c.validId(id,true));
 assert(c.validId('-9223372036854775808',false));
 for(const id of ['0','-0','01','+1','1.0','1e3','9223372036854775808','-9223372036854775809',1,null,'__proto__'])assert(!c.validId(id,false));
 assert(!c.validId('-1',true));
} else if(scenario==='refcounts') {
 const {c}=setup();
 const token=c.register(MESSAGE);assert.equal(token,1);assert.equal(c.register(MESSAGE),1);assert.equal(c.registrationCount,1);assert.equal(c.registrations[MESSAGE],2);
 c.enabled=true;c.ready=true;c.markers={[MESSAGE]:true};assert(c.contains(MESSAGE));
 c.unregister(MESSAGE,token);assert.equal(c.registrations[MESSAGE],1);assert(c.contains(MESSAGE));
 c.unregister(MESSAGE,token);assert.equal(c.registrationCount,0);assert(!c.contains(MESSAGE));assert.deepEqual(c.markers,{});
 assert.equal(c.register('__proto__'),-1);c.maxRegisteredIds=2;c.register('1');c.register('2');assert.equal(c.register('3'),-1);
} else if(scenario==='batches') {
 const h=setup(),c=h.c;
 for(let i=1;i<=600;i++)c.register(String(i));
 c.startCycle();assert.equal(h.xhrs.length,1);assert.equal(h.xhrs[0].body.messageIds.length,256);
 h.xhrs[0].respond(h.reply(h.xhrs[0],['1','256']));h.drain();
 assert.equal(h.xhrs[1].body.messageIds.length,256);h.xhrs[1].respond(h.reply(h.xhrs[1]));h.drain();
 assert.equal(h.xhrs[2].body.messageIds.length,88);h.xhrs[2].respond(h.reply(h.xhrs[2]));h.drain();
 assert.equal(h.xhrs.length,3);assert.equal(c.cycleIds.length,0);assert(c.contains('1'));assert(c.contains('256'));
 const ids=h.xhrs.flatMap(x=>Array.from(x.body.messageIds));assert.equal(new Set(ids).size,600);
 for(const x of h.xhrs){assert.equal(x.method,'POST');assert.equal(x.url,'http://127.0.0.1:18973/message-retention/lookup');assert.deepEqual(Object.keys(x.body).sort(),['accountId','chatId','messageIds','schema']);assert.equal(x.headers['X-Max-Mod-Local-Write'],'message-retention-v1');assert.equal(x.body.accountId,ACCOUNT);assert.equal(x.body.chatId,CHAT);}
} else if(scenario==='scope-race') {
 const h=setup(),c=h.c;c.register(MESSAGE);c.startCycle();const old=h.xhrs[0],late=old.onreadystatechange;
 c.accountId='44';c.chatId='55';c.resetScope();assert(old.aborted);assert.equal(c.registrationCount,0);assert.deepEqual(c.markers,{});
 c.register(MESSAGE);const nextScope=c.scopeGeneration;c.unregister(MESSAGE,nextScope-1);assert.equal(c.registrations[MESSAGE],1);
 old.status=200;old.responseText=JSON.stringify(h.reply(old,[MESSAGE]));old.readyState=4;late();assert(!c.contains(MESSAGE));
 c.startCycle();assert.equal(h.xhrs[1].body.accountId,'44');assert.equal(h.xhrs[1].body.chatId,'55');
 h.xhrs[1].respond(h.reply(h.xhrs[1],[MESSAGE]));h.drain();assert(c.contains(MESSAGE));
} else if(scenario==='unregister-race') {
 const h=setup(),c=h.c;c.register(MESSAGE);c.register('2');c.startCycle();c.unregister(MESSAGE,c.scopeGeneration);
 h.xhrs[0].respond(h.reply(h.xhrs[0],[MESSAGE,'2']));h.drain();assert(!c.contains(MESSAGE));assert(c.contains('2'));assert(!c.markers[MESSAGE]);
} else if(scenario==='invalid-replies') {
 const mutations=[v=>({...v,accountId:'4'}),v=>({...v,chatId:'4'}),v=>({...v,deletedIds:['44']}),v=>({...v,deletedIds:[MESSAGE,MESSAGE]}),v=>({...v,deletedIds:[Number(MESSAGE)]}),v=>({...v,revision:'x'}),v=>({...v,ready:1}),v=>({...v,secret:'unexpected'}),v=>({...v,schema:2})];
 for(const mutate of mutations){const h=setup(),c=h.c;c.register(MESSAGE);c.startCycle();h.xhrs[0].respond(mutate(h.reply(h.xhrs[0],[MESSAGE])));assert(!c.ready);assert(!c.contains(MESSAGE));assert.deepEqual(c.markers,{});}
 for(const [status,raw] of [[500,'{}'],[200,'x'.repeat(32769)],[200,'not-json']]){const h=setup();h.c.register(MESSAGE);h.c.startCycle();h.xhrs[0].respond(raw,status);assert(!h.c.ready);assert.equal(h.c.pendingRequest,null);}
} else if(scenario==='visibility-timeout') {
 const h=setup(),c=h.c;c.register(MESSAGE);c.active=false;c.startCycle();assert.equal(h.xhrs.length,0);
 c.active=true;c.handleActiveChanged();assert(h.timers.debounce.running);c.startCycle();const xhr=h.xhrs[0];
 c.active=false;c.handleActiveChanged();assert(xhr.aborted);assert(!c.ready);assert.deepEqual(c.markers,{});
 c.active=true;c.startCycle();c.abortPending();c.unavailable();assert(h.xhrs[1].aborted);assert(!c.ready);assert.equal(c.cycleIds.length,0);
 c.XMLHttpRequest=class{constructor(){throw Error('synthetic');}};c.startCycle();assert(!c.ready);assert.equal(c.pendingRequest,null);
} else if(scenario==='enabled-revision') {
 const h=setup(),c=h.c;c.register(MESSAGE);c.register('2');
 c.startCycle();let disabled=h.xhrs.at(-1);disabled.respond(h.reply(disabled,[MESSAGE],{enabled:false,ready:true}));h.drain();assert(c.contains(MESSAGE));assert.equal(c.enabled,false);
 for(const flags of [{enabled:true,ready:false},{enabled:false,ready:false}]){c.startCycle();const x=h.xhrs.at(-1);x.respond(h.reply(x,[MESSAGE],flags));h.drain();assert(!c.contains(MESSAGE));assert.deepEqual(c.markers,{});}
 c.startCycle();let x=h.xhrs.at(-1);x.respond(h.reply(x,[MESSAGE]));h.drain();assert(c.contains(MESSAGE));
 c.revision='b'.repeat(64);c.markers={'2':true};c.applyReply(h.reply(x,[MESSAGE]),[MESSAGE]);assert(!c.markers['2']);assert(c.contains(MESSAGE));
}
process.stdout.write(JSON.stringify({scenario,passed:true}));
'''


SETTINGS_HARNESS = r'''
const vm=require('vm'),assert=require('assert'),fs=require('fs');
const source=JSON.parse(fs.readFileSync(0,'utf8')), scenario=process.argv[1];
function setup(){
 const xhrs=[];const c={String,Object,Array,JSON,Number,Math,Date,
  revision:'',savedEnabled:false,editorEnabled:false,loaded:false,runtime:null,runtimeTrusted:false,pendingRequest:null,pendingMethod:'',needsRefresh:false,hasError:false,closing:false,statusText:'',
  retentionSettingsWatchdog:{interval:7000,stop(){},restart(){}},
  XMLHttpRequest:class{static DONE=4;constructor(){this.headers={};xhrs.push(this);}open(method,url){this.method=method;this.url=url;}setRequestHeader(k,v){this.headers[k]=v;}send(body){this.body=body?JSON.parse(body):null;}abort(){this.aborted=true;}respond(value,status=200){this.status=status;this.responseText=typeof value==='string'?value:JSON.stringify(value);this.readyState=4;this.onreadystatechange();}}
 };
 Object.defineProperty(c,'dirty',{get(){return c.loaded&&c.editorEnabled!==c.savedEnabled;}});
 vm.createContext(c);vm.runInContext(source,c,{timeout:1000});
 c.fail=message=>{c.hasError=true;c.statusText=message;};
 function reply(enabled=false,extra={}){return Object.assign({schema:1,revision:'a'.repeat(64),enabled,runtime:{ready:true,applied:true,enabled,retainedEvents:3,retainedMessages:7,error:null,historySyncVerified:false}},extra);}
 return{c,xhrs,reply};
}
if(scenario==='settings-save'){
 const h=setup(),c=h.c;c.save();assert.equal(h.xhrs.length,0);c.request('GET','',false);assert.equal(h.xhrs[0].url,'http://127.0.0.1:18973/message-retention/status');h.xhrs[0].respond(h.reply());assert(c.loaded);assert(!c.editorEnabled);
 c.editorEnabled=true;c.save();const x=h.xhrs[1];assert.equal(x.url,'http://127.0.0.1:18973/message-retention/settings');assert.equal(x.headers['X-Max-Mod-Local-Write'],'message-retention-v1');assert.deepEqual(x.body,{schema:1,revision:'a'.repeat(64),enabled:true});x.respond(h.reply(true));assert(!c.dirty);assert(c.savedEnabled);
}else if(scenario==='settings-conflict'){
 const h=setup(),c=h.c;c.request('GET','',false);h.xhrs[0].respond(h.reply());c.editorEnabled=true;c.save();h.xhrs[1].respond({},409);assert(c.needsRefresh);assert(c.editorEnabled);assert(!c.runtimeTrusted);c.save();assert.equal(h.xhrs.length,2);
 c.request('GET','',true);h.xhrs[2].respond(h.reply(false,{revision:'b'.repeat(64)}));assert(c.editorEnabled);assert(c.needsRefresh);c.save();assert.equal(h.xhrs.length,3);
 c.request('GET','',false);h.xhrs[3].respond(h.reply(false,{revision:'b'.repeat(64)}));assert(c.editorEnabled);assert(!c.needsRefresh);c.save();assert.equal(h.xhrs[4].body.revision,'b'.repeat(64));
}else if(scenario==='settings-invalid'){
 const h=setup(),c=h.c,v=h.reply();assert(c.validResponse(v));assert(c.validResponse(h.reply(false,{runtime:{...v.runtime,ready:false,applied:false}})));
 const invalid=[{...v,extra:true},{...v,revision:'x'},{...v,enabled:1},{...v,runtime:{...v.runtime,extra:true}},{...v,runtime:{...v.runtime,retainedMessages:-1}},{...v,runtime:{...v.runtime,retainedEvents:1.5}},{...v,runtime:{...v.runtime,error:'x'.repeat(129)}},{...v,runtime:{...v.runtime,ready:false,applied:true}},{...v,runtime:{...v.runtime,historySyncVerified:1}}];
 for(const value of invalid)assert(!c.validResponse(value));
 c.request('GET','',false);h.xhrs[0].respond(v);c.editorEnabled=true;c.request('GET','',true);h.xhrs[1].respond({...v,extra:true});assert(c.editorEnabled);assert(!c.runtimeTrusted);
}else if(scenario==='settings-timeout-close'){
 const h=setup(),c=h.c;c.request('GET','',false);h.xhrs[0].respond(h.reply());c.editorEnabled=true;c.save();assert.equal(c.retentionSettingsWatchdog.interval,10000);const x=h.xhrs[1],late=x.onreadystatechange;c.abortPending();c.runtimeTrusted=false;c.needsRefresh=true;assert(x.aborted);x.readyState=4;x.status=200;x.responseText=JSON.stringify(h.reply(true));late();assert(!c.savedEnabled);assert(c.editorEnabled);
 c.closing=true;c.request('GET','',false);assert.equal(h.xhrs.length,2);
}
process.stdout.write(JSON.stringify({scenario,passed:true}));
'''


class MessageRetentionTransformTests(unittest.TestCase):
    def sources(self):
        if not NATIVE.is_dir(): self.skipTest('Immutable native QML unavailable')
        names = ('HistoryListView.qml', 'HistoryMessageDelegate.qml',
                 'VideoMessageBubbleContent.qml', 'MessageTimeCapsule.qml')
        result = {name: (NATIVE / name).read_text('utf8') for name in names}
        # Production order: bounded native cache, visibility bookkeeping, retention.
        result['HistoryListView.qml'] = scroll_performance_ui.transform_history(
            history_cache_ui.transform(result['HistoryListView.qml']))
        self.assertEqual(hashlib.sha256(result['HistoryListView.qml'].encode()).hexdigest(), target.SOURCE_SHA256)
        return result

    def test_only_history_instance_changes_and_native_bubble_types_are_preserved(self):
        original = self.sources()
        result = target.transform_sources(original | {'Unrelated.qml': 'untouched'})
        self.assertEqual(result['Unrelated.qml'], 'untouched')
        for name, source in original.items():
            self.assertEqual(result[name].count(target.MARKER), int(name == 'HistoryListView.qml'))
            for action in ('deleteMessages(', 'setMessageSelected(', 'historyMenuHandler.show()', 'viewContextFactory.create(', 'text: root.viewContext.get().text'):
                self.assertEqual(result[name].count(action), source.count(action))
        self.assertIn('String(QmlUtils.contactIdString(root.commonDependency.selfInfo.id))', result['HistoryListView.qml'])
        self.assertIn('String(QmlUtils.chatIdString(root.chatPropertiesContext.get().chatId))', result['HistoryListView.qml'])
        self.assertIn('String(QmlUtils.messageIdString(messageContext.get().messageId))', result['HistoryListView.qml'])
        self.assertIn('String(QmlUtils.chatIdString(messageContext.get().chatId))', result['HistoryListView.qml'])
        self.assertIn('valmaxRetentionMessageChatId === valmaxRetentionRegistrar.chatId', result['HistoryListView.qml'])
        pins = {
            'HistoryMessageDelegate.qml': '0c52dbbb9d5c97549b5fe9092ab642fdec6a584f6108f7d8eb601557d078d853',
            'VideoMessageBubbleContent.qml': 'baf807a7c3f43828d328f6d3611b6a0cf797d439d7cdc2bba0caf130bf11f3d1',
            'MessageTimeCapsule.qml': '4a717046a8bcc40ca1a8a9d7d3a7b55eb776fe68c792be72009aae3ad9e90a5d',
        }
        self.assertEqual(target.RESOURCES, (target.PREFIX + 'HistoryListView.qml',))
        self.assertEqual(set(target.PRESERVED_COMPILED_RESOURCES), {target.PREFIX + name for name in pins})
        for name, pin in pins.items():
            self.assertEqual(result[name], original[name])
            self.assertEqual(hashlib.sha256(result[name].encode()).hexdigest(), pin)
            self.assertNotIn('valmaxRetainedDeleted', result[name])
        history = result['HistoryListView.qml']
        self.assertIn('readonly property bool valmaxRetainedDeleted:', history)
        self.assertIn('source: "qrc:/icon_pack/delete_fill.svg"', history)
        self.assertIn('messageDelegate.viewContext.get().isVisuallyOutgoing', history)
        self.assertIn(target.TOOLTIP, history)
        self.assertEqual(target.reverse_history(history), original['HistoryListView.qml'])

    def test_instance_badge_is_lazy_inert_and_does_not_override_native_geometry(self):
        badge = target.BADGE
        for required in ('parent: messageDelegate', 'active: messageDelegate.valmaxRetainedDeleted',
                         'sourceComponent: Item', 'HoverHandler', 'enabled: false', 'height: width',
                         'status === Loader.Ready && width > 0', 'clip: true'):
            self.assertIn(required, badge)
        for forbidden in ('sourceComponent: Control', 'MouseArea', 'TapHandler', 'WheelHandler',
                          'implicitHeight:', 'implicitWidth:', 'timeWidth', 'contentItem:',
                          'Layout.', 'messageDelegate.height =', 'messageDelegate.width ='):
            self.assertNotIn(forbidden, badge)

    def test_drift_duplicate_transform_and_missing_resource_fail_closed(self):
        original = self.sources()
        source = original['HistoryListView.qml']
        for bad in (None, '', source.replace('    id: root\n', '    id: renamed\n'), target.transform_history(source), source + '\n', (NATIVE / 'HistoryListView.qml').read_text('utf8')):
            with self.subTest(value=type(bad).__name__), self.assertRaises(ValueError):
                target.transform_history(bad)
        with self.assertRaises(ValueError): target.transform_sources({})
        with self.assertRaises(ValueError): target.once('anchor anchor', 'anchor', 'new')

    def test_controller_is_marker_only_and_bounded(self):
        source = target.controller_source()
        for value in ('message.text', 'senderId', 'downloadUrl', 'access_token', 'WebSocket', 'NativeFunction', 'console.', 'Qt.openUrl', 'deleteMessages(', 'sendMessage('):
            self.assertNotIn(value, source)
        self.assertIn('maxBatchIds: 256', source)
        self.assertIn('interval: 2000', source)
        self.assertIn('interval: 7000', source)
        self.assertNotIn('Number(', source)
        self.assertNotIn('parseInt(', source)

    def test_settings_entry_is_scoped_and_preserves_native_entries(self):
        source = (NATIVE / 'LeadingSettingsBar.qml').read_text('utf8')
        result = target.transform_leading(source)
        self.assertEqual(result.count('id: settingsValMAXRetention'), 1)
        self.assertIn('title: "Удалённые сообщения"', result)
        self.assertIn('settingsPage: settingsValMAXRetention', result)
        self.assertEqual(result.count('title: qsTranslate("Settings", "Privacy")'), source.count('title: qsTranslate("Settings", "Privacy")'))
        for bad in (None, '', result, source.replace('id: settingsDev', 'id: settingsChanged')):
            with self.assertRaises(ValueError): target.transform_leading(bad)
        panel = SETTINGS.read_text('utf8')
        self.assertIn('property bool savedEnabled: false', panel)
        self.assertIn('повторная синхронизация ещё проверяется', panel)
        self.assertNotIn('purge', panel)

    @unittest.skipUnless(shutil.which('node'), 'Node VM unavailable')
    def test_marker_controller_synthetic_behaviour(self):
        for scenario in ('ids', 'refcounts', 'batches', 'scope-race', 'unregister-race', 'invalid-replies', 'visibility-timeout', 'enabled-revision'):
            with self.subTest(scenario=scenario):
                result = subprocess.run([shutil.which('node'), '-e', HARNESS, scenario], input=json.dumps(controller_functions()), capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)['passed'])

    @unittest.skipUnless(shutil.which('node'), 'Node VM unavailable')
    def test_settings_controller_synthetic_behaviour(self):
        for scenario in ('settings-save', 'settings-conflict', 'settings-invalid', 'settings-timeout-close'):
            with self.subTest(scenario=scenario):
                result = subprocess.run([shutil.which('node'), '-e', SETTINGS_HARNESS, scenario], input=json.dumps(settings_functions()), capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)['passed'])

    @unittest.skipUnless(importlib.util.find_spec('PySide6'), 'Synthetic Qt unavailable')
    def test_synthetic_qml_lifetime_and_footer_rendering(self):
        root = Path('D:/ValMAX/analysis/message-retention-v1')
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='ui-qa-', dir=root) as temporary:
            result = subprocess.run([sys.executable, str(CONTROLLER.with_name('message_retention_qa.py')), '--output', str(Path(temporary) / 'proof')], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report['delegateRefcounts'])
            self.assertTrue(report['scopeReset'])
            self.assertTrue(report['footerWidthUnchanged'])
            self.assertTrue(report['footerByteEqualBaseline'])
            self.assertTrue(report['instanceBadgeLazy'])
            self.assertTrue(report['instanceBadgeGeometryUnchanged'])
            self.assertTrue(report['messageChatMismatchRejected'])
            self.assertTrue(report['unknownMessageChatRejected'])
            self.assertTrue(report['persistedMarkerWhileCaptureOff'])
            self.assertEqual(report['warnings'], [])
            self.assertFalse(report['realClientStarted'])


if __name__ == '__main__':
    unittest.main()
