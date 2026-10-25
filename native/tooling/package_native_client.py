"""Package an assembled native runtime with its matching native launcher.

Build tool only. Never imports the Python launcher/server or executes MAX.
Inno compilation is optional; no installer or user profile is opened here.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
from datetime import datetime, timedelta, timezone

from package_transfer_helper import pe_imports, system_dependency
from assemble_native_runtime import dependency_closure as runtime_dependency_closure

ROOT = Path(__file__).resolve().parents[2]
WORK_ROOTS = (Path("C:/ValMAXBuild"), Path("D:/ValMAXBuild"), Path("E:/ValMAXBuild"))
TEMPLATE = ROOT / "native/installer/valmax-native.iss"
HOOK_VERIFIER = ROOT / "native/tooling/hook_target_identity.py"
spec = importlib.util.spec_from_file_location("native_launcher_pins", ROOT / "native/launcher/launcher_generate_pins.py")
pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pins)

# These are unresolved product work, not switches selected by an evidence file.
# Replacing the receipts below with a green component build cannot clear them.
CURRENT_RELEASE_BLOCKERS = (
    ("message_retention", "Passive native capture, selected-chat in-history BETA bubbles and the account-scoped local archive are integrated. New captures carry an original-order key; comprehensive installed placement remains unverified. Older preview.11 records have no such key and remain in an approximate fallback. Mixed-account installed lifecycle, failure recovery and restart evidence remain insufficient for a full release."),
    ("early_protection", "Dynamic ghost/notification readiness before first use is unverified. Static privacy patches have separate package verification; the rejected early bootstrap remains unused."),
    ("installed_full_client", "The assembled native client has not passed complete installed feature and lifecycle checks."),
    ("max_redistribution_authorization", "No reviewed permission from the MAX rights holder covers publishing the modified MAX sources, assets and bundled installer. A tool or user assertion is not a substitute for that permission."),
)
LOCAL_PREVIEW_BLOCKERS = frozenset(key for key, _ in CURRENT_RELEASE_BLOCKERS) | {
    "release_evidence_missing", "whole_client_materials",
}
LOCAL_PREVIEW_INFO = "native-preview.txt"
RELEASE_SCENARIOS = {
    "accounts": ("names_colors", "account_switch", "working_hours"),
    "business": ("embedded_webview", "navigation_lifetime"),
    "chat_export": ("messages_and_media", "beta_partial_history_disclosed", "progress_and_pacing", "cancel_and_errors"),
    "chat_lock": ("protected_chat", "unprotected_chat", "restart"),
    "ghost": ("silent_reads", "local_unread_count", "presence_typing_stories"),
    "inline_media": ("search", "select_attachment", "failure_and_cancel"),
    "mdata": ("multi_account_import", "account_with_2fa", "export", "recovery"),
    "retention": ("received_then_deleted", "account_isolation", "restart"),
    "notifications": ("pin_chat_redaction", "ordinary_chat_unchanged"),
    "optimization": ("chat_list", "contacts_calls_switch", "history_with_photos", "resize", "settings_toggle_restart"),
    "privacy": ("before_first_use", "unwanted_telemetry_blocked", "normal_client_requests"),
    "profile_details": ("available_fields", "available_profile_links"),
    "quick_replies": ("text", "attachments", "sent_hint_dismissed"),
    "support": ("report_creation", "sensitive_data_excluded"),
    "client_lifecycle": ("first_run", "existing_profile", "tray_shortcut_activation", "shutdown_restart"),
    "installer": ("clean_install", "upgrade_preserves_profiles", "uninstall_preserves_profiles"),
}
MAX_EVIDENCE_AGE = timedelta(days=7)
SUBJECT_KEYS = {"version", "launcherSha256", "runtimeInventorySha256", "qmlProofSha256"}
ARTIFACT_IDENTITY_KEYS = SUBJECT_KEYS - {"version"}
ARTIFACT_AUDIT_SCOPE = ("Pinned bytes, static compiled-guard target identity against frozen sidecars and DLL SHA literals, and direct/delay dependency availability in explicit package directories, API schema and Windows files on this build host. "
                        "Guard activation, imported symbols, dynamic loads and clean-machine compatibility are not verified.")
ANCILLARY_FILES = {
    "ValMAX-инструкция.txt": ROOT / "native/installer/ValMAX-инструкция.txt",
    "LICENSE": ROOT / "LICENSE",
    "THIRD_PARTY_NOTICES.md": ROOT / "THIRD_PARTY_NOTICES.md",
}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ordinary(path, directory=False):
    info = path.lstat()
    require(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, "reparse entry")
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "unexpected entry type")
    if not directory:
        require(info.st_nlink == 1, "hardlinked input")
    return info


def owned(path):
    require(path.is_absolute(), "absolute owned path required")
    value = path.resolve()
    require(any(value != root.resolve() and value.is_relative_to(root.resolve()) for root in WORK_ROOTS),
            "path outside build roots")
    for ancestor in (path, *path.parents):
        if ancestor.exists():
            ordinary(ancestor, ancestor.is_dir())
    return value


def read_json(path):
    require(ordinary(path).st_size <= 16 << 20, "receipt too large")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate receipt key")
            result[key] = value
        return result
    def invalid_constant(_):
        raise ValueError("non-finite receipt value")
    return json.loads(path.read_text("utf8"), object_pairs_hook=pairs, parse_constant=invalid_constant)


def inventory_digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf8")).hexdigest()


def ancillary_inventory():
    rows = []
    for name, source in ANCILLARY_FILES.items():
        size = ordinary(source).st_size
        require(0 < size <= 16 << 20, "invalid ancillary document size: " + name)
        rows.append({"path": name, "size": size, "sha256": sha(source)})
    return rows


def copy_ancillary(payload, rows):
    require({row["path"] for row in rows} == set(ANCILLARY_FILES)
            and len(rows) == len(ANCILLARY_FILES), "exact ancillary documents required")
    for row in rows:
        source, target = ANCILLARY_FILES[row["path"]], payload / row["path"]
        require(ordinary(source).st_size == row["size"] and sha(source) == row["sha256"], "ancillary document changed: " + row["path"])
        require(not target.exists(), "new ancillary document target required")
        shutil.copyfile(source, target)
        require(sha(target) == row["sha256"] and sha(source) == row["sha256"], "ancillary copy mismatch: " + row["path"])


def evidence_path(value):
    require(isinstance(value, str), "absolute evidence path required")
    path = Path(value)
    require(path.is_absolute(), "absolute evidence path required")
    resolved = path.resolve(strict=True)
    allowed = (*WORK_ROOTS, ROOT / "analysis")
    require(any(resolved != root.resolve() and resolved.is_relative_to(root.resolve()) for root in allowed), "evidence outside report/build roots")
    for ancestor in (path, *path.parents):
        ordinary(ancestor, ancestor.is_dir())
    return resolved


def evidence_time(value, now):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value), "UTC evidence timestamp required")
    tested = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    require(tested <= now + timedelta(minutes=5), "future evidence timestamp")
    require(now - tested <= MAX_EVIDENCE_AGE, "stale evidence timestamp")
    return tested


def validate_subject(subject):
    require(isinstance(subject, dict) and set(subject) == SUBJECT_KEYS, "exact release subject required")
    require(isinstance(subject["version"], str) and re.fullmatch(r"\d+\.\d+\.\d+(?:[.-][A-Za-z0-9.]+)?", subject["version"]), "invalid release subject version")
    for name in SUBJECT_KEYS - {"version"}:
        require(isinstance(subject[name], str) and re.fullmatch(r"[0-9a-f]{64}", subject[name]), "invalid release subject digest")


def release_preflight(subject=None, evidence=None, mode="user-release", now=None,
                      artifact_identity=None, materials=None):
    """Receipts attest installed behavior for these exact bytes, never readiness
    inferred from DLL presence, a component test count, or localhost removal.
    Current unresolved work always remains a blocker, even with green receipts.
    """
    require(mode in ("user-release", "developer-staging", "local-preview"), "invalid distribution mode")
    now = now or datetime.now(timezone.utc)
    blockers = [{"id": key, "reason": reason} for key, reason in CURRENT_RELEASE_BLOCKERS]
    result = {"schema": 1, "checkedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "distributionMode": mode, "releaseEligible": False,
              "featureRuntimeTested": False, "subject": subject, "evidenceVerified": False,
              "wholeClientMaterialsVerified": False,
              "evidenceManifestSha256": None, "features": {}, "blockers": blockers,
              "requiredEvidenceIds": list(RELEASE_SCENARIOS),
              "scope": "Eligibility for a complete installed native client, not a component build or dependency check."}
    def block(key, reason):
        blockers.append({"id": key, "reason": reason})
    if (isinstance(materials, dict) and materials.get("completeForWholeClient") is True
            and materials.get("limitations") == []):
        result["wholeClientMaterialsVerified"] = True
    else:
        block("whole_client_materials", "The exact assembled runtime has no complete, limitation-free whole-client distribution materials receipt.")
    if subject is None and artifact_identity is None:
        block("exact_payload_missing", "The exact assembled launcher/runtime/QML subject is unavailable.")
    elif subject is not None:
        try:
            validate_subject(subject)
        except ValueError as error:
            block("invalid_payload_subject", str(error))
    if artifact_identity is not None:
        try:
            require(isinstance(artifact_identity, dict) and set(artifact_identity) == ARTIFACT_IDENTITY_KEYS
                    and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                            for value in artifact_identity.values()), "exact byte artifact identity required")
            require(subject is None or artifact_identity == {key: subject[key] for key in ARTIFACT_IDENTITY_KEYS}, "artifact identity and release subject differ")
        except (ValueError, TypeError, KeyError) as error:
            block("invalid_artifact_identity", str(error))
    for feature in RELEASE_SCENARIOS:
        result["features"][feature] = {"verified": False, "status": "missing"}
    if evidence is None:
        block("release_evidence_missing", "Installed full-feature evidence manifest was not supplied.")
        return result
    try:
        manifest_path = evidence_path(str(evidence))
        manifest = read_json(manifest_path)
        require(isinstance(manifest, dict) and set(manifest) == {"schema", "status", "complete", "subject", "generatedAt", "evidence", "unresolved"}, "exact release evidence manifest required")
        require(type(manifest["schema"]) is int and manifest["schema"] == 1 and manifest["status"] == "passed" and manifest["complete"] is True, "release manifest is not complete/passed")
        validate_subject(manifest["subject"])
        require(subject is not None and manifest["subject"] == subject, "release evidence does not match this payload")
        generated = evidence_time(manifest["generatedAt"], now)
        require(manifest["unresolved"] == [], "release manifest lists unresolved work")
        entries = manifest["evidence"]
        require(isinstance(entries, list) and len(entries) == len(RELEASE_SCENARIOS), "complete feature evidence list required")
        by_id = {}
        for entry in entries:
            require(isinstance(entry, dict) and set(entry) == {"id", "path", "sha256"}, "exact feature evidence reference required")
            key = entry["id"]
            require(isinstance(key, str) and key in RELEASE_SCENARIOS and key not in by_id, "unknown or duplicate feature evidence")
            require(isinstance(entry["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), "invalid feature evidence digest")
            by_id[key] = entry
        require(set(by_id) == set(RELEASE_SCENARIOS), "missing feature evidence")
        result["evidenceManifestSha256"] = sha(manifest_path)
    except (ValueError, OSError, TypeError, KeyError) as error:
        block("invalid_release_evidence", str(error))
        return result
    for key, scenarios in RELEASE_SCENARIOS.items():
        try:
            reference = by_id[key]
            proof_path = evidence_path(reference["path"])
            require(sha(proof_path) == reference["sha256"], "feature evidence changed")
            proof = read_json(proof_path)
            required = {"schema", "kind", "feature", "status", "complete", "testedAt", "subject", "clientExecuted", "scope", "scenarios", "unresolved"}
            require(isinstance(proof, dict) and set(proof) == required, "exact installed feature proof required")
            require(type(proof["schema"]) is int and proof["schema"] == 1 and proof["kind"] == "native-client-release-functional", "unsupported feature evidence schema")
            require(proof["feature"] == key and proof["status"] == "passed" and proof["complete"] is True, "feature is not complete/passed")
            require(proof["clientExecuted"] is True and proof["scope"] == "installed-native-client", "component/synthetic-only proof is not an installed client check")
            require(proof["subject"] == subject, "feature evidence targets another payload")
            tested = evidence_time(proof["testedAt"], now)
            require(tested <= generated + timedelta(minutes=5), "feature proof is newer than its manifest")
            require(proof["unresolved"] == [], "feature proof lists unresolved work")
            require(isinstance(proof["scenarios"], dict) and set(proof["scenarios"]) == set(scenarios)
                    and all(value is True for value in proof["scenarios"].values()), "required feature scenario is missing/failed")
            result["features"][key] = {"verified": True, "status": "passed", "proofSha256": reference["sha256"]}
        except (ValueError, OSError, TypeError, KeyError) as error:
            result["features"][key] = {"verified": False, "status": "invalid"}
            block("feature_" + key, str(error))
    verified = all(value["verified"] for value in result["features"].values())
    result["evidenceVerified"] = verified
    result["featureRuntimeTested"] = verified and not CURRENT_RELEASE_BLOCKERS
    result["releaseEligible"] = verified and not blockers
    return result


def write_preflight(path, result):
    path = Path(path)
    require(path.is_absolute(), "absolute report path required")
    resolved = path.resolve()
    allowed = (*WORK_ROOTS, ROOT / "analysis")
    require(any(resolved != root.resolve() and resolved.is_relative_to(root.resolve()) for root in allowed), "report outside report/build roots")
    for ancestor in (path, *path.parents):
        if ancestor.exists():
            ordinary(ancestor, ancestor.is_dir())
    require(not path.exists(), "new preflight report required")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")


def inspect_generated_qml(directory, client):
    """Verify every generated/embedded byte and collect legacy compatibility facts.

    Finding a legacy endpoint must not skip later patch-byte checks. This is
    read-only evidence for preflight, not authorization to package the resources.
    """
    proof = read_json(directory / "native-qml-proof.json")
    require(proof.get("nativeRuntime") is True, "native QML generation required")
    artifacts = proof.get("generatedArtifacts", {})
    require(set(artifacts) == {"ui_native.py", "icons/icon-pack-plus-valmax.rcc"}, "pinned generated artifacts required")
    for name, digest in artifacts.items():
        artifact = directory / name
        require(ordinary(artifact).st_size <= 64 << 20 and sha(artifact) == digest, "generated artifact changed: " + name)
    checked = {}
    for key, digest in proof["sourceHashes"].items():
        require(key.startswith(":/") and "/" in key, "invalid QML resource name")
        path = directory / key.rsplit("/", 1)[1]
        require(path.suffix == ".qml" and sha(path) == digest, "generated QML changed")
        checked[key] = path.read_bytes()
    for key, digest in proof["extraResourceHashes"].items():
        require(key.startswith(":/valmax/") and ".." not in key and "\\" not in key, "invalid extra resource name")
        path = directory / "icons/inputs" / key[2:]
        require(sha(path) == digest, "generated extra resource changed")
        if path.suffix == ".qml":
            checked[key] = path.read_bytes()
    require(checked and any(b"import ValMAX.Native" in data for data in checked.values()), "native bridge not generated")
    blocked = [name for name, data in checked.items()
               if re.search(rb"(?:127\.0\.0\.1|localhost)(?::|%3[aA])18973|privacy_server|privacy_monitor\.js", data)]
    tree = ast.parse((directory / "ui_native.py").read_text("utf8"))
    values = [ast.literal_eval(node.value) for node in tree.body
              if isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "PATCH_FILES"]
    require(len(values) == 1 and isinstance(values[0], list) and len(values[0]) == 1, "unexpected resource patch spec")
    patches = values[0][0]["patches"]
    require(0 < len(patches) < 4096, "invalid UI patch count")
    with client.open("rb") as stream:
        for entry in patches:
            offset = entry["offset"]
            require(type(offset) is int and 0 <= offset < (1 << 31), "invalid UI patch offset")
            require(isinstance(entry["replacement"], str) and len(entry["replacement"]) <= 64 << 20, "invalid UI patch size")
            replacement = bytes.fromhex(entry["replacement"])
            require(replacement, "empty UI patch")
            stream.seek(offset)
            require(stream.read(len(replacement)) == replacement, "runtime UI differs from generated native resources")
    return {"resources": len(checked), "proofSha256": sha(directory / "native-qml-proof.json"),
            "requiredNativeFiles": proof.get("requiredNativeFiles", []), "legacyResources": blocked}


def check_generated_qml(directory, client):
    """Strict packaging gate: verified bytes with no legacy helper references.

    The endpoint check is a build-time regression check, not a network audit.
    Legacy resources block packaging; the feature is never silently disabled.
    """
    result = inspect_generated_qml(directory, client)
    require(not result["legacyResources"], "legacy local helper remains in: " + ", ".join(result["legacyResources"]))
    return result


def dependency_closure(runtime, rows, launcher):
    """Reuse the staging graph's service/browser architecture boundaries.

    Both paths audit direct/delay module availability only. No target is loaded,
    PATH/SDK fallback is allowed, or clean-machine/symbol compatibility claimed.
    """
    graph = runtime_dependency_closure(runtime, rows)
    missing = graph["missing"]
    require(graph["verified"] is True and not missing,
            "unresolved runtime dependency graph: " + ", ".join(
                item["image"] + ":" + item["import"] + " (" + item["resolution"] + ")" for item in missing))
    edges = [{"image": item["image"], "name": item["import"], "kind": item["kind"],
              "resolution": item["target"] or item["resolution"]} for item in graph["edges"]]
    for entry in pe_imports(launcher):
        name = entry["name"]
        require(not name.startswith(("python", "frida")), "legacy runtime dependency")
        target = launcher.parent / name
        if target.is_file():
            ordinary(target)
            require(target != launcher, "self import")
            resolution = target.name
        else:
            resolution = system_dependency(name)
            require(resolution, "missing bundled dependency for ../ValMAX.exe: " + name)
        edges.append({"image": "../ValMAX.exe", **entry, "resolution": resolution})
    return edges


def recompute_hook_targets(hook_root, read_target):
    # Build-time static verification only; no DLL is loaded and no target runs.
    from hook_target_identity import verify_hook_targets
    try:
        return verify_hook_targets(hook_root, read_target)
    except RuntimeError as error:
        raise ValueError("compiled guard target verification failed: " + str(error)) from error


def hook_target_identity(runtime, rows):
    """Require the frozen assembly's actual-target guard consistency proof.

    Metadata stays in its explicit build root. Installed launcher file pins are
    independent and do not require access to these external build-only inputs.
    """
    receipt_path = runtime.parent / "assembly-manifest.json"
    ordinary(receipt_path)
    receipt_hash = sha(receipt_path)
    assembly = read_json(receipt_path)
    require(isinstance(assembly, dict) and type(assembly.get("schema")) is int and assembly["schema"] == 1
            and assembly.get("assembled") is True, "verified assembly receipt required")
    require(isinstance(assembly.get("runtime"), str) and owned(Path(assembly["runtime"])) == runtime,
            "assembly runtime differs from actual runtime")
    inventory = assembly.get("inventory")
    require(isinstance(inventory, list) and inventory, "assembly inventory required")
    normalized = []
    for row in inventory:
        require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}
                and isinstance(row["path"], str) and type(row["bytes"]) is int and row["bytes"] >= 0
                and isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]),
                "invalid assembly inventory row")
        normalized.append({"path": row["path"], "size": row["bytes"], "sha256": row["sha256"]})
    require(len({row["path"] for row in normalized}) == len(normalized)
            and sorted(normalized, key=lambda row: row["path"]) == sorted(rows, key=lambda row: row["path"]),
            "assembly and actual runtime inventory differ")
    require(assembly.get("runtimeInventorySha256") == inventory_digest(inventory), "assembly inventory digest changed")
    value = assembly.get("hookTargetIdentity")
    fields = {"schema", "verified", "activationVerified", "verifierSha256", "targetCount", "uniqueTargetCount", "hooks", "targets"}
    require(isinstance(value, dict) and set(value) == fields and type(value["schema"]) is int and value["schema"] == 1
            and value["verified"] is True and value["activationVerified"] is False
            and type(value["targetCount"]) is int and value["targetCount"] == 10
            and type(value["uniqueTargetCount"]) is int and value["uniqueTargetCount"] == 8,
            "verified static guard target identity required")
    ordinary(HOOK_VERIFIER)
    require(value["verifierSha256"] == sha(HOOK_VERIFIER), "guard target verifier source changed")
    require(isinstance(assembly.get("hookInputsRoot"), str), "frozen hook input root required")
    hook_root = owned(Path(assembly["hookInputsRoot"]))
    ordinary(hook_root, True)
    actual = {row["path"]: row for row in rows}

    def relative(name):
        require(isinstance(name, str) and name and "\\" not in name and ":" not in name
                and not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
                and str(PurePosixPath(name)) == name, "invalid guard identity relative path")
        return name

    hooks = value["hooks"]
    require(isinstance(hooks, dict) and set(hooks) == {"privacy", "ghost", "notification"}, "all three guard identities required")
    hook_fields = {"dll", "dllSha256", "dllBytes", "pinsFile", "pinsSha256", "headerFile", "headerSha256"}
    for entry in hooks.values():
        require(isinstance(entry, dict) and set(entry) == hook_fields
                and type(entry["dllBytes"]) is int and entry["dllBytes"] > 0, "invalid compiled guard identity")
        name = relative(entry["dll"])
        require(PurePosixPath(name).name == name, "guard DLL must be a filename")
        row = actual.get("mod/" + name)
        require(row is not None and row["size"] == entry["dllBytes"] and row["sha256"] == entry["dllSha256"],
                "staged compiled guard identity differs")
        for filename, expected in ((name, entry["dllSha256"]), (relative(entry["pinsFile"]), entry["pinsSha256"]),
                                   (relative(entry["headerFile"]), entry["headerSha256"])):
            require(PurePosixPath(filename).name == filename, "guard sidecar must be a filename")
            source = hook_root / filename
            ordinary(source)
            require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected) and sha(source) == expected,
                    "frozen guard sidecar or DLL changed")
        require(sha(runtime / "mod" / name) == entry["dllSha256"], "staged guard DLL changed after inventory")
    targets = value["targets"]
    require(isinstance(targets, list) and len(targets) == 10, "ten guard target records required")
    target_fields = {"hook", "relative", "sha256", "size", "imageSize"}
    for entry in targets:
        require(isinstance(entry, dict) and set(entry) == target_fields and entry["hook"] in hooks
                and type(entry["size"]) is int and entry["size"] > 0
                and type(entry["imageSize"]) is int and entry["imageSize"] > 0,
                "invalid static guard target record")
        name = relative(entry["relative"])
        row = actual.get("mod/" + name)
        require(row is not None and row["size"] == entry["size"] and row["sha256"] == entry["sha256"],
                "guard target differs from actual runtime inventory")
    require(len({(entry["hook"], entry["relative"].casefold()) for entry in targets}) == 10
            and len({entry["relative"].casefold() for entry in targets}) == 8,
            "guard targets must contain ten distinct family records and eight files")

    def read_target(name):
        relative(name)
        require("mod/" + name in actual, "guard target outside runtime inventory")
        path = runtime / "mod" / name
        ordinary(path)
        return path.read_bytes()

    fresh = recompute_hook_targets(hook_root, read_target)
    require(fresh == value, "recomputed static guard target identity differs from assembly snapshot")
    require(sha(receipt_path) == receipt_hash, "assembly receipt changed during guard verification")
    return {"assemblyReceiptSha256": receipt_hash, "identity": value}


def bound_materials(runtime, rows, assembly_receipt_sha):
    """Read materials from the same inventoried assembly verified for guards."""
    receipt_path = runtime.parent / "assembly-manifest.json"
    require(sha(receipt_path) == assembly_receipt_sha, "assembly changed before materials verification")
    assembly = read_json(receipt_path)
    status = assembly.get("materials")
    matches = [row for row in rows if row["path"] == "mod/licenses/STAGING-MATERIALS.json"]
    frozen = [row for row in assembly.get("inventory", [])
              if row.get("path") == "mod/licenses/STAGING-MATERIALS.json"]
    if status is None and not matches and not frozen:
        return None
    require(isinstance(status, dict) and len(matches) == len(frozen) == 1
            and frozen[0]["sha256"] == matches[0]["sha256"]
            and frozen[0]["bytes"] == matches[0]["size"],
            "assembly materials snapshot and runtime file must both exist")
    path = runtime / "mod/licenses/STAGING-MATERIALS.json"
    ordinary(path)
    require(sha(path) == matches[0]["sha256"], "inventoried materials file changed")
    require(read_json(path) == status, "assembly and runtime materials status differ")
    require(sha(receipt_path) == assembly_receipt_sha, "assembly changed during materials verification")
    return {"status": status, "sha256": matches[0]["sha256"]}


def packaging_inputs(runtime_path, qml_path, launcher_proof, *, preflight_only=False):
    runtime, qml = owned(runtime_path), owned(qml_path)
    rows = pins.inventory(runtime)
    for row in rows:
        name = Path(row["path"]).name.lower()
        require(name not in {"privacy_monitor.js", "ghost_monitor.js", "notification_monitor.js",
                             "notification_updater.js", "message_retention_monitor.js", "chat_export_end_monitor.js"}
                and not name.startswith(("python", "frida")), "legacy observer in runtime")
    qml_result = (inspect_generated_qml if preflight_only else check_generated_qml)(qml, runtime / "mod/max.exe")
    native_paths = {row["path"] for row in rows}
    required_native = qml_result["requiredNativeFiles"]
    require(isinstance(required_native, list) and required_native
            and all(isinstance(name, str) and "mod/" + name in native_paths for name in required_native),
            "generated native QML module dependency missing")
    proof_path = launcher_proof if launcher_proof.is_absolute() else Path.cwd() / launcher_proof
    build = read_json(evidence_path(str(proof_path)))
    require(build.get("compiled") is True, "compiled launcher receipt required")
    artifacts = build["artifacts"]
    launchers = [owned(Path(path)) for path in artifacts if Path(path).name == "ValMAX.exe"]
    require(len(launchers) == 1, "one native launcher required")
    launcher = launchers[0]
    launcher_hash = artifacts[str(launcher)]
    require(sha(launcher) == launcher_hash, "launcher changed")
    for name, digest in build["sourceHashes"].items():
        source = (ROOT / name).resolve()
        require(source.is_relative_to(ROOT) and sha(source) == digest, "launcher source changed")
    pin_reference = build["pinsReceipt"]
    pin_path = owned(Path(pin_reference["path"]))
    require(sha(pin_path) == pin_reference["sha256"], "launcher pins receipt changed")
    require(read_json(pin_path)["files"] == rows, "launcher and runtime inventory differ")
    hook_identity = hook_target_identity(runtime, rows)
    material_binding = bound_materials(runtime, rows, hook_identity["assemblyReceiptSha256"])
    closure = dependency_closure(runtime, rows, launcher)
    return {"runtime": runtime, "qml": qml, "rows": rows, "launcher": launcher,
            "launcherHash": launcher_hash, "qmlResult": qml_result, "closure": closure,
            "hookTargetIdentity": hook_identity, "materialBinding": material_binding}


def require_distribution(mode, report, installer=False):
    if mode == "local-preview":
        require(report.get("distributionMode") == mode and report.get("releaseEligible") is False
                and report.get("featureRuntimeTested") is False, "local preview cannot claim a complete release")
        require(report.get("artifactValidation", {}).get("verified") is True
                and report.get("nativeQmlCompatibility", {}).get("verifiedNoLegacyReferences") is True,
                "local preview requires verified artifacts without legacy helper references")
        blockers = report.get("blockers")
        require(isinstance(blockers, list) and 0 < len(blockers)
                and all(isinstance(item, dict) and isinstance(item.get("id"), str)
                        and isinstance(item.get("reason"), str) and 0 < len(item["reason"]) <= 4096
                        for item in blockers), "invalid local preview blocker list")
        ids = [item["id"] for item in blockers]
        require(len(set(ids)) == len(ids) and set(ids) <= LOCAL_PREVIEW_BLOCKERS
                and {key for key, _ in CURRENT_RELEASE_BLOCKERS} <= set(ids),
                "local preview accepts only the known unresolved product work")
        validate_subject(report.get("subject"))
        require_preview_version(report["subject"]["version"])
        return
    if mode == "user-release" or installer:
        require(report["releaseEligible"] is True, "complete user release is blocked: " + ", ".join(item["id"] for item in report["blockers"]))
    require(not installer or mode == "user-release", "developer staging cannot compile a user installer")


def require_preview_version(version):
    require(isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+-native\.preview\.\d+", version),
            "local preview version must visibly use -native.preview.<number>")


def preview_information(version):
    require_preview_version(version)
    return (
        "ValMAX — локальная нативная тестовая сборка\nВерсия: " + version + "\n\n"
        "Эта сборка предназначена для ручной проверки перенесённых функций. Полный перенос ещё не завершён.\n\n"
        "Известные ограничения:\n"
        "• Сохранение удалённых сообщений: бета; пузыри в переписке показывают локально сохранённый текст. Новые записи с ключом порядка показывают исходное время, старые записи без него имеют приблизительное положение. Доступны только уже полученные сообщения после включения и успешной проверки нативного хранилища. Прежние удаления не восстановить.\n"
        "• Экспорт чата: бета; доступная история, фото, видео и голосовые скачиваются с ограничениями, полнота всей серверной истории не подтверждена.\n"
        "• Режим призрака и защита уведомлений: готовность до первых фоновых действий не подтверждена.\n"
        "• Полная проверка установленного клиента и всех функций ещё не завершена.\n\n"
        "Предыдущая установка сохраняется рядом как резервная копия. Папка аккаунтов MXMOD и Windows credentials\n"
        "не переносятся и не очищаются установщиком. Не запускайте старый uninstaller из резервной копии.\n\n"
        "Это локальная preview-сборка, а не готовый публичный релиз. Весь список ограничений сохранён в native-package.json.\n\n"
        "ValMAX — local native preview\nVersion: " + version + "\n\n"
        "This build is for manual testing of migrated features. Migration is incomplete.\n"
        "Deleted-message retention is BETA. Selected-chat bubbles show locally saved text. New records with an order key show their original time; older records without it remain approximate. Only already received messages after enable and a successful native-store check are available. Earlier deletions cannot be recovered.\n"
        "Chat export is BETA; available history and media are downloaded with limits. Complete server history and protection readiness before the first background activity are unverified.\n"
        "Complete installed-client testing is outstanding. The previous installation is preserved beside the new one.\n"
        "Setup does not migrate or clear MXMOD accounts or Windows credentials. Do not run the old uninstaller from the backup.\n"
        "This is a local preview, not a complete public release. See native-package.json for the preserved blocker list.\n"
    ).encode("utf-8-sig")


def write_release_state(payload, manifest, report):
    digest = sha(payload / "native-package.json")
    require(manifest["distributionMode"] in ("user-release", "developer-staging", "local-preview"), "invalid native release marker mode")
    marker = ('; Generated by the native packager; verified installed functionality is separate from compilation.\n'
              '#define NativeDistributionMode "' + manifest["distributionMode"] + '"\n'
              '#define NativeReleaseEligible ' + str(int(report["releaseEligible"] is True)) + '\n'
              '#define NativeReleaseBlockerCount ' + str(len(report["blockers"])) + '\n'
              '#define NativePackageManifestSha256 "' + digest + '"\n')
    if manifest["distributionMode"] == "local-preview":
        require_distribution("local-preview", report)
        require(manifest.get("localPreview") is True and manifest.get("previewInformation") == {
            "path": LOCAL_PREVIEW_INFO, "sha256": sha(payload / LOCAL_PREVIEW_INFO)}, "preview information changed")
        require_preview_version(manifest["version"])
        marker += ('#define NativePreviewVersion "' + manifest["version"] + '"\n'
                   '#define NativePreviewInfoSha256 "' + manifest["previewInformation"]["sha256"] + '"\n')
    path = payload / "native-release-state.iss"
    require(not path.exists(), "new native release marker required")
    path.write_text(marker, encoding="utf8")
    return {"manifestSha256": digest, "releaseStateSha256": sha(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--qml-build", type=Path)
    parser.add_argument("--launcher-proof", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--version", help="Required for packaging; may be omitted for a technical preflight report.")
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--distribution", choices=("user-release", "developer-staging", "local-preview"), default="user-release")
    parser.add_argument("--release-evidence", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--preflight-report", type=Path)
    args = parser.parse_args()
    require(args.version is None or re.fullmatch(r"\d+\.\d+\.\d+(?:[.-][A-Za-z0-9.]+)?", args.version), "invalid product version")
    require(args.preflight_only or args.version is not None, "product version is required for packaging")
    if args.distribution == "local-preview":
        require_preview_version(args.version)
        require(args.release_evidence is None, "local preview does not consume full-release evidence")
    require(not args.preflight_only or args.iscc is None, "preflight never compiles an installer")
    require(args.preflight_report is not None or args.output is not None, "preflight report or output directory required")
    require(args.preflight_only or args.output is not None, "packaging output required")
    inputs, artifact_error = None, None
    if all((args.runtime, args.qml_build, args.launcher_proof)):
        try:
            inputs = packaging_inputs(args.runtime, args.qml_build, args.launcher_proof, preflight_only=args.preflight_only)
        except (ValueError, OSError, KeyError, TypeError, SyntaxError) as error:
            artifact_error = str(error)
    else:
        artifact_error = "Assembled runtime, generated QML and matching launcher receipt are required."
    artifact_identity = None if inputs is None else {"launcherSha256": inputs["launcherHash"],
        "runtimeInventorySha256": inventory_digest(inputs["rows"]), "qmlProofSha256": inputs["qmlResult"]["proofSha256"]}
    subject = None if artifact_identity is None or args.version is None else {"version": args.version, **artifact_identity}
    report = release_preflight(subject, args.release_evidence, args.distribution,
                               artifact_identity=artifact_identity,
                               materials=None if inputs is None or inputs.get("materialBinding") is None
                               else inputs["materialBinding"]["status"])
    report["requestedVersion"] = args.version
    if args.version is None:
        report["blockers"].append({"id": "product_version_missing", "reason": "A native user-release version has not been selected; artifact validation is still reported independently."})
    report["artifactValidation"] = {"verified": inputs is not None, "error": artifact_error, "identity": artifact_identity,
        "scope": ARTIFACT_AUDIT_SCOPE,
        "immutableRuntimeFiles": None if inputs is None else len(inputs["rows"]),
        "generatedResources": None if inputs is None else inputs["qmlResult"].get("resources"),
        "dependencyEdges": None if inputs is None else len(inputs["closure"]),
        "hookTargetIdentity": None if inputs is None else inputs["hookTargetIdentity"]}
    report["artifactValidation"]["materialsReceiptSha256"] = (None if inputs is None or inputs.get("materialBinding") is None
                                                              else inputs["materialBinding"]["sha256"])
    legacy_resources = [] if inputs is None else inputs["qmlResult"].get("legacyResources", [])
    report["nativeQmlCompatibility"] = {"legacyResources": legacy_resources,
        "verifiedNoLegacyReferences": inputs is not None and not legacy_resources,
        "scope": "Static generated/embedded resource references, not runtime network behavior."}
    if legacy_resources:
        report["releaseEligible"] = False
        report["featureRuntimeTested"] = False
        report["blockers"].append({"id": "legacy_local_helper", "reason": "Legacy helper references remain in generated resources: " + ", ".join(legacy_resources)})
    if artifact_error:
        report["releaseEligible"] = False
        report["featureRuntimeTested"] = False
        report["blockers"].append({"id": "artifact_validation", "reason": artifact_error})
    output = owned(args.output) if args.output else None
    if output:
        require(not output.exists(), "new isolated output required")
        if inputs:
            require(not output.is_relative_to(inputs["runtime"]) and not inputs["runtime"].is_relative_to(output), "new isolated output required")
    report_path = args.preflight_report or output / "packaging-preflight.json"
    write_preflight(report_path, report)
    if args.preflight_only:
        print(json.dumps({"preflight": True, "report": str(report_path), "releaseEligible": report["releaseEligible"],
                          "blockers": [item["id"] for item in report["blockers"]]}))
        return
    require_distribution(args.distribution, report, bool(args.iscc))
    require(inputs is not None, "native artifact validation failed; see " + str(report_path))
    runtime, qml, rows, launcher = inputs["runtime"], inputs["qml"], inputs["rows"], inputs["launcher"]
    launcher_hash, qml_result, closure = inputs["launcherHash"], inputs["qmlResult"], inputs["closure"]
    ancillary = ancillary_inventory()
    required_bytes = sum(row["size"] for row in rows) + launcher.stat().st_size + sum(row["size"] for row in ancillary)
    free_bytes = shutil.disk_usage(output.anchor).free
    require(free_bytes >= required_bytes * (2 if args.iscc else 1) + (512 << 20),
            "insufficient space for native payload and installer")
    output.mkdir(parents=True, exist_ok=True)
    payload = output / "payload"
    (payload / "runtime").mkdir(parents=True)
    for row in rows:
        source, target = runtime / row["path"], payload / "runtime" / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        require(sha(target) == row["sha256"], "runtime changed while copying")
    shutil.copyfile(launcher, payload / "ValMAX.exe")
    require(sha(payload / "ValMAX.exe") == launcher_hash and sha(launcher) == launcher_hash, "launcher copy mismatch")
    require(pins.inventory(payload / "runtime") == rows and pins.inventory(runtime) == rows, "runtime changed during packaging")
    copy_ancillary(payload, ancillary)
    preview = args.distribution == "local-preview"
    preview_info = None
    if preview:
        (payload / LOCAL_PREVIEW_INFO).write_bytes(preview_information(args.version))
        preview_info = {"path": LOCAL_PREVIEW_INFO, "sha256": sha(payload / LOCAL_PREVIEW_INFO)}
    manifest = {"schema": 1, "nativeOnly": True, "version": args.version,
                "launcherSha256": launcher_hash, "runtimeFiles": rows, "ancillaryFiles": ancillary, "qml": qml_result,
                "dependencyClosure": closure, "featureRuntimeTested": report["featureRuntimeTested"],
                "wholeClientMaterialsVerified": report["wholeClientMaterialsVerified"],
                "materialsReceiptSha256": report["artifactValidation"]["materialsReceiptSha256"],
                "hookTargetIdentity": inputs["hookTargetIdentity"],
                "distributionMode": args.distribution, "releaseEligible": report["releaseEligible"],
                "releaseBlockers": report["blockers"], "releaseEvidenceManifestSha256": report["evidenceManifestSha256"],
                "scope": ARTIFACT_AUDIT_SCOPE + " Installed feature testing is separate."}
    if preview:
        manifest.update(localPreview=True, previewInformation=preview_info)
    (payload / "native-package.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    receipt = {"schema": 1, "packaged": True, "nativeOnly": True, "installerCompiled": False,
               "payload": str(payload), "manifestSha256": sha(payload / "native-package.json"),
               "installedClientChanged": False, "featureRuntimeTested": report["featureRuntimeTested"],
               "wholeClientMaterialsVerified": report["wholeClientMaterialsVerified"],
               "materialsReceiptSha256": report["artifactValidation"]["materialsReceiptSha256"],
               "ancillaryFiles": ancillary,
               "distributionMode": args.distribution, "releaseEligible": report["releaseEligible"],
               "preflightReport": str(report_path), "preflightReportSha256": sha(report_path)}
    if preview:
        receipt.update(localPreview=True, previewInformation=preview_info)
    receipt.update(write_release_state(payload, manifest, report))
    if args.iscc:
        ordinary(args.iscc)
        require(sha(launcher) == launcher_hash and sha(payload / "ValMAX.exe") == launcher_hash, "launcher changed before Inno compilation")
        command = [str(args.iscc), "/Qp", "/DPayload=" + str(payload), "/DOutput=" + str(output / "release"),
                   "/DProductVersion=" + args.version, str(TEMPLATE)]
        with (output / "inno.log").open("xb") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1800,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        require(result.returncode == 0, "Inno compilation failed")
        setup = output / "release" / ("ValMAX-" + args.version + "-Setup.exe")
        require(setup.is_file(), "installer missing")
        receipt.update(installerCompiled=True, installer=str(setup), installerSha256=sha(setup))
    (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
