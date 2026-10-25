"""Build/run only the no-args System32 API-set probe, never a client module."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
ANALYSIS = ROOT / "analysis/native-migration-v1"
BUILD = Path("D:/ValMAXBuild/build/native-system-contract-loader-20261021")
REPORT = ANALYSIS / "native-system-contract-loader-proof-20261021.json"
SYSTEM = Path("C:/Windows/System32")
RAW = ANALYSIS / "native-profile-guard-bundled-symbols-20261021.json"
RAW_SHA = "6b73853637f83c75aa637abd23b593baa36a6574dba82d29cf44d01e6768eeac"
STATIC = ANALYSIS / "native-profile-guard-system-forwarder-candidates-20261021.json"
STATIC_SHA = "a3fa4d708e41929bb70bbef99588d528ee92a65fa67f28cd899d20ebb9011339"
PINNED_SYSTEM = {
    "kernelbase.dll": "becad014fb8efa8cb5e314931cca92778ad42c649b12a6909632cacd68af4f40",
    "ntdll.dll": "a74f7482085eab125ccc09152ab7e0b5994bcb13e1a7b29880bdbb24179ecb8b",
    "kernel32.dll": "8f8629741558b5df71def4e1dd8ef9645d4f806b949762c47757c50fea1a9e6d",
}
EXPECTED = {
    ("api-ms-win-core-synch-l1-2-0.dll", "WaitOnAddress"): "kernelbase.dll",
    ("api-ms-win-core-synch-l1-2-0.dll", "WakeByAddressAll"): "ntdll.dll",
    ("api-ms-win-core-synch-l1-2-0.dll", "WakeByAddressSingle"): "ntdll.dll",
    ("api-ms-win-core-handle-l1-1-0.dll", "CompareObjectHandles"): "kernelbase.dll",
}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ordinary(path):
    for candidate in (path, *path.parents):
        require(not candidate.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                "reparse source/output")
    require(stat.S_ISREG(path.lstat().st_mode), "ordinary file required")


def main():
    require(len(sys.argv) == 1, "no runtime path/symbol/policy arguments accepted")
    require(not BUILD.exists() and not REPORT.exists(), "new owned build/report required")
    for ancestor in BUILD.parents:
        if ancestor.exists():
            require(not ancestor.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                    "reparse build ancestor")
    require(sha(RAW) == RAW_SHA and sha(STATIC) == STATIC_SHA, "static evidence changed")
    parser_source = ROOT / "native/tooling/audit_payload_dependencies.py"
    spec = importlib.util.spec_from_file_location("system_probe_existing_audit", parser_source)
    existing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(existing)
    audit = existing.Audit()
    system_inputs = {}
    for name, expected in PINNED_SYSTEM.items():
        path = SYSTEM / name
        ordinary(path)
        require(sha(path) == expected, "pinned system file changed: " + name)
        ident = audit.add(path, "system64", expected=expected)
        require(audit.nodes[ident]["machine"] == "0x8664", "wrong system architecture")
        system_inputs[name] = audit.nodes[ident]
    base = audit.by_source[str(SYSTEM / "kernelbase.dll").lower()]
    require(audit.exports[base]["CompareObjectHandles"] is None
            and audit.exports[base]["WaitOnAddress"] is None
            and audit.exports[base]["WakeByAddressAll"] == "NTDLL.RtlWakeAddressAll"
            and audit.exports[base]["WakeByAddressSingle"] == "NTDLL.RtlWakeAddressSingle",
            "expected provider map differs from pinned static forwarders")
    source = ROOT / "native/tests/launcher/system_contract_probe.cpp"
    source_paths = [source, Path(__file__), parser_source, Path(existing.pefile.__file__),
                    ROOT / "native/tooling/native_env.ps1", ROOT / "native/tooling/msvc_env.ps1"]
    sources_before = {str(path): sha(path) for path in source_paths}
    compiler = Path(shutil.which("cl.exe") or "")
    require(compiler.is_file(), "invoke inside native_env.ps1")
    BUILD.mkdir()
    executable = BUILD / "system_contract_probe.exe"
    command = [str(compiler), "/nologo", "/std:c++20", "/O2", "/MT", "/W4", "/WX", "/EHsc",
               "/guard:cf", "/DUNICODE", "/D_UNICODE", str(source),
               "/Fe:" + str(executable), "/Fo:" + str(BUILD / "system_contract_probe.obj"),
               "/link", "/DYNAMICBASE", "/NXCOMPAT", "/GUARD:CF", "/SUBSYSTEM:CONSOLE"]
    environment = os.environ.copy()
    environment.update(TEMP="D:/ValMAXBuild/tmp", TMP="D:/ValMAXBuild/tmp")
    compile_result = subprocess.run(command, cwd=BUILD, env=environment, capture_output=True,
                                    timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    (BUILD / "compile.log").write_bytes(compile_result.stdout + compile_result.stderr)
    require(compile_result.returncode == 0, "system probe compile failed; inspect owned compile.log")
    ordinary(executable)
    require(executable.stat().st_nlink == 1, "hardlinked probe")
    image = audit.add(executable, "owned-probe")
    require(audit.nodes[image]["machine"] == "0x8664", "probe must be native x64")
    imports = audit.nodes[image]["imports"]
    require(not imports["delay"] and {row["name"] for row in imports["normal"]} == {"kernel32.dll"},
            "probe has non-Kernel32/static-CRT imports")
    executable_sha = sha(executable)
    # Execute only this newly built no-args own process. It cannot spawn children
    # or invoke the four queried functions. No payload DLL is copied here.
    result = subprocess.run([str(executable)], cwd=BUILD, env=environment, capture_output=True,
                            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    (BUILD / "stdout.json").write_bytes(result.stdout)
    (BUILD / "stderr.log").write_bytes(result.stderr)
    require(result.returncode == 0 and not result.stderr, "system probe failed; inspect owned output")
    value = json.loads(result.stdout.decode("ascii"))
    require(value["ok"] is True and value["checks"] == 4 and value["system32SearchOnly"] is True
            and value["resolvedFunctionsCalled"] is False, "probe policy/result mismatch")
    observed = {}
    for row in value["results"]:
        key = row["contract"], row["symbol"]
        require(key in EXPECTED and key not in observed, "unexpected/duplicate probe lookup")
        expected_provider = EXPECTED[key]
        provider = Path(row["providerPath"])
        require(row["expectedProvider"] == expected_provider and row["providerMatchesExpected"] is True
                and row["error"] == 0 and provider == SYSTEM / expected_provider,
                "provider is not the pinned System32 image")
        ordinary(provider)
        require(sha(provider) == PINNED_SYSTEM[expected_provider], "actual provider disk identity changed")
        observed[key] = row
    require(set(observed) == set(EXPECTED), "incomplete system contract checks")
    require(all(sha(SYSTEM / name) == expected for name, expected in PINNED_SYSTEM.items()), "system inputs changed")
    require(sha(executable) == executable_sha and sources_before == {str(path): sha(path) for path in source_paths},
            "probe/source changed")
    require(sha(RAW) == RAW_SHA and sha(STATIC) == STATIC_SHA, "old static evidence changed")
    proof = {"schema": 1, "verified": True, "build": str(BUILD), "checks": 4,
             "scope": "Own no-args x64 /MT process: two fixed API-set contracts, GetProcAddress for four fixed names, provider metadata only. Functions are not called. Runtime System32-only contract resolution on this host, not payload/MAX or clean-Windows proof.",
             "staticInputs": {"raw": {"path": str(RAW), "sha256": RAW_SHA},
                              "apiCandidates": {"path": str(STATIC), "sha256": STATIC_SHA}},
             "oldRawVerifiedFalsePreserved": True, "oldStaticReceiptsUnchanged": True,
             "executable": {"path": str(executable), "sha256": executable_sha,
                            "machine": audit.nodes[image]["machine"], "imports": imports},
             "compiler": {"path": str(compiler), "sha256": sha(compiler)}, "compileCommand": command,
             "compileLog": {"path": str(BUILD / "compile.log"), "sha256": sha(BUILD / "compile.log")},
             "run": {"arguments": [], "timeoutSeconds": 10, "exitCode": result.returncode,
                     "stdoutSha256": sha(BUILD / "stdout.json"), "stderrSha256": sha(BUILD / "stderr.log")},
             "result": value, "pinnedSystemFiles": system_inputs, "sourceHashes": sources_before,
             "sourceAndSystemHashesBeforeAfterMatch": True, "systemContractResolutionVerified": True,
             "resolvedFunctionsCalled": False, "clientOrPayloadDllLoaded": False,
             "actualMaxStarted": False, "realProfilesOrCredentialsAccessed": False,
             "knownFolderUsed": False, "networkUsed": False, "hooksUsed": False,
             "parentOrTokenChanges": False, "cleanWindowsTested": False,
             "arbitraryDynamicImportsVerified": False, "installedClientTested": False,
             "releaseEligible": False,
             "limits": ["Only this host and this System32-only own-process load route are confirmed.",
                        "No payload import-resolution or complete installed-client claim.",
                        "Static raw scan stays verified=false; broad system/dynamic dependencies remain outside this probe."]}
    with REPORT.open("x", encoding="utf8") as output:
        json.dump(proof, output, indent=2)
        output.write("\n")
    print(json.dumps({"report": str(REPORT), "sha256": sha(REPORT), "verified": True,
                      "checks": 4, "functionsCalled": False, "cleanWindowsTested": False}))


if __name__ == "__main__":
    main()
