// Native per-chat notification redaction. Only numeric identity metadata is
// inspected. Message strings are copied/destroyed exclusively by native C++.
'use strict';
const valmaxNotificationPrivacy = (() => {
    const ZERO = '0'.repeat(64), state = {hooks:false, applied:false, revision:ZERO,
        policyReady:false, deadline:0, error:null, masked:0, scopeUnknown:0};
    const spec=VALMAX_NOTIFICATION.specification, fixed=VALMAX_NOTIFICATION_PINS;
    const modules=new Map(), owners=new Map(), retained=[], ownedClones=new Set();
    let protectedScopes=new Set(), protectedChats=new Set(), maxModule=null, backendModule=null, lastGeneration=0, backendInFlight=0;
    let stringCopy=null, stringDestroy=null, headerDestroy=null, dataCopy=null;
    let bridge=null, virtualQuery=null, bridgeMeta=null, bridgeBooted=false, bridgeSequence=0, ackSequence=0;
    let lastAckId='0'.repeat(32), lastAckText='', pendingAcks=[];
    const BRIDGE_SIZE=65536, BRIDGE_MAGIC='56414c4e42523031';
    const MAX_COUNT=1000000;
    function object(v) {return v!==null && typeof v==='object' && !Array.isArray(v);}
    function exact(v,names) {return object(v) && Object.keys(v).length===names.length && names.every(k=>Object.prototype.hasOwnProperty.call(v,k));}
    function unsigned(v) {return typeof v==='string' && /^(?:0|[1-9][0-9]{0,19})$/.test(v) && (v.length<20 || v<='18446744073709551615');}
    function owner(v) {return typeof v==='string' && /^[1-9][0-9]{0,18}$/.test(v) && (v.length<19 || v<='9223372036854775807');}
    function chat(v) {if(typeof v!=='string' || !/^(?:0|-?[1-9][0-9]{0,18})$/.test(v)) return false;
        const n=v[0]==='-'?v.slice(1):v;return n.length<19 || n<=(v[0]==='-'?'9223372036854775808':'9223372036854775807');}
    function inc(k) {if(state[k]<MAX_COUNT) state[k]++;}
    function pathEqual(a,b) {return typeof a==='string' && typeof b==='string' && a.replace(/\//g,'\\').toLowerCase()===b.replace(/\//g,'\\').toLowerCase();}
    function bytes(p,n) {return Array.from(new Uint8Array(p.readByteArray(n))).map(x=>x.toString(16).padStart(2,'0')).join('');}
    function memory(p,n,write=false) {
        // Frida cloaks its own allocations from Process.findRangeByAddress.
        // Only exact still-owned Data buffers may bypass that native range test.
        if(ownedClones.has(p.toString()) && n<=spec.layout.dataSize) return true;
        const r=Process.findRangeByAddress(p);return r!==null && r.protection.includes('r') && (!write || r.protection.includes('w')) && p.compare(r.base)>=0 && p.add(n).compare(r.base.add(r.size))<=0;
    }
    function executable(p,n) {const r=Process.findRangeByAddress(p);return r!==null && r.protection.includes('r') && r.protection.includes('x') && p.add(n).compare(r.base.add(r.size))<=0;}
    function nativeFunction(p,result,args) {return new NativeFunction(p,result,args,{abi:'win64',scheduling:'cooperative',exceptions:'propagate'});}
    // Windows monotonic uptime prevents a wall-clock change extending a lease.
    let tick=null;
    function now() {return tick ? Number(tick().toString()) : 0;}
    function fresh() {return state.policyReady && tick!==null && now()<state.deadline;}
    function snapshot() {
        const lease=fresh();
        const healthy=state.hooks && state.applied && lease && state.error===null;
        return {ready:healthy,
            applied:healthy,revision:state.revision,
            error:state.error || (state.hooks && state.policyReady && !lease ? 'lease_expired' : null),
            masked:state.masked,scopeUnknown:state.scopeUnknown};
    }
    function bridgeValid() {
        if(bridge===null || virtualQuery===null) return false;
        const info=Memory.alloc(48),n=Number(virtualQuery(bridge,info,48).toString());
        if(n<48 || info.add(32).readU32()!==0x1000 || info.add(36).readU32()!==4) return false;
        const base=info.readPointer(),size=Number(info.add(24).readU64().toString());
        return Number.isSafeInteger(size) && bridge.compare(base)>=0 && bridge.add(BRIDGE_SIZE).compare(base.add(size))<=0 &&
            bytes(bridge,8)===BRIDGE_MAGIC && bridge.add(8).readU32()===1 && bridge.add(12).readU32()===BRIDGE_SIZE;
    }
    function writeAck(requestId,force=false) {
        if(backendInFlight!==0 || !bridgeValid()) return;
        const value=JSON.stringify({kind:'valmax-notification-policy-ack',requestId,snapshot:snapshot()});
        if(!force && value===lastAckText) return;
        if(ackSequence>0xfffffffc || value.length>32000) {state.error='bridge_unavailable';return;}
        const next=ackSequence+2;
        bridge.add(24).writeU32(next-1);bridge.add(28).writeU32(value.length);
        bridge.add(33024).writeUtf8String(value);bridge.add(24).writeU32(next);
        ackSequence=next;lastAckId=requestId;lastAckText=value;
    }
    function flushAcks() {
        if(backendInFlight!==0) return;
        while(pendingAcks.length) writeAck(pendingAcks.shift(),true);
        writeAck(lastAckId);
    }
    function pollBridge() {
        try {
            if(!bridgeValid()) {state.policyReady=false;state.error='bridge_unavailable';return;}
            const before=bridge.add(16).readU32();
            if(before!==bridgeSequence && (before&1)===0) {
                const length=bridge.add(20).readU32();
                if(length<1 || length>32000) {state.policyReady=false;state.error='invalid_policy';return;}
                const raw=bridge.add(256).readUtf8String(length),after=bridge.add(16).readU32();
                if(after!==before || (after&1)!==0) return;
                bridgeSequence=before;
                const message=JSON.parse(raw);
                if(exact(message,['type','policy','requestId','generation']) && message.type==='valmax-notification-policy' &&
                        typeof message.requestId==='string' && /^[a-f0-9]{32}$/.test(message.requestId) &&
                        Number.isSafeInteger(message.generation) && message.generation>0) {
                    if(message.generation>lastGeneration) {lastGeneration=message.generation;policy(message.policy);}
                    if(pendingAcks.length>=64) {state.policyReady=false;state.error='bridge_unavailable';}
                    else pendingAcks.push(message.requestId);
                } else {state.policyReady=false;state.applied=false;state.error='invalid_policy';}
            }
            flushAcks();
        } catch(_) {state.policyReady=false;state.applied=false;state.error='bridge_unavailable';}
    }
    function policy(p) {
        if(!exact(p,['schema','revision','ready','protected','leaseMs']) || p.schema!==1 ||
                typeof p.revision!=='string' || !/^[a-f0-9]{64}$/.test(p.revision) ||
                typeof p.ready!=='boolean' || !Number.isInteger(p.leaseMs) || p.leaseMs<1 || p.leaseMs>5000 ||
                !Array.isArray(p.protected) || p.protected.length>256) {
            state.policyReady=false;state.applied=false;state.error='invalid_policy';return false;
        }
        const scopes=new Set(), chats=new Set();
        for(const s of p.protected) {
            if(!exact(s,['ownerId','chatId']) || !owner(s.ownerId) || !chat(s.chatId) || scopes.has(s.ownerId+':'+s.chatId)) {
                state.policyReady=false;state.applied=false;state.error='invalid_policy';return false;
            }
            scopes.add(s.ownerId+':'+s.chatId);
            chats.add(s.chatId);
        }
        if(p.revision===state.revision && state.applied && (scopes.size!==protectedScopes.size || [...scopes].some(k=>!protectedScopes.has(k)))) {
            state.policyReady=false;state.applied=false;state.error='invalid_policy';return false;
        }
        protectedScopes=scopes;protectedChats=chats;state.revision=p.revision;state.policyReady=p.ready;
        state.deadline=now()+p.leaseMs;state.applied=true;
        state.error=p.ready ? null : 'catalog_unavailable';
        return true;
    }
    function validSpec() {
        if(!object(spec) || spec.schema!==1 || JSON.stringify(spec.layout)!==JSON.stringify(fixed.layout) ||
                !Array.isArray(spec.modules) || spec.modules.length!==fixed.modules.length) return false;
        return fixed.modules.every((expected,index)=>{
            const got=spec.modules[index];
            return object(got) && got.name===expected.name && got.baselineSha256===expected.baselineSha256 &&
                got.baselineSize===expected.baselineSize && got.baselineImageSize===expected.baselineImageSize &&
                JSON.stringify(got.pins)===JSON.stringify(expected.pins) && JSON.stringify(got.slots)===JSON.stringify(expected.slots) &&
                typeof got.modulePath==='string' && /^[a-f0-9]{64}$/.test(got.moduleSha256) &&
                Number.isInteger(got.imageSize) && got.imageSize>=expected.baselineImageSize &&
                Number.isInteger(got.moduleSize) && got.moduleSize>=expected.baselineSize &&
                (got.name!=='notifications.dll' || got.moduleSha256===expected.baselineSha256);
        });
    }
    function verify(module,item) {
        if(module.name.toLowerCase()!==item.name || !pathEqual(module.path,item.modulePath) || module.size!==item.imageSize) return false;
        for(const pin of item.pins) {
            const p=module.base.add(pin.rva),n=pin.expected.length/2;
            if(!executable(p,n) || bytes(p,n)!==pin.expected) return false;
        }
        return item.slots.every(s=>module.base.add(s.vtableRva+s.slot).readPointer().equals(module.base.add(s.thunkRva)));
    }
    function remember(context) {
        try {
            const l=spec.layout;
            if(!memory(context,l.nativeAccountOffset+8) || !context.readPointer().equals(maxModule.base.add(l.identityVtable))) return;
            const own=context.add(l.ownerOffset).readS64().toString(), id=context.add(l.nativeAccountOffset).readU64().toString();
            if(!owner(own) || !unsigned(id)) return;
            if(owners.has(id) && owners.get(id)!==own) {owners.set(id,null);return;}
            if(!owners.has(id) && owners.size>=64) return;
            owners.set(id,own);
        } catch(_) {state.error='identity_unavailable';}
    }
    function scope(data) {
        const l=spec.layout;
        if(!memory(data,l.dataSize) || !data.add(l.keyMarkerOffset).readPointer().equals(maxModule.base.add(l.keyMarkerRva))) return null;
        const native=data.add(l.accountOffset).readU64().toString(), duplicate=data.add(l.accountDuplicateOffset).readU64().toString();
        const selectedChat=data.add(l.chatOffset).readS64().toString();
        if(native!==duplicate || !unsigned(native) || !chat(selectedChat)) return null;
        const own=owners.get(native);
        return {key:owner(own) ? own+':'+selectedChat : null, chatId:selectedChat};
    }
    function destroyData(data) {
        // Exact native coroutine cleanup order. Frida owns only the outer buffer.
        for(const off of [0x120,0x100,0xe0,0xc0]) stringDestroy(data.add(off));
        headerDestroy(data);
    }
    function emptyString(data) {
        data.writeByteArray(new Uint8Array(32));data.add(0x18).writeU64(15);
    }
    function syntheticString(text) {
        const source=Memory.alloc(32);emptyString(source);
        const utf8=Memory.allocUtf8String(text);
        let length=0;
        for(const c of text) {const code=c.codePointAt(0);length+=code<=0x7f?1:code<=0x7ff?2:code<=0xffff?3:4;}
        if(length<=15) source.writeByteArray(utf8.readByteArray(length+1));
        else {source.writePointer(utf8);source.add(0x18).writeU64(length);}
        source.add(0x10).writeU64(length);
        const result=Memory.alloc(32);emptyString(result);
        stringCopy(result,source);
        // Keep the readonly source UTF8 reachable until native copy has returned.
        return {data:result,source,utf8};
    }
    function redactedCall(original,self,input,extra) {
        const built=[];let clone=null,cloned=false;
        try {
            for(const text of ['ValMAX','','Новое сообщение',VALMAX_NOTIFICATION.genericImagePath]) built.push(syntheticString(text));
            clone=Memory.alloc(spec.layout.dataSize);dataCopy(clone,input);cloned=true;
            for(let i=0;i<4;i++) {
                const field=clone.add(spec.layout.stringOffsets[i]);
                stringDestroy(field);
                field.writeByteArray(built[i].data.readByteArray(32));
                emptyString(built[i].data); // Heap ownership has moved into clone.
            }
            ownedClones.add(clone.toString());original(self,clone,...extra);inc('masked');
        } catch(_) {state.error='clone_failed';}
        finally {
            for(const item of built) {try {stringDestroy(item.data);} catch(_) {state.error='cleanup_failed';}}
            if(cloned) {ownedClones.delete(clone.toString());try {destroyData(clone);} catch(_) {state.error='cleanup_failed';}}
        }
    }
    function install() {
        if(state.hooks || !modules.has('max.exe') || !modules.has('notifications.dll')) return;
        const replacements=[],listeners=[];
        try {
            const a=spec.modules[0],b=spec.modules[1];maxModule=modules.get(a.name);backendModule=modules.get(b.name);
            if(!verify(maxModule,a) || !verify(backendModule,b)) {state.error='module_mismatch';return;}
            const address=(module,item,role)=>module.base.add(item.pins.find(p=>p.role===role).rva);
            stringCopy=nativeFunction(address(backendModule,b,'stringCopy'),'void',['pointer','pointer']);
            stringDestroy=nativeFunction(address(backendModule,b,'stringDestroy'),'void',['pointer']);
            headerDestroy=nativeFunction(address(backendModule,b,'headerDestroy'),'void',['pointer']);
            dataCopy=nativeFunction(address(backendModule,b,'dataCopy'),'void',['pointer','pointer']);
            listeners.push(Interceptor.attach(address(maxModule,a,'identitySetter'),{
                onEnter(args) {this.identity=args[0].sub(spec.layout.identityInterfaceOffset);},
                onLeave() {remember(this.identity);}
            }));
            // For a late attachment, accept a pair only after both native Qt
            // identity properties have been read on the same verified context.
            const late=new Map();
            listeners.push(Interceptor.attach(address(maxModule,a,'identityMetaCall'),{
                onEnter(args) {const call=args[1].toInt32(),index=args[2].toInt32();this.identity=null;
                    if((call===1 || call===8) && (index===0 || index===1)) {this.identity=args[0];this.which=index;}},
                onLeave() {if(this.identity===null) return;const k=this.identity.toString(),bits=(late.get(k)||0)|(1<<this.which);
                    if(late.size<64 || late.has(k)) late.set(k,bits);if(bits===3) remember(this.identity);}
            }));
            const entries=[['display',backendModule,b,false,false],['edit',backendModule,b,false,false],
                ['nativeDisplay',backendModule,b,true,false],['nativeEdit',backendModule,b,true,false],
                ['qtDisplay',maxModule,a,true,true],['qtEdit',maxModule,a,true,true]];
            for(const [role,module,item,last,isQt] of entries) {
                const at=address(module,item,role),args=isQt?['pointer','pointer','uchar']:['pointer','pointer'];
                const original=nativeFunction(at,'void',args);
                const callback=new NativeCallback((self,input,flag)=>{
                    if(last) backendInFlight++;
                    try {
                        pollBridge();
                        let selected=null;
                        try {selected=scope(input);} catch(_) {}
                        if(selected===null || selected.key===null) inc('scopeUnknown');
                        const extra=isQt?[flag]:[];
                        // A missing owner mapping does not make every chat PIN-protected.
                        // The fresh catalogue is authoritative when empty. Otherwise a
                        // verified chat ID absent from every protected account is safe
                        // to pass through; an account-ambiguous protected chat stays masked.
                        if(fresh() && (protectedScopes.size===0 || selected!==null &&
                                (selected.key!==null ? !protectedScopes.has(selected.key) : !protectedChats.has(selected.chatId)))) {
                            original(self,input,...extra);return;
                        }
                        if(!memory(input,spec.layout.dataSize)) {state.error='invalid_data';return;}
                        redactedCall(original,self,input,extra);
                    } finally {if(last) {backendInFlight--;flushAcks();}}
                },'void',args,'win64');
                retained.push(original,callback);Interceptor.replace(at,callback);replacements.push(at);
            }
            Interceptor.flush();
            // Host detach/unload removes Frida hooks. A retained permanent guard
            // needs a separately proven host lifecycle; Script.pin is not one.
            retained.push(...listeners);state.hooks=true;
            if(!bridgeBooted) {bridgeBooted=true;send({kind:'valmax-notification-bridge-ready',...bridgeMeta});}
        } catch(_) {
            for(const listener of listeners) {try {listener.detach();} catch(_) {}}
            for(const at of replacements) {try {Interceptor.revert(at);} catch(_) {}}
            try {Interceptor.flush();} catch(_) {}
            state.hooks=false;state.error='hook_failed';
        }
    }
    if(Process.platform!=='windows' || Process.arch!=='x64' || Process.pointerSize!==8) {state.error='unsupported_platform';return {snapshot};}
    if(!validSpec()) {state.error='invalid_specification';return {snapshot};}
    if(typeof VALMAX_NOTIFICATION.genericImagePath!=='string' || !/^[A-Za-z]:[\\/]/.test(VALMAX_NOTIFICATION.genericImagePath) ||
            VALMAX_NOTIFICATION.genericImagePath.length>32767 || /[\x00-\x1f]/.test(VALMAX_NOTIFICATION.genericImagePath)) {
        state.error='invalid_asset';return {snapshot};
    }
    try {
        const kernel=Process.getModuleByName('kernel32.dll');
        tick=nativeFunction(kernel.getExportByName('GetTickCount64'),'uint64',[]);
        virtualQuery=nativeFunction(kernel.getExportByName('VirtualQuery'),'uint64',['pointer','pointer','uint64']);
        bridge=Memory.alloc(BRIDGE_SIZE);bridge.writeByteArray(new Uint8Array(BRIDGE_SIZE));
        bridge.writeByteArray(Uint8Array.from(BRIDGE_MAGIC.match(/../g).map(x=>parseInt(x,16))));
        bridge.add(8).writeU32(1);bridge.add(12).writeU32(BRIDGE_SIZE);
        bridgeMeta={schema:1,pointer:bridge.toString(),size:BRIDGE_SIZE,magic:BRIDGE_MAGIC};
        if(!bridgeValid()) {state.error='bridge_unavailable';return {snapshot};}
    }
    catch(_) {state.error='clock_unavailable';return {snapshot};}
    policy(VALMAX_NOTIFICATION.policy);
    const observer=Process.attachModuleObserver({
        onAdded(module) {if(spec.modules.some(i=>i.name===module.name.toLowerCase())) {modules.set(module.name.toLowerCase(),module);install();}},
        onRemoved(module) {if(spec.modules.some(i=>i.name===module.name.toLowerCase())) {modules.delete(module.name.toLowerCase());state.hooks=false;state.error='module_unloaded';}}
    });
    retained.push(observer,bridge,setInterval(pollBridge,25));return {snapshot};
})();
function notificationPrivacySnapshot() {return valmaxNotificationPrivacy.snapshot();}
