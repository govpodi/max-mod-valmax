"""Run production Frida source against owned synthetic native ABI memory."""
from pathlib import Path
import json,shutil,subprocess,unittest
import notification_native as native

HARNESS=r"""
const vm=require('vm'),fs=require('fs'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(process.argv[1],'utf8'),fixed=JSON.parse(process.argv[2]);
const updaterSource=fs.readFileSync(path.join(path.dirname(process.argv[1]),'notification_updater.js'),'utf8');
function setup(options={}) {
 let next=0x700000000n,ticks=1000n,pins=0,observer,headerDestroyed=0;
 const blocks=new Map(),heaps=new Set(),replacements=new Map(),listeners=new Map(),pending=new Map(),shown=[],acks=[],boots=[],timers=[],visibleNative=new Set();
 function block(at,n,bytes=null){const data=Buffer.alloc(n);if(bytes)Buffer.from(bytes).copy(data);blocks.set(at,data);return new Ptr(at);}
 function raw(at,n){for(const [start,data] of blocks)if(at>=start&&at+BigInt(n)<=start+BigInt(data.length))return data.subarray(Number(at-start),Number(at-start)+n);throw Error('invalid owned memory');}
 class Ptr {
  constructor(v){this.value=BigInt(v);}add(v){return new Ptr(this.value+BigInt(v));}sub(v){return new Ptr(this.value-BigInt(v));}
  compare(p){return this.value===p.value?0:this.value<p.value?-1:1;}equals(p){return this.value===p.value;}
  toString(){return '0x'+this.value.toString(16);}toInt32(){return Number(BigInt.asIntN(32,this.value));}
  readByteArray(n){return Uint8Array.from(raw(this.value,n)).buffer;}
  writeByteArray(data){Buffer.from(data).copy(raw(this.value,data.byteLength===undefined?data.length:data.byteLength));}
  readPointer(){return new Ptr(raw(this.value,8).readBigUInt64LE());}writePointer(p){raw(this.value,8).writeBigUInt64LE(p.value);}
  readU64(){const v=raw(this.value,8).readBigUInt64LE();return {toString:()=>v.toString()};}
  readS64(){const v=raw(this.value,8).readBigInt64LE();return {toString:()=>v.toString()};}
  writeU64(v){raw(this.value,8).writeBigUInt64LE(BigInt(v));}writeS64(v){raw(this.value,8).writeBigInt64LE(BigInt(v));}
  readU32(){return raw(this.value,4).readUInt32LE();}writeU32(v){raw(this.value,4).writeUInt32LE(v);}
  readUtf8String(n){return raw(this.value,n).toString('utf8');}writeUtf8String(s){Buffer.from(s+'\0','utf8').copy(raw(this.value,Buffer.byteLength(s,'utf8')+1));}
 }
 function alloc(n){const p=block(next,n);next+=BigInt(n+32);return p;}
 function stringValue(p){const n=Number(raw(p.value+16n,8).readBigUInt64LE()),cap=raw(p.value+24n,8).readBigUInt64LE();return raw(cap<=15n?p.value:p.readPointer().value,n).toString('utf8');}
 function stringMake(p,text){const s=Buffer.from(text,'utf8');raw(p.value,32).fill(0);p.add(16).writeU64(s.length);p.add(24).writeU64(s.length<=15?15:s.length);
  if(s.length<=15)s.copy(raw(p.value,16));else{const h=alloc(s.length+1);s.copy(raw(h.value,s.length+1));p.writePointer(h);heaps.add(h.value);}}
 function stringDtor(p){if(raw(p.value+24n,8).readBigUInt64LE()>15n){const h=p.readPointer().value;assert(heaps.delete(h),'only a uniquely owned native allocation is freed');}stringMake(p,'');}
 const spec=JSON.parse(JSON.stringify(fixed));
 const main={name:'max.exe',path:'D:\\fixture\\max.exe',base:new Ptr(0x140000000n),size:fixed.modules[0].baselineImageSize};
 const backend={name:'notifications.dll',path:'D:\\fixture\\notifications.dll',base:new Ptr(0x180000000n),size:fixed.modules[1].baselineImageSize};
 for(let m=0;m<2;m++){const item=spec.modules[m],module=m?backend:main;Object.assign(item,{modulePath:module.path,moduleSha256:item.baselineSha256,moduleSize:item.baselineSize,imageSize:item.baselineImageSize});
  for(const p of item.pins)block(module.base.value+BigInt(p.rva),p.expected.length/2,Buffer.from(p.expected,'hex'));
  for(const s of item.slots)block(module.base.value+BigInt(s.vtableRva+s.slot),8).writePointer(module.base.add(s.thunkRva));}
 const role=(module,name)=>module.base.add(spec.modules[module===main?0:1].pins.find(p=>p.role===name).rva).toString();
 const originals=new Map();
 originals.set(role(backend,'stringCopy'),(to,from)=>{if(options.copyFailure)throw Error('synthetic native allocation failure');stringMake(to,stringValue(from));});
 originals.set(role(backend,'stringDestroy'),stringDtor);
 originals.set(role(backend,'headerDestroy'),data=>{headerDestroyed++;});
 originals.set(role(backend,'dataCopy'),(to,from)=>{Buffer.from(raw(from.value,0xc0)).copy(raw(to.value,0xc0));to.add(0x140).writeU64(from.add(0x140).readU64().toString());for(const off of [0xc0,0xe0,0x100,0x120])stringMake(to.add(off),stringValue(from.add(off)));});
 for(const [m,r] of [[backend,'display'],[backend,'edit'],[backend,'nativeDisplay'],[backend,'nativeEdit'],[main,'qtDisplay'],[main,'qtEdit']])originals.set(role(m,r),(self,data,flag)=>{
  if(options.duringBackend&&(r.startsWith('native')||r.startsWith('qt')))options.duringBackend();
  if(options.nestedNativeEdit&&r==='nativeEdit'){replacements.get(role(backend,'nativeDisplay'))(self,data);return;}
  shown.push({role:r,flag,values:[0xc0,0xe0,0x100,0x120].map(off=>stringValue(data.add(off))),header:Buffer.from(raw(data.value,0xc0)).toString('hex'),account:data.add(0x140).readU64().toString()});
 });
 if(options.badBytes)raw(main.base.value+BigInt(spec.modules[0].pins[0].rva),1)[0]^=1;
 if(options.badSlot)block(main.base.value+BigInt(spec.modules[0].slots[0].vtableRva+16),8).writePointer(main.base.add(42));
 if(options.badSpec)spec.layout.chatOffset++;
 const policy=options.policy||{schema:1,revision:'a'.repeat(64),ready:true,protected:[{ownerId:'200',chatId:'77'}],leaseMs:5000};
 const context={VALMAX_NOTIFICATION:{specification:spec,policy,genericImagePath:'D:\\fixture\\neutral.png'},VALMAX_NOTIFICATION_PINS:fixed,
  Memory:{alloc,allocUtf8String:text=>{const s=Buffer.from(text+'\0','utf8'),p=alloc(s.length);s.copy(raw(p.value,s.length));return p;}},
  Process:{platform:options.platform||'windows',arch:'x64',pointerSize:8,
   findRangeByAddress:p=>{for(const [start,data]of blocks)if(p.value>=start&&p.value<start+BigInt(data.length)){if(options.cloakAllocations&&start>=0x700000000n&&!visibleNative.has(start))return null;return {base:new Ptr(start),size:data.length,protection:p.value<0x200000000n?'r-x':'rw-'};}return null;},
   getModuleByName:()=>({getExportByName:name=>new Ptr(name==='GetTickCount64'?0x1111n:0x2222n)}),attachModuleObserver:o=>{observer=o;return {detach(){}};}},
  NativeFunction:function(at,result,args,config){assert.strictEqual(config.abi,'win64');if(at.value===0x1111n)return ()=>({toString:()=>ticks.toString()});
   if(at.value===0x2222n)return (p,info,n)=>{for(const [start,data]of blocks)if(p.value>=start&&p.value<start+BigInt(data.length)){info.writePointer(new Ptr(start));info.add(24).writeU64(data.length);info.add(32).writeU32(0x1000);info.add(36).writeU32(4);return {toString:()=> '48'};}return {toString:()=> '0'};};
   assert(originals.has(at.toString()),'known native functions only');return originals.get(at.toString());},
  NativeCallback:function(callback,result,args,abi){assert.strictEqual(result,'void');assert.strictEqual(abi,'win64');return callback;},
  Interceptor:{attach(at,value){listeners.set(at.toString(),value);return {detach(){listeners.delete(at.toString());}};},replace(at,fn){if(options.failReplace&&replacements.size===1)throw Error('synthetic installation failure');replacements.set(at.toString(),fn);},revert(at){replacements.delete(at.toString());},flush(){}},
  Script:{pin(){if(options.pinFailure)throw Error('pin unavailable');pins++;}},
  recv:(type,fn)=>pending.set(type,fn),send:p=>(p.kind==='valmax-notification-bridge-ready'?boots:acks).push(JSON.parse(JSON.stringify(p))),
  setInterval:fn=>{timers.push(fn);return timers.length;},ptr:value=>new Ptr(value),rpc:{exports:{}},unescape,encodeURIComponent};
 vm.runInNewContext(source,context,{timeout:1000});
 if(observer){observer.onAdded(main);observer.onAdded(backend);}
 let updater=null;
 if(boots.length){updater={...context,VALMAX_NOTIFICATION_BRIDGE:boots[0],rpc:{exports:{}},send:p=>acks.push(JSON.parse(JSON.stringify(p)))};vm.runInNewContext(updaterSource,updater,{timeout:1000});}
 function pump(){for(const fn of [...timers])fn();}
 function identity(own='200',native='1'){const p=alloc(0x100);visibleNative.add(p.value);p.writePointer(main.base.add(spec.layout.identityVtable));p.add(0x28).writeS64(own);p.add(0x38).writeU64(native);const cb=listeners.get(role(main,'identitySetter'));if(cb){const hit={};cb.onEnter.call(hit,[p.add(16)]);cb.onLeave.call(hit);}return p;}
 function data(native='1',chat='77',duplicate=native){const p=alloc(0x148);visibleNative.add(p.value);raw(p.value,0xc0).fill(0xa5);p.add(0x10).writeS64(chat);p.add(0x18).writeU64(native);p.add(0x38).writePointer(main.base.add(fixed.layout.keyMarkerRva));p.add(0x140).writeU64(duplicate);['Private sender','Private subtitle','Private body','D:\\private-photo.png'].forEach((s,i)=>stringMake(p.add([0xc0,0xe0,0x100,0x120][i]),s));return p;}
 function hit(data,r='display',flag=1){const p=role(r.startsWith('qt')?main:backend,r);(replacements.get(p)||originals.get(p))(new Ptr(1),data,flag);pump();}
 let generation=0;
 function post(p,selectedGeneration=null){pending.get('valmax-notification-policy')({type:'valmax-notification-policy',requestId:'b'.repeat(32),generation:selectedGeneration===null?++generation:selectedGeneration,policy:p});pump();}
 return {identity,data,hit,post,shown,acks,heaps,replacements,listeners,spec,main,backend,context,boots,updater,pump,role,originals,header: p=>Buffer.from(raw(p.value,0xc0)).toString('hex'),values:p=>[0xc0,0xe0,0x100,0x120].map(o=>stringValue(p.add(o))),
  expire:()=>{ticks+=5001n;},snapshot:()=>JSON.parse(JSON.stringify(context.notificationPrivacySnapshot())),pinCount:()=>pins,headerDestroyed:()=>headerDestroyed};
}
let checks=0;
{
 const h=setup();assert(h.snapshot().ready);assert.strictEqual(h.pinCount(),0);h.identity();const d=h.data(),before=h.values(d),header=h.header(d),heaps=h.heaps.size;
 h.hit(d);assert.deepStrictEqual(h.shown[0].values,['ValMAX','','Новое сообщение','D:\\fixture\\neutral.png']);assert.strictEqual(h.shown[0].header,header);assert.strictEqual(h.shown[0].account,'1');assert.deepStrictEqual(h.values(d),before);assert.strictEqual(h.heaps.size,heaps);assert.strictEqual(h.headerDestroyed(),1);checks+=8;
 h.hit(d,'edit');assert.strictEqual(h.shown[1].role,'edit');assert.strictEqual(h.snapshot().masked,2);checks+=2;
 const normal=h.data('1','78');h.hit(normal);assert.deepStrictEqual(h.shown[2].values,h.values(normal));checks++;
 // Account::Id is intentionally different from ContactId in this fixture.
 h.identity('300','2');const second=h.data('2','77');h.hit(second);assert.deepStrictEqual(h.shown[3].values,h.values(second));checks++;
 // An active/granted chat cannot bypass the protected scope set.
 h.hit(d);assert.strictEqual(h.snapshot().masked,3);checks++;
 h.expire();assert(!h.snapshot().ready&&!h.snapshot().applied);h.hit(normal);assert.strictEqual(h.shown[5].values[2],'Новое сообщение');checks+=2;
 h.post({schema:1,revision:'c'.repeat(64),ready:true,protected:[],leaseMs:5000});assert(h.acks[0].snapshot.applied);h.hit(d);assert.deepStrictEqual(h.shown[6].values,before);checks+=2;
 h.post({schema:1,revision:'d'.repeat(64),ready:true,protected:[{ownerId:'200',chatId:'77'}],leaseMs:5000},10);
 h.post({schema:1,revision:'e'.repeat(64),ready:true,protected:[],leaseMs:5000},9);
 assert.strictEqual(h.snapshot().revision,'d'.repeat(64));h.hit(d);assert.strictEqual(h.shown[7].values[2],'Новое сообщение');checks+=2;
}
{
 const h=setup();const d=h.data();h.hit(d);assert.strictEqual(h.snapshot().scopeUnknown,1);assert.strictEqual(h.shown[0].values[0],'ValMAX');h.identity();h.hit(h.data('1','77','2'));assert.strictEqual(h.snapshot().scopeUnknown,2);checks+=3;
 h.post({schema:1,revision:'d'.repeat(64),ready:false,protected:[],leaseMs:5000});assert(!h.acks[0].snapshot.applied);h.hit(h.data('1','78'));assert.strictEqual(h.shown[2].values[2],'Новое сообщение');checks+=2;
 for(const value of ['-0','00','9223372036854775808','']){h.post({schema:1,revision:'e'.repeat(64),ready:true,protected:[{ownerId:'200',chatId:value}],leaseMs:5000});assert(!h.snapshot().applied);checks++;}
 h.post({schema:1,revision:'f'.repeat(64),ready:true,protected:[{ownerId:'200',chatId:'0'}],leaseMs:5000});h.hit(h.data('1','0'));assert(h.snapshot().ready);assert.strictEqual(h.shown[3].values[2],'Новое сообщение');checks+=2;
}
{
 const h=setup({copyFailure:true});h.identity();const d=h.data(),before=h.values(d),heaps=h.heaps.size;h.hit(d);assert.strictEqual(h.shown.length,0);assert.deepStrictEqual(h.values(d),before);assert.strictEqual(h.heaps.size,heaps);assert(!h.snapshot().ready);assert.strictEqual(h.snapshot().error,'clone_failed');checks+=5;
}
for(const option of ['badBytes','badSlot','badSpec','failReplace']){const h=setup({[option]:true});assert(!h.snapshot().ready);assert.strictEqual(h.replacements.size,0);assert.strictEqual(h.listeners.size,0);checks+=3;}
{
 const h=setup({cloakAllocations:true,nestedNativeEdit:true});h.identity();const d=h.data(),before=h.values(d),heaps=h.heaps.size;
 assert.strictEqual(h.replacements.size,6);h.hit(d,'nativeEdit');assert.strictEqual(h.shown[0].role,'nativeDisplay');
 assert.strictEqual(h.shown[0].values[2],'Новое сообщение');assert.deepStrictEqual(h.values(d),before);assert.strictEqual(h.heaps.size,heaps);assert.strictEqual(h.snapshot().scopeUnknown,0);checks+=6;
 h.hit(d,'qtDisplay',0);h.hit(d,'qtEdit',1);assert.strictEqual(h.shown[1].flag,0);assert.strictEqual(h.shown[2].flag,1);checks+=2;
 // Simulate an already queued unredacted Data reaching the final backend
 // after PIN protection has been published and ACKed at the common Center.
 h.post({schema:1,revision:'c'.repeat(64),ready:true,protected:[],leaseMs:5000});
 h.hit(d);assert.strictEqual(h.shown[3].values[2],'Private body');
 h.post({schema:1,revision:'d'.repeat(64),ready:true,protected:[{ownerId:'200',chatId:'77'}],leaseMs:5000});
 h.hit(d,'nativeDisplay');assert.strictEqual(h.shown[4].values[2],'Новое сообщение');checks+=2;
}
{
 let h,once=false,insideAcks=null;
 const options={policy:{schema:1,revision:'a'.repeat(64),ready:true,protected:[],leaseMs:5000},duringBackend:()=>{
  if(once)return;once=true;h.post({schema:1,revision:'c'.repeat(64),ready:true,protected:[{ownerId:'200',chatId:'77'}],leaseMs:5000});insideAcks=h.acks.length;
 }};
 h=setup(options);h.identity();const d=h.data();h.hit(d,'nativeDisplay');
 assert.strictEqual(insideAcks,0);assert.strictEqual(h.acks.length,1);assert(h.acks[0].snapshot.ready);assert.strictEqual(h.acks[0].snapshot.revision,'c'.repeat(64));checks+=4;
 h.hit(d,'nativeDisplay');assert.strictEqual(h.shown[1].values[2],'Новое сообщение');checks++;
}
console.log(JSON.stringify({checks}));
"""

@unittest.skipUnless(shutil.which('node'),'Node unavailable for owned JS VM')
class NotificationJavascriptTests(unittest.TestCase):
    def run_scope_harness(self, checks):
        fixture=HARNESS.partition('let checks=0;')[0]
        result=subprocess.run([shutil.which('node'),'-e',fixture+checks,
                               str(Path(__file__).with_name('notification_monitor.js')),
                               json.dumps(native.FIXED)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_empty_fresh_catalog_keeps_native_notifications_without_identity_mapping(self):
        self.run_scope_harness(r"""
const h=setup({policy:{schema:1,revision:'a'.repeat(64),ready:true,protected:[],leaseMs:5000}});
const d=h.data(), before=h.values(d), header=h.header(d), heaps=h.heaps.size;
for(const role of ['display','edit','nativeDisplay','nativeEdit','qtDisplay','qtEdit'])h.hit(d,role,0);
assert.strictEqual(h.shown.length,6);
for(const value of h.shown){assert.deepStrictEqual(value.values,before);assert.strictEqual(value.header,header);}
assert.strictEqual(h.shown[4].flag,0);assert.strictEqual(h.shown[5].flag,0);
assert.strictEqual(h.snapshot().masked,0);assert.strictEqual(h.snapshot().scopeUnknown,6);
assert.strictEqual(h.headerDestroyed(),0);assert.strictEqual(h.heaps.size,heaps);assert.deepStrictEqual(h.values(d),before);
""")

    def test_unknown_owner_masks_only_a_chat_that_could_be_pin_protected(self):
        self.run_scope_harness(r"""
const h=setup(), normal=h.data('1','78'), normalValues=h.values(normal);
for(const role of ['display','edit','nativeDisplay','nativeEdit','qtDisplay','qtEdit'])h.hit(normal,role);
for(const value of h.shown)assert.deepStrictEqual(value.values,normalValues);
assert.strictEqual(h.snapshot().masked,0);assert.strictEqual(h.snapshot().scopeUnknown,6);
const protectedChat=h.data();
for(const role of ['display','edit','nativeDisplay','nativeEdit','qtDisplay','qtEdit'])h.hit(protectedChat,role);
for(const value of h.shown.slice(6))assert.strictEqual(value.values[2],'Новое сообщение');
assert.strictEqual(h.snapshot().masked,6);
// Contradicting account copies cannot authorise a pass-through with PINs present.
h.hit(h.data('1','78','2'));assert.strictEqual(h.shown[12].values[2],'Новое сообщение');
// A known different account with the same chat ID is not protected.
h.identity('300','2');const other=h.data('2','77');h.hit(other);
assert.deepStrictEqual(h.shown[13].values,h.values(other));
// An account ID collision leaves possible protected chats private.
h.identity('400','2');h.hit(other);assert.strictEqual(h.shown[14].values[2],'Новое сообщение');
h.hit(h.data('2','78'));assert.deepStrictEqual(h.shown[15].values,normalValues);
""")

    def test_scope_decisions_follow_pin_catalog_updates_and_expire_fail_closed(self):
        self.run_scope_harness(r"""
const h=setup({nestedNativeEdit:true,cloakAllocations:true}), d=h.data('1','78'), before=h.values(d), heaps=h.heaps.size;
h.hit(d,'nativeEdit');assert.deepStrictEqual(h.shown[0].values,before);assert.strictEqual(h.snapshot().masked,0);
h.post({schema:1,revision:'b'.repeat(64),ready:true,protected:[{ownerId:'200',chatId:'78'}],leaseMs:5000});
h.hit(d,'nativeEdit');assert.strictEqual(h.shown[1].values[2],'Новое сообщение');
h.post({schema:1,revision:'c'.repeat(64),ready:true,protected:[],leaseMs:5000});
h.hit(d,'nativeEdit');assert.deepStrictEqual(h.shown[2].values,before);
assert.strictEqual(h.heaps.size,heaps);assert.deepStrictEqual(h.values(d),before);
h.expire();h.hit(d,'nativeEdit');assert.strictEqual(h.shown[3].values[2],'Новое сообщение');
assert.strictEqual(h.snapshot().ready,false);
h.post({schema:1,revision:'d'.repeat(64),ready:false,protected:[],leaseMs:5000});
h.hit(d,'nativeEdit');assert.strictEqual(h.shown[4].values[2],'Новое сообщение');
""")

    def test_native_scope_redaction_ownership_lease_ack_and_atomic_guard_failure(self):
        result=subprocess.run([shutil.which('node'),'-e',HARNESS,str(Path(__file__).with_name('notification_monitor.js')),json.dumps(native.FIXED)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertGreaterEqual(json.loads(result.stdout)['checks'],45)


if __name__=='__main__':unittest.main()
