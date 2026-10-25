"""Synthetic native DLL ABI/import proof. Never launches MAX or reads profiles."""
from __future__ import annotations
import ctypes as c
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid
import pefile

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path("C:/ValMAXBuild/build/hooks")


class Policy(c.Structure):
    _fields_ = [("size", c.c_uint32), ("version", c.c_uint32), ("revision", c.c_char * 64),
                ("enabled", c.c_uint8), ("hide_typing", c.c_uint8), ("hide_read", c.c_uint8),
                ("hide_online", c.c_uint8), ("hide_stories", c.c_uint8), ("reserved", c.c_uint8 * 3)]


class Status(c.Structure):
    _fields_ = [("size", c.c_uint32), ("version", c.c_uint32), ("error", c.c_uint32), ("ready", c.c_uint32),
                ("policy", Policy), ("typing", c.c_uint64), ("read", c.c_uint64), ("presence", c.c_uint64),
                ("stories", c.c_uint64), ("faults", c.c_uint64), ("replacements", c.c_uint32), ("listeners", c.c_uint32)]


def main():
    c.windll.kernel32.SetErrorMode(0x8003)
    dll_path = BUILD / "valmax_ghost.dll"
    dll = c.CDLL(str(dll_path))
    dll.valmax_ghost_set_policy.argtypes = [c.POINTER(Policy)]
    dll.valmax_ghost_status.argtypes = [c.POINTER(Status)]
    dll.valmax_ghost_install.argtypes = [c.c_wchar_p, c.c_wchar_p]
    dll.valmax_ghost_shutdown.argtypes = []
    dll.valmax_ghost_invalidate_unread.argtypes = []
    dll.valmax_ghost_invalidate_unread.restype = None
    checks = []
    def check(name, value):
        assert value, name
        checks.append(name)
    check("C layout matches Rust-compatible80/144", c.sizeof(Policy) == 80 and c.sizeof(Status) == 144)
    check("null policy rejected", dll.valmax_ghost_set_policy(None) == 1)
    check("null status rejected", dll.valmax_ghost_status(None) == 1)
    check("null path rejected", dll.valmax_ghost_install(None, None) == 1)
    state = Status(c.sizeof(Status), 1)
    check("cold state not ready", dll.valmax_ghost_status(c.byref(state)) == 0 and not state.ready and not state.replacements and not state.listeners)
    policy = Policy(c.sizeof(Policy), 1, b"a" * 64, 1, 1, 1, 1, 1)
    check("valid policy accepted without hooks", dll.valmax_ghost_set_policy(c.byref(policy)) == 0)
    check("policy does not fabricate readiness", dll.valmax_ghost_status(c.byref(state)) == 0 and not state.ready and state.policy.revision == b"a" * 64)
    policy.hide_read = 0
    check("same revision different flag rejected", dll.valmax_ghost_set_policy(c.byref(policy)) == 6)
    policy.hide_read = 2
    check("non-boolean rejected", dll.valmax_ghost_set_policy(c.byref(policy)) == 1)
    policy.hide_read = 1; policy.revision = b"A" * 64
    check("non-canonical revision rejected", dll.valmax_ghost_set_policy(c.byref(policy)) == 1)
    policy.revision = b"b" * 64; policy.reserved[0] = 1
    check("unknown reserved flag rejected", dll.valmax_ghost_set_policy(c.byref(policy)) == 1)
    check("production cannot install unpinned file", dll.valmax_ghost_install("C:/ValMAXBuild/nonexistent/core.dll", "C:/ValMAXBuild/nonexistent/max.exe") == 2)
    check("mismatched parent rejected", dll.valmax_ghost_install("C:/ValMAXBuild/nonexistent/core.dll", "C:/ValMAXBuild/other/max.exe") == 1)
    dll.valmax_ghost_invalidate_unread()
    check("cold shutdown safe", dll.valmax_ghost_shutdown() == 0)
    pe = pefile.PE(str(dll_path))
    exports = sorted(symbol.name.decode() for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols if symbol.name)
    imports = sorted(item.dll.decode() for item in pe.DIRECTORY_ENTRY_IMPORT)
    check("only production C ABI exports", exports == sorted(["valmax_ghost_install", "valmax_ghost_set_policy", "valmax_ghost_status", "valmax_ghost_invalidate_unread", "valmax_ghost_shutdown"]))
    check("no Python JS or dynamic CRT dependency", not any(any(word in name.lower() for word in ("python", "quickjs", "v8", "vcruntime", "msvcp")) for name in imports))
    native = json.loads(subprocess.run([str(BUILD / "valmax_ghost_test.exe")], check=True, text=True, capture_output=True, timeout=20).stdout)
    base = json.loads(subprocess.run([str(BUILD / "valmax_hooks_test.exe")], check=True, text=True, capture_output=True, timeout=20).stdout)
    invalid = Path("C:/ValMAXBuild/fixtures") / ("ghost-invalid-pins-" + uuid.uuid4().hex)
    invalid.mkdir()
    (invalid / "core.dll").write_bytes(b"MZ")
    (invalid / "max.exe").write_bytes(b"MZ")
    generated = invalid / "must-not-exist.h"
    reject = subprocess.run([sys.executable, "-O", str(ROOT / "hooks/ghost_generate_pins.py"), "--runtime-root", str(invalid), "--output", str(generated), "--proof", str(invalid / "proof.json")], capture_output=True, text=True, timeout=20)
    optimized_rejection = reject.returncode != 0 and "Unsupported core size" in reject.stderr and not generated.exists()
    assert optimized_rejection, "Python optimization must not bypass pin validation"
    sources = [*ROOT.glob("hooks/ghost*"), ROOT / "hooks/CMakeLists.txt", Path(__file__).with_name("ghost_test.cpp"), Path(__file__)]
    report = {"schema": 1, "ok": True, "scope": "synthetic native/C ABI proof; live MAX integration and server behavior not claimed", "dllSha256": hashlib.sha256(dll_path.read_bytes()).hexdigest(), "dllSize": dll_path.stat().st_size, "nativeChecks": native, "baseHookChecks": base, "abiChecks": checks, "optimizedPythonPinRejection": optimized_rejection, "exports": exports, "imports": imports, "pins": json.loads((BUILD / "ghost-pins.json").read_text()), "sourceSha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}, "realProfilesAccessed": False, "networkUsed": False, "installedClientModified": False}
    target = ROOT.parent / "analysis/native-migration-v1/ghost-native-proof.json"
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "nativeChecks": native["checks"], "abiChecks": len(checks), "baseHookChecks": base["checks"], "dllSha256": report["dllSha256"], "report": str(target)}))


if __name__ == "__main__":
    main()
