"""Build and exercise the ordinary C++ launcher against only owned fake payloads.

Python is a build/test tool. Neither produced ValMAX.exe nor its child uses it.
The GUI entrypoint is compiled with a synthetic inventory but is NOT executed:
its KnownFolder lookup deliberately has no test override for real profiles.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid
import pefile

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / "native"
STOCK_SOURCE_ROOTS = (Path("D:/ValMAXBuild/build"), Path("C:/ValMAXBuild/build"))

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def run(args, build):
    result = subprocess.run([str(arg) for arg in args], cwd=ROOT, capture_output=True,
        encoding="utf8", errors="strict", timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    with (build / "proof-build.log").open("a", encoding="utf8") as stream:
        stream.write(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("Launcher fixture failed; inspect proof-build.log")
    return result.stdout

def assert_owned_directory(path, parent):
    path = path.absolute()
    if not path.is_relative_to(parent) or path == parent:
        raise ValueError("explicit owned output directory required")
    import stat
    for candidate in (path, *path.parents):
        if candidate.exists() and candidate.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("reparse output path")
    return path


def stock_icon_source(path, expected):
    if (not path.is_absolute() or path.name != 'max.exe' or path.parent.name != 'mod'
            or path.parent.parent.name != 'runtime'
            or not any(path.is_relative_to(root) for root in STOCK_SOURCE_ROOTS)):
        raise ValueError('explicit pinned owned runtime/mod/max.exe required')
    if len(expected) != 64 or any(character not in '0123456789abcdef' for character in expected):
        raise ValueError('explicit lowercase stock SHA-256 required')
    for candidate in (path, *path.parents):
        if candidate.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('reparse stock icon path')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or digest(path) != expected:
        raise ValueError('stock icon source is not the pinned ordinary file')
    return path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--proof", required=True, type=Path)
    parser.add_argument('--stock-icon-source', required=True, type=Path,
                        help='Pinned FINAL runtime/mod/max.exe; used only as compile-time icon data, never executed')
    parser.add_argument('--stock-icon-sha256', required=True,
                        help='Expected hash from the matching FINAL launcher pins receipt')
    args = parser.parse_args()
    build = assert_owned_directory(args.build, Path("D:/ValMAXBuild/build"))
    stock = stock_icon_source(args.stock_icon_source, args.stock_icon_sha256)
    if args.proof.exists():
        raise ValueError("proof already exists")
    build.mkdir(parents=True, exist_ok=True)
    sources = sorted([*(NATIVE / "launcher").glob("*.cpp"), *(NATIVE / "launcher").glob("*.h"),
        *(NATIVE / "launcher").glob("*.cmake"), NATIVE / "launcher/CMakeLists.txt",
        *(NATIVE / "launcher").glob("*.py"), NATIVE / "tooling/build_launcher.py", NATIVE / "core/valmax_core.h",
        NATIVE / "transfer/closed_clients.cpp", NATIVE / "transfer/closed_clients.h", NATIVE / "transfer/closed_clients.cmake",
        NATIVE / "common/startup_context.cpp", NATIVE / "common/startup_context.h", NATIVE / "common/startup_context.cmake",
        *(NATIVE / "tests/launcher").glob("*launch*.cpp"), Path(__file__)])
    source_hashes = {p.relative_to(ROOT).as_posix(): digest(p) for p in sources}
    run(["cmake", "-S", NATIVE / "launcher", "-B", build, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release"], build)
    run(["cmake", "--build", build, "--target", "valmax_launch_session_test", "valmax_launcher_owned_test",
        "valmax_launcher_child", "valmax_launcher_test_core", "--parallel", "1"], build)
    policy = json.loads(run([build / "valmax_launch_session_test.exe"], build))
    fixture = Path("D:/ValMAXBuild/fixtures/native-launcher") / uuid.uuid4().hex
    actual = json.loads(run([build / "valmax_launcher_owned_test.exe", fixture,
        build / "max.exe", build / "valmax_core.dll"], build))
    assert policy["ok"] and actual["ok"] and actual["ownChildOnly"]
    assert not actual["realProfilesTouched"] and not actual["realCredentialsTouched"]
    spec = importlib.util.spec_from_file_location("launcher_pins_generator", NATIVE / "launcher/launcher_generate_pins.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    runtime = build / ("synthetic-" + uuid.uuid4().hex) / "runtime"
    # Compile the exact GUI entrypoint and required complete inventory. Only the
    # compile-only mod/max.exe is stock icon data; executable Win32 tests above
    # use their separate fake child/service tree. No stock PE is executed.
    # All DLLs except fake core contain inert text, so this cannot be distributed.
    for name in sorted(generator.REQUIRED):
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == 'mod/max.exe':
            shutil.copyfile(stock, target)
            assert digest(target) == args.stock_icon_sha256
        elif name.endswith("valmax_core.dll"):
            shutil.copyfile(build / "valmax_core.dll", target)
        elif name.endswith(".exe"):
            shutil.copyfile(build / "max.exe", target)
        else:
            target.write_bytes(b"owned compile-only fixture\n")
    generator_checks = 0
    original = generator.inventory(runtime)
    assert len(original) == len(generator.REQUIRED)
    generator_checks += 1
    forbidden = runtime / "mod/not-distributable.py"
    forbidden.write_bytes(b"owned development artifact\n")
    try:
        generator.inventory(runtime)
        raise AssertionError("Python payload accepted")
    except ValueError as error:
        assert "development/private" in str(error)
        generator_checks += 1
    # Only this fixed, newly-created own file is unlinked; no recursive cleanup.
    forbidden.unlink()
    mutable = runtime / generator.MUTABLE
    mutable.write_bytes(b"{}")
    assert generator.inventory(runtime) == original
    generator_checks += 1
    mutable.write_bytes(b"x" * 4097)
    try:
        generator.inventory(runtime)
        raise AssertionError("Oversized mutable accepted")
    except ValueError as error:
        assert "oversized mutable" in str(error)
        generator_checks += 1
    mutable.write_bytes(b"{}")
    run(["cmake", "-S", NATIVE / "launcher", "-B", build,
        f"-DVALMAX_LAUNCHER_RUNTIME_ROOT={runtime}", f"-DVALMAX_BUILD_PYTHON={sys.executable}"], build)
    run(["cmake", "--build", build, "--target", "ValMAX", "--parallel", "1"], build)
    executable = build / "ValMAX.exe"
    image = pefile.PE(str(executable), fast_load=False)
    assert image.FILE_HEADER.Machine == 0x8664 and image.OPTIONAL_HEADER.Subsystem == 2
    imports = sorted(row.dll.decode("ascii").lower() for row in image.DIRECTORY_ENTRY_IMPORT)
    assert not any(any(word in name for word in ("python", "frida", "winhttp", "wininet")) for name in imports)
    image.close()
    icon_spec = importlib.util.spec_from_file_location('launcher_icon_verify', NATIVE / 'launcher/launcher_icon.py')
    icon_generator = importlib.util.module_from_spec(icon_spec)
    icon_spec.loader.exec_module(icon_generator)
    icon_receipt = build / 'stock-icon/launcher-icon.json'
    icon_verification = icon_generator.verify(runtime / 'mod/max.exe', executable, icon_receipt)
    assert digest(stock) == args.stock_icon_sha256, 'Pinned stock icon source changed during proof'
    assert source_hashes == {p.relative_to(ROOT).as_posix(): digest(p) for p in sources}, "Source changed during proof"
    args.proof.parent.mkdir(parents=True, exist_ok=True)
    result = {"schema": 1, "ok": True, "buildDirectory": str(build), "fixtureDirectory": str(fixture),
        "policy": policy, "ownedWin32": actual, "inventoryChecks": generator_checks,
        "guiEntrypointCompiled": True, "guiEntrypointExecuted": False,
        "syntheticPayloadOnly": True, "distributionReady": False, "protectionReadinessTested": False,
        'stockIconSource': {'path': str(stock), 'sha256': args.stock_icon_sha256,
                            'ordinaryFileVerified': True, 'executed': False, 'compileOnly': True},
        'iconReceipt': {'path': str(icon_receipt), 'sha256': digest(icon_receipt)},
        'iconVerification': icon_verification,
        "executable": {"path": str(executable), "sha256": digest(executable), "imports": imports},
        "pinReceipt": {"path": str(build / "launcher-pins.json"), "sha256": digest(build / "launcher-pins.json")},
        "sources": source_hashes}
    args.proof.write_text(json.dumps(result, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"ok": True, "policyChecks": policy["checks"], "ownedWin32Checks": actual["checks"],
        "inventoryChecks": generator_checks, "guiEntrypointCompiled": True, "distributionReady": False}))

if __name__ == "__main__":
    main()
