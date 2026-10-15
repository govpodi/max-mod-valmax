"""Strict candidate transforms and message-reset invariants; own files only."""
from pathlib import Path
import hashlib,json,re,shutil,subprocess,unittest
import chat_pool_ui as target
import chat_lock_ui
NATIVE=Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi")
class PoolCandidateTests(unittest.TestCase):
    def text(self,name):
        if not NATIVE.is_dir(): self.skipTest("Immutable native QML unavailable")
        if name == "ChatDelegate.baseline.qml":
            return chat_lock_ui.transform_delegate((NATIVE/"ChatDelegate.qml").read_text("utf-8"))
        if name == "ChatListView.baseline.qml":
            return (NATIVE/"ChatListView.qml").read_text("utf-8")
        if name == "ChatDelegate.qml":
            value = target.transform_delegate(self.text("ChatDelegate.baseline.qml"))
            self.assertEqual(hashlib.sha256(value.encode()).hexdigest(), "1c9c11bd58e19d53603ee2cfec909b8551e0e356774f4916aafb32e463802db8")
            return value
        if name == "ChatListView.qml":
            value = target.transform_chat_list(self.text("ChatListView.baseline.qml"))
            self.assertEqual(hashlib.sha256(value.encode()).hexdigest(), "1d487cd96a20ad668e76145bdd8b13f249d4e4a408315bf2a4dbed76f1195cb6")
            return value
        raise AssertionError("Unknown fixture resource")
    def test_pinned_native_inputs_transform_to_frozen_candidates_and_reverse(self):
        for name,forward,reverse in [('ChatDelegate',target.transform_delegate,target.reverse_delegate),('ChatListView',target.transform_chat_list,target.reverse_chat_list)]:
            source=self.text(name+'.baseline.qml');candidate=self.text(name+'.qml')
            self.assertEqual(forward(source),candidate);self.assertEqual(reverse(candidate),source)
            self.assertEqual(forward(source.replace('\n','\r\n')),candidate)
    def test_wrong_order_drift_duplicate_and_missing_resources_fail(self):
        for name,forward in [('ChatDelegate',target.transform_delegate),('ChatListView',target.transform_chat_list)]:
            source=self.text(name+'.baseline.qml')
            for bad in [None,'',source+'\n',self.text(name+'.qml'),source.replace('    id: root','    id: drift',1)]:
                with self.assertRaises(ValueError):forward(bad)
        with self.assertRaises(ValueError):target.transform_sources({})
    def test_existing_model_native_context_and_pin_bindings_preserved(self):
        baseline=self.text('ChatDelegate.baseline.qml');candidate=self.text('ChatDelegate.qml')
        for fragment in ['required property var chatId','required property var chatClientId','required property var contactId','readonly property var presenceContext:','readonly property var chatPropertiesContext:','// VALMAX_CHAT_LOCK_DELEGATE','value: !root.valmaxChatLockMask','Component.onDestruction:','onPresenceContextChanged:','onStoriesContextChanged:','onClicked: {','onDoubleClicked:']:
            self.assertEqual(candidate.count(fragment),baseline.count(fragment))
        self.assertNotRegex(candidate,r'root\.(?:chatId|chatClientId|contactId)\s*=(?!=)')
        self.assertIn('active: !root.valmaxPooled',candidate)
        self.assertIn('enabled: !valmaxPooled',candidate)
        self.assertIn('hoverEnabled: !valmaxPooled',candidate)
        self.assertIn('cursorShapeEnabled: !valmaxPooled',candidate)
        self.assertIn('allChatsListView.delegateRemoved(chatDelegate.chatId)',self.text('ChatListView.qml'))
        sources={'ChatDelegate.qml':baseline,'ChatListView.qml':self.text('ChatListView.baseline.qml'),'Other.qml':'unchanged'}
        self.assertEqual(target.transform_sources(sources)['Other.qml'],'unchanged')
    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_null_empty_and_live_message_context_reset_before_unpool(self):
        functions='\n'.join(re.findall(r'^    function [^\n]+\n.*?^    \}\n',target.ROOT_INSERT,re.M|re.S))
        js=r'''
const vm=require('vm'),assert=require('assert'),fs=require('fs');const source=JSON.parse(fs.readFileSync(0,'utf8'));
const empty={isNull:true,chatId:'empty'};const c={MessageUtils:{emptyMessageContext(){return empty;}},hasMessage:true,lastMessageContext:{isNull:false,chatId:'old'},valmaxPooled:true};
vm.createContext(c);vm.runInContext(source,c);
for(const value of [null,undefined,empty,{isNull:false,chatId:'new'}]){
 c.messageContext=value;c.valmaxPooled=true;c.lastMessageContext={isNull:false,chatId:'old'};c.hasMessage=true;
 c.valmaxSyncMessageContext();assert(c.valmaxPooled);assert.equal(c.hasMessage,!!value&&!value.isNull);assert.strictEqual(c.lastMessageContext,value==null?empty:value);
 c.valmaxReuse();assert(!c.valmaxPooled);assert.equal(c.hasMessage,!!value&&!value.isNull);assert.strictEqual(c.lastMessageContext,value==null?empty:value);
 c.valmaxPool();assert(c.valmaxPooled);
}
process.stdout.write(JSON.stringify({passed:true}));
'''
        result=subprocess.run([shutil.which('node'),'-e',js],input=json.dumps(functions),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertTrue(json.loads(result.stdout)['passed'])
if __name__=='__main__':unittest.main(verbosity=2)
