"""MyTracker-only service patch: pinned bytes, ownership evidence and Win64 stub ABI.

No MAX process, real profile, credential, SDK connection or network is used.
The executable image is read only. The native test executes only our 5-byte
return stub and a synthetic caller in this test process, never the SDK code.
"""
from pathlib import Path
import copy
import ctypes
import hashlib
import json
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

import build_mod
import ghost_native
import privacy
import privacy_monitor_specs

BASE = Path('D:/CodexLabs/max-mod-26.35.1/runtime/baseline')
RELATIVE = 'max-service/max-service.exe'
SERVICE_SHA = 'bd82b3fa0a6d24ee87bb9e0ec32ee311df7c40d8d233a71537685af00d50e0eb'

def entry():
    spec = next(item for item in privacy.PATCH_FILES if item['relative_path'] == RELATIVE)
    rule = next(item for item in spec['patches'] if item['id'] == 'no_mytracker_stats_init')
    return spec, rule


class MyTrackerPatchTests(unittest.TestCase):
    def test_only_service_void_initializer_and_exact_guard(self):
        spec, rule = entry()
        self.assertEqual(spec['sha256'], SERVICE_SHA)
        self.assertEqual(rule['offset'], 0x160C9B0)
        self.assertEqual(rule['expected'], '48895c2410')
        self.assertEqual(rule['replacement'], 'c390909090')
        self.assertEqual([item['id'] for item in spec['patches']], ['no_mytracker_stats_init'])
        ids = [rule['id'] for spec in privacy.PATCH_FILES for rule in spec['patches']]
        self.assertEqual(ids.count('no_mytracker_stats_init'), 1)
        self.assertEqual([p['id'] for p in next(s for s in privacy.PATCH_FILES if s['relative_path']=='core.dll')['patches']],
                         ['no_analytics_session', 'no_analytics_flush'])

    def test_small_fixture_exact_reverse_and_mismatched_entry_refused(self):
        _, rule = entry()
        synthetic = copy.deepcopy(rule); synthetic['offset'] = 8
        original = b'fixture!' + bytes.fromhex(rule['expected']) + b'owned remainder'
        changed = build_mod.patch_bytes(original, [synthetic])
        self.assertEqual(changed[:8], original[:8])
        self.assertEqual(changed[8:13], bytes.fromhex(rule['replacement']))
        self.assertEqual(changed[13:], original[13:])
        self.assertEqual(build_mod.patch_bytes(changed, [synthetic], reverse=True), original)
        with self.assertRaisesRegex(ValueError, 'Unexpected bytes'):
            build_mod.patch_bytes(original[:8]+b'XXXXX'+original[13:], [synthetic])

    @unittest.skipUnless(os.name == 'nt' and struct.calcsize('P') == 8, 'Owned Win64 ABI fixture')
    def test_native_stub_ignores_poison_arguments_preserves_stack_and_callee_registers(self):
        _, rule = entry()
        # The caller owns the record. Invalid argument addresses are never
        # dereferenced because the patched leaf returns before its prologue.
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.VirtualAlloc.argtypes = [ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.c_uint32]
        k32.VirtualAlloc.restype = ctypes.c_void_p
        k32.VirtualProtect.argtypes = [ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint32)]
        k32.VirtualProtect.restype = ctypes.c_int
        k32.VirtualFree.argtypes = [ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32]
        k32.VirtualFree.restype = ctypes.c_int
        k32.FlushInstructionCache.argtypes = [ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t]
        k32.FlushInstructionCache.restype = ctypes.c_int
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        memory = k32.VirtualAlloc(None,4096,0x3000,0x04)
        self.assertTrue(memory)
        try:
            stub = bytes.fromhex(rule['replacement'])
            ctypes.memmove(memory,stub,len(stub))
            markers = [0x1122334455667700+i for i in range(8)]
            homes = [0x2233445566778800+i for i in range(4)]
            # Save all Win64 nonvolatile integer registers, reserve aligned
            # shadow space and private locals, then invoke the real patch bytes.
            code = bytearray.fromhex('5355565741544155415641574883ec3848894c242048895424284889642430')
            for offset,value in zip((0,8,0x10,0x18),homes):
                code += b'\x48\xb8'+struct.pack('<Q',value)
                code += b'\x48\x89\x04\x24' if offset==0 else b'\x48\x89\x44\x24'+bytes([offset])
            for op,value in zip(('48bb','48bd','48be','48bf','49bc','49bd','49be','49bf'),markers):
                code += bytes.fromhex(op)+struct.pack('<Q',value)
            for op,value in zip(('48b9','48ba','49b8','49b9'),(1,2,3,4)):
                code += bytes.fromhex(op)+struct.pack('<Q',value)
            code += bytes.fromhex('ff542420488b442428')
            for op,offset in zip(('488918','488968','488970','488978','4c8960','4c8968','4c8970','4c8978'),range(0,64,8)):
                code += bytes.fromhex(op) if offset==0 else bytes.fromhex(op)+bytes([offset])
            code += bytes.fromhex('48896040488b4c243048894848')
            for source,dest in zip((0,8,0x10,0x18),(0x50,0x58,0x60,0x68)):
                code += bytes.fromhex('488b0c24') if source==0 else bytes.fromhex('488b4c24')+bytes([source])
                code += bytes.fromhex('488948')+bytes([dest])
            code += bytes.fromhex('4883c438415f415e415d415c5f5e5d5bc3')
            caller = memory+0x100
            ctypes.memmove(caller,bytes(code),len(code))
            old = ctypes.c_uint32()
            self.assertTrue(k32.VirtualProtect(memory,4096,0x20,ctypes.byref(old)))
            self.assertTrue(k32.FlushInstructionCache(k32.GetCurrentProcess(),memory,4096))
            record = (ctypes.c_uint64*16)()
            ctypes.WINFUNCTYPE(None,ctypes.c_void_p,ctypes.c_void_p)(caller)(memory,ctypes.addressof(record))
            self.assertEqual(list(record)[:8], markers)
            self.assertEqual(record[8],record[9])
            self.assertEqual(list(record)[10:14], homes)
        finally:
            self.assertTrue(k32.VirtualFree(memory,0,0x8000))


@unittest.skipUnless((BASE/RELATIVE).is_file(), 'Pinned immutable vendor image unavailable')
class MyTrackerPinnedImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (BASE/RELATIVE).read_bytes()
        if build_mod.sha(cls.original) != SERVICE_SHA:
            raise AssertionError('Unsupported baseline service')
        cls.spec = next(s for s in build_mod.specifications(['accounts','privacy','isolation']) if s['relative_path']==RELATIVE)
        cls.modified = build_mod.patch_bytes(cls.original,cls.spec['patches'])

    def test_pinned_real_image_rva_full_reverse_and_only_expected_spans(self):
        import pefile
        pe = pefile.PE(data=self.original,fast_load=True)
        _,rule=entry()
        self.assertEqual(pe.get_offset_from_rva(0x160D3B0),rule['offset'])
        self.assertEqual(pe.get_data(0x160D3B0,5).hex(),rule['expected'])
        self.assertEqual(build_mod.patch_bytes(self.modified,self.spec['patches'],True),self.original)
        occupied = {i for p in self.spec['patches'] for i in range(p['offset'],p['offset']+len(bytes.fromhex(p['expected'])))}
        self.assertTrue(all(a==b or i in occupied for i,(a,b) in enumerate(zip(self.original,self.modified))))
        self.assertEqual(len(self.original),len(self.modified))

    def test_cache_identity_updated_without_changing_tracer_callsites(self):
        cachepath=Path(__file__).with_name('privacy_callsites.json')
        cache=cachepath.read_bytes()
        self.assertEqual(build_mod.sha(cache),privacy_monitor_specs.CACHE_SHA256)
        records=json.loads(cache)['entries']
        record=next(s for s in records if s['relative_path']==RELATIVE)
        self.assertEqual(record['sha256'],build_mod.sha(self.modified))
        import pefile
        pe=pefile.PE(data=self.modified,fast_load=True)
        for call in record['callsites']:
            self.assertEqual(pe.get_data(call['rva'],len(call['expected'])//2).hex(),call['expected'])
        realread=Path.read_bytes
        syntheticroot=Path('D:/ValMAX/analysis/privacy-v2/owned-memory-only')
        def read(path):
            if path==syntheticroot/RELATIVE:return self.modified
            return realread(path)
        with patch.object(Path,'read_bytes',read):
            spec=privacy_monitor_specs.specifications(syntheticroot,service=True)
        self.assertEqual(spec['tracerCallsites'],record['callsites'])

    def test_core_identity_remains_compatible_with_ghost_and_retention(self):
        spec=next(s for s in build_mod.specifications(['accounts','privacy','isolation']) if s['relative_path']=='core.dll')
        original=(BASE/'core.dll').read_bytes()
        self.assertEqual(build_mod.sha(original),spec['sha256'])
        self.assertEqual(build_mod.sha(build_mod.patch_bytes(original,spec['patches'])),ghost_native.CORE_SHA256)


if __name__=='__main__':unittest.main()
