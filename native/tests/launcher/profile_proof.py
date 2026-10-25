"""Build-only proof for explicit config preparation and its typed C boundary."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / "native"
SOURCES = (
    "native/core/src/launcher_profile.rs", "native/core/src/launcher_ffi.rs",
    "native/core/src/launcher_config.rs", "native/core/src/settings_io.rs",
    "native/core/src/lib.rs", "native/core/valmax_core.h", "native/Cargo.lock",
    "native/tests/launcher/profile_proof.py",
)
def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
def main() -> None:
    before = {p: sha(ROOT / p) for p in SOURCES}
    command = ["cargo", "test", "--release", "--offline", "--locked", "--lib",
               "launcher_", "--", "--test-threads=1"]
    result = subprocess.run(command, cwd=NATIVE, capture_output=True, text=True,
                            encoding="utf-8", timeout=180,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    text = result.stdout + result.stderr
    log = Path("C:/ValMAXBuild/fixtures/launcher-profile-tests.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(text, encoding="utf-8")
    if result.returncode or "10 passed; 0 failed; 0 ignored" not in text:
        raise SystemExit("launcher profile tests failed; inspect owned test log")
    after = {p: sha(ROOT / p) for p in SOURCES}
    if after != before:
        raise SystemExit("source changed during tests")
    match = re.search(r"Running unittests .*?\(([^\r\n]+\.exe)\)", text)
    if not match:
        raise SystemExit("missing test executable identity")
    binary = Path(match[1])
    report = {
        "schema": 1, "ok": True,
        "scope": "explicit native launcher config preparation and typed host-only boundary",
        "sourceSha256": before, "command": command,
        "test": {"passed": 10, "failed": 0, "ignored": 0,
                 "binary": str(binary), "binarySha256": sha(binary),
                 "log": str(log), "logSha256": sha(log)},
        "productionMutexAcquiredByTests": False,
        "installedClientTouched": False,
        "runtimeProcessesStarted": False,
        "checks": ["missing check-only is read-only; full config created once",
                   "existing bytes and unknown fields preserved",
                   "unsafe, malformed and oversized config rejected",
                   "zero/malformed/directory/hardlinked/busy recovery marker blocks mutation",
                   "recursive and abandoned test mutex; cross-thread busy",
                   "typed ABI layout, bounded malformed input, finite state/error mapping"],
        "limits": ["C boundary successful preparation currently tested through injected unit callee; no C++ production integration yet",
                   "internal lease covers only this function's IO; outer transfer guard must remain on same OS thread through startup",
                   "PreparedConfig is a result, not a retained filesystem proof",
                   "non-cooperating writers can change config/marker after the final check; recheck readiness before spawn",
                   "check-only skips recovery-marker check and does not authorize launch",
                   "no process startup, MData restore, credentials, recovery mutation, or installer acceptance tested"]}
    target = ROOT / "analysis/native-migration-v1/launcher-profile-proof.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "passed": 10, "proof": str(target)}))
if __name__ == "__main__":
    main()
