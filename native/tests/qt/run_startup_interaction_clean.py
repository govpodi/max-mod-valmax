"""Run the generated-window QML fixture with isolated user and Qt settings.

This checks UI behavior using the test interpreter's PySide6. It never starts
MAX, opens a real profile, or claims native background readiness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
TEST = Path(__file__).with_name("test_startup_interaction_ui.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--qpa-platform", choices=("offscreen", "minimal"), default="offscreen")
    args = parser.parse_args()
    if not args.source.is_file():
        raise RuntimeError("Generated MeWindow source is unavailable")
    if args.report.parent.resolve() != (ROOT / "analysis/native-migration-v1").resolve() or args.report.exists():
        raise RuntimeError("A new proof path under analysis/native-migration-v1 is required")

    build_root = Path("C:/ValMAXBuild/build") if os.name == "nt" else Path(tempfile.gettempdir())
    fixture = Path(tempfile.mkdtemp(prefix="startup-ui-clean-", dir=build_root))
    for name in ("home", "local", "roaming", "temp", "cache", "config", "data"):
        (fixture / name).mkdir()
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT"}}
        env.update(PATH=str(Path(sys.executable).parent) + os.pathsep + str(system_root / "System32"),
                   USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"),
                   APPDATA=str(fixture / "roaming"), TEMP=str(fixture / "temp"),
                   TMP=str(fixture / "temp"))
    else:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(fixture / "home"),
               "TMPDIR": str(fixture / "temp"), "XDG_CACHE_HOME": str(fixture / "cache"),
               "XDG_CONFIG_HOME": str(fixture / "config"), "XDG_DATA_HOME": str(fixture / "data")}
    env.update(QT_QPA_PLATFORM=args.qpa_platform, QT_QUICK_BACKEND="software",
               QML_DISABLE_DISK_CACHE="1", PYTHONNOUSERSITE="1")
    child_report = fixture / "ui-result.json"
    run = subprocess.run([sys.executable, str(TEST), "--source", str(args.source),
                          "--report", str(child_report)], cwd=fixture, env=env,
                         capture_output=True, text=True, timeout=30,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if run.returncode:
        raise RuntimeError(f"Isolated startup QML fixture failed (exit {run.returncode}): {run.stderr[-1500:]}")
    result = json.loads(child_report.read_text("utf8"))
    if result.get("verified") is not True or result.get("nativeBackgroundStartupProven") is not False:
        raise RuntimeError("QML fixture result is incomplete")
    proof = {
        **result, "cleanEnvironment": True, "isolatedUserDirectories": True,
        "platform": sys.platform, "qpaPlatform": args.qpa_platform, "fixture": str(fixture),
        "sourceSha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "testSha256": hashlib.sha256(TEST.read_bytes()).hexdigest(),
        "runnerSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Generated native window QML in a clean user environment; PySide6 test interpreter, no native MAX process",
    }
    args.report.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", "utf8")
    print(json.dumps({"verified": True, "checks": proof["checks"], "report": str(args.report)}))


if __name__ == "__main__":
    main()
