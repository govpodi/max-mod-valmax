"""Build-only compatibility check: old store formats, real native core, own data.

Does not discover a user profile, start MAX, read credentials or import MData.
Python is the test oracle; it is not part of the installed application.
"""
from __future__ import annotations

import argparse
import copy
import ctypes as C
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "modkit"))
from account_preferences import AccountPreferencesStore, defaults as account_defaults
from ghost_store import GhostSettingsStore
from launch_mod import make_config
from optimization_store import OptimizationSettingsStore
from quick_reply_store import QuickReplyStore


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ordinary(path, directory=False):
    info = path.lstat()
    assert not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    assert stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not directory:
        assert info.st_nlink == 1


class Buffer(C.Structure):
    _fields_ = [("data", C.POINTER(C.c_uint8)), ("len", C.c_size_t), ("capacity", C.c_size_t)]


class ProfileResult(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in ("size", "version", "code", "state")]


class Native:
    def __init__(self, dll):
        self.dll = C.CDLL(str(dll), winmode=0x100 | 0x800)
        self.runtime = C.c_void_p()
        self.checks = 0
        self.dll.valmax_core_buffer_free.argtypes = [C.POINTER(Buffer)]
        self.dll.valmax_core_buffer_free.restype = None
        self.dll.valmax_launcher_prepare.argtypes = [C.c_char_p, C.c_size_t, C.c_uint32, C.POINTER(ProfileResult)]
        self.dll.valmax_launcher_prepare.restype = C.c_int32
        self.dll.valmax_runtime_open.argtypes = [C.c_char_p, C.c_size_t, C.POINTER(C.c_void_p), C.POINTER(Buffer)]
        self.dll.valmax_runtime_open.restype = C.c_int32
        self.dll.valmax_runtime_dispatch.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t, C.POINTER(Buffer)]
        self.dll.valmax_runtime_dispatch.restype = C.c_int32
        self.dll.valmax_runtime_cancel_pending.argtypes = [C.c_void_p]
        self.dll.valmax_runtime_cancel_pending.restype = None
        self.dll.valmax_runtime_close.argtypes = [C.POINTER(C.c_void_p)]
        self.dll.valmax_runtime_close.restype = None

    def check(self, condition, label):
        self.checks += 1
        if not condition:
            raise AssertionError(label)

    def decode(self, buffer):
        self.check(bool(buffer.data) and 0 < buffer.len <= 16 << 20, "native result bounds")
        try:
            data = C.string_at(buffer.data, buffer.len)
        finally:
            self.dll.valmax_core_buffer_free(C.byref(buffer))
        self.check(not buffer.data and buffer.len == 0 and buffer.capacity == 0, "result freed by its library")
        value = json.loads(data)
        self.check(value.get("schema") == 1 and type(value.get("ok")) is bool, "finite native envelope")
        return value

    def prepare(self, local):
        data = str(local).encode("utf8")
        result = ProfileResult()
        code = self.dll.valmax_launcher_prepare(data, len(data), 0, C.byref(result))
        self.check(code == 0 and result.size == 16 and result.version == 1, "launcher ABI")
        self.check(result.code == 0 and result.state == 2, "existing config accepted without replacement")

    def open(self, profile, runtime):
        self.check(not self.runtime.value, "one runtime at a time")
        data = json.dumps({"profileDirectory": str(profile), "runtimeDirectory": str(runtime),
                           "existingClient": False}).encode("utf8")
        output = Buffer()
        code = self.dll.valmax_runtime_open(data, len(data), C.byref(self.runtime), C.byref(output))
        self.check(code == 0, "native runtime open ABI")
        value = self.decode(output)
        self.check(value["ok"] and self.runtime.value is not None, "native runtime opened")

    def call(self, operation, **fields):
        data = json.dumps({"operation": operation, **fields}, ensure_ascii=False).encode("utf8")
        output = Buffer()
        code = self.dll.valmax_runtime_dispatch(self.runtime, data, len(data), C.byref(output))
        self.check(code == 0, "native dispatch ABI")
        value = self.decode(output)
        self.check(value["ok"], "native operation failed: " + str(value.get("error", "unknown")))
        return value["result"]

    def close(self):
        if self.runtime.value:
            self.dll.valmax_runtime_cancel_pending(self.runtime)
            self.dll.valmax_runtime_close(C.byref(self.runtime))
            self.check(self.runtime.value is None, "runtime closed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-proof", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    assert not args.report.exists(), "new proof required"
    verified = json.loads(args.runtime_proof.read_text("utf8"))
    assert verified["verified"] and not verified["actualMaxStarted"]
    dll = Path(verified["fixture"]) / "valmax_core.dll"
    assert dll.is_relative_to(Path("D:/ValMAXBuild/build"))
    for ancestor in dll.parents:
        ordinary(ancestor, directory=True)
    ordinary(dll)
    assert sha(dll) == verified["coreSha256"]
    paths = ["native/tests/launcher/run_upgrade_compatibility.py", "modkit/account_preferences.py", "modkit/ghost_store.py",
             "modkit/optimization_store.py", "modkit/quick_reply_store.py", "modkit/launch_mod.py", "modkit/config.defaults.json"]
    source_hashes = {name: sha(ROOT / name) for name in paths}
    fixture = Path("D:/ValMAXBuild/fixtures/native-upgrade-compatibility") / uuid.uuid4().hex
    for ancestor in fixture.parents:
        if ancestor.exists():
            ordinary(ancestor, directory=True)
    fixture.mkdir(parents=True)
    ordinary(fixture, directory=True)
    local, profile, runtime = fixture / "local", fixture / "local/MXMOD", fixture / "program/runtime/mod"
    profile.mkdir(parents=True); runtime.mkdir(parents=True)

    # These are authored test values, not a copy of any real MAX account or DB.
    config = make_config()
    config["upgradeSyntheticExtra"] = {"preserve": "Тестовая настройка 🌙", "count": 2}
    (profile / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=3) + "\n\n", encoding="utf8")
    db_path = profile / "synthetic-accounts.sqlite"
    with sqlite3.connect(db_path) as database:
        database.execute("create table own_fixture (id integer primary key, label text)")
        database.executemany("insert into own_fixture values (?, ?)", [(99101, "Синтетический A"), (99102, "Синтетический B")])
    sentinel = profile / "opaque-owned-account-state.bin"
    sentinel.write_bytes(b"synthetic-only-no-token\x00" * 17)
    immutable = {name: sha(profile / name) for name in ("config.json", db_path.name, sentinel.name)}

    accounts = AccountPreferencesStore(profile / "account-settings.json")
    rows = [account_defaults("99101"), account_defaults("99102")]
    rows[0].update(displayName="Продажи 🌙", color="#8b5cf6")
    rows[0]["workHours"].update(enabled=True, days=[5, 1, 3], start="08:15", end="19:30")
    rows[1].update(displayName="Поддержка", color="#0088ff")
    rows[1]["ghost"][next(iter(rows[1]["ghost"]))] = True
    accounts.replace(accounts.snapshot()["revision"], rows)
    ghost = GhostSettingsStore(profile / "ghost-settings.json")
    ghost_values = ghost.snapshot()["settings"]
    ghost_values = {key: index % 2 == 0 for index, key in enumerate(ghost_values)}
    ghost.replace(ghost.snapshot()["revision"], ghost_values)
    optimization = OptimizationSettingsStore(profile / "optimization-settings.json")
    flags = optimization.snapshot()["settings"]
    flags["photoDecodeResize"] = False
    optimization.replace(optimization.snapshot()["revision"], flags)
    replies = QuickReplyStore(profile / "quick-replies.json")
    replies.replace(replies.snapshot()["revision"], [
        {"id": "a" * 32, "command": "help", "title": "Общий ответ", "text": "Синтетический ответ"},
        {"id": "b" * 32, "command": "help", "accountId": "99102", "title": "Для поддержки", "text": "Другой ответ"},
    ])
    stores = {"accounts": (accounts, "accounts"), "ghost": (ghost, "settings"),
              "optimization": (optimization, "settings"), "quick_replies": (replies, "replies")}
    old_files = {p.name: sha(p) for p in profile.iterdir() if p.is_file()}
    native = Native(dll)
    try:
        native.prepare(local)
        native.check(old_files == {p.name: sha(p) for p in profile.iterdir() if p.is_file()}, "launcher preserves all existing profile bytes")
        native.open(profile, runtime)
        for family, (store, key) in stores.items():
            expected, actual = store.snapshot(), native.call(family + ".get")
            native.check(expected[key] == actual[key], family + " old values are preserved")
            native.check(expected["revision"] == actual["revision"], family + " revisions agree")
        native.check(old_files == {p.name: sha(p) for p in profile.iterdir() if p.is_file()}, "first native read never normalizes existing stores")
        changed = copy.deepcopy(native.call("accounts.get"))
        changed["accounts"][0]["displayName"] = "Продажи после обновления"
        result = native.call("accounts.set", revision=changed["revision"], accounts=changed["accounts"])
        native.check(accounts.snapshot() == result, "old reader understands native account write")
        for family, (store, key) in stores.items():
            if family == "accounts":
                continue
            value = copy.deepcopy(native.call(family + ".get"))
            if family == "quick_replies":
                value[key][0]["text"] = "Изменено нативным модулем"
            else:
                first = next(iter(value[key]))
                value[key][first] = not value[key][first]
            result = native.call(family + ".set", revision=value["revision"], **{key: value[key]})
            old = store.snapshot()
            native.check(old[key] == result[key] and old["revision"] == result["revision"], family + " old reader understands native write")
        native.close()
        native.open(profile, runtime)
        for family, (store, key) in stores.items():
            expected, actual = store.snapshot(), native.call(family + ".get")
            native.check(expected[key] == actual[key] and expected["revision"] == actual["revision"], family + " survives restart")
        native.check(native.call("optimization.get")["restartRequired"] is False, "desired settings applied on restart")
        native.check(immutable == {name: sha(profile / name) for name in immutable}, "config and opaque synthetic account files unchanged")
    finally:
        native.close()
    assert source_hashes == {name: sha(ROOT / name) for name in paths}, "sources changed during compatibility check"
    proof = {"schema": 1, "verified": True, "checks": native.checks, "fixture": str(fixture),
             "coreSha256": sha(dll), "runtimeProofSha256": sha(args.runtime_proof),
             "sourceHashes": source_hashes,
             "legacyFormatsRead": list(stores), "nativeWritesReadableByLegacy": True,
             "configAndOpaqueSyntheticAccountFilesPreserved": True,
             "realProfilesRead": False, "realCredentialsUsed": False, "actualMaxStarted": False,
             "productionTransferMutexUsedOnSyntheticRoot": True,
             "scope": "Existing config and old settings files consumed by the real native DLL without rewriting; bidirectional settings compatibility and restart persistence. Opaque fixture DB bytes preserved; actual account sign-in is not tested."}
    args.report.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"verified": True, "checks": native.checks, "realProfilesRead": False}))


if __name__ == "__main__":
    main()
