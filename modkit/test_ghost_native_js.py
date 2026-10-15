"""Execute the production ghost JS in a Node VM with synthetic native memory."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
import unittest
import ghost_native
import ghost_unread_native


HARNESS = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
const fixture = JSON.parse(process.argv[2]), selected = process.argv[3];
function setup(options = {}) {
  const calls = [], codeReads = [], replacements = new Map(), originals = new Map(), pending = new Map(), attached = new Map();
  let moduleObserver, replaceCount = 0, flushed = 0;
  const baseValue = 0x180000000n, rawBytes = new Map(), pointers = new Map();
  class Ptr {
    constructor(value) {this.value = BigInt(value);}
    add(delta) {return new Ptr(this.value + BigInt(delta));}
    and(mask) {return new Ptr(this.value & BigInt(mask));}
    compare(other) {return this.value === other.value ? 0 : this.value < other.value ? -1 : 1;}
    equals(other) {return this.value === other.value;}
    toString() {return '0x' + this.value.toString(16);}
    readByteArray(count) {
      codeReads.push({at:this.toString(), count});
      const data = rawBytes.get(this.toString()); assert(data, 'unexpected native memory read');
      assert(count <= data.length, 'code read exceeded guard');
      return Uint8Array.from(data.subarray(0,count)).buffer;
    }
    readPointer() {const result = pointers.get(this.toString()); assert(result,'unexpected pointer read'); return result;}
    writeU8(value) {assert.strictEqual(this.value, 0x70000000n); rawBytes.set(this.toString(),Buffer.from([value]));}
    readU8() {assert.strictEqual(this.value, 0x70000000n); return rawBytes.get(this.toString())[0];}
  }
  const base = new Ptr(baseValue), spec = JSON.parse(JSON.stringify(fixture));
  spec.modulePath = 'D:\\synthetic\\core.dll';
  spec.unread.modulePath = 'D:\\synthetic\\max.exe';
  spec.unread.moduleSha256 = 'f'.repeat(64); spec.unread.moduleSize = 70000000; spec.unread.imageSize = 0x5000000;
  const mainBase = new Ptr(0x140000000n);
  for(const item of spec.unread.guards) rawBytes.set(mainBase.add(item.rva).toString(),Buffer.from(item.expected,'hex'));
  pointers.set(mainBase.add(spec.unread.vtableRva).toString(),mainBase.add(spec.unread.destructorRva));
  pointers.set(mainBase.add(spec.unread.vtableRva+8).toString(),mainBase.add(spec.unread.readRva));
  const selfInfo = spec.unread.selfInfo;
  for(const item of selfInfo.guards) rawBytes.set(base.add(item.rva).toString(),Buffer.from(item.expected,'hex'));
  pointers.set(base.add(selfInfo.readerVtableRva+selfInfo.readerSlot).toString(),base.add(selfInfo.readerThunkRva));
  pointers.set(base.add(selfInfo.modelVtableRva+selfInfo.modelSlot).toString(),base.add(selfInfo.modelThunkRva));
  for (const item of spec.wrappers) {
    rawBytes.set(base.add(item.rva).toString(), Buffer.from(item.expected,'hex'));
    rawBytes.set(base.add(item.thunkRva).toString(), Buffer.from(item.thunkExpected,'hex'));
    pointers.set(base.add(item.vtableRva+item.slot).toString(),base.add(item.thunkRva));
    originals.set(base.add(item.rva).toString(), (...args)=>calls.push({feature:item.feature,args}));
  }
  for (const item of Object.values(spec.online)) rawBytes.set(base.add(item.rva).toString(), Buffer.from(item.expected,'hex'));
  const ping=spec.online.ping, login=spec.online.login;
  rawBytes.set(base.add(ping.serializerRva).toString(),Buffer.from(ping.serializerExpected,'hex'));
  rawBytes.set(base.add(ping.thunkRva).toString(),Buffer.from(ping.thunkExpected,'hex'));
  rawBytes.set(base.add(login.thunkRva).toString(),Buffer.from(login.thunkExpected,'hex'));
  pointers.set(base.add(login.vtableRva+login.slot).toString(),base.add(login.thunkRva));
  pointers.set(base.add(ping.vtableRva+ping.slot).toString(),base.add(ping.thunkRva));
  originals.set(base.add(login.rva).toString(), (...args)=>calls.push({feature:'login',args}));
  originals.set(base.add(spec.online.encoder.rva).toString(), (byte,output)=>{assert.strictEqual(byte.readU8(),0);output.encoded.push(false);});
  if (options.badBytes) rawBytes.set(base.add(spec.wrappers[1].rva).toString(), Buffer.alloc(42));
  if (options.badThunk) rawBytes.set(base.add(spec.wrappers[0].thunkRva).toString(), Buffer.alloc(5));
  if (options.badVtable) pointers.set(base.add(spec.wrappers[1].vtableRva+8).toString(),base.add(17));
  if (options.badSpec) spec.wrappers[0].rva++;
  if (options.badLogin) rawBytes.set(base.add(login.rva).toString(),Buffer.alloc(9));
  if (options.badEncoder) rawBytes.set(base.add(spec.online.encoder.rva).toString(),Buffer.alloc(41));
  if (options.badPing) rawBytes.set(base.add(ping.serializerRva).toString(),Buffer.alloc(315));
  if (options.badOnlineSpec) spec.online.login.rva++;
  const initial = options.initial || {revision:'a'.repeat(64),settings:{enabled:false,hideTyping:false,hideRead:false}};
  const context = {
    MONITOR:{ghost: options.noSpec ? null : spec,ghostSettings:initial},
    Memory:{alloc:count=>{assert([1,4096].includes(count));return new Ptr(count===1?0x70000000n:0x71000000n);}},
    Process:{platform:options.platform||'windows',arch:options.arch||'x64',pointerSize:8,
      findRangeByAddress:()=>options.nonexec ? {base,size:spec.imageSize,protection:'r--'} : {base,size:spec.imageSize,protection:'r-x'},
      attachModuleObserver: observer=>{moduleObserver=observer;return {detach(){}};}},
    NativeFunction:function(address,result,args,opts) {
      assert(['void','pointer'].includes(result)); assert.strictEqual(opts.abi,'win64');
      assert.strictEqual(opts.scheduling,'cooperative'); assert.strictEqual(opts.exceptions,'propagate');
      const item=spec.wrappers.find(i=>base.add(i.rva).equals(address));
      assert.deepStrictEqual(Array.from(args),item ? item.arguments : ['pointer','pointer']);
      if(result==='pointer')return()=>{throw Error('unexpected model call');};
      return (...values)=>originals.get(address.toString())(...values);
    },
    NativeCallback:function(callback,result,args,abi) {
      assert.strictEqual(result,'void'); assert.strictEqual(abi,'win64');
      assert(args[1]==='int64' || args[1]==='pointer'); return callback;
    },
    Interceptor:{replace(address,callback) {
      if (++replaceCount === options.failReplace) throw new Error('synthetic failure');
      replacements.set(address.toString(),callback);
    },revert(address){replacements.delete(address.toString());},flush(){flushed++;},
    attach(address,callbacks){if(options.failAttach)throw Error('synthetic attach failure');attached.set(address.toString(),callbacks);return {detach(){attached.delete(address.toString());}};}},
    recv:(type,callback)=>pending.set(type,callback)
  };
  vm.runInNewContext(source,context,{timeout:1000});
  const module = {name:'core.dll',path:options.foreignPath?'D:\\foreign\\core.dll':spec.modulePath,
      base,size:options.badImageSize?1:spec.imageSize};
  const mainModule={name:'max.exe',path:spec.unread.modulePath,base:mainBase,size:spec.unread.imageSize};
  if(moduleObserver)moduleObserver.onAdded(mainModule);
  if (!options.delayed && moduleObserver) moduleObserver.onAdded(module);
  function hit(feature,...args) {
    const pin=spec.wrappers.find(item=>feature==='chatRead' ? item.selectorExcept===1 : item.feature===feature);
    const thunk=base.add(pin.vtableRva+pin.slot).readPointer();
    assert(thunk.equals(base.add(pin.thunkRva)));
    const key=base.add(pin.rva).toString();
    return (replacements.get(key)||originals.get(key))(...args);
  }
  function post(settings,revision='b'.repeat(64),extra={}) {
    const callback=pending.get('valmax-ghost-settings');
    if(callback) callback({type:'valmax-ghost-settings',revision,settings,...extra});
  }
  function hitLogin(self,output) {const key=base.add(login.rva).toString();return(replacements.get(key)||originals.get(key))(self,output);}
  function hitPing(context) {const listener=attached.get(base.add(ping.rva).toString());if(listener)listener.onEnter.call({context});}
  return {calls,codeReads,replacements,attached,hit,hitLogin,hitPing,post,context,module,Ptr,
    added:()=>moduleObserver.onAdded(module),removed:()=>moduleObserver.onRemoved(module),
    snapshot:()=>JSON.parse(JSON.stringify(context.ghostSnapshot())),flushes:()=>flushed};
}
function delegation() {
  const value=setup(); assert.strictEqual(value.snapshot().ready,true);
  const poisoned = new Proxy({}, {get(){throw new Error('argument inspected');}});
  const id = new Proxy({}, {get(){throw new Error('64-bit id inspected');}});
  value.hit('hideTyping',poisoned,id,7); value.hit('hideRead',poisoned,id,poisoned);
  assert.strictEqual(value.calls.length,2);
  assert.strictEqual(value.calls[0].args[0],poisoned); assert.strictEqual(value.calls[0].args[1],id);
  assert.strictEqual(value.calls[1].args[2],poisoned);
  assert.deepStrictEqual(value.snapshot().blocked,{typing:0,read:0,presence:0,stories:0});
  assert.strictEqual(value.replacements.size,5); assert.strictEqual(value.attached.size,7); assert.strictEqual(value.flushes(),2);
  return 9;
}
function controls() {
  const value=setup(), input={enabled:true,hideTyping:true,hideRead:false};
  value.post(input); input.hideTyping=false;
  value.hit('hideTyping',null,9223372036854775807n,2); value.hit('hideRead',null,9223372036854775807n,null);
  assert.strictEqual(value.calls.length,1); assert.strictEqual(value.snapshot().blocked.typing,1);
  value.post({enabled:true,hideTyping:false,hideRead:true},'c'.repeat(64));
  value.hit('hideTyping',null,-9223372036854775808n,0); value.hit('hideRead',null,0n,null);
  assert.strictEqual(value.calls.length,2); assert.deepStrictEqual(value.snapshot().blocked,{typing:1,read:1,presence:0,stories:0});
  value.post({enabled:false,hideTyping:true,hideRead:true},'d'.repeat(64));
  value.hit('hideTyping',null,1n,0); value.hit('hideRead',null,1n,null);
  assert.strictEqual(value.calls.length,4); assert.strictEqual(value.snapshot().revision,'d'.repeat(64));
  return 6;
}
function validation() {
  const value=setup(), unchanged=value.snapshot();
  for(const settings of [null,[],{enabled:1,hideTyping:true,hideRead:true},
      {enabled:true,hideTyping:true}, {enabled:true,hideTyping:true,hideRead:true,hidden:true}]) value.post(settings);
  value.post({enabled:true,hideTyping:true,hideRead:true},'bad');
  value.post({enabled:true,hideTyping:true,hideRead:true},'b'.repeat(64),{extra:true});
  value.post({enabled:true,hideTyping:true,hideRead:true},unchanged.revision);
  assert.deepStrictEqual(value.snapshot(),unchanged);
  for(const initial of [null,{}, {revision:'f'.repeat(64),settings:{enabled:true,hideTyping:true,hideRead:1}}]) {
    const bad=setup({initial:initial===null?{}:initial});
    assert.strictEqual(bad.snapshot().ready,false); assert.strictEqual(bad.snapshot().error,'invalid_settings');
    assert.strictEqual(bad.replacements.size,0);
  }
  return 10;
}
function guards() {
  for(const option of ['badBytes','badThunk','badVtable','foreignPath','badImageSize','nonexec','badSpec','badLogin','badEncoder','badPing','badOnlineSpec']) {
    const value=setup({[option]:true});
    assert.strictEqual(value.snapshot().ready,false,option); assert.strictEqual(value.replacements.size,0,option);
    assert(['module_mismatch','invalid_specification'].includes(value.snapshot().error));
  }
  for(const options of [{platform:'linux'},{arch:'arm64'}]) {
    const value=setup(options); assert.strictEqual(value.snapshot().error,'unsupported_platform');
    assert.strictEqual(value.replacements.size,0);
  }
  const absent=setup({noSpec:true}); assert.strictEqual(absent.snapshot().ready,false);
  assert.strictEqual(absent.snapshot().error,null);
  const fail=setup({failReplace:2}); assert.strictEqual(fail.snapshot().error,'hook_failed');
  assert.strictEqual(fail.replacements.size,0,'first replacement must roll back');
  for (const options of [{failReplace:5},{failAttach:true}]) {const fail=setup(options);assert.strictEqual(fail.snapshot().ready,false);assert.strictEqual(fail.replacements.size,0);assert.strictEqual(fail.attached.size,0);}
  return 29;
}
function lifecycle() {
  const value=setup({delayed:true,initial:{revision:'e'.repeat(64),settings:{enabled:true,hideTyping:true,hideRead:true}}});
  assert.strictEqual(value.snapshot().ready,false); assert.strictEqual(value.snapshot().error,'waiting_for_core');
  value.added(); assert.strictEqual(value.snapshot().ready,true);
  value.hit('hideTyping',null,0n,0); value.hit('hideRead',null,0n,null);
  assert.strictEqual(value.calls.length,0); assert.deepStrictEqual(value.snapshot().blocked,{typing:1,read:1,presence:0,stories:0});
  value.added(); assert.strictEqual(value.replacements.size,5);
  const snapshot=value.snapshot(); snapshot.blocked.typing=900; snapshot.features.hideRead=false;
  assert.strictEqual(value.snapshot().blocked.typing,1); assert.strictEqual(value.snapshot().features.hideRead,true);
  value.removed(); assert.strictEqual(value.snapshot().ready,false); assert.strictEqual(value.snapshot().error,'module_unloaded');
  return 10;
}
function bound() {
  const value=setup({initial:{revision:'e'.repeat(64),settings:{enabled:true,hideTyping:true,hideRead:true}}});
  for(let index=0;index<1000005;index++) value.hit('hideTyping',null,0n,0);
  assert.strictEqual(value.snapshot().blocked.typing,1000000);
  assert.strictEqual(value.calls.length,0);
  return 2;
}
function chatRead() {
  const value=setup({initial:{revision:'e'.repeat(64),settings:{enabled:true,hideTyping:true,hideRead:true}}});
  const self=Object.freeze({}), chat=9223372036854775807n;
  for (const selector of [0,2,-1,2147483647,-2147483648]) value.hit('chatRead',self,chat,selector);
  assert.strictEqual(value.calls.length,0); assert.strictEqual(value.snapshot().blocked.read,5);
  value.hit('chatRead',self,chat,1);
  assert.strictEqual(value.calls.length,1); assert.strictEqual(value.calls[0].args[2],1);
  assert.strictEqual(value.calls[0].args[0],self); assert.strictEqual(value.calls[0].args[1],chat);
  value.post({enabled:true,hideTyping:true,hideRead:false},'f'.repeat(64));
  value.hit('chatRead',self,chat,0); value.hit('chatRead',self,chat,1);
  assert.strictEqual(value.calls.length,3); assert.strictEqual(value.snapshot().blocked.read,5);
  return 8;
}
function onlineStories() {
  const value=setup(), poisoned=new Proxy({}, {get(){throw Error('private argument inspected');}}), output={encoded:[]};
  value.hit('hideStories',poisoned,poisoned,-9223372036854775808n);
  value.hitLogin(poisoned,output); assert.strictEqual(value.calls.length,2);
  value.post({enabled:true,hideTyping:false,hideRead:false,hideOnline:true,hideStories:true});
  value.hit('hideStories',poisoned,poisoned,9223372036854775807n); value.hitLogin(poisoned,output);
  assert.strictEqual(value.calls.length,2);assert.deepStrictEqual(output.encoded,[false]);
  const registers={rax:new value.Ptr('0x123456789abcde01'),rbx:new value.Ptr('0x76543210'),rflags:new value.Ptr(0x202)};
  const other=registers.rbx, flags=registers.rflags;
  value.hitPing(registers);assert.strictEqual(registers.rax.value,0x123456789abcde00n);assert.strictEqual(registers.rbx,other);assert.strictEqual(registers.rflags,flags);
  assert.deepStrictEqual(value.snapshot().blocked,{typing:0,read:0,presence:2,stories:1});
  // No setter/clear-reaction wrapper (opcode 213) is replaced.
  assert(!value.replacements.has(value.module.base.add(0x123d4e0).toString()));assert(!value.replacements.has(value.module.base.add(0x1241fa0).toString()));
  value.post({enabled:false,hideTyping:false,hideRead:false,hideOnline:true,hideStories:true},'c'.repeat(64));
  value.hit('hideStories',poisoned,poisoned,7n);value.hitLogin(poisoned,output);
  registers.rax=new value.Ptr('0x123456789abcde01');value.hitPing(registers);assert.strictEqual(registers.rax.value,0x123456789abcde01n);
  assert.strictEqual(value.calls.length,4);assert.deepStrictEqual(output.encoded,[false]);
  // Migration of a legacy policy never silently enables the new filters.
  value.post({enabled:true,hideTyping:true,hideRead:true},'d'.repeat(64));
  assert.strictEqual(value.snapshot().hideOnline,false);assert.strictEqual(value.snapshot().hideStories,false);
  return 15;
}
const cases={delegation,controls,validation,guards,lifecycle,bound,chatRead,onlineStories}; assert(cases[selected]);
console.log(JSON.stringify({case:selected,checks:cases[selected]()}));
"""


@unittest.skipUnless(shutil.which('node'), 'Node unavailable for JS VM')
class GhostMonitorJavascriptTests(unittest.TestCase):
    def fixture(self, name, checks):
        spec = {'schema': 1, 'moduleSha256': ghost_native.CORE_SHA256,
                'moduleSize': ghost_native.CORE_FILE_SIZE, 'imageSize': ghost_native.CORE_IMAGE_SIZE,
                'wrappers': list(ghost_native.WRAPPERS), 'online': ghost_native.ONLINE}
        spec['unread'] = ghost_unread_native.PIN
        result = subprocess.run([shutil.which('node'), '-e', HARNESS,
                                 str(Path(__file__).with_name('ghost_monitor.js')),
                                 json.dumps(spec), name], capture_output=True, text=True,
                                timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'case': name, 'checks': checks})

    def test_void_delegation_preserves_args_and_never_dereferences_them(self):
        self.fixture('delegation', 9)

    def test_each_toggle_and_master_switch_and_copied_settings(self):
        self.fixture('controls', 6)

    def test_invalid_policy_messages_do_not_override_last_valid_state(self):
        self.fixture('validation', 10)

    def test_platform_complete_byte_virtual_slot_path_and_rollback_guards(self):
        self.fixture('guards', 29)

    def test_synchronous_module_install_initial_enabled_and_unload_status(self):
        self.fixture('lifecycle', 10)

    def test_blocked_counter_saturates_at_the_host_schema_limit(self):
        self.fixture('bound', 2)

    def test_manual_read_control_blocks_read_selectors_but_preserves_set_as_unread(self):
        self.fixture('chatRead', 8)

    def test_online_boolean_only_and_story_seen_without_touching_reactions(self):
        self.fixture('onlineStories', 15)


if __name__ == '__main__':
    unittest.main()
