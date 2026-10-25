"""Exercise the packaged standalone Qt helper in owned synthetic fixtures."""
from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("transfer_helper_job", Path(__file__).with_name("run_native_media_plugin.py"))
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)
sha, require = job.sha, job.require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-proof", required=True, type=Path)
    parser.add_argument("--package-proof", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--work-root", type=Path, choices=(Path("C:/ValMAXBuild"), Path("D:/ValMAXBuild")),
                        default=Path("C:/ValMAXBuild"))
    args = parser.parse_args()
    require(not args.report.exists(), "proof must be new")
    report_root = (ROOT / "analysis/native-migration-v1").resolve()
    require(args.report.resolve().parent == report_root, "report outside proof root")
    build = json.loads(args.build_proof.read_text("utf8"))
    package = json.loads(args.package_proof.read_text("utf8"))
    bundle = Path(package["package"])
    source_hashes = {str(path.relative_to(ROOT)).replace("\\", "/"): sha(path) for path in
                     (Path(__file__), Path(__file__).with_name("run_native_media_plugin.py"),
                      Path(__file__).with_name("run_webview_blank_native.py"))}
    for path, digest in build["sourceHashes"].items():
        require(sha(ROOT / path) == digest, "build source changed: " + path)
    for path, digest in build["artifacts"].items():
        require(sha(path) == digest, "build artifact changed: " + Path(path).name)
    for path, digest in package["fileHashes"].items():
        require(sha(bundle / path) == digest, "package changed: " + path)
    require(sha(bundle / "valmax-transfer.exe") == sha(Path(build["build"]) / "valmax-transfer.exe"),
            "package and build helper differ")
    require(sha(bundle / "valmax_core.dll") == build["coreSha256"], "package and build core differ")
    fixture = args.work_root / "build" / ("qt-transfer-helper-fixture-" + uuid.uuid4().hex)
    # Keep fixtures independent. Persistent hardlinks make the immutable release
    # tree writable through test aliases and violate the installer's nlink gate.
    shutil.copytree(bundle, fixture, copy_function=shutil.copy2)
    executable = fixture / "valmax_transfer_app_test.exe"
    shutil.copy2(Path(build["build"]) / executable.name, executable)
    for name in ("home", "local", "roaming", "temp", "screenshots"):
        (fixture / name).mkdir()

    # Python runs only in the build harness. The child loads Qt/C++/Rust and
    # synthetic ciphertext; no profile discovery or credential API is exercised.
    sys.path.insert(0, str(ROOT / "modkit"))
    import mdata_archive
    from session_archive import ArchivePolicy
    password = "synthetic-transfer-helper-password"
    accounts = {"42": {"first_name": "Alice", "last_name": "Example", "short_name": "AE"},
                "9223372036854775807": {"first_name": "Борис", "last_name": "Тестовый", "short_name": "БТ"}}
    auth = {"auth_" + key: json.dumps({**value, "private": "synthetic-private-not-projected"},
                                    ensure_ascii=False).encode() for key, value in accounts.items()}
    tokens = {"access_token_" + key + "@MXMOD": "synthetic-opaque-only-" + key for key in accounts}
    manifest = {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
                "credentials": [{"name": name, "blob": base64.b64encode(value.encode()).decode()}
                                for name, value in tokens.items()],
                "files": [{"path": name, "size": len(value)} for name, value in auth.items()]}
    policy = mdata_archive.MDataPolicy(ArchivePolicy(), lambda name: name in auth, lambda name: name in tokens)
    selected = fixture / "synthetic-selected.mdata"
    with selected.open("xb") as output:
        mdata_archive.encrypt_stream(manifest, password, lambda name: io.BytesIO(auth[name]), output,
                                    policy=policy, archive_kind="login")
    tampered = bytearray(selected.read_bytes())
    tampered[-1] ^= 1
    (fixture / "synthetic-tampered.mdata").write_bytes(tampered)
    (fixture / "synthetic-invalid.mdata").write_bytes(b"SYNTHETIC-NOT-ARCHIVE")

    job.c.windll.kernel32.SetErrorMode(0x8003)
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(fixture) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_QUICK_BACKEND="software", QT_DEBUG_PLUGINS="1",
               QML_DISABLE_DISK_CACHE="1", USERPROFILE=str(fixture / "home"),
               LOCALAPPDATA=str(fixture / "local"), APPDATA=str(fixture / "roaming"),
               TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    # Deliberately no SDK/Python PATH and no QML_IMPORT_PATH: relative qt.conf
    # and the deployed QML modules must suffice on their own.
    results = {}
    for mode in ("preview", "controller", "recovery"):
        results[mode] = job.run(fixture, executable, [mode, fixture], env, mode)
        modules = results[mode]["result"].get("loadedModules", {})
        require({"valmax_core.dll", "Qt6Core.dll", "Qt6Qml.dll", "Qt6Quick.dll", "qwindows.dll"} <= set(modules),
                "loaded dependency evidence missing")
        for name, path in modules.items():
            expected = fixture / "plugins/platforms/qwindows.dll" if name == "qwindows.dll" else fixture / name
            require(Path(path).resolve() == expected.resolve() and sha(path) == sha(expected),
                    "module loaded outside packaged fixture: " + name)
    for path in fixture.glob("*.log"):
        content = path.read_text("utf8", errors="replace")
        require(all(secret not in content for secret in
                    (password, "synthetic-private-not-projected", *tokens.values())), "synthetic secret echoed")
    for path, digest in build["sourceHashes"].items():
        require(sha(ROOT / path) == digest, "build source changed during test: " + path)
    for path, digest in source_hashes.items():
        require(sha(ROOT / path) == digest, "runner changed during test: " + path)
    for path, digest in package["fileHashes"].items():
        require(sha(bundle / path) == digest and sha(fixture / path) == digest, "bundle mutated during test: " + path)
    screenshots = {str(path): sha(path) for path in (fixture / "screenshots").glob("*.png")}
    require(screenshots, "UI render evidence missing")
    require(not any((fixture / "local").iterdir()), "synthetic helper wrote profile state")
    proof = {"schema": 1, "verified": True, "fixture": str(fixture),
             "buildProof": str(args.build_proof), "buildProofSha256": sha(args.build_proof),
             "packageProof": str(args.package_proof), "packageProofSha256": sha(args.package_proof),
             "sourceHashes": source_hashes, "results": results, "screenshots": screenshots,
             "coreSha256": build["coreSha256"], "sanitizedEnvironment": True, "syntheticOnly": True,
             "successfulProductionImportTested": False, "realCredentialsUsed": False,
             "successfulProductionRecoveryTested": False,
             "installedClientChanged": False,
             "scope": "Actual Qt/Rust authenticated preview and recovery status; injected import/recovery controller lifecycle and synthetic UI renders with packaged dependencies. No real profile import/recovery, MAX launch or server request."}
    with args.report.open("x", encoding="utf8") as output:
        output.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"verified": True, "checks": sum(value["result"]["checks"] for value in results.values()),
                      "report": str(args.report), "screenshots": list(screenshots)}))


if __name__ == "__main__":
    main()
