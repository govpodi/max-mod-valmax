"""Reproducible activation proof; real Win32 only on an owned hidden window."""
from __future__ import annotations
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pefile

ROOT=Path(__file__).resolve().parents[2]
BUILD=Path("C:/ValMAXBuild/build/launcher")
def main():
    native={}
    for name in ("valmax_activation_test", "valmax_activation_metadata_test"):
        path=BUILD/(name+".exe")
        result=json.loads(subprocess.run([str(path)],capture_output=True,text=True,check=True,timeout=20).stdout)
        pe=pefile.PE(str(path))
        result.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            imports={item.dll.decode():sorted(symbol.name.decode() for symbol in item.imports if symbol.name)
                     for item in pe.DIRECTORY_ENTRY_IMPORT})
        forbidden={"ReadProcessMemory","WriteProcessMemory","GetWindowTextW","GetWindowTextA",
                   "SendMessageW","SendMessageA","AttachThreadInput","CreateProcessW"}
        if any(forbidden.intersection(names) for names in result["imports"].values()):
            raise AssertionError("Unexpected process/window-content capability")
        if not set(n.lower() for n in result["imports"]) <= {"kernel32.dll","user32.dll"}:
            raise AssertionError("Unexpected dependency")
        native[name]=result
    legacy=subprocess.run([sys.executable,"-m","unittest","test_client_activation","-q"],
        cwd=ROOT.parent/"modkit",capture_output=True,text=True,check=True,timeout=20)
    legacy_output=legacy.stderr.strip().splitlines()
    sources=[ROOT/"launcher/activation.h",ROOT/"launcher/activation.cpp",ROOT/"launcher/CMakeLists.txt",
        Path(__file__),Path(__file__).with_name("activation_test.cpp"),Path(__file__).with_name("activation_metadata_test.cpp")]
    library=BUILD/"valmax_activation.lib"
    report={"schema":1,"ok":True,"scope":"standalone C++ existing-client activation support",
        "library":{"path":str(library),"sha256":hashlib.sha256(library.read_bytes()).hexdigest(),"size":library.stat().st_size},
        "nativeTests":native,"legacyPythonTests":legacy_output,
        "sourceSha256":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "limits":{"windowsPerScan":4096,"scanAttempts":51,"overallRetryBudgetMs":2500,"showPollAttempts":16,"showRetryBudgetMs":750},
        "access":{"processRights":"PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE","foreignProcessMemory":False,
            "windowTitlesOrText":False,"commandLines":False,"processSpawnOrKill":False,"foregroundBypass":False},
        "realUserWindowsTouched":False,"installedClientModified":False,"actualClientActivationTested":False,
        "requirements":["The future launcher must independently verify exact package bytes and matching running-instance readiness before this helper",
            "Only one eligible Qt main window from the exact expected executable may be activated",
            "False never authorizes spawning another client","Foreground refusal does not negate a confirmed visible restored window"],
        "limitations":["The static MSVC CRT imports TerminateProcess for its own fatal-runtime handling; activation code never calls it or opens a process with termination rights",
            "Timing bounds constrain retries/sleeps; native Windows calls and scheduler latency do not provide a hard real-time deadline",
            "HWND identity checks reduce reuse races; Windows does not expose an atomic verify-and-ShowWindowAsync operation",
            "Production show/foreground actions were tested with a fake API; actual Win32 test only read the owned hidden fixture's metadata",
            "No user app has been launched, restored, closed, installed or modified"]}
    path=ROOT.parent/"analysis/native-migration-v1/launcher-activation-proof.json"
    path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"ok":True,"nativeChecks":sum(x["checks"] for x in native.values()),"librarySha256":report["library"]["sha256"],"proof":str(path)}))
if __name__=="__main__":main()
