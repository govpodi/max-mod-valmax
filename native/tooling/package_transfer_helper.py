"""Build-time packaging of the standalone native transfer helper; never run it.

The package is a new, isolated ValMAXBuild directory on C: or D:. Tools/absolute source
paths stay in the external receipt; qt.conf and the runtime manifest are relative.
The dependency model checks direct AND delay imports, not arbitrary LoadLibrary
calls, symbol versions, clean-machine execution or complete licensing compliance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import uuid

import pefile

ROOT = Path(__file__).resolve().parents[2]
BUILD_ROOT = Path("C:/ValMAXBuild")
WORK_ROOTS = (BUILD_ROOT, Path("D:/ValMAXBuild"))
PACKAGE_ROOT = BUILD_ROOT / "packages"
QT = BUILD_ROOT / "Qt"
CRT = BUILD_ROOT / "msvc/VC/Redist/MSVC/14.44.35112/x64/Microsoft.VC143.CRT"
RUST_NOTICES = BUILD_ROOT / "package-notices/rust-20261016-b"
APP = ROOT / "native/transfer/app"
REPORT_ROOT = ROOT / "analysis/native-migration-v1"
SYSTEM = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
MAX_FILES, MAX_BYTES, MAX_PE_BYTES, MAX_IMPORTS = 8192, 512 << 20, 128 << 20, 4096
QT_CONF = b"[Paths]\nPrefix=.\nLibraries=.\nPlugins=plugins\nQmlImports=qml\nTranslations=translations\n"
SYSTEM_NAMES = set("""advapi32 authz bcrypt bcryptprimitives cabinet cfgmgr32 clbcatq combase comctl32 comdlg32
crypt32 cryptbase cryptsp d2d1 d3d9 d3d11 d3d12 d3dcompiler_47 dcomp dbghelp dnsapi dwmapi dwrite dxcore dxgi
gdi32 hid icuuc imagehlp imm32 iphlpapi kernel32 kernelbase mpr msimg32 netapi32 normaliz ntdll ole32 oleaut32
opengl32 powrprof profapi propsys psapi rpcrt4 runtimeobject secur32 setupapi shcore shell32 shlwapi sspicli
ucrtbase uiautomationcore urlmon user32 userenv usp10 uxtheme version windowscodecs winhttp wininet winmm
winspool wintrust wldap32 ws2_32 wtsapi32""".split())
_API_CONTRACTS = None


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def ordinary(path, directory=False):
    info = Path(path).lstat()
    require(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, "reparse input or package entry")
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "wrong entry type")
    return info


def below(path, root, label):
    path, root = Path(path), Path(root)
    require(path.is_absolute(), label + " must be absolute")
    require(all(part not in (".", "..") and ":" not in part and not part.endswith((".", " "))
                for part in path.parts[1:]), label + " has ambiguous components")
    resolved, allowed = path.resolve(), root.resolve()
    require(resolved != allowed and resolved.is_relative_to(allowed), label + " escapes permitted directory")
    for parent in (path, *path.parents):
        if parent.exists():
            require(not parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, "reparse ancestor")
    return resolved


def owned_build(path, label):
    for root in WORK_ROOTS:
        if path.is_absolute() and path.resolve().is_relative_to((root / "build").resolve()):
            return below(path, root / "build", label)
    raise ValueError(label + " escapes owned build roots")


def files(directory):
    ordinary(directory, True)
    result, total = [], 0
    for path in sorted(Path(directory).rglob("*")):
        info = path.lstat()
        require(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, "reparse package/source entry")
        if path.is_dir():
            continue
        ordinary(path)
        result.append(path)
        total += info.st_size
        require(len(result) <= MAX_FILES and total <= MAX_BYTES, "package inventory bound exceeded")
    return result


def pe_imports(path):
    """Same explicit-directory parsing policy as audit_payload_dependencies.py.

    No target loading. Unlike the full audit, this narrow packager does not
    inspect exports or claim successful imported-symbol resolution.
    """
    require(ordinary(path).st_size <= MAX_PE_BYTES, "PE size bound exceeded")
    pe = pefile.PE(str(path), fast_load=True)
    try:
        require(pe.FILE_HEADER.Machine == 0x8664, "non-x64 package PE")
        pe.parse_data_directories(directories=[1, 13])
        result = []
        for kind, attribute, index in (("direct", "DIRECTORY_ENTRY_IMPORT", 1), ("delay", "DIRECTORY_ENTRY_DELAY_IMPORT", 13)):
            entries = getattr(pe, attribute, [])
            directory = pe.OPTIONAL_HEADER.DATA_DIRECTORY[index]
            require(not (directory.VirtualAddress and directory.Size and not entries), "unparsed PE import directory")
            for entry in entries:
                name = entry.dll.decode("ascii").lower()
                require(re.fullmatch(r"[a-z0-9_.-]+\.(dll|drv)", name), "invalid import name")
                require(len(entry.imports) <= 65536, "import symbol bound exceeded")
                result.append({"kind": kind, "name": name})
                require(len(result) <= MAX_IMPORTS, "import descriptor bound exceeded")
        return sorted(result, key=lambda value: (value["kind"], value["name"]))
    finally:
        pe.close()


def api_contracts():
    """Bounded read of the same schema used by the existing payload auditor."""
    global _API_CONTRACTS
    if _API_CONTRACTS is not None:
        return _API_CONTRACTS
    pe = pefile.PE(str(SYSTEM / "apisetschema.dll"), fast_load=True)
    try:
        sections = [section for section in pe.sections if section.Name.rstrip(b"\0") == b".apiset"]
        require(len(sections) == 1, "missing API-set schema")
        data = sections[0].get_data()
    finally:
        pe.close()
    require(28 <= len(data) <= 4 << 20, "API-set size bound")
    version, size, _, count, entries, _, _ = struct.unpack_from("<7I", data)
    require(version == 6 and size <= len(data) and count <= 8192 and entries + count * 24 <= size,
            "unsupported API-set schema")
    def text(offset, length):
        require(length % 2 == 0 and offset + length <= size, "invalid API-set string")
        return data[offset:offset + length].decode("utf-16-le").lower()
    exact, prefixes = set(), set()
    for index in range(count):
        _, offset, length, hashed, values, total = struct.unpack_from("<6I", data, entries + index * 24)
        require(hashed <= length and total <= 128 and values + total * 20 <= size, "invalid API-set entry")
        hosts = []
        for value in range(total):
            _, _, _, target, target_size = struct.unpack_from("<5I", data, values + value * 20)
            host = text(target, target_size)
            if host:
                require(re.fullmatch(r"[a-z0-9_.-]+\.(dll|drv|exe|sys)", host), "invalid API-set host")
                hosts.append(host)
        if hosts and all((SYSTEM / host).is_file() for host in hosts):
            exact.add(text(offset, length) + ".dll")
            prefixes.add(text(offset, hashed))
    _API_CONTRACTS = exact, prefixes
    return _API_CONTRACTS


def system_dependency(name):
    # API sets remain OS contracts, never copied into the package or resolved
    # through PATH. Prefix matches stay distinct from exact schema names.
    if re.fullmatch(r"(?:api-ms|ext-ms)-[a-z0-9-]+\.dll", name):
        exact, prefixes = api_contracts()
        if name in exact:
            return "windows-api-contract-present-on-build-host"
        if name.removesuffix(".dll").rsplit("-", 1)[0] in prefixes:
            return "windows-api-schema-prefix-candidate-not-loader-verified"
        return None
    base, _, suffix = name.rpartition(".")
    if base in SYSTEM_NAMES and suffix in ("dll", "drv"):
        path = SYSTEM / name
        if path.is_file():
            ordinary(path)
            return "windows-system-file-present-on-build-host"
    return None


def resolve_name(name, relative, inventory, qt_bin, crt_bin):
    for candidate in ((Path(relative).parent / name).as_posix().lower(), name):
        if candidate in inventory:
            return "package", inventory[candidate]
    if name in crt_bin:
        return "vc-redist", crt_bin[name]
    if name in qt_bin:
        return "qt-bin", qt_bin[name]
    system = system_dependency(name)
    if system:
        return system, None
    raise ValueError("unresolved dependency: " + name)


def copy_new(source, target):
    ordinary(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    require(sha(source) == sha(target), "copy changed input")


def run_tool(command, environment):
    result = subprocess.run([str(value) for value in command], env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    require(result.returncode == 0, "Qt deployment tool failed")
    return {"tool": Path(command[0]).name, "returnCode": result.returncode,
            "stdoutSha256": hashlib.sha256(result.stdout).hexdigest(),
            "stderrSha256": hashlib.sha256(result.stderr).hexdigest(),
            "stderr": result.stderr.decode("utf-8", errors="replace")[:8192]}


def child_environment(work_root=BUILD_ROOT):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("QT_", "QML", "PYTHON"))}
    env["PATH"] = os.pathsep.join(map(str, (QT / "bin", CRT, SYSTEM)))
    temp = work_root / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    ordinary(temp, True)
    env["TEMP"] = env["TMP"] = str(temp)
    return env


def complete_closure(package, origins):
    qt_bin = {path.name.lower(): path for path in (QT / "bin").glob("*.dll")}
    crt_bin = {path.name.lower(): path for path in CRT.glob("*.dll")}
    parsed, edges = set(), []
    while True:
        current = files(package)
        inventory = {path.relative_to(package).as_posix().lower(): path.relative_to(package).as_posix() for path in current}
        require(len(inventory) == len(current), "case-insensitive package collision")
        pending = [path for path in current if path.suffix.lower() in (".exe", ".dll") and path not in parsed]
        if not pending:
            break
        for path in pending:
            relative = path.relative_to(package).as_posix()
            for imported in pe_imports(path):
                kind, target = resolve_name(imported["name"], relative, inventory, qt_bin, crt_bin)
                if kind in ("vc-redist", "qt-bin"):
                    source = target
                    destination = package / source.name
                    if not destination.exists():
                        copy_new(source, destination)
                    else:
                        require(sha(source) == sha(destination), "dependency basename collision")
                    origins[source.name] = {"component": kind, "source": source.name}
                    target = source.name
                    inventory[source.name.lower()] = source.name
                    kind = "package"
                edges.append({"image": relative, **imported, "resolution": kind, "target": target})
            parsed.add(path)
    return sorted(edges, key=lambda edge: (edge["image"], edge["kind"], edge["name"]))


def deploy_origins(package):
    origins = {}
    for path in files(package):
        relative = path.relative_to(package)
        if relative.as_posix() in ("valmax-transfer.exe", "valmax_core.dll"):
            continue
        # qmltypes and debug/linker metadata are unnecessary in an installed
        # application. Delete only these exact files inside our new stage.
        if path.suffix.lower() in (".qmltypes", ".pdb", ".lib", ".prl", ".exp"):
            ordinary(path)
            path.unlink()
            continue
        source = QT / relative if relative.parts[0] in ("qml", "plugins", "translations") else QT / "bin" / path.name
        require(source.is_file() and sha(source) == sha(path), "deployment contains unknown or patched file")
        require(path.suffix.lower() != ".exe", "unexpected deployment executable")
        require(not re.fullmatch(r"Qt6.*d\.dll", path.name), "debug Qt DLL")
        origins[relative.as_posix()] = {"component": "qt", "source": source.relative_to(QT).as_posix()}
    return origins


def add_notices(package, origins):
    copied = {}
    sources = [(ROOT / "LICENSE", "licenses/ValMAX-MIT.txt")]
    rust = json.loads((RUST_NOTICES / "rust-crates.json").read_text("utf-8"))
    require(rust["cargoLockSha256"] == sha(ROOT / "native/Cargo.lock"), "Rust license inventory is stale")
    for path in files(RUST_NOTICES):
        sources.append((path, "licenses/Rust/" + path.relative_to(RUST_NOTICES).as_posix()))
    for module in ("qtbase", "qtdeclarative", "qtshadertools"):
        sources.append((QT / "sbom" / (module + "-6.11.2.spdx.json"), "licenses/Qt/" + module + "-6.11.2.spdx.json"))
    for source, relative in sources:
        copy_new(source, package / relative)
        origins[relative] = {"component": "notice", "source": relative}
        copied[str(source)] = sha(source)
    notices = (
        "ValMAX native MData transfer helper\n\n"
        "Original ValMAX code: MIT; see licenses/ValMAX-MIT.txt.\n"
        "Rust dependency copyright/license texts: licenses/Rust.\n"
        "Qt 6.11.2 shared libraries: QtBase, QtDeclarative, QtShaderTools.\n"
        "Copyright The Qt Company Ltd. and other contributors.\n"
        "The SDK SPDX inventory is preserved under licenses/Qt.\n"
        "Upstream sources: https://code.qt.io/cgit/qt/qtbase.git/?h=v6.11.2\n"
        "https://code.qt.io/cgit/qt/qtdeclarative.git/?h=v6.11.2\n"
        "https://code.qt.io/cgit/qt/qtshadertools.git/?h=v6.11.2\n"
        "Qt's full applicable license texts and module-specific notices/source delivery\n"
        "have not yet been assembled for this staging package. Distribution review remains open.\n"
        "Microsoft VC143 redistributable DLLs come only from the official VC/Redist directory.\n"
        "They retain Microsoft's redistribution terms. Windows DLLs/API sets are not bundled.\n"
        "No Python runtime, MAX executable, profiles, passwords or archives are included.\n"
    ).encode("utf-8")
    (package / "THIRD-PARTY-NOTICES.txt").write_bytes(notices)
    origins["THIRD-PARTY-NOTICES.txt"] = {"component": "generated", "source": "package_transfer_helper.py"}
    return copied


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--build-proof", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, choices=WORK_ROOTS, default=BUILD_ROOT)
    args = parser.parse_args(argv)
    args.exe = owned_build(args.exe, "helper executable")
    require(args.exe.name == "valmax-transfer.exe", "expected valmax-transfer.exe")
    ordinary(args.exe)
    args.report = below(args.report, REPORT_ROOT, "report")
    require(args.report.parent.is_dir() and not args.report.exists(), "report must be new under existing analysis directory")
    args.build_proof = below(args.build_proof, REPORT_ROOT, "build proof")
    return args


def build_inputs(executable, proof_path):
    """Select the exact EXE/core pair recorded by the completed native build."""
    require(ordinary(proof_path).st_size <= 8 << 20, "build proof bound")
    proof = json.loads(proof_path.read_text("utf-8"))
    require(proof.get("schema") == 1 and proof.get("compiled") is True, "uncompiled build proof")
    build = owned_build(Path(proof["build"]), "helper build")
    core = owned_build(Path(proof["coreDll"]), "Rust core")
    require(executable == build / "valmax-transfer.exe" and core.name == "valmax_core.dll", "build input mismatch")
    expected = proof["coreSha256"]
    require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), "invalid core hash")
    artifacts = proof["artifacts"]
    require(artifacts.get(str(core)) == expected and artifacts.get(str(executable)) == sha(executable), "unrecorded build pair")
    for name, digest in artifacts.items():
        path = owned_build(Path(name), "build artifact")
        ordinary(path)
        require(sha(path) == digest, "build artifact changed")
    for relative, digest in proof["sourceHashes"].items():
        path = below(ROOT / relative, ROOT, "build source")
        require(sha(path) == digest, "build source changed")
    return core, expected


def main():
    args = arguments()
    package_root = args.work_root / "packages"
    core, core_sha = build_inputs(args.exe, args.build_proof)
    require('set(PACKAGE_VERSION "6.11.2")' in (QT / "lib/cmake/Qt6/Qt6ConfigVersionImpl.cmake").read_text(), "wrong Qt SDK")
    require((APP / "Main.qml").is_file(), "helper QML source is not ready")
    require(CRT.is_dir(), "official VC redist missing")
    app_files = {str(path.relative_to(ROOT)).replace("\\", "/"): sha(path) for path in files(APP)}
    input_hashes = {str(args.exe): sha(args.exe), str(core): core_sha,
                    str(args.build_proof): sha(args.build_proof), str(Path(__file__).resolve()): sha(__file__)}
    for relative in ("bin/windeployqt.exe", "bin/qtpaths.exe", "bin/qmlimportscanner.exe", "bin/Qt6Core.dll"):
        input_hashes[str(QT / relative)] = sha(QT / relative)
    for relative in ("native/Cargo.lock", "analysis/native-migration-v1/qt-sdk.json"):
        input_hashes[str(ROOT / relative)] = sha(ROOT / relative)
    package_root.mkdir(parents=True, exist_ok=True)
    below(package_root, args.work_root, "package root")
    ordinary(package_root, True)
    # The random input stage is also an owned hash-named child. On failure it is
    # retained for inspection; never recursively delete another build's files.
    stage = package_root / ("valmax-transfer-" + hashlib.sha256(uuid.uuid4().bytes).hexdigest())
    stage.mkdir()
    copy_new(args.exe, stage / "valmax-transfer.exe")
    copy_new(core, stage / "valmax_core.dll")
    command = [QT / "bin/windeployqt.exe", "--release", "--no-translations", "--no-compiler-runtime",
               "--no-patchqt", "--no-opengl-sw", "--no-system-d3d-compiler", "--no-system-dxc-compiler", "--no-ffmpeg",
               "--skip-plugin-types", "generic,qmltooling,qmllint,qmlls,designer,help,networkinformation,tls,sqldrivers",
               "--exclude-plugins", "qdirect2d,qminimal,qoffscreen", "--qtpaths", QT / "bin/qtpaths.exe",
               "--qmldir", APP, "--qmlimport", QT / "qml", "--qmlimporttimeout", "30000",
               "--dir", stage, "--plugindir", stage / "plugins", "--qml-deploy-dir", stage / "qml",
               stage / "valmax-transfer.exe"]
    deployment = run_tool(command, child_environment(args.work_root))
    origins = deploy_origins(stage)
    origins.update({"valmax-transfer.exe": {"component": "valmax", "source": "valmax-transfer.exe"},
                    "valmax_core.dll": {"component": "rust-core", "source": "valmax_core.dll"}})
    edges = complete_closure(stage, origins)
    require((stage / "plugins/platforms/qwindows.dll").is_file(), "Windows platform plugin missing")
    for module in ("QtQuick", "QtQuick/Controls", "QtQuick/Controls/Fusion", "QtQuick/Layouts", "QtQuick/Dialogs"):
        require((stage / "qml" / module / "qmldir").is_file(), "QML module missing: " + module)
    with (stage / "qt.conf").open("xb") as stream:
        stream.write(QT_CONF)
    origins["qt.conf"] = {"component": "generated", "source": "relative Qt paths"}
    input_hashes.update(add_notices(stage, origins))
    require(sha(args.exe) == input_hashes[str(args.exe)] and sha(core) == core_sha, "native input changed during deployment")
    require(all(sha(ROOT / name) == digest for name, digest in app_files.items()), "app source changed during deployment")
    require(all(sha(path) == digest for path, digest in input_hashes.items()), "packaging input changed")
    inventory = []
    for path in files(stage):
        relative = path.relative_to(stage).as_posix()
        require(relative in origins, "untracked package file")
        require(path.suffix.lower() not in (".py", ".pyc", ".pyd", ".dmp", ".lib", ".pdb", ".mdata"), "development/private file in package")
        inventory.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha(path), "origin": origins[relative]})
    manifest = {"schema": 1, "product": "ValMAX native transfer helper", "qtVersion": "6.11.2",
                "coreSha256": core_sha, "files": inventory, "dependencyClosure": edges,
                "symbolsVerified": False, "cleanMachineTested": False,
                "distributionBlockers": ["Complete Qt module license texts/notices and source-delivery review remain outstanding."],
                "osBoundary": "Windows x64 system DLLs and API-set contracts; presence only checked on build host, not every supported Windows build.",
                "dynamicBoundary": "QML deployment uses Qt scanner; arbitrary dynamic LoadLibrary and optional rendering backends need runtime smoke tests."}
    manifest_bytes = canonical(manifest) + b"\n"
    package_hash = hashlib.sha256(manifest_bytes).hexdigest()
    final = package_root / ("valmax-transfer-" + package_hash)
    require(not final.exists(), "identical package target already exists; nothing overwritten")
    with (stage / "package-manifest.json").open("xb") as stream:
        stream.write(manifest_bytes)
    require(stage.parent.resolve() == package_root.resolve() and final.parent.resolve() == package_root.resolve(), "unsafe stage move")
    stage.rename(final)
    report = {"schema": 1, "packaged": True, "verifiedBundle": True, "package": str(final), "manifestSha256": package_hash,
              "fileHashes": {**{entry["path"]: entry["sha256"] for entry in inventory}, "package-manifest.json": package_hash},
              "inputHashes": input_hashes, "appSourceHashes": app_files, "deployment": deployment,
              "files": len(inventory), "bytes": sum(entry["bytes"] for entry in inventory),
              "dependencyEdges": len(edges), "missingNonSystemDependencies": [], "cleanMachineTested": False,
              "runtimeExecuted": False, "installedClientChanged": False, "distributionReady": False, "distributable": False,
              "distributionBlockers": manifest["distributionBlockers"],
              "limitations": [manifest["osBoundary"], manifest["dynamicBoundary"],
                              "No test binaries or Python runtime are packaged; this script is a build tool only."]}
    with args.report.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"package": str(final), "report": str(args.report), "manifestSha256": package_hash,
                      "files": len(inventory), "distributionReady": False}))


if __name__ == "__main__":
    main()
