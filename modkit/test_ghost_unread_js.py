"""Run the production unread bridge against synthetic account/chat models."""
from pathlib import Path
import json, shutil, subprocess, unittest
import ghost_unread_native

HARNESS = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const all=fs.readFileSync(process.argv[1],'utf8').replace(/\r\n/g,'\n');
const source=all.split('// BEGIN_UNREAD_UI_BRIDGE\n')[1].split('// END_UNREAD_UI_BRIDGE')[0];
const pin=JSON.parse(process.argv[2]);
let checks=0;
function fixture(options={}) {
 const pointers=new Map(),integers=new Map(),bytes=new Map(),hooks=new Map();let next=0x50000000n,active=true,attaches=0,getters=0;
 class I64{constructor(value){this.value=BigInt.asIntN(64,BigInt(value instanceof I64?value.value:value));}compare(other){const v=BigInt(other instanceof I64?other.value:other);return this.value===v?0:this.value<v?-1:1;}toString(){return this.value.toString();}}
 class Ptr{constructor(value){this.value=BigInt.asUintN(64,BigInt(value));}add(v){return new Ptr(this.value+BigInt(v));}sub(v){return new Ptr(this.value-BigInt(v));}equals(other){return this.value===other.value;}toString(){return'0x'+this.value.toString(16);}readPointer(){assert(pointers.has(this.toString()),'unplanned pointer read');return pointers.get(this.toString());}writePointer(v){pointers.set(this.toString(),v);return this;}readS64(){assert(integers.has(this.toString()),'unplanned integer read');return new I64(integers.get(this.toString()));}writeS64(v){integers.set(this.toString(),BigInt(v instanceof I64?v.value:v));return this;}readS32(){return Number(this.readS64().value);}readU8(){assert(bytes.has(this.toString()),'unplanned byte read');return bytes.get(this.toString());}}
 function alloc(size=0x100){const p=new Ptr(next);next+=BigInt(size+0x100);return p;}
 const base=new Ptr(0x140000000n),coreBase=new Ptr(0x180000000n),scratch=[];
 const context={Int64:I64,ptr:v=>new Ptr(v),Memory:{alloc:size=>{const value=alloc(size);scratch.push(value);return value;}},
  NativeFunction:function(address,result,args,opts){assert.equal(result,'pointer');assert.equal(opts.abi,'win64');assert.equal(address.value,coreBase.add(pin.selfInfo.getterRva).value);return(reader,output)=>{getters++;output.writeS64(options.invalidId?0:55);return output;};},
  Interceptor:{attach(address,callbacks){if(++attaches===options.failAttach)throw Error('fixture attach failure');const key=address.toString();hooks.set(key,callbacks);return{detach(){hooks.delete(key);}};}}};
 vm.runInNewContext(source,context);
 const bridge=context.unreadBridge({base},pin,()=>active,{base:coreBase});bridge.install();
 const owners=[];
 function owner(){const o=alloc(0x200),reader=alloc(),model=alloc();
  o.add(0x68).writePointer(reader);reader.writePointer(coreBase.add(pin.selfInfo.readerVtableRva));reader.add(pin.selfInfo.modelOffset).writePointer(model);model.writePointer(coreBase.add(pin.selfInfo.modelVtableRva));
  coreBase.add(pin.selfInfo.readerVtableRva+pin.selfInfo.readerSlot).writePointer(coreBase.add(pin.selfInfo.readerThunkRva));coreBase.add(pin.selfInfo.modelVtableRva+pin.selfInfo.modelSlot).writePointer(coreBase.add(pin.selfInfo.modelThunkRva));owners.push(o);return o;}
 function hook(rva,registers,args=[],leave=false,invocation=null){const at=(rva===pin.deletionRva?coreBase:base).add(rva);const cb=hooks.get(at.toString());assert(cb,'missing hook');const call=invocation||{threadId:3,context:registers};if(leave)cb.onLeave.call(call);else if(typeof cb==='function')cb.call(call,args);else cb.onEnter.call(call,args);return call;}
 function incoming(owner,chatId,raw,server,readCursor=-1,id=readCursor){
  const chat=alloc(0x600),state=alloc(0x60),head=alloc(),participant=alloc(),sentinel=alloc();
  chat.writeS64(chatId);chat.add(0x260).writeS64(raw);chat.add(0x18).writePointer(head);head.add(8).writePointer(participant);
  bytes.set(chat.add(0x580).toString(),0);
  participant.add(0x19);bytes.set(participant.add(0x19).toString(),0);participant.add(0x20).writeS64(55);participant.add(0x28).writeS64(server);
  state.add(0x18).writeS64(id);state.add(0x20).writeS64(readCursor);
  const c={rdi:owner,r15:chat,r14:state,rbx:new Ptr(readCursor),rdx:sentinel,r9:sentinel};
  const call=hook(pin.incomingRva,c,[owner,chat,state]);hook(pin.missingRva,c);hook(pin.countRva,c);
  const credit=c.rdx.equals(sentinel)?0:Number(c.rdx.add(0x28).readS64().value);
  const value=Math.max(0,raw-credit);assert.equal(state.add(0x20).readS64().value,BigInt(readCursor));checks++;
  hook(pin.incomingRva,c,[],true,call);return{value,credit,node:c.rdx,branch:c.rbx};
 }
 function read(owner,chatId,cursor,before,after,id=cursor){const node=alloc(),pair=alloc();node.add(0x10).writeS64(chatId);pair.writeS64(id);pair.add(8).writeS64(cursor);hook(pin.decrementRva,{rsi:owner,rbp:node,r14:pair,rcx:new Ptr(before),rbx:new Ptr(after)});}
 return{bridge,owner,incoming,read,hook,Ptr,I64,hooks,scratch,getters:()=>getters,setActive:value=>{active=value;bridge.clear();}};
}
const f=fixture(),a=f.owner(),b=f.owner();
assert.equal(f.incoming(a,1,10,0).value,10);checks++;
f.read(a,1,5,10,5);assert.equal(f.incoming(a,1,12,0,5).value,7);checks++;
// Partial ACK must retire credit: raw9, not the incorrect 9-5=4.
assert.equal(f.incoming(a,1,9,3,5).value,9);checks++;
assert.equal(f.incoming(a,1,10,3,5).value,10);checks++;
f.read(a,1,8,10,7);assert.equal(f.incoming(a,1,10,3,8).value,7);checks++;
assert.equal(f.incoming(a,1,7,8,8).value,7);checks++;
assert.equal(f.incoming(a,1,8,8,8).value,8);checks++;
// Same chat ID on a different account never inherits credit.
assert.equal(f.incoming(b,1,10,0).value,10);checks++;
f.read(b,1,4,10,6);assert.equal(f.incoming(b,1,12,0,4).value,8);checks++;
// Deletion/raw decrease gives the conservative server value.
assert.equal(f.incoming(b,1,11,0,4).value,11);checks++;
f.incoming(b,5,10,0);f.read(b,5,5,10,5);f.bridge.clear();
assert.equal(f.incoming(b,5,10,0,5).value,10);checks++;
// Use actual clamped decrement, not a larger loaded-range delta.
assert.equal(f.incoming(a,2,2,0).value,2);f.read(a,2,10,2,0);
assert.equal(f.incoming(a,2,3,0,10).value,1);checks+=2;
// Repeated cursor cannot accrue credit twice.
f.read(a,2,10,2,0);assert.equal(f.incoming(a,2,3,0,10).value,1);checks++;
// Clearing policy keeps scratch nodes alive until the script unloads.
const retained=f.incoming(a,2,3,0,10).node;f.setActive(false);
assert.equal(retained.add(0x28).readS64().value,2n);assert.equal(f.incoming(a,2,3,0,10).value,3);checks+=2;
f.setActive(true);f.incoming(a,3,6,0);f.read(a,3,3,6,3);
f.hook(pin.chatDeleteRva,{},[a.add(8),new f.Ptr(3)]);
assert.equal(f.incoming(a,3,6,0,3).value,6);checks++;
f.incoming(a,4,6,0);f.read(a,4,3,6,3);f.hook(pin.destructorRva,{},[a]);
assert.equal(f.incoming(a,4,6,0,3).value,6);checks++;
const unknown=fixture({invalidId:true}),o=unknown.owner();unknown.incoming(o,1,5,0);unknown.read(o,1,5,5,0);
assert.equal(unknown.incoming(o,1,6,0,5).value,6);checks++;
assert(f.getters()>0);checks++;
for(const failAttach of [1,2,3,4,5,6])assert.throws(()=>fixture({failAttach}),/fixture attach failure/);checks+=6;
f.bridge.detach();assert.equal(f.hooks.size,0);checks++;
console.log(JSON.stringify({checks}));
"""


class GhostUnreadJavascriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_native_decrement_overlay_account_scope_ack_and_lifetime(self):
        run = subprocess.run([shutil.which('node'), '-e', HARNESS,
                              str(Path(__file__).with_name('ghost_monitor.js')),
                              json.dumps(ghost_unread_native.PIN)],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertGreaterEqual(json.loads(run.stdout)['checks'], 40)

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_existing_delete_entry_invalidates_even_when_retention_is_disabled(self):
        source=Path(__file__).with_name('message_retention_monitor.js').read_text(encoding='utf8')
        start=source.index('    function processorEnter(args) {')
        stop=source.index('        if (!filtering()) return;',start)+len('        if (!filtering()) return;')
        prefix=source[start:stop]+'\n}'
        script=r"""
const vm=require('vm'),assert=require('assert');let clears=0;
const source=process.argv[1];const poison=new Proxy({}, {get(){throw Error('deletion argument inspected');}});
const context={filtering:()=>false,invalidateUnreadCredits:function(){assert.equal(arguments.length,0);clears++;}};
vm.runInNewContext(source,context);context.processorEnter(poison);assert.equal(clears,1);
const absent={filtering:()=>false};vm.runInNewContext(source,absent);absent.processorEnter(poison);
"""
        run=subprocess.run([shutil.which('node'),'-e',script,prefix],capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stderr)


if __name__ == '__main__':
    unittest.main()
