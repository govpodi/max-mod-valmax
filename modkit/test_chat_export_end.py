"""Pinned binary guards and actual JS on poisoned synthetic native headers."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import chat_export_end_specs as module


BASELINE = Path("D:/CodexLabs/max-mod-26.35.1/runtime/baseline")
JOB = "a" * 32


def page(**changes):
    result = {"kind": "chat-export-history", "jobId": JOB, "event": "page", "direction": "backward",
              "count": 5, "pendingDone": True, "moreBackward": False, "moreForward": True}
    result.update(changes)
    return result


class ContractTests(unittest.TestCase):
    def test_finite_exact_event_fields_and_no_identifiers_or_payload(self):
        self.assertEqual(module.checked_event(page()), page())
        self.assertEqual(module.proven_directions(page()), ("backward",))
        for changes in ({"count": True}, {"count": -1}, {"count": 100001}, {"pendingDone": 1},
                        {"direction": []}, {"direction": "unknown"}, {"jobId": "bad"},
                        {"object": "0x1234"}, {"messageId": "synthetic"}, {"url": "https://invalid.example/"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                module.checked_event(page(**changes))

    def test_empty_or_inflight_callbacks_never_prove_end(self):
        self.assertEqual(module.proven_directions(page(count=0, moreForward=False)), ())
        self.assertEqual(module.proven_directions(page(pendingDone=False, moreForward=False)), ())
        self.assertEqual(module.proven_directions(page(direction="forward", moreForward=False)), ("forward",))
        self.assertEqual(module.proven_directions(page(direction="center", moreForward=False)), ("backward", "forward"))
        self.assertEqual(module.proven_directions(page(direction="center", moreBackward=True, moreForward=False)), ("forward",))
        self.assertEqual(module.proven_directions({"kind": "chat-export-history", "jobId": JOB, "event": "bound"}), ())

    def test_bound_marker_is_exact_and_returned_contract_is_copied(self):
        value = {"kind": "chat-export-history", "jobId": JOB, "event": "bound"}
        result = module.checked_event(value)
        result["jobId"] = "b" * 32
        self.assertEqual(value["jobId"], JOB)
        with self.assertRaises(ValueError): module.checked_event({**value, "count": 0})

    @unittest.skipUnless((BASELINE / "Qt6Core.dll").is_file(), "Immutable baseline not installed")
    def test_immutable_baseline_matches_exact_qt_exports_code_and_type_guards(self):
        value = module.specification(BASELINE)
        self.assertEqual(value["contract"], "valmax-chat-end-v1")
        self.assertEqual(value["qt"]["sha256"], module.QT_SHA256)
        self.assertEqual(len(value["qt"]["exports"]), 3)
        self.assertEqual(value["max"]["anchors"][0]["rva"], 0xE27A00)
        self.assertEqual(value["max"]["anchors"][-1]["rva"], 0x11E15F0)
        self.assertEqual(len(value["max"]["anchors"][-1]["expected"]) // 2, 618)
        self.assertEqual(value["max"]["vtables"][1], {"rva": 0x376B808, "target": 0x4D4B4})
        source = module.source_for(BASELINE)
        self.assertTrue(source.startswith("const VALMAX_CHAT_END_SPEC="))
        self.assertNotIn("rpc.exports", source)
        self.assertNotIn("new NativeFunction", source)

    def test_relative_program_root_is_rejected_without_file_access(self):
        with patch.object(module, "_image", side_effect=AssertionError("No reads allowed")):
            with self.assertRaises(ValueError): module.specification(Path("relative"))


HARNESS = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8'), fixture=JSON.parse(process.argv[2]), selected=process.argv[3];
function setup(options={}) {
  const hooks=new Map(), sent=[], reads=[], raw=new Map(), pointers=new Map(), integers=new Map(), strings=new Map();
  let observer,clock=0,hookNumber=0,detached=0;
  class Ptr {
    constructor(value){this.value=BigInt(value);}
    add(value){return new Ptr(this.value+BigInt(value));}
    sub(value){return new Ptr(this.value-(value instanceof Ptr?value.value:BigInt(value)));}
    isNull(){return this.value===0n;}
    compare(other){return this.value===other.value?0:this.value<other.value?-1:1;}
    equals(other){return this.value===other.value;}
    toString(){return '0x'+this.value.toString(16);}
    toInt32(){return Number(BigInt.asIntN(32,this.value));}
    readByteArray(count){reads.push({kind:'code',at:this.toString(),count});const value=raw.get(this.toString());assert(value,'Unexpected non-code byte read');assert(count<=value.length);return Uint8Array.from(value.subarray(0,count)).buffer;}
    readPointer(){reads.push({kind:'pointer',at:this.toString()});const value=pointers.get(this.toString());assert(value,'Unexpected payload/request-ID pointer read');return value;}
    readS64(){reads.push({kind:'size',at:this.toString()});assert(integers.has(this.toString()),'Unexpected wide value read');return {toString:()=>String(integers.get(this.toString()))};}
    readU32(){reads.push({kind:'pending',at:this.toString()});assert(integers.has(this.toString()));return integers.get(this.toString());}
    readU8(){reads.push({kind:'flag',at:this.toString()});assert(integers.has(this.toString()));return integers.get(this.toString());}
    readUtf16String(count){reads.push({kind:'marker',at:this.toString(),count});assert(strings.has(this.toString()),'Unexpected name/body read');const value=strings.get(this.toString());assert(count<=value.length);return value.slice(0,count);}
  }
  const maximum={name:'max.exe',base:new Ptr(0x140000000n),size:fixture.max.imageSize,path:'D:\\synthetic\\max.exe'};
  const qt={name:'Qt6Core.dll',base:new Ptr(0x180000000n),size:fixture.qt.imageSize,path:'D:\\synthetic\\Qt6Core.dll',
    findExportByName:name=>{const entry=fixture.qt.exports.find(item=>item.name===name);return entry&&!options.missingExport?qt.base.add(entry.rva):null;}};
  const spec=JSON.parse(JSON.stringify(fixture));spec.max.modulePath=maximum.path;spec.qt.modulePath=qt.path;
  for(const [module,pin] of [[maximum,spec.max],[qt,spec.qt]])for(const entry of pin.anchors)raw.set(module.base.add(entry.rva).toString(),Buffer.from(entry.expected,'hex'));
  for(const entry of spec.max.vtables)pointers.set(maximum.base.add(entry.rva).toString(),maximum.base.add(entry.target));
  if(options.badBytes)raw.set(maximum.base.add(0xe27a00).toString(),Buffer.alloc(33));
  if(options.badPresenter)raw.set(maximum.base.add(0x11e15f0).toString(),Buffer.alloc(618));
  if(options.badVtable)pointers.set(maximum.base.add(0x376b808).toString(),maximum.base.add(123));
  if(options.badSpec)spec.qt.sha256='0'.repeat(64);
  if(options.badPath)qt.path='D:\\foreign\\Qt6Core.dll';
  if(options.badImage)qt.size++;
  const context={VALMAX_CHAT_END_SPEC:spec,ptr:value=>new Ptr(value),send:value=>sent.push(JSON.parse(JSON.stringify(value))),
    Date:{now:()=>clock},Process:{platform:options.platform||'windows',arch:options.arch||'x64',pointerSize:8,
      findRangeByAddress:address=>{for(const module of [maximum,qt])if(address.compare(module.base)>=0&&address.compare(module.base.add(module.size))<0)return {base:module.base,size:module.size,protection:options.nonexec?'r--':'r-x'};
        if(address.compare(new Ptr(0x300000000n))>=0&&address.compare(new Ptr(0x400000000n))<0)return {base:new Ptr(0x300000000n),size:0x100000000,protection:'rw-'};return null;},
      attachModuleObserver:value=>{observer=value;return {detach(){}};}},
    Interceptor:{attach:(address,callback)=>{if(++hookNumber===options.failHook)throw new Error('synthetic hook failure');hooks.set(address.toString(),callback);return {detach(){detached++;hooks.delete(address.toString());}};}},
    rpc:{exports:{sentinel:true}}};
  vm.runInNewContext(source,context,{timeout:1000});
  if(observer){observer.onAdded(maximum);if(!options.delayed)observer.onAdded(qt);}
  let next=0x300100000n;
  function block(){const value=new Ptr(next);next+=0x1000n;return value;}
  function view(real=true){const self=block();pointers.set(self.toString(),maximum.base.add(real?0x376b768:0x12345));integers.set(self.add(0x670).toString(),1);integers.set(self.add(0x680).toString(),1);return self;}
  function name(value){const header=block(),data=block();integers.set(header.add(16).toString(),value.length);pointers.set(header.add(8).toString(),data);strings.set(data.toString(),value);strings.set(data.add(28).toString(),value.slice(14));return header;}
  function call(key,args,leave=true,invocation={}){const hook=hooks.get(key.toString());assert(hook,'Expected observer hook missing');const state={...invocation};hook.onEnter.call(state,args);if(leave&&hook.onLeave)hook.onLeave.call(state,new Ptr(0));return {hook,state};}
  function rename(self,text){return call(qt.base.add(0xf1c00),[self,name(text)]);}
  function beginPage(self,count=3,kind=0,pending=1,moreBack=0,moreForward=1,source={}){
    const header=block(),info=block(),capture=block(),body=new Ptr(0x500000000n); // Body is deliberately unmapped/poisoned.
    pointers.set(header.toString(),count===0?new Ptr(0):body);pointers.set(header.add(8).toString(),count===0?new Ptr(0):body.add(count*0x1a8));
    integers.set(info.add(8).toString(),pending); // info[0] request ID is deliberately unreadable.
    // All other lambda capture fields are deliberately absent/unreadable.
    if(!source.missingFields){integers.set(capture.add(0x38).toString(),source.state===undefined?1:source.state);integers.set(capture.add(0x3c).toString(),source.kind===undefined?kind:source.kind);}
    integers.set(self.add(0x670).toString(),moreBack);integers.set(self.add(0x680).toString(),moreForward);
    const invocation={};if(!source.noOrigin)invocation.returnAddress=maximum.base.add(source.foreignOrigin?0x11e185b:0x11e185a);
    if(!source.noContext)invocation.context={};if(invocation.context&&!source.noRdi)invocation.context.rdi=source.invalidRdi?body:source.nullRdi?new Ptr(0):capture;
    const result=call(maximum.base.add(0xe27a00),[self.add(16),header,new Ptr(kind),info],false,invocation);result.capture=capture;return result;
  }
  function endPage(call){call.hook.onLeave.call(call.state,new Ptr(0));}
  function page(self,...args){const operation=beginPage(self,...args);endPage(operation);}
  function destroy(self){call(qt.base.add(0xeabd0),[self]);}
  return {context,hooks,sent,reads,view,rename,page,beginPage,endPage,destroy,maximum,qt,pointers,integers,strings,block,
    advance:value=>{clock+=value;},added:()=>observer.onAdded(qt),removed:()=>observer.onRemoved(qt),
    status:()=>JSON.parse(vm.runInNewContext('JSON.stringify(valmaxChatEndMonitor.status())',context)),detached:()=>detached};
}
const id='a'.repeat(32), marker='valmax-export-'+id;
function bounded() {
  const value=setup(),self=value.view();value.rename(self,marker);value.page(self,5,0,1,0,1);
  assert.deepStrictEqual(value.sent,[{kind:'chat-export-history',jobId:id,event:'bound'},
    {kind:'chat-export-history',jobId:id,event:'page',direction:'backward',count:5,pendingDone:true,moreBackward:false,moreForward:true}]);
  assert(value.reads.filter(item=>item.kind==='marker').every(item=>item.count===14||item.count===32));
  assert(value.reads.filter(item=>item.kind==='pointer').every(item=>!item.at.startsWith('0x500')));
  assert(value.sent.every(item=>!JSON.stringify(item).includes('0x')));assert.deepStrictEqual(value.context.rpc.exports,{sentinel:true});
  return 6;
}
function identity() {
  const value=setup(),ordinary=value.view(false),selected=value.view();
  const before=value.reads.length;value.rename(ordinary,marker);
  assert.strictEqual(value.reads.filter(item=>item.kind==='marker'||item.kind==='size').length,0);
  value.rename(selected,'x'.repeat(46));assert.strictEqual(value.sent.length,0);
  assert(value.reads.filter(item=>item.kind==='marker').every(item=>item.count===14));
  value.rename(selected,'valmax-export-'+'z'.repeat(32));value.rename(selected,'valmax-export-'+id+'x');
  assert.strictEqual(value.sent.length,0);value.rename(selected,marker);value.rename(selected,marker);
  assert.strictEqual(value.sent.length,1);assert.strictEqual(value.status().bound,1);return 6;
}
function synchronous() {
  const value=setup(),self=value.view();value.page(self,7,1,1,0,0);assert.strictEqual(value.sent.length,0);
  assert.strictEqual(value.status().cached,1);value.rename(self,marker);
  assert.strictEqual(value.sent.length,2);assert.strictEqual(value.sent[0].event,'bound');
  assert.deepStrictEqual(value.sent[1],{kind:'chat-export-history',jobId:id,event:'page',direction:'center',count:7,pendingDone:true,moreBackward:false,moreForward:false});
  assert.strictEqual(value.status().cached,0);
  const late=value.view();value.page(late,1,0,1,0,0);value.advance(2001);value.rename(late,'valmax-export-'+'b'.repeat(32));
  assert.strictEqual(value.sent.length,3);assert.strictEqual(value.status().cached,0);return 8;
}
function cacheLimit() {
  const value=setup();let last;
  for(let index=0;index<300;index++){last=value.view();for(let page=0;page<5;page++)value.page(last,page+1,0,1,0,1);}
  assert.strictEqual(value.status().cached,256);assert.strictEqual(value.sent.length,0);
  value.rename(last,marker);assert.strictEqual(value.sent.length,5);assert.deepStrictEqual(value.sent.slice(1).map(item=>item.count),[2,3,4,5]);
  value.advance(2001);value.rename(value.view(),'valmax-export-'+'b'.repeat(32));assert.strictEqual(value.status().cached,0);return 5;
}
function lifecycle() {
  const value=setup(),self=value.view();value.rename(self,marker);value.page(self,3,0,1,0,0);
  const operation=value.beginPage(self,5,2,1,0,0);value.destroy(self);const before=value.reads.length;value.endPage(operation);
  assert.strictEqual(value.reads.length,before,'Destroyed view was dereferenced on callback leave');
  assert.strictEqual(value.status().bound,0);assert.strictEqual(value.status().active,0);
  assert.deepStrictEqual(value.sent.at(-1),{kind:'chat-export-history',jobId:id,event:'page',direction:'center',count:0,pendingDone:false,moreBackward:true,moreForward:true});
  const next=value.view();value.rename(next,marker);value.rename(next,'ordinary');assert.strictEqual(value.status().bound,0);
  const previous=value.sent.length;value.page(next,5,0,1,0,0);assert.strictEqual(value.sent.length,previous);return 6;
}
function metadata() {
  const value=setup(),self=value.view();value.rename(self,marker);
  for(const args of [[0,0,1,0,0],[4,2,0,1,0],[8,1,1,0,0]])value.page(self,...args);
  assert.deepStrictEqual(value.sent.slice(1).map(item=>[item.count,item.direction,item.pendingDone]),[[0,'backward',true],[4,'forward',false],[8,'center',true]]);
  const count=value.sent.length;for(const args of [[1,3,1,0,0],[1,0,2,0,0],[1,0,1,7,0],[100001,0,1,0,0]])value.page(self,...args);
  assert(value.sent.slice(count).every(item=>item.count===0&&item.moreBackward&&item.moreForward));
  assert.strictEqual(value.status().active,0);return 3;
}
function recursive() {
  const value=setup(),self=value.view();value.rename(self,marker);
  // A cache-only nonempty outer callback must not inherit terminal flags from
  // a nested failed EMPTY callback.
  const outer=value.beginPage(self,9,0,1,1,1),inner=value.beginPage(self,0,0,1,0,0);
  value.endPage(inner);value.endPage(outer);
  assert.strictEqual(value.sent.length,2);assert.strictEqual(value.sent[1].count,0);
  // A stale outer terminal callback must not reassert EOF after the nested
  // newer callback restored the native more-history flags.
  const terminal=value.beginPage(self,4,0,1,0,0),newer=value.beginPage(self,5,0,1,1,1);
  value.endPage(newer);value.endPage(terminal);
  assert.strictEqual(value.sent.length,3);assert.strictEqual(value.sent[2].count,5);
  assert.strictEqual(value.sent[2].moreBackward,true);assert.strictEqual(value.status().active,0);
  // Flags changing during a callback cannot be sampled later as a proof for
  // the already captured response.
  const captured=value.beginPage(self,7,0,1,1,1);value.integers.set(self.add(0x670).toString(),0);
  value.endPage(captured);assert.strictEqual(value.sent[3].moreBackward,true);return 7;
}
function sourceState() {
  const value=setup(),self=value.view();value.rename(self,marker);
  value.page(self,7,0,1,0,0,{state:1});assert.strictEqual(value.sent.at(-1).pendingDone,true);
  // A cache-only outer callback can auto-fetch recursively BEFORE its own
  // IView call. The failed inner response clears flags, but those flags must
  // not turn the later nonempty cached State=0 callback into terminal history.
  value.page(self,0,0,1,0,0,{state:1});value.page(self,9,0,1,0,0,{state:0});
  assert.strictEqual(value.sent.at(-1).count,9);assert.strictEqual(value.sent.at(-1).pendingDone,false);
  for(const source of [{state:0},{state:2},{kind:2},{foreignOrigin:true},{noOrigin:true},{noContext:true},{noRdi:true},{invalidRdi:true},{nullRdi:true},{missingFields:true}]){
    const before=value.reads.length;value.page(self,5,0,1,0,0,source);
    assert.strictEqual(value.sent.at(-1).pendingDone,false,JSON.stringify(source));
    assert.strictEqual(value.status().ready,true,'Unknown origin is a partial page, not a monitor failure');
    assert.strictEqual(value.status().active,0);
    if(source.foreignOrigin||source.noOrigin)assert.strictEqual(value.reads.slice(before).filter(item=>item.kind==='pending').length,1,'Foreign origin read lambda enums');
  }
  // Nested IView callbacks must keep the source enums captured onEnter.
  const outer=value.beginPage(self,9,0,1,1,1,{state:0}),inner=value.beginPage(self,4,0,1,0,0,{state:1});
  value.endPage(inner);const count=value.sent.length;value.endPage(outer);
  assert.strictEqual(value.sent.length,count);assert.strictEqual(value.sent.at(-1).count,4);assert.strictEqual(value.sent.at(-1).pendingDone,true);
  const captured=value.beginPage(self,6,0,1,0,0,{state:1});value.integers.set(captured.capture.add(0x38).toString(),0);
  value.endPage(captured);assert.strictEqual(value.sent.at(-1).pendingDone,true,'Capture enums were reread onLeave');
  assert(value.reads.filter(item=>item.kind==='pointer').every(item=>!item.at.startsWith('0x500')));
  assert(value.sent.every(item=>Object.keys(item).length===(item.event==='bound'?3:8)));
  assert(value.sent.every(item=>!('sourceState' in item)));return 41;
}
function guards() {
  for(const flag of ['badBytes','badPresenter','badVtable','badPath','badImage','missingExport','badSpec','nonexec']){
    const value=setup({[flag]:true});assert.strictEqual(value.status().ready,false,flag);assert.strictEqual(value.hooks.size,0,flag);assert.strictEqual(value.sent.length,0);
  }
  for(const options of [{platform:'linux'},{arch:'arm64'}])assert.strictEqual(setup(options).status().error,'unsupported_platform');
  const failed=setup({failHook:2});assert.strictEqual(failed.status().error,'hook_failed');assert.strictEqual(failed.hooks.size,0);assert.strictEqual(failed.detached(),1);
  return 29;
}
function delayed() {
  const value=setup({delayed:true});assert.strictEqual(value.status().ready,false);value.added();assert.strictEqual(value.status().ready,true);
  const self=value.view();value.rename(self,marker);value.removed();assert.strictEqual(value.status().ready,false);assert.strictEqual(value.hooks.size,0);assert.strictEqual(value.status().bound,0);
  assert.strictEqual(value.sent.at(-1).moreBackward,true);return 6;
}
const cases={bounded,identity,synchronous,cacheLimit,lifecycle,metadata,recursive,sourceState,guards,delayed};assert(cases[selected]);
console.log(JSON.stringify({case:selected,checks:cases[selected]()}));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js unavailable for poisoned-pointer JS fixture")
class JavascriptTests(unittest.TestCase):
    def fixture(self, name, checks):
        value = {"schema": 1, "contract": module.CONTRACT,
                 "max": {"modulePath": "D:/synthetic/max.exe", "imageSize": module.BASELINE_MAX_IMAGE_SIZE,
                         "anchors": [{"rva": rva, "expected": expected} for rva, expected in module.MAX_ANCHORS],
                         "vtables": [{"rva": rva, "target": target} for rva, target in module.MAX_VTABLES]},
                 "qt": {"modulePath": "D:/synthetic/Qt6Core.dll", "sha256": module.QT_SHA256,
                        "imageSize": module.QT_IMAGE_SIZE,
                        "anchors": [{"rva": rva, "expected": expected} for rva, expected in module.QT_ANCHORS],
                        "exports": [{"name": key, "rva": rva} for key, rva in module.QT_EXPORTS]}}
        result = subprocess.run([shutil.which("node"), "-e", HARNESS,
                                 str(Path(__file__).with_name("chat_export_end_monitor.js")), json.dumps(value), name],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertEqual(json.loads(result.stdout), {"case": name, "checks": checks})

    def test_only_headers_flags_and_exact_marker_cross_native_boundary(self): self.fixture("bounded", 6)
    def test_object_type_and_exact_marker_gate_before_any_name_payload_read(self): self.fixture("identity", 6)
    def test_synchronous_factory_callback_flush_and_expiry(self): self.fixture("synchronous", 8)
    def test_pre_marker_cache_bounded_by_views_pages_and_time(self): self.fixture("cacheLimit", 5)
    def test_destroy_rename_and_inflight_object_lifetime_revoke_prior_proof(self): self.fixture("lifecycle", 6)
    def test_direction_pending_and_nonboolean_flags_are_not_guessed(self): self.fixture("metadata", 3)
    def test_recursive_callbacks_cannot_promote_or_reassert_stale_end_flags(self): self.fixture("recursive", 7)
    def test_terminal_origin_state_is_pinned_and_cache_recursion_cannot_borrow_flags(self): self.fixture("sourceState", 41)
    def test_code_type_path_export_platform_guards_and_partial_hook_rollback(self): self.fixture("guards", 29)
    def test_module_arrival_and_removal_fail_closed(self): self.fixture("delayed", 6)


if __name__ == "__main__":
    unittest.main()
