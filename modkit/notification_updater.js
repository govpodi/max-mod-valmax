// Ordinary host-connected updater for the separate eternal native guardian.
// Only the guardian's bounded policy/finite-status buffer is accessed.
'use strict';
const valmaxNotificationUpdater = (() => {
    const meta=VALMAX_NOTIFICATION_BRIDGE, bridge=ptr(meta.pointer);
    let sequence=0, seenAck=0;
    const pending=new Set();
    let latest={ready:false,applied:false,revision:'0'.repeat(64),error:'bridge_unavailable',masked:0,scopeUnknown:0};
    function exact(v,keys) {return v!==null && typeof v==='object' && !Array.isArray(v) && Object.keys(v).length===keys.length && keys.every(k=>Object.prototype.hasOwnProperty.call(v,k));}
    function bytes(p,n) {return Array.from(new Uint8Array(p.readByteArray(n))).map(x=>x.toString(16).padStart(2,'0')).join('');}
    const query=new NativeFunction(Process.getModuleByName('kernel32.dll').getExportByName('VirtualQuery'),'uint64',
        ['pointer','pointer','uint64'],{abi:'win64',scheduling:'cooperative',exceptions:'propagate'});
    function valid() {
        const info=Memory.alloc(48),n=Number(query(bridge,info,48).toString());
        if(n<48 || info.add(32).readU32()!==0x1000 || info.add(36).readU32()!==4) return false;
        const base=info.readPointer(),size=Number(info.add(24).readU64().toString());
        return Number.isSafeInteger(size) && bridge.compare(base)>=0 && bridge.add(meta.size).compare(base.add(size))<=0 &&
            bytes(bridge,8)===meta.magic && bridge.add(8).readU32()===meta.schema && bridge.add(12).readU32()===meta.size;
    }
    function unavailable() {latest={...latest,ready:false,applied:false,error:'bridge_unavailable'};}
    function snapshot() {return {...latest};}
    function poll() {
        try {
            if(!valid()) {unavailable();return;}
            const before=bridge.add(24).readU32();
            if(before===seenAck || (before&1)!==0) return;
            const length=bridge.add(28).readU32();
            if(length<1 || length>32000) {unavailable();return;}
            const value=bridge.add(33024).readUtf8String(length),after=bridge.add(24).readU32();
            if(before!==after || (after&1)!==0) return;
            const ack=JSON.parse(value);
            if(!exact(ack,['kind','requestId','snapshot']) || ack.kind!=='valmax-notification-policy-ack' ||
                    typeof ack.requestId!=='string' || !/^[a-f0-9]{32}$/.test(ack.requestId) ||
                    !exact(ack.snapshot,['ready','applied','revision','error','masked','scopeUnknown']) ||
                    typeof ack.snapshot.ready!=='boolean' || typeof ack.snapshot.applied!=='boolean' ||
                    typeof ack.snapshot.revision!=='string' || !/^[a-f0-9]{64}$/.test(ack.snapshot.revision) ||
                    !(ack.snapshot.error===null || typeof ack.snapshot.error==='string' && /^[a-z_]{1,48}$/.test(ack.snapshot.error)) ||
                    !['masked','scopeUnknown'].every(k=>Number.isInteger(ack.snapshot[k]) && ack.snapshot[k]>=0 && ack.snapshot[k]<=1000000)) {unavailable();return;}
            seenAck=before;latest={...ack.snapshot};
            if(pending.delete(ack.requestId)) send(ack);
        } catch(_) {unavailable();}
    }
    function receive() {
        recv('valmax-notification-policy',message=>{
            try {
                if(!valid() || !exact(message,['type','requestId','generation','policy']) || message.type!=='valmax-notification-policy' ||
                        typeof message.requestId!=='string' || !/^[a-f0-9]{32}$/.test(message.requestId) ||
                        !Number.isSafeInteger(message.generation) || message.generation<1 || pending.size>=64 || sequence>0xfffffffc) {
                    unavailable();receive();return;
                }
                const raw=JSON.stringify(message);
                // IDs and wire keys are ASCII after host validation. Count UTF8
                // independently so an invalid Unicode policy cannot overrun it.
                let length=0;for(const c of raw){const code=c.codePointAt(0);length+=code<=0x7f?1:code<=0x7ff?2:code<=0xffff?3:4;}
                if(length<1 || length>32000) {unavailable();receive();return;}
                pending.add(message.requestId);
                const next=sequence+2;
                bridge.add(16).writeU32(next-1);bridge.add(20).writeU32(length);
                bridge.add(256).writeUtf8String(raw);bridge.add(16).writeU32(next);sequence=next;
                poll();
            } catch(_) {unavailable();}
            receive();
        });
    }
    if(!valid()) throw new Error('Unsupported notification bridge');
    receive();setInterval(poll,25);return {snapshot};
})();
rpc.exports={snapshot(){return valmaxNotificationUpdater.snapshot();}};
