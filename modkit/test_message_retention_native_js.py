"""Run production retention JS against synthetic Frida memory/ownership only."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import message_retention_native as native


HARNESS = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[2],'utf8'), fixture=JSON.parse(fs.readFileSync(process.argv[3],'utf8')), selected=process.argv[4];
function setup(options={}) {
  const raw=new Map(), heaps=[], hooks=new Map(), pending=new Map(), sent=[], reads=[], writes=[], nearRequests=[], nativeCallbacks=[], generatedPredicates=new Map(), originalPrefixes=new Map();
  const bases=new Map([['core.dll',0x180000000n],['storage.dll',0x7ff900000000n]]);
  let moduleObserver, attachCount=0, next=0x10000000000n, currentThread=7;
  function memory(at,count) {
    const address=BigInt(at), heap=heaps.find(h=>address>=h.at && address+BigInt(count)<=h.at+BigInt(h.data.length));
    if(heap) {if(heap.poison) throw Error('synthetic bad pointer');return {buffer:heap.data,offset:Number(address-heap.at)};}
    const exact=raw.get(address.toString());
    if(!exact || count>exact.length) throw Error('unexpected synthetic read');
    return {buffer:exact,offset:0};
  }
  class Ptr {
    constructor(value){this.value=BigInt(value);}
    add(value){return new Ptr(this.value+BigInt(value));}
    sub(value){return new Ptr(this.value-value.value);}
    compare(value){return this.value===value.value?0:this.value<value.value?-1:1;}
    equals(value){return this.value===value.value;}
    isNull(){return this.value===0n;}
    toString(){return '0x'+this.value.toString(16);}
    toUInt32(){return Number(this.value&0xffffffffn);}
    readByteArray(count){reads.push({at:this.value,count,kind:'code'});const m=memory(this.value,count);return Uint8Array.from(m.buffer.subarray(m.offset,m.offset+count)).buffer;}
    readPointer(){reads.push({at:this.value,count:8,kind:'pointer'});const m=memory(this.value,8);return new Ptr(m.buffer.readBigUInt64LE(m.offset));}
    readS64(){reads.push({at:this.value,count:8,kind:'id'});const m=memory(this.value,8);return m.buffer.readBigInt64LE(m.offset);}
    readU8(){reads.push({at:this.value,count:1,kind:'flag'});const m=memory(this.value,1);return m.buffer[m.offset];}
    readS32(){reads.push({at:this.value,count:4,kind:'status'});const m=memory(this.value,4);return m.buffer.readInt32LE(m.offset);}
    readU32(){return this.readS32()>>>0;}
    writeByteArray(value){const m=memory(this.value,value.length);Buffer.from(value).copy(m.buffer,m.offset);}
    writePointer(value){writes.push({at:this.value,value:value.value});const m=memory(this.value,8);m.buffer.writeBigUInt64LE(value.value,m.offset);}
  }
  function alloc(size,protection='rw-'){next=(next+7n)&~7n;const at=next;next+=BigInt(size)+32n;const heap={at,data:Buffer.alloc(size),protection,poison:false};heaps.push(heap);return new Ptr(at);}
  function setPointer(at,value){const m=memory(at.value,8);m.buffer.writeBigUInt64LE(value.value,m.offset);}
  function setId(at,value){const m=memory(at.value,8);m.buffer.writeBigInt64LE(BigInt(value),m.offset);}
  function setByte(at,value){const m=memory(at.value,1);m.buffer[m.offset]=value;}
  function setStatus(at,value){const m=memory(at.value,4);m.buffer.writeInt32LE(value,m.offset);}
  const config=JSON.parse(JSON.stringify(fixture));
  config.retentionEnabled=!!options.enabled;
  config.knownMessages=options.knownMessages||[];
  for(const item of config.specification.modules) {
    const base=new Ptr(bases.get(item.name));item.modulePath='D:\\synthetic\\'+item.name;
    for(const pin of item.pins)raw.set(base.add(pin.rva).value.toString(),Buffer.from(pin.expected,'hex'));
    for(const slot of item.slots) {
      raw.set(base.add(slot.thunkRva).value.toString(),Buffer.from(slot.thunkExpected,'hex'));
      const value=Buffer.alloc(8);value.writeBigUInt64LE(base.add(slot.thunkRva).value);
      raw.set(base.add(slot.vtableRva+slot.slot).value.toString(),value);
    }
  }
  const core=new Ptr(bases.get('core.dll'));
  if(options.badBytes)raw.set(core.add(0xc03db0).value.toString(),Buffer.alloc(64));
  if(options.badStorage)raw.set(new Ptr(bases.get('storage.dll')).add(0x3c6662).value.toString(),Buffer.alloc(64));
  if(options.badThunk)raw.set(core.add(0x71b7).value.toString(),Buffer.alloc(5));
  if(options.badSlot)raw.set(core.add(0x18de708+0x38).value.toString(),Buffer.alloc(8));
  if(options.badSpec)config.specification.modules[0].pins[0].rva++;
  if(options.badSettings)config.retentionEnabled=1;
  const context={VALMAX_RETENTION:config,Process:{platform:options.platform||'windows',arch:options.arch||'x64',pointerSize:8,
    getCurrentThreadId(){return currentThread;},pageSize:4096,
    findRangeByAddress(pointer){
      for(const item of config.specification.modules) {
        const base=new Ptr(bases.get(item.name));
        if(pointer.compare(base)>=0&&pointer.compare(base.add(item.imageSize))<0)
          return {base,size:item.imageSize,protection:options.nonexec?'r--':'r-x'};
      }
      const heap=heaps.find(h=>pointer.value>=h.at&&pointer.value<h.at+BigInt(h.data.length));
      if(!heap||heap.poison)return null;
      return {base:new Ptr(heap.at),size:heap.data.length,protection:heap.protection};
    },attachModuleObserver(observer){moduleObserver=observer;return {detach(){}};}},
    Interceptor:{attach(address,callbacks){
      assert.strictEqual(typeof callbacks,'object');
      if(++attachCount===options.failAttach)throw Error('synthetic attach failure');
      hooks.set(address.toString(),callbacks);return {detach(){hooks.delete(address.toString());}};
    },replaceFast(address,code){
      if(++attachCount===options.failAttach)throw Error('synthetic replace failure');
      hooks.set(address.toString(),{replacement:generatedPredicates.get(code.toString()),target:code});
      originalPrefixes.set(address.toString(),Buffer.from(raw.get(address.value.toString())));
      const patch=Buffer.from(raw.get(address.value.toString()));patch[0]=0xe9;patch.writeInt32LE(Number(code.value-address.value-5n),1);patch.fill(0x90,5,19);raw.set(address.value.toString(),patch);
      return address.add(64);
    },revert(address){hooks.delete(address.toString());if(originalPrefixes.has(address.toString()))raw.set(address.value.toString(),originalPrefixes.get(address.toString()));},flush(){}},
    ptr:value=>new Ptr(value),Memory:{alloc(size,opts){
      if(opts&&opts.near){
        nearRequests.push(opts.maxDistance);
        const second=opts.near.equals(new Ptr(bases.get('storage.dll')).add(0x3d15b0));
        if(options.failNear==='all'||options.failNear==='narrow'&&opts.maxDistance===0x1000000||options.failNear==='second'&&second)
          throw Error('synthetic near allocation exhausted');
        const saved=next;
        next=opts.near.value+(options.redirectDisplacement!==undefined?5n+options.redirectDisplacement:
          (opts.maxDistance===0x1000000?0x400000n:0x4000000n)+BigInt(heaps.length*4096));
        const value=alloc(size,'rwx');next=saved;return value;
      }
      return alloc(size,'rwx');
    },protect(){return !options.badProtect;},patchCode(address,count,callback){callback(address);}},
    NativeCallback:function(callback){const value=alloc(16,'r-x');value.callback=callback;nativeCallbacks.push(value);return value;},
    X86Writer:function(address){return new Proxy({offset:128,pc:address.add(128),
      putMovRegAddress(register,value){if(value.callback)generatedPredicates.set(address.toString(),value);return true;}},
      {get(object,name){if(name in object)return object[name];return ()=>true;}});},
    X86Relocator:function(address,writer){let index=0,prior=0;const first=address.equals(new Ptr(bases.get('storage.dll')).add(0x3d0da0));
      const ends=first?[7,10,12,19]:[4,8,12,15,19],mnemonics=first?['lea','test','je','cmp']:['mov','mov','mov','sub','sar'];
      this.readOne=()=>{this.input={mnemonic:options.badRelocation?'call':mnemonics[index],address:address.add(prior)};prior=ends[index++];return prior;};this.writeAll=()=>true;this.dispose=()=>{};},
    Instruction:{parse(address){const value=hooks.get(address.toString());return {mnemonic:'jmp',opStr:value.target.toString(),size:options.badOverwrite?14:5};}},
    recv:(type,callback)=>pending.set(type,callback),
    send(value){if(options.sendThrows)throw Error('synthetic send failure');sent.push(JSON.parse(JSON.stringify(value)));}};
  vm.runInNewContext(source,context,{timeout:1000});
  const nativeModules=config.specification.modules.map(item=>({name:item.name,base:new Ptr(bases.get(item.name)),
    path:options.foreignPath&&item.name==='core.dll'?'D:\\other\\core.dll':item.modulePath,
    size:options.badImageSize&&item.name==='core.dll'?1:item.imageSize}));
  function added(index){moduleObserver.onAdded(nativeModules[index]);}
  if(!options.delayed&&moduleObserver){added(0);added(1);}
  function post(type,body){const cb=pending.get(type);if(cb)cb({type,...body});}
  function makeEvent(detail={}) {
    const event=alloc(0x2400),reader=alloc(16),adapter=alloc(0x28),processor=alloc(0x100),chat=alloc(0x620),vector=alloc(24);
    const ids=detail.ids||['9007199254740993','9223372036854775806'],data=alloc(Math.max(8,ids.length*8));
    setPointer(event,core.add(0x18eaf80));setPointer(reader,adapter);setPointer(adapter,core.add(0x18de2e8));
    setPointer(adapter.add(0x18),processor);setPointer(processor,core.add(0x18de708));
    setId(processor.add(0xd8),detail.account||'123456789');setId(chat,detail.chat||'-987654321');
    setByte(event.add(0x23e9),detail.ttlPresent||0);setByte(event.add(0x23e8),detail.ttl||0);
    setByte(event.add(0x2378),detail.post||0);ids.forEach((id,index)=>setId(data.add(index*8),id));
    setPointer(vector,data);setPointer(vector.add(8),data.add(ids.length*8));setPointer(vector.add(16),data.add(ids.length*8));
    return {event,reader,adapter,processor,chat,vector,data,ids};
  }
  const storage=new Ptr(bases.get('storage.dll'));
  function makeOwner(account='123456789',existing=null) {
    const processor=alloc(0x100),data=alloc(0xa0),history=alloc(0x98),factory=alloc(0x58);
    const database=existing?existing.database:alloc(16),control=existing?existing.control:alloc(16);
    setPointer(processor,core.add(0x18de708));setId(processor.add(0xd8),account);setPointer(processor.add(0x30),data);
    setPointer(data,storage.add(0x983ab8));setId(data.add(0x18),'47');setPointer(data.add(0x90),history);
    setPointer(history,storage.add(0x985058));setPointer(history.add(0x88),factory);
    setPointer(factory,storage.add(0x9849c8));setPointer(factory.add(0x48),database);setPointer(factory.add(0x50),control);
    const call={},hook=hooks.get(core.add(0xbf36b0).toString());
    if(hook){hook.onEnter.call(call,[processor]);hook.onLeave.call(call,processor);}
    return {processor,data,history,factory,database,control};
  }
  function makeRows(detail={}) {
    const owner=detail.owner||makeOwner(detail.account),history=alloc(0xe8),vector=alloc(24),stack=alloc(4096);
    const rows=detail.rows||[{id:'901',status:0},{id:'902',status:2},{id:'902',status:0},{id:'903',status:2}];
    const begin=alloc(Math.max(8,rows.length*0x1a8)),sp=stack.add(0x800),chat=detail.chat||'-987654321';
    setPointer(history,storage.add(0x9895d8));setPointer(history.add(0x30),owner.database);setPointer(history.add(0x38),owner.control);
    setId(history.add(0xd8),chat);setStatus(sp.add(0x28),detail.mode||0);
    setPointer(vector,begin);setPointer(vector.add(8),begin.add(rows.length*0x1a8));setPointer(vector.add(16),begin.add(rows.length*0x1a8));
    rows.forEach((row,index)=>{setId(begin.add(index*0x1a8),row.id);setStatus(begin.add(index*0x1a8+0x128),row.status);});
    return {owner,history,vector,begin,sp,rows,chat};
  }
  function syncRows(item,detail={}) {
    const thread=detail.thread||7,call={threadId:thread,context:{sp:item.sp}};
    const hook=hooks.get(storage.add(0x3d0b90).toString()),first=hooks.get(storage.add(0x3d0da0).toString()),second=hooks.get(storage.add(0x3d15b0).toString());
    if(!detail.noEntry&&hook)hook.onEnter.call(call,[item.history,new Ptr(0),item.vector,new Ptr(0)]);
    const before=writes.length, skipped=[],written=[],elements=[];currentThread=thread;
    const gateSp=item.sp.add(detail.badStack?-0x7a0:-0x7a8),bp=item.sp.add(-0x6a8);
    item.rows.forEach((row,index)=>{
      const pointer=item.begin.add(index*0x1a8);
      const skip=first&&first.replacement?first.replacement.callback(item.history,pointer,bp,gateSp):0;
      if(skip===1)skipped.push(index);else written.push(index);
      if(detail.afterFirst)detail.afterFirst(index);
    });
    item.rows.forEach((row,index)=>{
      const skip=second&&second.replacement?second.replacement.callback(item.history,item.begin.add(index*0x1a8),bp,gateSp):0;
      if(skip!==1)elements.push(index);
    });
    if(!detail.noEntry&&hook)hook.onLeave.call(call,new Ptr(0));
    return {skipped,written,elements,inputWrites:writes.slice(before),originalCleanup:true};
  }
  function eventEnter(item,thread=7){const call={threadId:thread};const hook=hooks.get(core.add(0x153a1a0).toString());if(hook)hook.onEnter.call(call,[item.event,item.reader]);return call;}
  function eventLeave(call){const hook=hooks.get(core.add(0x153a1a0).toString());if(hook)hook.onLeave.call(call,new Ptr(0));}
  function process(item,detail={}) {
    const startWrites=writes.length, hook=hooks.get(core.add(0xc03db0).toString());
    if(hook)hook.onEnter.call({threadId:detail.thread||7,returnAddress:core.add(detail.wrongCaller?0:0xbcef5f)},[item.processor,item.chat,item.vector]);
    const begin=item.vector.readPointer(),end=item.vector.add(8).readPointer(),cap=item.vector.add(16).readPointer();
    // Simulate the exact owned-vector contract, SQL loop and original cleanup.
    const native={begin:begin.toString(),end:end.toString(),cap:cap.toString(),writes:writes.slice(startWrites),
      deleted:end.equals(begin)?[]:item.ids.slice(),ownedArgumentsDestroyed:true,coroutineScheduled:true};
    const m=memory(item.vector.value,24);m.buffer.fill(0,m.offset,m.offset+24);
    return native;
  }
  function invoke(item,detail={}) {const call=detail.noScope?null:eventEnter(item,detail.thread||7);const result=process(item,detail);if(call)eventLeave(call);result.ackContinued=true;return result;}
  return {context,config,hooks,reads,writes,sent,nearRequests,core,storage,makeEvent,makeOwner,makeRows,syncRows,eventEnter,eventLeave,process,invoke,post,setPointer,setId,setByte,setStatus,
    poison(pointer){heaps.find(h=>h.at===pointer.value).poison=true;},readonly(pointer){heaps.find(h=>h.at===pointer.value).protection='r--';},
    added,removed:index=>moduleObserver.onRemoved(nativeModules[index]),snapshot:()=>JSON.parse(JSON.stringify(context.messageRetentionSnapshot()))};
}
function preservation() {
  const value=setup({enabled:true}),item=value.makeEvent(),result=value.invoke(item);
  assert.strictEqual(value.snapshot().ready,true);assert.strictEqual(value.hooks.size,7);
  assert.deepStrictEqual(result.deleted,[]);assert.strictEqual(result.begin,result.end);
  assert.strictEqual(result.cap,item.data.add(item.ids.length*8).toString());
  assert.strictEqual(result.writes.length,1);assert.strictEqual(result.writes[0].at,item.vector.add(8).value);
  assert(result.ownedArgumentsDestroyed&&result.coroutineScheduled&&result.ackContinued);
  assert.deepStrictEqual(value.sent,[{kind:'retained-delete',schema:1,accountId:'123456789',chatId:'-987654321',messageIds:item.ids}]);
  assert.deepStrictEqual(value.snapshot(),{ready:true,enabled:true,retainedEvents:1,retainedMessages:2,error:null,historySyncVerified:false});
  return 11;
}
function passThrough() {
  for(const detail of [{noScope:true},{wrongCaller:true}]) {
    const value=setup({enabled:true}),item=value.makeEvent(),result=value.invoke(item,detail);
    assert.deepStrictEqual(result.deleted,item.ids);assert.strictEqual(result.writes.length,0);assert.strictEqual(value.sent.length,0);
  }
  const off=setup(),item=off.makeEvent();assert.deepStrictEqual(off.invoke(item).deleted,item.ids);assert.strictEqual(off.sent.length,0);
  const value=setup({enabled:true}),poison=new Proxy({}, {get(){throw Error('argument read outside scope');}});
  value.hooks.get(value.core.add(0xc03db0).toString()).onEnter.call({threadId:99},[poison,poison,poison]);
  assert.strictEqual(value.snapshot().error,null);
  return 9;
}
function ttlAndNesting() {
  for(const detail of [{ttlPresent:1,ttl:1},{ttlPresent:2},{ttlPresent:1,ttl:3},{post:1}]) {
    const value=setup({enabled:true}),item=value.makeEvent(detail),result=value.invoke(item);
    assert.deepStrictEqual(result.deleted,item.ids);assert.strictEqual(value.sent.length,0);
  }
  const value=setup({enabled:true}),outer=value.makeEvent(),inner=value.makeEvent({ttlPresent:1,ttl:1});
  // Same processor instance makes this a meaningful nested-scope barrier test.
  value.setPointer(inner.adapter.add(0x18),outer.processor);inner.processor=outer.processor;
  const out=value.eventEnter(outer),inside=value.eventEnter(inner);
  assert.deepStrictEqual(value.process(inner).deleted,inner.ids);value.eventLeave(inside);
  assert.deepStrictEqual(value.process(outer).deleted,[]);value.eventLeave(out);
  const explicit=value.makeEvent({ttlPresent:1,ttl:0});assert.deepStrictEqual(value.invoke(explicit).deleted,[]);
  const absent=value.makeEvent({ttlPresent:0,ttl:213});assert.deepStrictEqual(value.invoke(absent).deleted,[]);
  return 12;
}
function bindings() {
  const value=setup({enabled:true}),one=value.makeEvent(),two=value.makeEvent({account:'456789123'}),call=value.eventEnter(one);
  assert.deepStrictEqual(value.process(two).deleted,two.ids);
  assert.deepStrictEqual(value.process(one,{thread:8}).deleted,one.ids);
  value.eventLeave(call);assert.strictEqual(value.sent.length,0);
  for(const detail of [{account:'0'},{account:'-1'},{account:'9223372036854775807'},
      {chat:'0'},{chat:'9223372036854775807'},{ids:['-1']},{ids:['0']},{ids:['9223372036854775807']}]) {
    const v=setup({enabled:true}),item=v.makeEvent(detail),result=v.invoke(item);
    assert.deepStrictEqual(result.deleted,item.ids);assert.strictEqual(v.sent.length,0);
  }
  return 19;
}
function bounds() {
  for(const which of ['vector','chat','processor','data']) {
    const value=setup({enabled:true}),item=value.makeEvent();
    if(which==='vector')value.readonly(item.vector);else value.poison(item[which]);
    const call=value.eventEnter(item);
    const cb=value.hooks.get(value.core.add(0xc03db0).toString());
    cb.onEnter.call({threadId:7,returnAddress:value.core.add(0xbcef5f)},[item.processor,item.chat,item.vector]);
    value.eventLeave(call);assert.strictEqual(value.writes.length,0);assert.strictEqual(value.sent.length,0);
  }
  for(const count of [0,4097]) {
    const value=setup({enabled:true}),item=value.makeEvent({ids:Array(count).fill('12345')}),result=value.invoke(item);
    assert.strictEqual(result.writes.length,0);assert.strictEqual(value.sent.length,0);
  }
  const max=setup({enabled:true}),large=max.makeEvent({ids:Array.from({length:4096},(_,index)=>String(12345+index))});
  assert.deepStrictEqual(max.invoke(large).deleted,[]);assert.strictEqual(max.sent[0].messageIds.length,4096);
  for(const mode of ['unaligned','reverse','overcap','fraction']) {
    const value=setup({enabled:true}),item=value.makeEvent();
    if(mode==='unaligned')value.setPointer(item.vector,item.data.add(1));
    if(mode==='reverse')value.setPointer(item.vector.add(8),item.data.sub({value:8n}));
    if(mode==='overcap')value.setPointer(item.vector.add(16),item.data);
    if(mode==='fraction')value.setPointer(item.vector.add(8),item.data.add(9));
    assert.strictEqual(value.invoke(item).writes.length,0);assert.strictEqual(value.sent.length,0);
  }
  return 22;
}
function settingsAndStorage() {
  const value=setup({enabled:true}),before=value.snapshot();
  for(const bad of [{enabled:1},{enabled:true,other:1},{}])value.post('valmax-retention-settings',bad);
  for(const bad of [{available:0},{available:false,other:1},{}])value.post('valmax-retention-storage',bad);
  assert.deepStrictEqual(value.snapshot(),before);
  value.post('valmax-retention-storage',{available:false});
  assert.strictEqual(value.snapshot().ready,false);assert.strictEqual(value.snapshot().error,'storage_unavailable');
  let item=value.makeEvent();assert.deepStrictEqual(value.invoke(item).deleted,item.ids);assert.strictEqual(value.sent.length,0);
  value.post('valmax-retention-storage',{available:true});assert.strictEqual(value.snapshot().ready,true);
  item=value.makeEvent();assert.deepStrictEqual(value.invoke(item).deleted,[]);
  value.post('valmax-retention-settings',{enabled:false});item=value.makeEvent();assert.deepStrictEqual(value.invoke(item).deleted,item.ids);
  assert.strictEqual(value.snapshot().retainedEvents,1);return 9;
}
function guards() {
  for(const which of ['badBytes','badStorage','badThunk','badSlot','foreignPath','badImageSize','nonexec','badSpec','badSettings']) {
    const value=setup({[which]:true,enabled:true});assert.strictEqual(value.snapshot().ready,false,which);
    assert.strictEqual(value.hooks.size,0,which);assert(['module_mismatch','invalid_specification'].includes(value.snapshot().error));
  }
  for(const detail of [{platform:'linux'},{arch:'arm64'}]) {
    const value=setup(detail);assert.strictEqual(value.snapshot().error,'unsupported_platform');assert.strictEqual(value.hooks.size,0);
  }
  const failed=setup({enabled:true,failAttach:2});assert.strictEqual(failed.snapshot().error,'hook_failed');assert.strictEqual(failed.hooks.size,0);
  for(const detail of [{failAttach:6},{failAttach:7},{badProtect:true},{badRelocation:true},{badOverwrite:true}]) {
    const failed=setup({...detail,enabled:true});assert.strictEqual(failed.snapshot().error,'hook_failed');assert.strictEqual(failed.hooks.size,0);
    for(const rva of [0x3d0da0,0x3d15b0]) {
      const pin=failed.config.specification.modules[1].pins.find(item=>item.rva===rva);
      const actual=Buffer.from(failed.storage.add(rva).readByteArray(32)).toString('hex');assert.strictEqual(actual,pin.expected);
    }
  }
  return 53;
}
function lifecycle() {
  const value=setup({enabled:true,delayed:true});assert.strictEqual(value.snapshot().ready,false);
  value.added(0);assert.strictEqual(value.hooks.size,0);value.added(1);assert.strictEqual(value.snapshot().ready,true);
  value.added(0);assert.strictEqual(value.hooks.size,7);
  value.removed(1);assert.strictEqual(value.snapshot().error,'module_unloaded');assert.strictEqual(value.snapshot().ready,false);
  const item=value.makeEvent();assert.deepStrictEqual(value.invoke(item).deleted,item.ids);
  value.added(1);assert.strictEqual(value.snapshot().ready,false);return 8;
}
function allocationFallback() {
  const narrow=0x1000000,wide=0x70000000;
  const fallback=setup({enabled:true,failNear:'narrow'});
  assert.deepStrictEqual(fallback.nearRequests,[narrow,wide,narrow,wide]);
  assert.strictEqual(fallback.snapshot().ready,true);assert.strictEqual(fallback.hooks.size,7);
  assert.deepStrictEqual(fallback.invoke(fallback.makeEvent()).deleted,[]);
  for(const failNear of ['all','second']) {
    const failed=setup({enabled:true,failNear});
    assert.deepStrictEqual(failed.nearRequests,failNear==='all'?[narrow,wide]:[narrow,narrow,wide]);
    assert.strictEqual(failed.snapshot().error,'hook_failed');assert.strictEqual(failed.hooks.size,0);
    for(const rva of [0x3d0da0,0x3d15b0]) {
      const pin=failed.config.specification.modules[1].pins.find(item=>item.rva===rva);
      assert.strictEqual(Buffer.from(failed.storage.add(rva).readByteArray(32)).toString('hex'),pin.expected);
    }
  }
  // Allocation options are not a substitute for checking the real pointer.
  for(const redirectDisplacement of [0x80000010n,-0x80000010n]) {
    const failed=setup({enabled:true,redirectDisplacement});
    assert.strictEqual(failed.snapshot().error,'hook_failed');assert.strictEqual(failed.hooks.size,0);
    assert.deepStrictEqual(failed.nearRequests,[narrow]);
  }
  return 21;
}
function sendFailure() {
  const value=setup({enabled:true,sendThrows:true}),item=value.makeEvent(),result=value.invoke(item);
  assert.deepStrictEqual(result.deleted,item.ids);assert.strictEqual(value.sent.length,0);
  assert.strictEqual(result.writes.length,2);assert.strictEqual(result.end,item.data.add(item.ids.length*8).toString());
  assert.strictEqual(value.snapshot().error,'emit_failed');assert.strictEqual(value.snapshot().retainedEvents,0);
  assert(result.ackContinued&&result.ownedArgumentsDestroyed);return 7;
}
function duplicateIds() {
  const value=setup({enabled:true}),item=value.makeEvent({ids:['9007199254740993','12345','9007199254740993','12345']});
  assert.deepStrictEqual(value.invoke(item).deleted,[]);
  assert.deepStrictEqual(value.sent[0].messageIds,['9007199254740993','12345']);
  assert.strictEqual(value.snapshot().retainedMessages,2);return 3;
}
function knownRows() {
  const value=setup({enabled:true,knownMessages:[{accountId:'123456789',chatId:'-987654321',messageIds:['902']}]}),item=value.makeRows();
  const result=value.syncRows(item);
  assert.deepStrictEqual(result.skipped,[1]);assert.deepStrictEqual(result.written,[0,2,3]);
  assert.deepStrictEqual(result.elements,[0,2,3]);assert.strictEqual(result.inputWrites.length,0);
  assert.deepStrictEqual(value.sent,[{kind:'received-messages',schema:1,accountId:'123456789',chatId:'-987654321',messageIds:['901']},
    {kind:'retained-delete',schema:1,accountId:'123456789',chatId:'-987654321',messageIds:['902']}]);
  // An inactive account's fresh normal message seeds receipt before a later
  // tombstone in the SAME native vector; no host round trip is required.
  const same=value.makeRows({rows:[{id:'904',status:1},{id:'904',status:2}]});
  assert.deepStrictEqual(value.syncRows(same).skipped,[1]);
  assert.strictEqual(value.snapshot().retainedMessages,2);return 9;
}
function rowScopeGuards() {
  const seed=[{accountId:'123456789',chatId:'-987654321',messageIds:['902']}];
  for(const detail of [{mode:1},{mode:2},{chat:'-987654322'},{account:'7654321'}]) {
    const value=setup({enabled:true,knownMessages:seed}),item=value.makeRows(detail);
    assert.deepStrictEqual(value.syncRows(item).skipped,[]);assert.strictEqual(value.sent.filter(x=>x.kind==='retained-delete').length,0);
  }
  for(const detail of [{noEntry:true},{badStack:true}]) {
    const value=setup({enabled:true,knownMessages:seed});assert.deepStrictEqual(value.syncRows(value.makeRows(),detail).skipped,[]);
  }
  const value=setup({enabled:true,knownMessages:seed}),owner=value.makeOwner(),item=value.makeRows({owner});
  value.setPointer(item.history.add(0x38),value.core);assert.deepStrictEqual(value.syncRows(item).skipped,[]);
  // Internal Account::Id47 is deliberately NOT canonical selfContact123456789.
  const valid=value.makeRows({owner});assert.deepStrictEqual(value.syncRows(valid).skipped,[1]);return 12;
}
function ownerLifecycle() {
  const seed=[{accountId:'123456789',chatId:'-987654321',messageIds:['902']}];
  const value=setup({enabled:true,knownMessages:seed}),owner=value.makeOwner(),item=value.makeRows({owner});
  const saved=owner.data.add(0x90).readPointer();value.setPointer(owner.data.add(0x90),new (value.core.constructor)(0));
  assert.deepStrictEqual(value.syncRows(item).skipped,[]);value.setPointer(owner.data.add(0x90),saved);
  assert.deepStrictEqual(value.syncRows(item).skipped,[1]);
  value.makeOwner('555555',owner);assert.deepStrictEqual(value.syncRows(item).skipped,[]);
  const fresh=setup({enabled:true,knownMessages:seed}),own=fresh.makeOwner(),rows=fresh.makeRows({owner:own});
  fresh.hooks.get(fresh.core.add(0xbf4a30).toString()).onEnter.call({},[own.processor]);
  assert.deepStrictEqual(fresh.syncRows(rows).skipped,[]);return 4;
}
function receiptBatches() {
  const value=setup({enabled:true}),item=value.makeRows({rows:Array.from({length:700},(_,index)=>({id:String(10000+index),status:index%2}))});
  const result=value.syncRows(item);assert.strictEqual(result.written.length,700);assert.strictEqual(result.skipped.length,0);
  assert.deepStrictEqual(value.sent.map(x=>x.messageIds.length),[256,256,188]);
  assert(value.sent.every(x=>x.kind==='received-messages'));assert.strictEqual(result.inputWrites.length,0);return 5;
}
function knownValidationAndCapacity() {
  const value=setup({enabled:true}),record={accountId:'123456789',chatId:'-987654321',messageIds:['902']};
  for(const records of [[{...record,other:true}],[{...record,messageIds:['01']}],[{...record,messageIds:['9223372036854775808']}],[{...record,messageIds:['902']},{}]])
    value.post('valmax-retention-known',{schema:1,records});
  assert.deepStrictEqual(value.syncRows(value.makeRows({rows:[{id:'902',status:2}]})).skipped,[]);
  value.post('valmax-retention-known',{schema:1,records:[record]});
  assert.deepStrictEqual(value.syncRows(value.makeRows()).skipped,[1]);
  // Canonical MAX values in a host seed are valid even though native sentinel
  // values are intentionally never protected as actual received rows.
  value.post('valmax-retention-known',{schema:1,records:[{...record,messageIds:['9223372036854775807']}]});
  assert.strictEqual(value.snapshot().error,null);
  const full=setup({enabled:true});
  for(let batch=0;batch<16;batch++) {
    const records=Array.from({length:16},(_,group)=>({accountId:'123456789',chatId:'-987654321',
      messageIds:Array.from({length:256},(_,index)=>String(1000+batch*4096+group*256+index))}));
    full.post('valmax-retention-known',{schema:1,records});
  }
  full.post('valmax-retention-known',{schema:1,records:[{...record,messageIds:['9999999']}]});
  assert.strictEqual(full.snapshot().error,'known_capacity');assert.strictEqual(full.snapshot().ready,false);
  assert.deepStrictEqual(full.syncRows(full.makeRows({rows:[{id:'1000',status:2}]})).skipped,[]);return 7;
}
function midCallDisable() {
  const value=setup({enabled:true,knownMessages:[{accountId:'123456789',chatId:'-987654321',messageIds:['902']}]}),item=value.makeRows();
  const result=value.syncRows(item,{afterFirst(index){if(index===1)value.post('valmax-retention-settings',{enabled:false});}});
  assert.deepStrictEqual(result.skipped,[1]);assert.deepStrictEqual(result.elements,[0,2,3]);
  assert.strictEqual(result.inputWrites.length,0);assert.strictEqual(value.snapshot().enabled,false);return 4;
}
const cases={preservation,passThrough,ttlAndNesting,bindings,bounds,settingsAndStorage,guards,lifecycle,sendFailure,duplicateIds,
  knownRows,rowScopeGuards,ownerLifecycle,receiptBatches,knownValidationAndCapacity,midCallDisable,allocationFallback};
assert(cases[selected]);console.log(JSON.stringify({case:selected,checks:cases[selected]()}));
"""


@unittest.skipUnless(shutil.which('node'), 'Node unavailable for synthetic JS VM')
class MonitorTests(unittest.TestCase):
    def run_case(self, name):
        modules = copy.deepcopy(list(native.MODULES))
        for item in modules:
            item['modulePath'] = 'D:/synthetic/' + item['name']
        config = {'specification': {'schema': 1, 'modules': modules,
                                   'limits': native.LIMITS, 'historySyncVerified': False},
                  'retentionEnabled': False, 'storageAvailable': True}
        directory = Path(__file__).resolve().parents[1] / 'analysis/message-retention-v1/vm-tests'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as owned:
            harness, fixture = Path(owned) / 'harness.js', Path(owned) / 'fixture.json'
            harness.write_text(HARNESS, encoding='utf8')
            fixture.write_text(json.dumps(config), encoding='utf8')
            result = subprocess.run([shutil.which('node'), str(harness),
                                     str(Path(native.__file__).with_name('message_retention_monitor.js')),
                                     str(fixture), name], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['case'], name)

    def test_owned_vector_original_cleanup_and_ack(self): self.run_case('preservation')
    def test_local_range_unknown_paths_and_default_off(self): self.run_case('passThrough')
    def test_ttl_and_nested_event_barrier(self): self.run_case('ttlAndNesting')
    def test_account_thread_instance_and_id_bindings(self): self.run_case('bindings')
    def test_memory_vector_bounds_and_empty_semantics(self): self.run_case('bounds')
    def test_strict_settings_and_host_storage_failure(self): self.run_case('settingsAndStorage')
    def test_all_module_and_byte_guards_before_attach(self): self.run_case('guards')
    def test_both_modules_required_and_unload_disables(self): self.run_case('lifecycle')
    def test_synchronous_send_failure_restores_owned_vector(self): self.run_case('sendFailure')
    def test_duplicate_ids_are_deduplicated_for_marker_store(self): self.run_case('duplicateIds')
    def test_generic_known_rows_and_pointer_specific_second_phase(self): self.run_case('knownRows')
    def test_generic_mode_frame_account_and_database_binding(self): self.run_case('rowScopeGuards')
    def test_native_owner_lazy_join_ambiguity_and_destructor(self): self.run_case('ownerLifecycle')
    def test_received_rows_batch_after_original_sync(self): self.run_case('receiptBatches')
    def test_known_batch_atomic_validation_capacity_and_int64(self): self.run_case('knownValidationAndCapacity')
    def test_already_skipped_pointer_remains_paired_after_disable(self): self.run_case('midCallDisable')
    def test_bounded_near_allocation_fallback_and_failure_rollback(self): self.run_case('allocationFallback')


if __name__ == '__main__':
    unittest.main()
