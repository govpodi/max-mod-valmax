"""Synthetic Node VM only: abort hooks never inspect arguments or log strings."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


HARNESS = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const hooks = new Map(), sent = [], opaque = Object.freeze({});
let added, traceCount = 0;
const address = label => Object.freeze({toString: () => label,
    readByteArray: () => {throw Error('diagnostic read native memory');}});
const frame = Object.freeze({sub: () => Object.freeze({toUInt32: () => 0x1234})});
const context = {
    MONITOR: {root: 'D:\\synthetic', tracerModule: 'unused.dll', tracerPath: '', tracerCallsites: []},
    Process: {
        attachModuleObserver: callback => {added = callback.onAdded; return {};},
        findModuleByAddress: value => {assert.strictEqual(value, frame); return {name: 'max.exe', base: opaque};}
    },
    Thread: {backtrace: (value, mode) => {
        assert.strictEqual(value, opaque); assert.strictEqual(mode, 'accurate'); traceCount++; return [frame];
    }},
    Backtracer: {ACCURATE: 'accurate'},
    Interceptor: {attach: (where, callbacks) => {
        assert.strictEqual(typeof callbacks, 'object', 'function entry requires object callbacks');
        assert.strictEqual(typeof callbacks.onEnter, 'function');
        assert.deepStrictEqual(Object.keys(callbacks), ['onEnter']);
        hooks.set(where.toString(), callbacks.onEnter); return {};
    }},
    rpc: {exports: {}}, recv: () => {}, send: value => sent.push(value)
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context, {timeout: 1000});
const names = ['abort', 'terminate', '_invoke_watson', '_invalid_parameter_noinfo_noreturn'];
for (const module of ['ucrtbase.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'kernelbase.dll', 'other.dll']) {
    const symbols = [...names, '__std_terminate', 'RaiseFailFastException', 'unrelated'];
    added({name: module, enumerateExports: () => symbols.map(name => ({type: 'function', name,
        address: address(module + ':' + name)})).concat([{type: 'variable', name: 'abort', address: address('variable')}])});
}
for (const module of ['ucrtbase.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'])
    for (const name of names) assert(hooks.has(module + ':' + name));
assert(hooks.has('kernelbase.dll:RaiseFailFastException'));
assert(hooks.has('vcruntime140.dll:__std_terminate'));
assert(hooks.has('vcruntime140_1.dll:__std_terminate'));
assert(!hooks.has('ucrtbase.dll:__std_terminate'));
assert.strictEqual(hooks.size, 15, 'unknown exports/modules must not be hooked');
const argumentsTrap = new Proxy({}, {get() {throw Error('native arguments were read');}});
for (let i = 0; i < 6; i++) hooks.get('ucrtbase.dll:abort').call({context: opaque}, argumentsTrap);
assert.strictEqual(sent.length, 4); assert.strictEqual(traceCount, 4);
for (const message of sent) {
    assert.deepStrictEqual(Object.keys(message).sort(), ['frames', 'kind']);
    assert.strictEqual(message.kind, 'fatal');
    assert.deepStrictEqual(JSON.parse(JSON.stringify(message.frames)), [{module: 'max.exe', rva: 0x1234}]);
}
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.rpc.exports.snapshot().stats)),
    {tracerHooks: 0, transportHooks: 0});
console.log(JSON.stringify({checks: 8}));
"""


class AbortDiagnosticTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_abort_entry_hooks_are_fixed_bounded_and_metadata_only(self):
        result = subprocess.run([shutil.which("node"), "-e", HARNESS,
                                 str(Path(__file__).with_name("privacy_monitor.js"))],
                                capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), {"checks": 8})


if __name__ == "__main__":
    unittest.main()
