"""Execute only the new account-limit leaf in a separate synthetic process.

No MAX image is loaded, modified or started; no profiles or credentials are read.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import unittest

import accounts


_SYNTHETIC_LEAF = r'''
import ctypes
from ctypes import wintypes
import json
import struct
import sys

leaf = bytes.fromhex(sys.argv[1])
# Save RBX, use the RCX argument as the synthetic object, reserve shadow space,
# call the leaf at +0x40, restore RBX/stack and return RAX.
wrapper = bytes.fromhex("534889cb4883ec20e8330000004883c4205bc3")
payload = wrapper + b"\xcc" * (0x40 - len(wrapper)) + leaf
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
address = kernel.VirtualAlloc(None, 4096, 0x3000, 0x04)
if not address:
    raise ctypes.WinError(ctypes.get_last_error())
results = []
try:
    ctypes.memmove(address, payload, len(payload))
    previous = wintypes.DWORD()
    if not kernel.VirtualProtect(address, 4096, 0x20, ctypes.byref(previous)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.FlushInstructionCache(kernel.GetCurrentProcess(), address, len(payload)):
        raise ctypes.WinError(ctypes.get_last_error())
    run = ctypes.WINFUNCTYPE(ctypes.c_uint64, ctypes.c_void_p)(address)
    obj = ctypes.create_string_buffer(0x450)
    for value in [0, 1, 2, 3, 4, 7, 255, 2**31, 2**32, 2**63 - 1, 2**63, 2**64 - 1]:
        ctypes.memmove(ctypes.addressof(obj) + 0x448, struct.pack("<Q", value), 8)
        before = bytes(obj)
        actual = run(ctypes.addressof(obj))
        if bytes(obj) != before:
            raise AssertionError("The synthetic object was modified")
        results.append([value, actual])
finally:
    if not kernel.VirtualFree(address, 0, 0x8000):
        raise ctypes.WinError(ctypes.get_last_error())
print(json.dumps(results))
'''


@unittest.skipUnless(os.name == "nt" and ctypes.sizeof(ctypes.c_void_p) == 8,
                     "Windows x64 machine-code test")
class AccountLimitLeafTests(unittest.TestCase):
    def test_local_minimum_three_preserves_larger_limits_and_negative_sentinels(self):
        leaf = next(patch["replacement"] for entry in accounts.PATCH_FILES
                    for patch in entry["patches"]
                    if patch["id"] == "accounts.ui_zero_limit_leaf")
        result = subprocess.run([sys.executable, "-B", "-c", _SYNTHETIC_LEAF, leaf],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        cases = json.loads(result.stdout)
        for before, after in cases:
            with self.subTest(server_limit=before):
                self.assertEqual(after, 3 if before < 3 else before)
        self.assertEqual(cases[:4], [[0, 3], [1, 3], [2, 3], [3, 3]])
        self.assertIn([2**64 - 1, 2**64 - 1], cases)


if __name__ == "__main__":
    unittest.main(verbosity=2)
