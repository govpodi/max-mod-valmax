"""Exercise actual observer JS in Node VM, without Frida/MAX/profile access."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


HARNESS = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
const selected = process.argv[2];
const NETWORK_BYTES = 'ff150d920b00';
function setup(options = {}) {
    const hooks = new Map(), sent = [], codeReads = [];
    let added;
    const address = (label, bytes = NETWORK_BYTES) => Object.freeze({
        toString: () => label,
        readByteArray: size => {
            codeReads.push({label, size});
            const data = Buffer.from(bytes, 'hex');
            assert(size <= data.length, 'code read exceeded synthetic instruction');
            return Uint8Array.from(data.subarray(0, size)).buffer;
        }
    });
    const tracerEntries = [
        {rva: 32, symbol: 'tracer_init', expected: 'ff1511223344'},
        {rva: 64, symbol: 'tracer_upload_async', expected: 'e801020304'}
    ];
    const context = {
        MONITOR: {root: 'D:\\synthetic', tracerModule: 'tracer_crash_reporter.dll',
            tracerPath: 'D:\\synthetic\\tracer_crash_reporter.dll', tracerCallsites: tracerEntries,
            networkPath: 'D:\\synthetic\\network.dll', packetHook: {rva: 16, expected: NETWORK_BYTES}},
        Process: {attachModuleObserver: callback => {added = callback.onAdded; return {}; }},
        Interceptor: {attach: (where, callback) => {
            const label = where.toString();
            assert(!hooks.has(label), 'duplicate installation');
            hooks.set(label, {form: typeof callback, callback,
                enter: typeof callback === 'function' ? callback : callback.onEnter});
            return {detach: () => {}};
        }},
        rpc: {exports: {}}, recv: () => {}, send: (...args) => sent.push(args)
    };
    vm.runInNewContext(source, context, {timeout: 1000});
    added({name: 'Qt6Core.dll', path: 'D:\\synthetic\\Qt6Core.dll', enumerateExports: () => [
        {type: 'function', name: '?fatal@QMessageLogger@@synthetic', address: address('qt-fatal')},
        {type: 'function', name: '?qt_message_output@@synthetic', address: address('qt-output')},
        {type: 'function', name: '?other@@synthetic', address: address('qt-unrelated')}
    ]});
    added({name: 'tracer_crash_reporter.dll',
        path: options.foreignTracer ? 'D:\\other\\tracer_crash_reporter.dll' : context.MONITOR.tracerPath,
        base: {add: rva => address('tracer-' + rva,
            options.tracerMismatch && rva === 32 ? '000000000000' : tracerEntries.find(e => e.rva === rva).expected)}
    });
    added({name: 'network.dll', path: options.foreignNetwork ? 'D:\\other\\network.dll' : context.MONITOR.networkPath,
        base: {add: () => address('packet', options.networkMismatch ? '000000000000' : NETWORK_BYTES)}});
    added({name: 'ws2_32.dll', path: 'C:\\Windows\\System32\\ws2_32.dll',
        findExportByName: name => address(name)});
    function hit(label, receiver = {}, args = []) {
        const hook = hooks.get(label);
        assert(hook && typeof hook.enter === 'function', 'missing synthetic hook');
        return hook.enter.call(receiver, args);
    }
    function packet(opcode, size, control = 0, declaredSize = size - 10) {
        const data = Buffer.alloc(size, 0x5a), original = Buffer.from(data), reads = [];
        data[4] = opcode >> 8; data[5] = opcode & 255; data[6] = control;
        data[7] = declaredSize >> 16 & 255; data[8] = declaredSize >> 8 & 255; data[9] = declaredSize & 255;
        data.copy(original);
        const pointer = Object.freeze({isNull: () => false, readByteArray: count => {
            reads.push(count);
            assert(count <= data.length, 'packet read out of bounds');
            return Uint8Array.from(data.subarray(0, count)).buffer;
        }});
        const registers = Object.freeze({rdx: pointer, r8: Object.freeze({toString: () => String(size)})});
        hit('packet', {context: registers});
        assert.deepStrictEqual(data, original, 'observer modified packet bytes');
        return reads;
    }
    function connect() {
        const bytes = Uint8Array.from([2,0,1,187,192,0,2,7,0,0,0,0,0,0,0,0]);
        const pointer = Object.freeze({isNull: () => false, readU16: () => 2,
            add: offset => Object.freeze({readU8: () => bytes[offset]})});
        hit('connect', {}, Object.freeze([null, pointer, Object.freeze({toInt32: () => 16})]));
    }
    return {hooks, hit, packet, connect, sent, codeReads, snapshot: () => context.rpc.exports.snapshot()};
}
function contract() {
    const value = setup();
    for (const label of ['packet', 'tracer-32', 'tracer-64'])
        assert.strictEqual(value.hooks.get(label).form, 'function', 'instruction must use a probe');
    for (const label of ['qt-fatal', 'qt-output', 'connect', 'WSAConnect']) {
        const hook = value.hooks.get(label);
        assert.strictEqual(hook.form, 'object', 'export hook must retain function-entry callbacks');
        assert.strictEqual(typeof hook.callback.onEnter, 'function');
    }
    assert(!value.hooks.has('qt-unrelated'));
    assert.deepStrictEqual(JSON.parse(JSON.stringify(value.snapshot().stats)), {tracerHooks: 2, transportHooks: 3});
    return {checks: 8};
}
function guards() {
    let value = setup({networkMismatch: true, tracerMismatch: true});
    assert(!value.hooks.has('packet'));
    assert(!value.hooks.has('tracer-32'));
    assert.strictEqual(value.hooks.get('tracer-64').form, 'function');
    let state = value.snapshot();
    assert.strictEqual(state.failures, 2);
    assert.strictEqual(state.stats.tracerHooks, 1);
    assert.strictEqual(state.stats.transportHooks, 2);
    assert(value.codeReads.some(item => item.label === 'packet' && item.size === 6));
    value = setup({foreignNetwork: true, foreignTracer: true});
    assert(!value.hooks.has('packet') && !value.hooks.has('tracer-32') && !value.hooks.has('tracer-64'));
    assert.strictEqual(value.codeReads.length, 0, 'foreign module bytes must not be read');
    return {checks: 9};
}
function metadata() {
    const value = setup();
    assert.deepStrictEqual(value.packet(27, 80), [10]);
    assert.deepStrictEqual(value.packet(19, 60), [10]);
    assert.deepStrictEqual(value.packet(6, 20), [10, 20]);
    assert.deepStrictEqual(value.packet(6, 16385), [10]);
    assert.deepStrictEqual(value.packet(6, 20, 1), [10]);
    assert.deepStrictEqual(value.packet(27, 20, 0, 0), [10]);
    assert.strictEqual(value.sent.length, 1);
    assert.strictEqual(value.sent[0][0].kind, 'session-init');
    assert.strictEqual(value.sent[0][1].byteLength, 20);
    assert.strictEqual(value.snapshot().failures, 0);
    return {checks: 10};
}
function counters() {
    const value = setup();
    value.hit('tracer-32'); value.hit('tracer-32'); value.hit('tracer-64');
    value.packet(27, 20); value.packet(27, 30); value.packet(19, 40); value.connect();
    let state = value.snapshot();
    assert.strictEqual(state.tracerCounts.tracer_init, 2);
    assert.strictEqual(state.tracerCounts.tracer_upload_async, 1);
    assert.strictEqual(state.connections['192.0.2.7|443'], 1);
    const group = state.packets.find(item => item.metadata.opcode === 27);
    assert.strictEqual(group.count, 2); assert.strictEqual(group.bytesTotal, 50);
    assert.strictEqual(group.minBytes, 20); assert.strictEqual(group.maxBytes, 30);
    assert.strictEqual(state.failures, 0);
    assert.strictEqual(value.sent.length, 0, 'header counters must stay batched');
    state = value.snapshot();
    assert.strictEqual(Object.keys(state.tracerCounts).length, 0);
    assert.strictEqual(Object.keys(state.connections).length, 0);
    assert.strictEqual(state.packets.length, 0);
    assert.strictEqual(state.stats.tracerHooks, 2);
    assert.strictEqual(state.stats.transportHooks, 3);
    return {checks: 14};
}
const cases = {contract, guards, metadata, counters};
assert(Object.prototype.hasOwnProperty.call(cases, selected));
console.log(JSON.stringify({case: selected, ...cases[selected]()}));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js unavailable for JS-only observer fixture")
class PrivacyHookContractTests(unittest.TestCase):
    def run_fixture(self, case, checks):
        source = Path(__file__).with_name("privacy_monitor.js")
        result = subprocess.run([shutil.which("node"), "-e", HARNESS, str(source), case],
                                capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), {"case": case, "checks": checks})

    def test_instruction_probe_and_export_function_forms_are_distinct(self):
        self.run_fixture("contract", 8)

    def test_expected_bytes_and_exact_module_path_guards_remain_enforced(self):
        self.run_fixture("guards", 9)

    def test_packet_bounds_header_only_reads_and_no_payload_modification(self):
        self.run_fixture("metadata", 10)

    def test_instruction_hits_keep_counts_batching_and_snapshot_reset(self):
        self.run_fixture("counters", 14)


if __name__ == "__main__":
    unittest.main()
