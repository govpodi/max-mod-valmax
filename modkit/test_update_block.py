"""Update-disable leaves and fail-closed patch checks, without loading MAX.

The optional Windows test executes only our replacement bytes in an isolated
synthetic child process. It never loads a MAX/WinSparkle image, touches a
profile, reads the registry or makes a network request.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import unittest

import build_mod
import isolation


UPDATE_IDS = {
    "isolation.disable_update_dispatcher",
    "isolation.disable_update_worker",
    "isolation.disable_update_check_with_ui",
    "isolation.disable_update_check_and_install",
    "isolation.disable_update_check_without_ui",
}


def update_patches():
    return [(spec, entry) for spec in isolation.PATCH_FILES
            for entry in spec["patches"] if entry["id"] in UPDATE_IDS]


_SYNTHETIC_CALL = r'''
import ctypes
from ctypes import wintypes
import json
import struct
import sys

leaves = json.loads(sys.argv[1])
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
kernel.VirtualAlloc.restype = ctypes.c_void_p
kernel.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
                                 ctypes.POINTER(wintypes.DWORD)]
kernel.VirtualProtect.restype = wintypes.BOOL
kernel.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
kernel.VirtualFree.restype = wintypes.BOOL
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.FlushInstructionCache.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t]
kernel.FlushInstructionCache.restype = wintypes.BOOL
marker = 0xFEDCBA9876543210
results = {}
for name, encoded in leaves.items():
    # Proper Windows x64 shadow space/alignment; preload RAX so a missing
    # false return cannot pass by inheriting a zero register from the caller.
    wrapper = bytes.fromhex("4883ec2848b8") + struct.pack("<Q", marker)
    wrapper += b"\xe8" + struct.pack("<i", 0x40 - (len(wrapper) + 5))
    wrapper += bytes.fromhex("4883c428c3")
    payload = wrapper + b"\xcc" * (0x40 - len(wrapper)) + bytes.fromhex(encoded)
    address = kernel.VirtualAlloc(None, 4096, 0x3000, 0x04)
    if not address:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ctypes.memmove(address, payload, len(payload))
        previous = wintypes.DWORD()
        if not kernel.VirtualProtect(address, 4096, 0x20, ctypes.byref(previous)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.FlushInstructionCache(kernel.GetCurrentProcess(), address, len(payload)):
            raise ctypes.WinError(ctypes.get_last_error())
        run = ctypes.WINFUNCTYPE(ctypes.c_uint64, ctypes.c_void_p,
                               ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(address)
        obj = ctypes.create_string_buffer(bytes(range(256)))
        before = bytes(obj)
        pointer = ctypes.addressof(obj)
        values = [run(pointer, pointer, pointer, pointer) for _ in range(100)]
        if bytes(obj) != before:
            raise AssertionError("Replacement touched synthetic argument memory")
        expected = 0 if name.endswith("dispatcher") else marker
        if values != [expected] * 100:
            raise AssertionError("Replacement returned an unexpected register value")
        results[name] = {"calls": len(values), "argumentMemoryUnchanged": True,
                         "returnsFalse": expected == 0}
    finally:
        if not kernel.VirtualFree(address, 0, 0x8000):
            raise ctypes.WinError(ctypes.get_last_error())
print(json.dumps(results))
'''


class UpdatePatchGuardsTests(unittest.TestCase):
    def test_all_native_update_entry_points_are_mandatory_isolation_patches(self):
        patches = update_patches()
        self.assertEqual({entry["id"] for _, entry in patches}, UPDATE_IDS)
        self.assertEqual(len(patches), len(UPDATE_IDS))
        self.assertEqual({spec["relative_path"] for spec, _ in patches},
                         {"max.exe", "WinSparkle.dll"})
        self.assertTrue(all(len(spec["sha256"]) == 64 for spec, _ in patches))

    def test_each_update_patch_reverses_and_rejects_corruption(self):
        for _, entry in update_patches():
            with self.subTest(entry=entry["id"]):
                expected = bytes.fromhex(entry["expected"])
                # Compact surrogate retains the exact pinned instruction bytes.
                compact = dict(entry, offset=3)
                original = b"abc" + expected + b"xyz"
                modified = build_mod.patch_bytes(original, [compact])
                self.assertEqual(build_mod.patch_bytes(modified, [compact], reverse=True), original)
                corrupted = bytearray(original)
                corrupted[3] ^= 1
                with self.assertRaisesRegex(ValueError, "Unexpected bytes"):
                    build_mod.patch_bytes(bytes(corrupted), [compact])
                corrupted = bytearray(modified)
                corrupted[3] ^= 1
                with self.assertRaisesRegex(ValueError, "Unexpected bytes"):
                    build_mod.patch_bytes(bytes(corrupted), [compact], reverse=True)

    def test_win_sparkle_cleanup_and_configuration_are_not_replaced(self):
        spec = next(spec for spec in isolation.PATCH_FILES
                    if spec["relative_path"] == "WinSparkle.dll")
        self.assertEqual(len(spec["patches"]), 4)
        # cleanup RVA0xeb90 / raw0xdf90 remains callable after a skipped init.
        for entry in spec["patches"]:
            self.assertFalse(entry["offset"] <= 0xDF90 <
                             entry["offset"] + len(bytes.fromhex(entry["expected"])))

    @unittest.skipUnless(os.name == "nt" and ctypes.sizeof(ctypes.c_void_p) == 8,
                         "Windows x64 isolated replacement-byte test")
    def test_native_leaves_return_without_touching_arguments_or_starting_work(self):
        leaves = {entry["id"]: entry["replacement"] for _, entry in update_patches()}
        result = subprocess.run([sys.executable, "-B", "-c", _SYNTHETIC_CALL,
                                 json.dumps(leaves)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(set(observed), UPDATE_IDS)
        self.assertTrue(all(value["calls"] == 100 and value["argumentMemoryUnchanged"]
                            for value in observed.values()))
        self.assertTrue(observed["isolation.disable_update_dispatcher"]["returnsFalse"])
        self.assertTrue(all(not value["returnsFalse"] for name, value in observed.items()
                            if name != "isolation.disable_update_dispatcher"))


if __name__ == "__main__":
    unittest.main()
