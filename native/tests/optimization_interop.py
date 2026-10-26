"""Python/native store interop in a new owned E:/ValMAXBuild/tmp directory."""
from __future__ import annotations
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "modkit"))
from optimization_store import OptimizationSettingsError, OptimizationSettingsStore, defaults, canonical, parse
from quick_reply_store import _writer_lock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver", type=pathlib.Path, default=pathlib.Path("E:/ValMAXBuild/target/debug/examples/optimization_probe.exe"))
    parser.add_argument("--scratch-root", type=pathlib.Path, default=pathlib.Path("E:/ValMAXBuild/tmp"))
    args = parser.parse_args()
    base = args.scratch_root
    base.mkdir(parents=True, exist_ok=True)
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="optimization-interop-Ёж-İ-", dir=base))
    checks = []

    def call(actions, root=scratch):
        result = subprocess.run([str(args.driver)], input=json.dumps({"root": str(root), "actions": actions}, ensure_ascii=False).encode(), capture_output=True, timeout=15, check=True)
        assert not result.stderr, result.stderr.decode(errors="replace")
        return json.loads(result.stdout)

    path = scratch / "prefs.json"
    python = OptimizationSettingsStore(path)
    original = python.snapshot()
    assert call([{"operation": "snapshot"}])[0]["value"] == original
    assert not path.exists()
    checks.append("missing_default_snapshot_revision_matches_python_without_write")
    modified = {**defaults(), "enabled": False, "photoDecodeResize": False}
    saved = call([{"operation": "replace", "revision": original["revision"], "settings": modified}])[0]
    assert saved["ok"] and saved["value"] == python.snapshot()
    assert path.read_bytes() == canonical(modified)
    checks.append("rust_write_python_read_byte_exact_canonical_and_sha256")
    restored = python.replace(saved["value"]["revision"], defaults())
    assert call([{"operation": "snapshot"}])[0]["value"] == restored
    checks.append("python_write_rust_read_snapshot")
    actions = [{"operation": "activate", "existingClient": False},
               {"operation": "replace", "revision": restored["revision"], "settings": modified},
               {"operation": "activate", "existingClient": False}, {"operation": "snapshot"}]
    output = call(actions)
    assert output[0]["value"] == defaults() and output[2]["value"] == defaults()
    assert output[3]["value"]["restartRequired"] is True
    assert parse((scratch / "runtime/optimization-active.json").read_bytes()) == defaults()
    checks.append("active_fixed_once_pending_flags_do_not_reconfigure_live_client")
    existing = call([{"operation": "activate", "existingClient": True}, {"operation": "snapshot"}])
    assert existing[0]["value"] == defaults() and existing[1]["value"]["restartRequired"] is True
    next_launch = call([{"operation": "activate", "existingClient": False}, {"operation": "snapshot"}])
    assert next_launch[0]["value"] == modified and next_launch[1]["value"]["restartRequired"] is False
    checks.append("existing_client_adopts_active_new_launch_applies_desired")

    upgrade_root = pathlib.Path(tempfile.mkdtemp(prefix="optimization-interop-upgrade-", dir=base))
    upgrade_store = OptimizationSettingsStore(upgrade_root / "prefs.json")
    upgrade_store.replace(upgrade_store.snapshot()["revision"], modified)
    upgrade_active = upgrade_root / "runtime" / "optimization-active.json"
    assert not upgrade_active.exists()
    resumed = call([{"operation": "activate", "existingClient": True}, {"operation": "snapshot"}], root=upgrade_root)
    assert resumed[0]["value"] == modified and resumed[1]["value"]["restartRequired"] is False
    assert upgrade_active.read_bytes() == canonical(modified)
    checks.append("rust_existing_client_recovers_missing_active_from_python_saved_disabled_flags")
    upgrade_active.unlink()
    python_resumed = OptimizationSettingsStore(upgrade_root / "prefs.json")
    assert python_resumed.activate(upgrade_root / "runtime", existing_client=True) == modified
    assert upgrade_active.read_bytes() == canonical(modified)
    checks.append("python_existing_client_recovers_missing_active_from_same_saved_flags")
    malformed_active = b'{"schema":1,"settings":{}}'
    upgrade_active.write_bytes(malformed_active)
    assert call([{"operation": "activate", "existingClient": True}], root=upgrade_root) == [{"ok": False, "error": "invalid_store"}]
    try:
        OptimizationSettingsStore(upgrade_root / "prefs.json").activate(upgrade_root / "runtime", existing_client=True)
    except OptimizationSettingsError as error:
        assert error.code == "invalid_store"
    else:
        raise AssertionError("Python accepted a malformed active file")
    assert upgrade_active.read_bytes() == malformed_active
    checks.append("missing_active_recovery_preserves_malformed_present_file")
    original = python.snapshot()
    with _writer_lock(path):
        started = time.monotonic()
        result = call([{"operation": "replace", "revision": original["revision"], "settings": defaults()}])
        elapsed = time.monotonic() - started
    assert result == [{"ok": False, "error": "store_busy"}] and elapsed >= 1.8
    assert python.snapshot() == original
    checks.append("python_mutex_excludes_rust_writer_with_unicode_normcase_path")
    path.write_bytes(b'{"schema":1,"settings":{},"settings":{}}')
    before = path.read_bytes()
    results = call([{"operation": "snapshot"}, {"operation": "replace", "revision": original["revision"], "settings": defaults()}])
    assert results == [{"ok": False, "error": "invalid_store"}] * 2 and path.read_bytes() == before
    checks.append("malformed_duplicate_store_preserved_not_treated_as_missing")

    # PowerShell creates a directory junction only between this test's own
    # directories. No elevated symlink privileges, system/profile files involved.
    outside = scratch / "outside"
    outside.mkdir()
    (outside / "optimization-active.json").write_bytes(canonical(defaults()))
    junction_root = pathlib.Path(tempfile.mkdtemp(prefix="optimization-interop-junction-", dir=base))
    script = scratch / "make-junction.ps1"
    script.write_text("param([string]$LinkPath,[string]$Destination)\nNew-Item -ItemType Junction -Path $LinkPath -Target $Destination -ErrorAction Stop | Out-Null\n", encoding="utf-8")
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-LinkPath", str(junction_root / "runtime"), "-Destination", str(outside)], check=True, capture_output=True)
    result = call([{"operation": "activate", "existingClient": True}], root=junction_root)
    assert result == [{"ok": False, "error": "unsafe_path"}]
    assert (outside / "optimization-active.json").read_bytes() == canonical(defaults())
    checks.append("junction_ancestor_rejected_and_target_preserved")

    proof = {"schema": 1, "passed": len(checks), "checks": checks,
             "driverSha256": hashlib.sha256(args.driver.read_bytes()).hexdigest(),
             "pythonOracleSha256": hashlib.sha256((ROOT / "modkit/optimization_store.py").read_bytes()).hexdigest(),
             "nativeSources": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT / "native/core/src/settings_io.rs", ROOT / "native/core/src/optimization_store.rs"]},
             "realProfilesAccessed": False, "networkUsedByTests": False,
             "scratch": str(scratch), "upgradeScratch": str(upgrade_root),
             "scope": "Native Windows filesystem, mutex, desired/active/revision interoperability; no client UI or installed runtime integration"}
    (ROOT / "analysis/native-migration-v1/optimization-interop.json").write_text(json.dumps(proof, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(checks), "proof": "analysis/native-migration-v1/optimization-interop.json"}))


if __name__ == "__main__":
    main()
