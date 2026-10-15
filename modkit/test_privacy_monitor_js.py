"""Exercise the actual observer JS with synthetic pointers, never MAX/Frida."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


HARNESS = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
function setup() {
    const hooks = {}, sent = [], reads = [];
    let added;
    const address = label => ({toString: () => label, readByteArray: () => Uint8Array.from([255,21,13,146,11,0]).buffer});
    const context = {
        MONITOR: {root:'D:\\synthetic', tracerModule:'unused.dll', tracerPath:'unused', tracerCallsites:[], networkPath:'D:\\synthetic\\network.dll', packetHook:{rva:1, expected:'ff150d920b00'}},
        Process: {attachModuleObserver: value => {added=value.onAdded; return {}; }},
        Interceptor: {attach: (where, callback) => {hooks[where.toString()]=typeof callback === 'function' ? callback : callback.onEnter; return {}; }},
        rpc: {exports:{}}, recv: () => {}, send: (...args) => sent.push(args)
    };
    vm.runInNewContext(source, context);
    added({name:'network.dll',path:'D:\\synthetic\\network.dll',base:{add:()=>address('packet')}});
    added({name:'ws2_32.dll',path:'C:\\Windows\\System32\\ws2_32.dll',findExportByName:address});
    function packet(opcode, length) {
        const header = new Uint8Array(10), body = length-10;
        header[4]=opcode>>8; header[5]=opcode&255;
        header[7]=(body>>16)&255;header[8]=(body>>8)&255;header[9]=body&255;
        const pointer = {isNull:()=>false, readByteArray:n=>{reads.push(n);assert.strictEqual(n,10,'unexpected body read');return header.buffer;}};
        hooks.packet.call({context:{rdx:pointer,r8:{toString:()=>String(length)}}});
    }
    function connect(host, port) {
        const bytes = new Uint8Array(16);bytes[2]=port>>8;bytes[3]=port&255;bytes.set(host.split('.').map(Number),4);
        const pointer={isNull:()=>false,readU16:()=>2,add:offset=>({readU8:()=>bytes[offset]})};
        hooks.connect.call({},[null,pointer,{toInt32:()=>16}]);
    }
    return {packet,connect,snapshot:()=>context.rpc.exports.snapshot(),sent,reads};
}
const a=setup(); let total=0;
for(let size=11;size<=210;size++){a.packet(27,size);total+=size;}
a.packet(19,1061);
let s=a.snapshot();
assert.strictEqual(s.packets.length,2);
let group=s.packets.find(p=>p.metadata.opcode===27);
assert.strictEqual(group.count,200);assert.strictEqual(group.bytesTotal,total);
assert.strictEqual(group.minBytes,11);assert.strictEqual(group.maxBytes,210);
assert.strictEqual(group.metadata.bytes,11);assert.strictEqual(s.omittedPackets,0);
assert.strictEqual(a.sent.length,0);assert(a.reads.every(n=>n===10));
assert.strictEqual(a.snapshot().packets.length,0);
const b=setup();for(let op=7;op<264;op++)b.packet(op,24);
s=b.snapshot();assert.strictEqual(s.packets.length,256);assert.strictEqual(s.omittedPackets,1);assert.strictEqual(s.omittedBytes,24);
assert.strictEqual(b.snapshot().omittedPackets,0);
const c=setup();c.connect('127.0.0.1',18973);c.connect('127.0.0.1',5555);c.connect('8.8.8.8',443);
s=c.snapshot();assert.strictEqual(s.connections['127.0.0.1|18973'],undefined);
assert.strictEqual(s.connections['127.0.0.1|5555'],1);assert.strictEqual(s.connections['8.8.8.8|443'],1);
console.log(JSON.stringify({checks:5,headerOnlyReads:a.reads.length}));
"""


class ObserverJavascriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable for JS-only observer fixture")
    def test_batch_sizes_overflow_reset_and_self_requests_use_actual_js(self):
        source = Path(__file__).with_name("privacy_monitor.js")
        result = subprocess.run([shutil.which("node"), "-e", HARNESS, str(source)],
                                capture_output=True, text=True, timeout=10, check=True)
        value = json.loads(result.stdout)
        self.assertEqual(value["checks"], 5)
        self.assertEqual(value["headerOnlyReads"], 201)


if __name__ == "__main__":
    unittest.main()
