"""Build and attest one Rust core pair from frozen local sources and real Cargo runs.

This is a build-host tool. It never starts MAX, opens a profile, or claims that
the installed client passed a release regression. Outputs must be new paths on E:.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tomllib

import pefile


ROOT = Path(__file__).resolve().parents[2]
WORK = Path("E:/ValMAXBuild")
TEST_RESULT = re.compile(
    r"test result: ok\.\s*(\d+) passed; (\d+) failed; (\d+) ignored; "
    r"\d+ measured; (\d+) filtered out;"
)
EXPORT_NAME = re.compile(r"valmax_[A-Za-z0-9_]+\Z")
CORE_EXTERNAL_INPUTS = frozenset({
    "native/Cargo.lock", "native/Cargo.toml", "native/rust-toolchain.toml",
    "modkit/config.defaults.json",
})
CORE_REQUIRED_INPUTS = CORE_EXTERNAL_INPUTS | {"native/core/Cargo.toml"}
INCLUDE_MACRO = re.compile(r"\b(?:include_bytes|include_str|include)\s*!\s*\(")
PATH_ATTRIBUTE = re.compile(r"#\s*\[\s*path\s*=\s*")
LITERAL_PATH = re.compile(r'\s*"([A-Za-z0-9_./-]+)"\s*\)')
ATTRIBUTE_PATH = re.compile(r'\s*"([A-Za-z0-9_./-]+)"\s*\]')
REQUIRED_RETENTION_TESTS = (
    "inline_ids_remain_decimal_strings_and_input_is_exactly_scoped",
    "legacy_inline_pages_skip_newer_ordered_rows_without_losing_old_captures",
    "range_uses_exact_order_strings_scope_cursor_and_restart",
    "large_text_pages_keep_full_rows_and_fit_transport_budget",
)


def require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ordinary(path: Path) -> Path:
    path = path.absolute()
    require(path.is_file() and not path.is_symlink(), "missing_or_linked_file")
    require(not (path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT),
            "reparse_file")
    return path


def owned(path: Path, parent: Path, *, must_exist: bool) -> Path:
    path = path.absolute()
    parent = parent.absolute()
    require(path != parent and path.is_relative_to(parent)
            and all(part not in (".", "..") and not part.endswith((".", " "))
                    for part in path.parts[1:]), "outside_or_ambiguous_owned_path")
    for candidate in (path, *path.parents):
        if candidate.exists():
            require(not candidate.is_symlink() and not candidate.is_junction(),
                    "reparse_owned_path")
    require(path.exists() == must_exist, "owned_path_exists_state_mismatch")
    return path


def core_input_name(name: str) -> bool:
    return name in CORE_EXTERNAL_INPUTS or name.startswith("native/core/")


def verify_core_inventory(root: Path, names: set[str] | dict[str, str]) -> None:
    """Reject new, ignored, linked or disappearing files in the compiled core tree."""
    core = root / "native/core"
    require(core.is_dir() and not core.is_symlink() and not core.is_junction(),
            "unsafe_core_tree")
    found: set[str] = set()
    for directory, subdirs, files in os.walk(core, topdown=True, followlinks=False):
        for item in subdirs:
            child = Path(directory) / item
            require(not child.is_symlink() and not child.is_junction(),
                    "linked_core_directory")
        for item in files:
            child = ordinary(Path(directory) / item)
            found.add(child.relative_to(root).as_posix())
    expected = {name for name in names if name.startswith("native/core/")}
    require(found == expected, "core_tree_inventory_changed")


def verify_embedded_inputs(root: Path, names: set[str] | dict[str, str]) -> None:
    """Require literal Rust include and #[path] targets to be in the frozen set."""
    frozen = set(names)
    root = root.resolve(strict=True)
    for name in frozen:
        if not name.startswith("native/core/") or not name.endswith(".rs"):
            continue
        source = ordinary(root / name)
        body = source.read_text(encoding="utf8")
        for pattern, literal in ((INCLUDE_MACRO, LITERAL_PATH),
                                 (PATH_ATTRIBUTE, ATTRIBUTE_PATH)):
            for match in pattern.finditer(body):
                argument = literal.match(body, match.end())
                require(argument is not None, "nonliteral_core_include:" + name)
                embedded = (source.parent / argument.group(1)).resolve(strict=True)
                require(embedded.is_relative_to(root), "core_include_outside_root:" + name)
                embedded_name = embedded.relative_to(root).as_posix()
                require(embedded_name in frozen, "core_include_not_frozen:" + embedded_name)


def verify_manifest_inputs(root: Path, names: set[str] | dict[str, str]) -> None:
    workspace = tomllib.loads((root / "native/Cargo.toml").read_text(encoding="utf8"))
    core = tomllib.loads((root / "native/core/Cargo.toml").read_text(encoding="utf8"))
    source_root = root.resolve(strict=True)
    core_root = (root / "native/core").resolve(strict=True)
    frozen = set(names)
    require(workspace.get("workspace", {}).get("members") == ["core"],
            "unaudited_cargo_workspace_layout")
    require(core.get("package", {}).get("build") in (None, False),
            "unaudited_core_build_script")

    def walk(node: object, parents: tuple[str, ...] = (), *, core_manifest: bool) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "path":
                    require(core_manifest and parents == ("bin",) and isinstance(value, str),
                            "unaudited_cargo_path_dependency")
                    selected = (core_root / value).resolve(strict=True)
                    require(selected.is_relative_to(core_root),
                            "core_binary_path_outside_tree")
                    require(selected.relative_to(source_root).as_posix() in frozen,
                            "core_binary_path_not_frozen")
                    ordinary(selected)
                walk(value, (*parents, key), core_manifest=core_manifest)
        elif isinstance(node, list):
            for value in node:
                walk(value, parents, core_manifest=core_manifest)

    walk(workspace, core_manifest=False)
    walk(core, core_manifest=True)


def core_source_paths() -> list[Path]:
    command = ["git", "ls-files", "-z", "--", *sorted(CORE_EXTERNAL_INPUTS),
               "native/core"]
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    names = result.stdout.decode("utf8").rstrip("\0").split("\0")
    require(len(names) >= 50 and len(names) == len(set(names)), "core_source_inventory_too_small")
    require(CORE_REQUIRED_INPUTS <= set(names),
            "core_manifest_missing")
    require("native/core/build.rs" not in names, "unaudited_core_build_script")
    paths = []
    for name in names:
        require(core_input_name(name), "unexpected_core_source")
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts, "unsafe_core_source")
        paths.append(ordinary(ROOT / relative))
    verify_core_inventory(ROOT, set(names))
    verify_embedded_inputs(ROOT, set(names))
    verify_manifest_inputs(ROOT, set(names))
    return paths


def source_snapshot(paths: list[Path]) -> dict[str, str]:
    return {path.relative_to(ROOT).as_posix(): sha(path) for path in paths}


def verify_source_bytes(root: Path, snapshot: dict[str, str]) -> None:
    for name, digest in snapshot.items():
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts
                and core_input_name(name), "unsafe_source_snapshot")
        require(sha(ordinary(root / relative)) == digest, "core_source_changed:" + name)
    verify_embedded_inputs(root, snapshot)


def baseline(path: Path, expected_sha: str) -> dict:
    require(re.fullmatch(r"[0-9a-f]{64}", expected_sha) is not None,
            "invalid_baseline_sha256")
    require(sha(ordinary(path)) == expected_sha, "baseline_proof_changed")
    value = json.loads(path.read_text(encoding="utf8"))
    require(value.get("ok") is True and type(value.get("passed")) is int
            and value["passed"] > 0 and value.get("failed") == 0
            and type(value.get("ignored")) is int and value["ignored"] >= 0,
            "invalid_baseline_test_result")
    exports = value.get("dll", {}).get("exports")
    require(isinstance(exports, list) and len(exports) >= 20
            and exports == sorted(set(exports))
            and all(isinstance(name, str) and EXPORT_NAME.fullmatch(name) for name in exports),
            "invalid_baseline_exports")
    return value


def test_result(log: Path, frozen_at: float, reference: dict) -> dict:
    require(ordinary(log).stat().st_mtime >= frozen_at - 2, "stale_cargo_test_log")
    text = log.read_text(encoding="utf8", errors="replace")
    matches = TEST_RESULT.findall(text)
    require(len(matches) == 1 and "Finished `release` profile" in text
            and "running " in text and "test result: FAILED" not in text,
            "missing_or_ambiguous_cargo_test_result")
    passed, failed, ignored, filtered = map(int, matches[0])
    require(failed == 0 and filtered == 0 and passed >= reference["passed"]
            and ignored <= reference["ignored"]
            and passed + ignored >= reference["passed"] + reference["ignored"],
            "core_test_inventory_or_result_regressed")
    require(re.search(rf"(?m)^running {passed + ignored} tests\s*$", text) is not None,
            "core_test_count_mismatch")
    return {"path": str(log), "sha256": sha(log), "passed": passed,
            "failed": failed, "ignored": ignored, "filteredOut": filtered}


def retention_result(log: Path, frozen_at: float, reference: dict) -> dict:
    require(ordinary(log).stat().st_mtime >= frozen_at - 2, "stale_retention_test_log")
    body = log.read_text(encoding="utf8", errors="replace")
    matches = TEST_RESULT.findall(body)
    require(len(matches) == 1 and "Finished `release` profile" in body
            and "test result: FAILED" not in body, "missing_retention_test_result")
    passed, failed, ignored, filtered = map(int, matches[0])
    previous = reference.get("testResults", {}).get("retentionInline", {})
    require(type(previous.get("passed")) is int and previous["passed"] >= 6
            and passed >= previous["passed"] + 1 and passed >= 7
            and failed == ignored == filtered == 0,
            "retention_test_inventory_or_result_regressed")
    require(re.search(rf"(?m)^running {passed} tests\s*$", body) is not None,
            "retention_test_count_mismatch")
    for name in REQUIRED_RETENTION_TESTS:
        require(re.search(rf"(?m)^test {re.escape(name)} \.\.\. ok\s*$", body) is not None,
                "required_retention_test_missing:" + name)
    return {"path": str(log), "sha256": sha(log), "passed": passed,
            "failed": failed, "ignored": ignored, "filteredOut": filtered,
            "requiredTests": list(REQUIRED_RETENTION_TESTS)}


def release_result(log: Path, frozen_at: float) -> dict:
    require(ordinary(log).stat().st_mtime >= frozen_at - 2, "stale_cargo_build_log")
    require("Finished `release` profile" in log.read_text(encoding="utf8", errors="replace"),
            "cargo_release_build_not_finished")
    return {"path": str(log), "sha256": sha(log)}


def read_exports(path: Path) -> list[str]:
    image = pefile.PE(str(path), fast_load=False)
    try:
        require(image.FILE_HEADER.Machine == 0x8664
                and image.OPTIONAL_HEADER.Magic == 0x20B, "core_not_win64_pe")
        exports = sorted(item.name.decode("ascii") for item in image.DIRECTORY_ENTRY_EXPORT.symbols
                         if item.name)
    finally:
        image.close()
    require(exports == sorted(set(exports))
            and all(EXPORT_NAME.fullmatch(name) for name in exports), "invalid_core_exports")
    return exports


def release_pair(target: Path, frozen_at: float, reference: dict) -> tuple[dict, dict]:
    dll = ordinary(target / "release/valmax_core.dll")
    library = ordinary(target / "release/valmax_core.dll.lib")
    require(dll.stat().st_mtime >= frozen_at - 2
            and library.stat().st_mtime >= frozen_at - 2, "stale_core_release_pair")
    exports = read_exports(dll)
    require(exports == reference["dll"]["exports"], "core_export_abi_changed")
    return ({"path": str(dll), "sha256": sha(dll), "bytes": dll.stat().st_size,
             "exports": exports},
            {"path": str(library), "sha256": sha(library),
             "bytes": library.stat().st_size})


def build_environment(target: Path) -> dict[str, str]:
    environment = os.environ.copy()
    allowed_cargo = {"CARGO_HOME", "CARGO_TARGET_DIR",
                     "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER"}
    allowed_rust = {"RUSTUP_HOME", "RUST_LOG", "RUST_BACKTRACE"}
    for name in environment:
        upper = name.upper()
        require((not upper.startswith("CARGO_") or upper in allowed_cargo)
                and (not upper.startswith("RUST") or upper in allowed_rust),
                "untrusted_cargo_environment:" + name)
    environment["CARGO_TARGET_DIR"] = str(target)
    scratch = owned(WORK / "tmp", WORK, must_exist=True)
    require(scratch.parent == WORK and scratch.is_dir(), "E_temp_missing")
    environment["TEMP"] = str(scratch)
    environment["TMP"] = str(scratch)
    return environment


def environment_fingerprint(environment: dict[str, str]) -> dict:
    names = {"PATH", "INCLUDE", "LIB", "LIBPATH", "TEMP", "TMP"}
    prefixes = ("CARGO_", "RUST", "CC_", "CXX_", "AR_", "CMAKE_", "VC",
                "WINDOWSSDK", "UNIVERSALCRT", "UCRTVERSION")
    relevant = {name: value for name, value in environment.items()
                if (name.upper() in names or name.upper().startswith(prefixes))
                and not any(secret in name.upper()
                            for secret in ("TOKEN", "PASSWORD", "SECRET", "CREDENTIAL"))}
    encoded = json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode("utf8")
    return {"sha256": hashlib.sha256(encoded).hexdigest(),
            "variableNames": sorted(relevant)}


def toolchain_identity(environment: dict[str, str]) -> tuple[Path, dict]:
    for directory in (ROOT / "native", *(ROOT / "native").parents):
        for filename in ("config", "config.toml"):
            require(not (directory / ".cargo" / filename).exists(),
                    "cargo_config_override_requires_review")
    cargo_home_name = environment.get("CARGO_HOME")
    require(bool(cargo_home_name), "cargo_home_missing")
    cargo_home = Path(cargo_home_name)
    require(cargo_home.is_dir(), "cargo_home_missing")
    for filename in ("config", "config.toml"):
        require(not (cargo_home / filename).exists(),
                "cargo_home_config_override_requires_review")
    executable: dict[str, Path] = {}
    for name in ("cargo", "rustc", "rustup", "link"):
        found = shutil.which(name, path=environment.get("PATH"))
        require(found is not None, name + "_not_on_path")
        executable[name] = ordinary(Path(found))

    def output(command: list[str]) -> str:
        result = subprocess.run(command, cwd=ROOT / "native", env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.stdout.decode("utf8", errors="replace").strip()

    channel = tomllib.loads((ROOT / "native/rust-toolchain.toml").read_text(encoding="utf8"))\
        ["toolchain"]["channel"]
    require(re.fullmatch(r"\d+\.\d+\.\d+", channel) is not None,
            "unpinned_rust_toolchain")
    versions = {name: output([str(executable[name]), "--version", "--verbose"])
                for name in ("cargo", "rustc")}
    require(re.search(rf"(?m)^release: {re.escape(channel)}$", versions["rustc"]) is not None,
            "rustc_version_differs_from_pin")
    binaries = {name: {"path": str(path), "sha256": sha(path)}
                for name, path in executable.items()}
    for name in ("cargo", "rustc"):
        selected = ordinary(Path(output([str(executable["rustup"]), "which", name])))
        binaries[name + "Selected"] = {"path": str(selected), "sha256": sha(selected)}
    linker = ordinary(Path(environment.get("CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER", "")))
    require(linker.samefile(executable["link"]), "cargo_linker_differs_from_path")
    return executable["cargo"], {"rustToolchainChannel": channel,
                                 "binaries": binaries, "versions": versions,
                                 "environment": environment_fingerprint(environment)}


def run_cargo(command: list[str], log: Path, environment: dict[str, str]) -> None:
    with log.open("xb") as stream:
        result = subprocess.run(command, cwd=ROOT / "native", env=environment,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=3600,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    require(result.returncode == 0, "cargo_failed:" + log.name)


def require_free_space(minimum_gib: int, reason: str) -> None:
    require(shutil.disk_usage(WORK).free >= (minimum_gib << 30), reason)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, type=Path,
                        help="Existing mutable Cargo target under E:/ValMAXBuild")
    parser.add_argument("--job", required=True, type=Path,
                        help="New isolated job under E:/ValMAXBuild/build")
    parser.add_argument("--report", required=True, type=Path,
                        help="New proof under E:/ValMAXBuild/analysis")
    parser.add_argument("--baseline-proof", required=True, type=Path)
    parser.add_argument("--baseline-proof-sha256", required=True)
    args = parser.parse_args()
    target = owned(args.target, WORK, must_exist=True)
    job = owned(args.job, WORK / "build", must_exist=False)
    report = owned(args.report, WORK / "analysis", must_exist=False)
    require(target.parent == WORK and target.name.startswith("target")
            and job.parent == WORK / "build"
            and report.parent == WORK / "analysis", "direct_E_build_paths_required")
    require_free_space(12, "insufficient_E_space_for_core_preflight")
    reference = baseline(args.baseline_proof, args.baseline_proof_sha256)
    frozen_target = Path(reference["dll"]["path"]).parent.parent.absolute()
    require(target != frozen_target, "cannot_mutate_baseline_cargo_target")
    environment = build_environment(target)
    cargo, toolchain = toolchain_identity(environment)
    paths = core_source_paths()
    snapshot = source_snapshot(paths)
    job.mkdir(parents=True)
    frozen_at = datetime.now(timezone.utc)
    freeze = {"schema": 1, "sourceRoot": str(ROOT),
              "frozenAtUtc": frozen_at.isoformat(), "sourceFiles": snapshot,
              "toolchain": toolchain,
              "baselineProof": {"path": str(args.baseline_proof),
                                "sha256": args.baseline_proof_sha256},
              "cargoCommands": {
                  "test": [str(cargo), "test", "--locked", "--offline", "-p", "valmax-core",
                           "--lib", "--release", "--", "--test-threads=1"],
                  "retention": [str(cargo), "test", "--locked", "--offline", "-p",
                                "valmax-core", "--test", "retention_runtime_selected",
                                "--release", "--", "--test-threads=1"],
                  "build": [str(cargo), "build", "--locked", "--offline", "-p", "valmax-core",
                            "--release"]}}
    freeze_path = job / "core-source-freeze.json"
    freeze_path.write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf8")
    test_log = job / "cargo-core-lib.log"
    retention_log = job / "cargo-retention-runtime.log"
    build_log = job / "cargo-core-release.log"
    run_cargo(freeze["cargoCommands"]["test"], test_log, environment)
    verify_core_inventory(ROOT, snapshot)
    verify_source_bytes(ROOT, snapshot)
    tests = test_result(test_log, frozen_at.timestamp(), reference)
    require_free_space(4, "insufficient_E_space_after_core_tests")
    run_cargo(freeze["cargoCommands"]["retention"], retention_log, environment)
    verify_core_inventory(ROOT, snapshot)
    verify_source_bytes(ROOT, snapshot)
    retention = retention_result(retention_log, frozen_at.timestamp(), reference)
    require_free_space(4, "insufficient_E_space_after_retention_tests")
    run_cargo(freeze["cargoCommands"]["build"], build_log, environment)
    verify_core_inventory(ROOT, snapshot)
    verify_source_bytes(ROOT, snapshot)
    release = release_result(build_log, frozen_at.timestamp())
    dll, library = release_pair(target, frozen_at.timestamp(), reference)
    proof = {"schema": 1, "ok": True, "passed": tests["passed"],
             "failed": tests["failed"], "ignored": tests["ignored"],
             "sourceSnapshotAtReportTime": snapshot,
             "dll": dll, "importLibrary": library,
             "testResults": {"fullLib": tests, "retentionInline": retention},
             "releaseLog": release,
             "sourceFreeze": {"path": str(freeze_path), "sha256": sha(freeze_path)},
             "toolchain": toolchain,
             "baselineProof": freeze["baselineProof"],
             "exportHistoryHookLoaded": False,
             "fullChatExportVerified": False,
             "releaseEligible": False,
             "scope": "Fresh offline locked Cargo library and selected retention "
                      "integration tests, Win64 release pair, first-party source hashes, "
                      "recorded toolchain/environment fingerprint, and baseline ABI/count "
                      "comparison. This is not a hermetic or reproducible-build proof; "
                      "installed client, server history EOF and media are separate."}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x", encoding="utf8") as stream:
        json.dump(proof, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"ok": True, "report": str(report),
                      "reportSha256": sha(report), "passed": tests["passed"],
                      "ignored": tests["ignored"], "dllSha256": dll["sha256"]}))


if __name__ == "__main__":
    main()
