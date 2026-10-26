"""Package an assembled native runtime with its matching native launcher.

Build tool only. Never imports the Python launcher/server or executes MAX.
Inno compilation is optional; no installer or user profile is opened here.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import zipfile
from datetime import datetime, timedelta, timezone

from package_transfer_helper import pe_imports, system_dependency
from assemble_native_runtime import dependency_closure as runtime_dependency_closure
from webview2_bootstrapper import verify as verify_webview2_bootstrapper
from webview2_bootstrapper import load_pin as webview2_bootstrapper_pin

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
)
MAX_RIGHTS_ATTESTATION_BLOCKER = (
    "max_redistribution_attestation",
    "The publisher has not attested that the MAX rights holder authorized distribution of the modified sources, assets and installer.",
)
LOCAL_PREVIEW_BLOCKERS = frozenset(key for key, _ in CURRENT_RELEASE_BLOCKERS) | {
    MAX_RIGHTS_ATTESTATION_BLOCKER[0], "release_evidence_missing", "whole_client_materials",
}
LOCAL_PREVIEW_INFO = "native-preview.txt"
PUBLIC_BETA_INFO = "native-beta.txt"
PUBLIC_BETA_MATERIAL_INFO = "native-beta-materials.json"
FFMPEG_SOURCE_INFO = "FFMPEG-SOURCE-AVAILABILITY.txt"
QT_SOURCE_INFO = "QT-SOURCE-AVAILABILITY.txt"
PUBLIC_BETA_SCENARIOS = (
    "installed_launch",
    "existing_profile_and_accounts",
    "account_switch",
    "chat_send_receive",
    "deleted_message_bubble",
    "export_photo_video_voice_with_beta_notice",
    "app_exit_restart",
)
PUBLIC_BETA_LIMITATIONS = (
    {"id": "retention_scope", "reason": "Only messages already received after capture was enabled can be retained. Older saved records may have approximate placement and earlier deletions cannot be recovered."},
    {"id": "chat_export_history", "reason": "The server has not confirmed that every older message is available. Media export can report individual attachment failures and the archive may be incomplete."},
    {"id": "early_protection", "reason": "Ghost and notification protection before the first background event has not been demonstrated on the installed client."},
    {"id": "full_feature_regression", "reason": "The complete 16-group installed-client regression required for a stable release has not been completed."},
)
PUBLIC_BETA_MATERIAL_LIMITATIONS = (
    {"id": "max_baseline_notices_unassembled",
     "statusText": "The baseline MAX runtime contains no license/notice files; applicable MAX and bundled third-party redistribution materials have not been assembled.",
     "noticeRu": "У исходного набора MAX нет единого комплекта лицензий и уведомлений; применимость условий ко всем вложенным компонентам ещё проверяется.",
     "noticeEn": "The original MAX runtime has no complete notice set; terms for all bundled components are still being reviewed."},
    {"id": "qt_imageformats_codec_scope_unverified",
     "statusText": "Five QtImageFormats plugins now match official 6.11.2 binaries and carry corresponding sources and original libtiff/libwebp notices; actual codec linkage and whole-client license scope remain unverified.",
     "noticeRu": "Пять плагинов QtImageFormats и их материалы сверены; состав кодеков и применимость лицензий ко всему клиенту ещё не подтверждены.",
     "noticeEn": "Five QtImageFormats plugins and materials match; codec linkage and whole-client license scope remain unverified."},
    {"id": "qt_remaining_plugin_scope_unverified",
     "statusText": "Six additional root Qt DLLs and two service copies remain after Qt Lottie was omitted. Their official 6.11.2 source/license texts are staged; other bundled Qt plugins and third-party materials still require whole-client review.",
     "noticeRu": "Исходники и лицензии для восьми оставшихся копий Qt добавлены; другие Qt-плагины и сторонние материалы требуют проверки.",
     "noticeEn": "Sources and licenses for eight remaining Qt copies are staged; other Qt plugins and third-party materials need review."},
    {"id": "webview2_terms_unverified",
     "statusText": "Modified Qt WebView source and original SDK notices are included; applicable Microsoft Fixed Version Runtime redistribution terms have not been independently collected here.",
     "noticeRu": "Исходники Qt WebView и уведомления SDK включены; применимые условия распространения Microsoft WebView2 Fixed Version ещё не подтверждены.",
     "noticeEn": "Qt WebView source and SDK notices are included; applicable Microsoft WebView2 Fixed Version redistribution terms remain unverified."},
    {"id": "ffmpeg_source_provenance_unverified",
     "statusText": "Versioned FFmpeg/OpenSSL/WinSparkle notices now match audited runtime images; FFmpeg corresponding source/build, modified WinSparkle provenance and any unaudited family image remain unresolved.",
     "noticeRu": "FFmpeg: версии DLL, конфигурации, хэши и upstream-архивы приложены, но соответствие исходников именно этим сборкам не доказано. Происхождение изменённого WinSparkle и части семейства также открыто.",
     "noticeEn": "FFmpeg DLL versions, build configurations, hashes and upstream archives are included, but exact corresponding source for these builds is unproven. Modified WinSparkle provenance and one family image remain open."},
    {"id": "gum_static_notice_scope_unverified",
     "statusText": "The tagged Frida Gum COPYING is retained, but static object and transitive dependency notice closure remain unverified.",
     "noticeRu": "Уведомление Frida Gum приложено; состав статически связанных объектов и их зависимостей проверен не полностью.",
     "noticeEn": "The Frida Gum notice is included; static objects and transitive dependency notices remain unverified."},
    {"id": "rust_linked_object_license_scope_unverified",
     "statusText": "Windows Rust crate archives and original notices match Cargo.lock and the exact compiled-core proof; linked-object and bundled C/ASM license review remain open.",
     "noticeRu": "Исходники Rust crate и уведомления сверены с Cargo.lock; лицензии связанных объектов и вложенного C/ASM ещё проверяются.",
     "noticeEn": "Rust crate sources and notices match Cargo.lock; linked-object and bundled C/ASM license review remains open."},
)


def beta_material_limitations(webview2_mode="fixed", qt_source_mode="inline"):
    """Exact, ordered limitations for the byte-verified runtime modes."""
    require(webview2_mode in ("fixed", "evergreen")
            and qt_source_mode in ("inline", "separate-asset"),
            "unknown public BETA material mode")
    require(qt_source_mode != "separate-asset" or webview2_mode == "evergreen",
            "separate Qt source asset requires Evergreen mode")
    items = [dict(item) for item in PUBLIC_BETA_MATERIAL_LIMITATIONS]
    if qt_source_mode == "separate-asset":
        items[1] = {"id": "qt_imageformats_codec_scope_unverified",
            "statusText": "Five QtImageFormats plugins match official 6.11.2 binaries; corresponding source archives are pinned to a separate Qt asset and original libtiff/libwebp notices remain installed; actual codec linkage and whole-client license scope remain unverified.",
            "noticeRu": "Пять плагинов QtImageFormats сверены; исходники вынесены в отдельный проверяемый архив, а уведомления libtiff/libwebp остаются в установке. Состав кодеков и применимость лицензий ко всему клиенту ещё не подтверждены.",
            "noticeEn": "Five QtImageFormats plugins match; sources are in a separate pinned asset and libtiff/libwebp notices remain installed. Codec linkage and whole-client license scope remain unverified."}
        items[2] = {"id": "qt_remaining_plugin_scope_unverified",
            "statusText": "Six additional root Qt DLLs and two service copies remain after Qt Lottie was omitted. Their official 6.11.2 source archives are pinned to a separate Qt asset and license texts remain installed; other bundled Qt plugins and third-party materials still require whole-client review.",
            "noticeRu": "Исходники для восьми оставшихся копий Qt вынесены в отдельный проверяемый архив; тексты лицензий остаются в установке. Другие плагины Qt и сторонние материалы ещё требуют проверки.",
            "noticeEn": "Sources for eight remaining Qt copies are in a separate pinned asset, while license texts remain installed. Other Qt plugins and third-party materials need review."}
        items.insert(3, {"id": "qt_source_asset_delivery_unverified",
            "statusText": "Qt 6.11.2 source archives are outside the installer in a separate SHA-256-pinned asset; its publication and complete legal coverage remain unverified.",
            "noticeRu": "Исходники Qt 6.11.2 находятся в отдельном архиве с указанным SHA-256. Публикация архива вместе с установщиком и полнота правовых материалов пока не подтверждены.",
            "noticeEn": "Qt 6.11.2 sources are in a separate SHA-256-pinned asset. Publication alongside the installer and complete legal coverage remain unverified."})
    if webview2_mode == "evergreen":
        index = next(index for index, item in enumerate(items) if item["id"] == "webview2_terms_unverified")
        items[index] = {"id": "webview2_evergreen_prerequisite_unverified",
            "statusText": "Evergreen WebView2 is a system prerequisite; clean-PC, absent-runtime and offline installation behavior remain unverified.",
            "noticeRu": "Для мини-приложений нужен системный Microsoft Edge WebView2 Evergreen Runtime. Проверка установки на чистом ПК, без Runtime и без сети ещё не завершена.",
            "noticeEn": "Mini apps require the system Microsoft Edge WebView2 Evergreen Runtime. Clean-PC, missing-runtime and offline installation behavior remain unverified."}
    return tuple(items)
PUBLIC_BETA_REQUIRED_NOTICE_PATHS = frozenset({
    "mod/licenses/BaselineUpstream/FFmpeg/COPYING.LGPLv2.1",
    "mod/licenses/BaselineUpstream/FFmpeg/COPYING.GPLv2",
    "mod/licenses/BaselineUpstream/FFmpeg/COPYING.LGPLv3",
    "mod/licenses/BaselineUpstream/FFmpeg/COPYING.GPLv3",
    "mod/licenses/BaselineUpstream/FFmpeg/LICENSE.md",
    "mod/licenses/BaselineUpstream/OpenSSL/LICENSE.txt",
    "mod/licenses/BaselineUpstream/WinSparkle/COPYING",
    "mod/licenses/BaselineUpstream/WinSparkle/COPYING.expat",
    "mod/licenses/Gum/COPYING",
})
PUBLIC_BETA_FFMPEG_NAMES = frozenset({
    "avcodec-61.dll", "avformat-61.dll", "avutil-59.dll", "swresample-5.dll", "swscale-8.dll",
})
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


def public_beta_preflight(subject, evidence, now, artifact_identity, materials,
                          artifact_verified, qml_compatibility_verified, max_rights_attested,
                          beta_materials=None, beta_materials_error=None,
                          webview2_mode="fixed", qt_source_mode="inline",
                          ffmpeg_source_mode="inline"):
    """A public BETA requires installed core use on the exact packaged bytes.

    Accepted BETA limitations are disclosed, while byte integrity, exact
    documented materials and a passing installed-client smoke stay gates.
    """
    require(ffmpeg_source_mode in ("inline", "separate-asset"),
            "unknown FFmpeg source packaging mode")
    material_limitations = beta_material_limitations(webview2_mode, qt_source_mode)
    blockers = []
    result = {"schema": 1, "checkedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "distributionMode": "public-beta", "releaseEligible": False,
              "webview2Mode": webview2_mode, "qtSourceMode": qt_source_mode,
              "ffmpegSourceMode": ffmpeg_source_mode,
              "featureRuntimeTested": False, "betaCoreTested": False,
              "subject": subject, "evidenceVerified": False,
              "wholeClientMaterialsVerified": False, "materialsDocumentedForBeta": False,
              "betaMaterials": None, "maxRightsAttested": max_rights_attested is True,
              "evidenceManifestSha256": None, "installedSmokeSha256": None,
              "features": {}, "blockers": blockers,
              "requiredEvidenceIds": list(PUBLIC_BETA_SCENARIOS),
              "knownLimitations": ([dict(item) for item in PUBLIC_BETA_LIMITATIONS]
                                   + [{"id": item["id"], "reason": item["statusText"]}
                                      for item in material_limitations]),
              "scope": "Public BETA eligibility for exact bytes and installed core use; the stable 16-group feature regression is separate."}

    def block(key, reason):
        blockers.append({"id": key, "reason": reason})

    ffmpeg_asset_valid = ffmpeg_source_mode == "inline" and (
        not isinstance(beta_materials, dict) or beta_materials.get("ffmpegSourceDistribution") is None)
    if ffmpeg_source_mode == "separate-asset" and isinstance(beta_materials, dict):
        try:
            validate_ffmpeg_source_distribution(beta_materials.get("ffmpegSourceDistribution"), subject)
            ffmpeg_asset_valid = True
        except (ValueError, TypeError, KeyError):
            pass
    if (ffmpeg_asset_valid and isinstance(beta_materials, dict) and beta_materials.get("verified") is True
            and beta_materials.get("completeForWholeClient") is False
            and beta_materials.get("exactCorrespondingSourceProven") is False
            and beta_materials.get("knownLimitations") ==
                [{"id": item["id"], "reason": item["statusText"]}
                 for item in material_limitations]
            and isinstance(materials, dict) and materials.get("completeForWholeClient") is False
            and materials.get("limitations") ==
                [item["statusText"] for item in material_limitations]
            and (qt_source_mode == "inline" or
                 beta_materials.get("qtSourceDistribution") == materials.get("qtSourceDistribution"))):
        result["materialsDocumentedForBeta"] = True
        result["betaMaterials"] = beta_materials
    else:
        block("beta_materials", beta_materials_error or "The exact assembled runtime has no complete documented BETA materials receipt.")
    if max_rights_attested is not True:
        block("max_redistribution_attestation", "The MAX rights holder permission must be attested for this public BETA; the confidential document is not stored in the package.")
    try:
        validate_subject(subject)
        require_preview_version(subject["version"])
    except (ValueError, TypeError, KeyError) as error:
        block("invalid_payload_subject", str(error))
    try:
        require(isinstance(artifact_identity, dict) and set(artifact_identity) == ARTIFACT_IDENTITY_KEYS
                and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                        for value in artifact_identity.values()), "exact byte artifact identity required")
        require(subject is not None and artifact_identity == {key: subject[key] for key in ARTIFACT_IDENTITY_KEYS},
                "artifact identity and release subject differ")
        require(artifact_verified is True, "exact assembled artifact validation has not passed")
        require(qml_compatibility_verified is True, "generated QML still has legacy references or lacks verification")
    except (ValueError, TypeError, KeyError) as error:
        block("artifact_integrity", str(error))
    if evidence is None:
        block("installed_smoke_missing", "A passing installed-client BETA smoke receipt was not supplied.")
        return result
    try:
        smoke_path = evidence_path(str(evidence))
        smoke = read_json(smoke_path)
        required = {"schema", "kind", "status", "subject", "testedAt", "clientExecuted", "scope",
                    "installedLauncher", "launchMode", "scenarios", "unresolvedCritical"}
        require(isinstance(smoke, dict) and set(smoke) == required, "exact installed BETA smoke receipt required")
        require(type(smoke["schema"]) is int and smoke["schema"] == 1
                and smoke["kind"] == "native-public-beta-installed-smoke"
                and smoke["status"] == "passed", "unsupported or failed installed BETA smoke")
        validate_subject(smoke["subject"])
        require(smoke["subject"] == subject, "installed BETA smoke targets another payload")
        evidence_time(smoke["testedAt"], now)
        require(smoke["clientExecuted"] is True and smoke["scope"] == "installed-native-client",
                "component/synthetic-only proof is not installed-client smoke")
        launcher_ref = smoke["installedLauncher"]
        require(smoke["launchMode"] == "outer-installed-launcher"
                and isinstance(launcher_ref, dict) and set(launcher_ref) == {"path", "sha256"}
                and launcher_ref["sha256"] == subject["launcherSha256"],
                "installed BETA smoke must use the exact outer ValMAX launcher")
        launcher_path = Path(launcher_ref["path"])
        require(launcher_path.is_absolute() and launcher_path.name.casefold() == "valmax.exe",
                "installed BETA smoke did not use ValMAX.exe")
        for ancestor in (launcher_path, *launcher_path.parents):
            ordinary(ancestor, ancestor.is_dir())
        require(sha(launcher_path) == subject["launcherSha256"],
                "installed launcher bytes differ from BETA subject")
        require(smoke["unresolvedCritical"] == [], "installed BETA smoke lists critical unresolved work")
        require(isinstance(smoke["scenarios"], dict) and set(smoke["scenarios"]) == set(PUBLIC_BETA_SCENARIOS)
                and all(value is True for value in smoke["scenarios"].values()),
                "required installed BETA scenario is missing/failed")
        result["installedSmokeSha256"] = sha(smoke_path)
        result["evidenceManifestSha256"] = result["installedSmokeSha256"]
        result["evidenceVerified"] = True
        result["betaCoreTested"] = True
    except (ValueError, OSError, TypeError, KeyError) as error:
        block("invalid_installed_smoke", str(error))
    result["releaseEligible"] = result["betaCoreTested"] and not blockers
    return result


def release_preflight(subject=None, evidence=None, mode="user-release", now=None,
                      artifact_identity=None, materials=None, artifact_verified=False,
                      qml_compatibility_verified=False, max_rights_attested=False,
                      beta_materials=None, beta_materials_error=None,
                      webview2_mode="fixed", qt_source_mode="inline",
                      ffmpeg_source_mode="inline",
                      qml_proof_path=None):
    """Receipts attest installed behavior for exact bytes, never DLL presence.

    Stable release retains every unresolved product blocker. Public BETA has
    a separate installed-core contract and explicitly disclosed limitations.
    """
    require(mode in ("user-release", "developer-staging", "local-preview", "public-beta"), "invalid distribution mode")
    now = now or datetime.now(timezone.utc)
    if mode == "public-beta":
        return public_beta_preflight(subject, evidence, now, artifact_identity, materials,
                                     artifact_verified, qml_compatibility_verified, max_rights_attested,
                                     beta_materials, beta_materials_error,
                                     webview2_mode, qt_source_mode, ffmpeg_source_mode)
    rights_attested = mode == "user-release" and max_rights_attested is True
    blockers = [{"id": key, "reason": reason} for key, reason in CURRENT_RELEASE_BLOCKERS]
    if not rights_attested:
        key, reason = MAX_RIGHTS_ATTESTATION_BLOCKER
        blockers.append({"id": key, "reason": reason})
    result = {"schema": 1, "checkedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "distributionMode": mode, "releaseEligible": False,
              "featureRuntimeTested": False, "subject": subject, "evidenceVerified": False,
              "wholeClientMaterialsVerified": False, "maxRightsAttested": rights_attested,
              "stableUiChannelVerified": False,
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
    if mode == "user-release":
        try:
            require(qml_proof_path is not None, "stable-candidate native QML proof required")
            proof_path = evidence_path(str(qml_proof_path))
            require(proof_path.name == "native-qml-proof.json", "native QML proof path required")
            require(isinstance(subject, dict) and isinstance(subject.get("qmlProofSha256"), str),
                    "exact release subject QML digest required")
            require(sha(proof_path) == subject["qmlProofSha256"],
                    "native QML proof differs from exact release subject")
            qml_proof = read_json(proof_path)
            require(isinstance(qml_proof, dict) and type(qml_proof.get("schema")) is int
                    and qml_proof["schema"] == 1 and qml_proof.get("nativeRuntime") is True
                    and qml_proof.get("uiReleaseChannel") == "stable-candidate",
                    "stable-candidate native QML release channel required")
            result["stableUiChannelVerified"] = True
        except (ValueError, OSError, TypeError, KeyError) as error:
            block("stable_ui_channel", str(error))
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


def bound_qt_source_asset(runtime, rows, assembly, material_binding, qt_source_mode):
    """Verify the separate asset and the rewritten transfer inventory by bytes."""
    require(qt_source_mode in ("inline", "separate-asset"), "unknown Qt source mode")
    receipt = assembly.get("qtSourceDistribution")
    if qt_source_mode == "inline":
        require(receipt is None and (material_binding is None or
                material_binding["status"].get("qtSourceDistribution") is None),
                "inline runtime unexpectedly claims an external Qt source asset")
        return None
    require(isinstance(receipt, dict) and receipt.get("mode") == "separate-asset"
            and receipt.get("requiredTogetherWithInstaller") is True
            and receipt.get("legalCompleteness") == "not-assessed"
            and isinstance(material_binding, dict)
            and material_binding["status"].get("qtSourceDistribution") == receipt
            and material_binding["status"].get("copiedQtCorrespondingSourceModules") == []
            and material_binding["status"].get("externalQtCorrespondingSourceModules") ==
                ["qtbase", "qtdeclarative"],
            "separate Qt source asset is not bound to assembled materials")
    expected_rows = receipt.get("omittedRuntimeCopies")
    require(isinstance(expected_rows, list) and expected_rows
            and all(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}
                    and isinstance(row["path"], str) and row["path"].startswith(("mod/", "transfer/"))
                    and type(row["bytes"]) is int and row["bytes"] > 0
                    and isinstance(row["sha256"], str)
                    and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                    for row in expected_rows), "invalid omitted Qt source inventory")
    names = {row["path"] for row in rows}
    omitted_names = {row["path"] for row in expected_rows}
    require(len(omitted_names) == len(expected_rows) and not omitted_names & names
            and receipt.get("omittedRuntimeBytes") == sum(row["bytes"] for row in expected_rows)
            and receipt.get("sourceArchiveCount") == len({Path(row["path"]).name for row in expected_rows})
            and not any(name.lower().endswith(".tar.xz") and "/sources/" in name.lower()
                        and Path(name).name.lower().startswith("qt") for name in names),
            "Qt source copies remain in the installed inventory or omission count differs")
    imageformat_proof = material_binding["status"].get("additionalQtImageFormatsMaterialProof") or {}
    imageformat_installed = {name for name in names if name.startswith("mod/licenses/QtImageFormats/")}
    imageformat_omitted = {name for name in omitted_names
                           if name.startswith("mod/licenses/QtImageFormats/sources/")}
    require(len(imageformat_omitted) == 1
            and imageformat_proof.get("externalSourceArchiveCount") == 1
            and imageformat_proof.get("externalSourceAssetSha256") == receipt["assetSha256"]
            and imageformat_proof.get("copiedMaterialFiles") == len(imageformat_installed)
            and imageformat_proof.get("plannedMaterialFiles") == len(imageformat_installed) + 1,
            "QtImageFormats material count still claims an installed source archive")
    refs = assembly.get("inputReceipts") or {}
    zip_ref, manifest_ref = refs.get("qtSourceBundle"), refs.get("qtSourceBundleManifest")
    require(isinstance(zip_ref, dict) and isinstance(manifest_ref, dict),
            "Qt source bundle input references missing from assembly")
    archive = evidence_path(zip_ref.get("path"))
    manifest_file = evidence_path(manifest_ref.get("path"))
    require(archive.name == receipt.get("assetName")
            and ordinary(archive).st_size == receipt.get("assetBytes")
            and sha(archive) == zip_ref.get("sha256") == receipt.get("assetSha256")
            and sha(manifest_file) == manifest_ref.get("sha256") == receipt.get("manifestSha256"),
            "separate Qt source asset or manifest changed")
    with zipfile.ZipFile(archive) as bundle:
        require(bundle.read("manifest.json") == manifest_file.read_bytes(),
                "Qt source ZIP embedded manifest changed")
        source_rows = read_json(manifest_file).get("retainedSourceArchives")
        require(isinstance(source_rows, list) and len(source_rows) == receipt["sourceArchiveCount"],
                "Qt source ZIP manifest archive count differs")
        pinned = {row["path"]: row for row in expected_rows}
        declared = {}
        for source in source_rows:
            require(isinstance(source, dict) and isinstance(source.get("sourcePathsInP16"), list)
                    and isinstance(source.get("name"), str), "invalid external Qt source record")
            member = "sources/" + source["name"]
            with bundle.open(member) as stream:
                digest, count = hashlib.sha256(), 0
                while chunk := stream.read(1 << 20):
                    digest.update(chunk)
                    count += len(chunk)
            require(count == source.get("bytes") and digest.hexdigest() == source.get("sha256"),
                    "Qt source asset archive member changed")
            for path in source["sourcePathsInP16"]:
                require(path not in declared and path in pinned
                        and pinned[path]["bytes"] == count
                        and pinned[path]["sha256"] == digest.hexdigest(),
                        "Qt source asset no longer covers omitted runtime copy")
                declared[path] = source
        require(set(declared) == omitted_names, "external Qt asset does not cover all omitted sources")
    transfer_manifest = read_json(runtime / "transfer/package-manifest.json")
    require(transfer_manifest.get("qtSourceDistribution") == receipt
            and transfer_manifest.get("sourcePackageManifestSha256") ==
                assembly.get("transferSourcePackageManifestSha256")
            and transfer_manifest.get("redistributionMaterialsComplete") is False
            and isinstance(transfer_manifest.get("distributionBlockers"), list)
            and any("separate byte-bound asset" in item or
                    (receipt.get("manifestDistribution") == "local-prototype-only"
                     and "separate local prototype asset" in item)
                    for item in transfer_manifest["distributionBlockers"])
            and sha(runtime / "transfer/package-manifest.json") ==
                assembly.get("transferPackageManifestSha256"),
            "transfer helper still has stale Qt source manifest")
    transfer_rows = {row["path"][len("transfer/"):]: row for row in rows
                     if row["path"].startswith("transfer/")
                     and row["path"] != "transfer/package-manifest.json"}
    frozen_transfer = transfer_manifest.get("files")
    require(isinstance(frozen_transfer, list)
            and len(frozen_transfer) == len(transfer_rows)
            and all(isinstance(row, dict) and row.get("path") in transfer_rows
                    and row.get("sha256") == transfer_rows[row["path"]]["sha256"]
                    and row.get("bytes") == transfer_rows[row["path"]]["size"]
                    for row in frozen_transfer), "transfer helper inventory differs from slim runtime")
    return {"receipt": receipt, "archive": archive, "manifest": manifest_file}


def validate_ffmpeg_source_distribution(receipt, subject):
    """The separate FFmpeg ZIP is a required release asset, not legal clearance."""
    validate_subject(subject)
    require(isinstance(receipt, dict) and set(receipt) == {
                "mode", "assetName", "assetBytes", "assetSha256", "manifestSha256",
                "subject", "sourceBundleCount", "requiredTogetherWithInstaller",
                "manifestDistribution", "availabilityDraftIncluded", "legalCompleteness",
                "exactCorrespondingSourceProven"}
            and receipt["mode"] == "separate-asset"
            and receipt["assetName"] == "ValMAX-FFmpeg-sources-" + subject["version"] + ".zip"
            and PurePosixPath(receipt["assetName"]).name == receipt["assetName"]
            and type(receipt["assetBytes"]) is int and receipt["assetBytes"] > 0
            and isinstance(receipt["assetSha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", receipt["assetSha256"])
            and isinstance(receipt["manifestSha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", receipt["manifestSha256"])
            and receipt["subject"] == subject and receipt["sourceBundleCount"] == 2
            and receipt["requiredTogetherWithInstaller"] is True
            and receipt["manifestDistribution"] == "public-beta-asset"
            and receipt["availabilityDraftIncluded"] is False
            and receipt["legalCompleteness"] == "not-assessed"
            and receipt["exactCorrespondingSourceProven"] is False,
            "separate FFmpeg asset is unbound, optional or claims unproved source correspondence")


def ffmpeg_source_asset_plan(subject, source_paths, source_rows, config_row, config_bytes,
                             reference_hashes):
    """Build a deterministic source ZIP in memory, including its byte manifest."""
    require_preview_version(subject["version"])
    validate_subject(subject)
    require([row["version"] for row in source_rows] == ["7.1.3", "7.1.5"]
            and config_row["assetMember"] == "build-configurations.json",
            "exact external FFmpeg source inventory required")
    manifest = {"schema": "valmax.ffmpeg-source-asset.v1",
                "releaseSubject": subject, "sourceBundles": source_rows,
                "buildConfigurations": config_row,
                "referenceHashes": reference_hashes,
                "exactCorrespondingSourceProven": False,
                "completeForWholeClient": False}
    manifest_bytes = (json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf8")
    members = [("manifest.json", manifest_bytes)]
    for row in source_rows:
        for kind, item in (("source", row), ("signature", row["signature"])):
            source = source_paths[row["version"]][kind]
            data = source.read_bytes()
            require(ordinary(source).st_size == item["size"]
                    and hashlib.sha256(data).hexdigest() == item["sha256"],
                    "FFmpeg source or signature changed before external asset creation")
            members.append((item["assetMember"], data))
    require(len(config_bytes) == config_row["size"]
            and hashlib.sha256(config_bytes).hexdigest() == config_row["sha256"],
            "FFmpeg build configurations changed before external asset creation")
    members.append((config_row["assetMember"], config_bytes))
    require(len({name for name, _ in members}) == len(members)
            and all(PurePosixPath(name).as_posix() == name and ".." not in PurePosixPath(name).parts
                    and "\\" not in name
                    for name, _ in members), "unsafe or duplicate FFmpeg source asset member")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in members:
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_STORED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    data = buffer.getvalue()
    receipt = {"mode": "separate-asset",
               "assetName": "ValMAX-FFmpeg-sources-" + subject["version"] + ".zip",
               "assetBytes": len(data), "assetSha256": hashlib.sha256(data).hexdigest(),
               "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
               "subject": subject, "sourceBundleCount": 2,
               "requiredTogetherWithInstaller": True,
               "manifestDistribution": "public-beta-asset",
               "availabilityDraftIncluded": False,
               "legalCompleteness": "not-assessed",
               "exactCorrespondingSourceProven": False}
    validate_ffmpeg_source_distribution(receipt, subject)
    return {"receipt": receipt, "bytes": data, "manifest": manifest}


def verify_ffmpeg_source_asset(path, receipt, summary):
    """Reject a missing, changed or incomplete external ZIP before compilation."""
    validate_ffmpeg_source_distribution(receipt, summary["ffmpegSourceDistribution"]["subject"])
    require(ordinary(path).st_size == receipt["assetBytes"]
            and sha(path) == receipt["assetSha256"],
            "separate FFmpeg source asset is missing or changed")
    with zipfile.ZipFile(path) as archive:
        expected = {"manifest.json", "build-configurations.json"}
        for row in summary["sourceBundles"]:
            expected.update((row["assetMember"], row["signature"]["assetMember"]))
        require(set(archive.namelist()) == expected and len(archive.namelist()) == len(expected),
                "separate FFmpeg source asset has missing or extra members")
        manifest_bytes = archive.read("manifest.json")
        require(hashlib.sha256(manifest_bytes).hexdigest() == receipt["manifestSha256"],
                "separate FFmpeg source asset manifest changed")
        manifest = json.loads(manifest_bytes)
        reference_keys = ("ffmpegReferenceBundleManifestSha256", "ffmpegAuditSha256",
                          "ffmpegRuntimeManifestSha256", "ffmpegSignatureVerificationSha256")
        require(set(manifest) == {"schema", "releaseSubject", "sourceBundles",
                                  "buildConfigurations", "referenceHashes",
                                  "exactCorrespondingSourceProven", "completeForWholeClient"}
                and manifest.get("schema") == "valmax.ffmpeg-source-asset.v1"
                and manifest.get("releaseSubject") == receipt["subject"]
                and manifest.get("sourceBundles") == summary["sourceBundles"]
                and manifest.get("buildConfigurations") == summary["buildConfigurations"]
                and manifest.get("referenceHashes") == {key: summary[key] for key in reference_keys}
                and manifest.get("exactCorrespondingSourceProven") is False
                and manifest.get("completeForWholeClient") is False,
                "separate FFmpeg source asset manifest differs from the package")
        for row in (*summary["sourceBundles"],
                    *(item["signature"] for item in summary["sourceBundles"]),
                    summary["buildConfigurations"]):
            data = archive.read(row["assetMember"])
            require(len(data) == row["size"] and hashlib.sha256(data).hexdigest() == row["sha256"],
                    "separate FFmpeg source asset member differs from the package")
    return True


def beta_materials_documentation(binding, rows, assembly_receipt_sha, subject, evidence,
                                 webview2_mode="fixed", qt_source_mode="inline",
                                 ffmpeg_source_mode="inline"):
    """Document a bounded BETA materials gap without asserting legal completeness.

    `binding` is already byte-bound to the assembled runtime by bound_materials.
    FFmpeg archives are supplied either in the package or in a byte-bound
    mandatory release asset. Exact FFmpeg DLL correspondence is not claimed.
    """
    require(ffmpeg_source_mode in ("inline", "separate-asset"),
            "unknown FFmpeg source packaging mode")
    require(isinstance(binding, dict) and isinstance(binding.get("status"), dict)
            and isinstance(binding.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", binding["sha256"]),
            "bound materials receipt required for public BETA")
    status = binding["status"]
    material_limitations = beta_material_limitations(webview2_mode, qt_source_mode)
    require(status.get("completeForWholeClient") is False
            and status.get("limitations") == [item["statusText"] for item in material_limitations],
            "unknown, missing or reordered public BETA materials limitation")
    qt_source_delivery = status.get("qtSourceDistribution")
    if qt_source_mode == "separate-asset":
        require(isinstance(qt_source_delivery, dict)
                and qt_source_delivery.get("mode") == "separate-asset"
                and qt_source_delivery.get("requiredTogetherWithInstaller") is True
                and qt_source_delivery.get("legalCompleteness") == "not-assessed"
                and qt_source_delivery.get("manifestDistribution") == "public-beta-asset"
                and qt_source_delivery.get("availabilityDraftIncluded") is False
                and isinstance(qt_source_delivery.get("assetSha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", qt_source_delivery["assetSha256"])
                and status.get("copiedQtCorrespondingSourceModules") == []
                and status.get("externalQtCorrespondingSourceModules") == ["qtbase", "qtdeclarative"]
                and not any(row["path"].lower().endswith(".tar.xz")
                            and "/sources/" in row["path"].lower()
                            and Path(row["path"]).name.lower().startswith("qt") for row in rows),
                "separate Qt source asset is prototype-only, unbound or its archives remain installed")
    else:
        require(qt_source_delivery is None, "inline Qt runtime unexpectedly claims a separate source asset")
    qt = status.get("additionalQtMaterialProof") or {}
    imageformats = status.get("additionalQtImageFormatsMaterialProof") or {}
    if qt_source_mode == "separate-asset":
        installed_imageformat_count = sum(
            row["path"].startswith("mod/licenses/QtImageFormats/") for row in rows)
        require(imageformats.get("copiedMaterialFiles") == installed_imageformat_count
                and imageformats.get("plannedMaterialFiles") == installed_imageformat_count + 1
                and imageformats.get("externalSourceArchiveCount") == 1
                and imageformats.get("externalSourceAssetSha256") == qt_source_delivery["assetSha256"],
                "QtImageFormats BETA summary counts a source archive outside the installer")
    baseline = status.get("baselineUpstreamNoticeProof") or {}
    gum = status.get("gumTaggedNoticeProof") or {}
    rust = status.get("nativeRustSourceGraphProof") or {}
    lottie = status.get("qtLottieRuntimeOmission") or {}
    require(status.get("helperMaterialsKeptIndependent") is True
            and qt.get("runtimeImagesMatched") == 12 and qt.get("licenseTexts") == 48
            and qt.get("runtimeDllsMatched") is True
            and imageformats.get("runtimeImagesMatched") == 5
            and imageformats.get("copiedMaterialFiles", 0) >= 5
            and imageformats.get("wholeClientMaterialsComplete") is False
            and baseline.get("copiedNoticeFiles") == 8
            and baseline.get("uncoveredRuntimeImages") in ([], ["swscale-8.dll"])
            and baseline.get("ffmpegExactCorrespondingSourceProven") is False
            and gum.get("taggedNoticeCopied") is True
            and rust.get("sourceArchives") == 70 and rust.get("licenseTexts") == 139
            and rust.get("resolvedDependencyGraphBoundToCoreProof") is True
            and rust.get("wholeClientMaterialsComplete") is False
            and len(lottie.get("omittedRuntimeFiles", [])) == 7
            and status.get("fixedBrowserFilesUnmodified") is (webview2_mode == "fixed"),
            "required staged source or notice proof is missing for public BETA")
    inventory = {row["path"]: row for row in rows}
    require(len(inventory) == len(rows) and PUBLIC_BETA_REQUIRED_NOTICE_PATHS <= set(inventory),
            "required staged FFmpeg/OpenSSL/WinSparkle/Gum notice is missing")
    require(evidence is not None, "public BETA materials evidence is missing")
    evidence_file = evidence_path(str(evidence))
    material_manifest = read_json(evidence_file)
    required = {"schema", "kind", "subject", "materialsReceiptSha256", "assemblyReceiptSha256",
                "knownLimitationIds", "ffmpegReferenceBundle", "exactCorrespondingSourceProven",
                "completeForWholeClient"}
    require(isinstance(material_manifest, dict) and set(material_manifest) == required
            and type(material_manifest["schema"]) is int and material_manifest["schema"] == 1
            and material_manifest["kind"] == "native-public-beta-materials"
            and material_manifest["subject"] == subject
            and material_manifest["materialsReceiptSha256"] == binding["sha256"]
            and material_manifest["assemblyReceiptSha256"] == assembly_receipt_sha
            and material_manifest["knownLimitationIds"] == [item["id"] for item in material_limitations]
            and material_manifest["exactCorrespondingSourceProven"] is False
            and material_manifest["completeForWholeClient"] is False,
            "public BETA materials manifest does not describe this exact incomplete assembly")
    bundle_ref = material_manifest["ffmpegReferenceBundle"]
    require(isinstance(bundle_ref, dict) and set(bundle_ref) == {"path", "sha256"}
            and isinstance(bundle_ref["sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", bundle_ref["sha256"]),
            "exact FFmpeg reference bundle manifest required")
    bundle_path = evidence_path(bundle_ref["path"])
    require(bundle_path.name == "manifest.json" and sha(bundle_path) == bundle_ref["sha256"],
            "FFmpeg reference bundle manifest changed")
    bundle = read_json(bundle_path)
    require(bundle.get("schema") == "valmax.ffmpeg-public-beta-reference-bundle.v1"
            and bundle.get("completeForWholeClient") is False
            and bundle.get("ffmpegExactCorrespondingSourceProven") is False
            and bundle.get("ffmpegSourceProvenanceUnverified") is True
            and bundle.get("knownLimitationIds") == ["ffmpeg_source_provenance_unverified",
                "ffmpeg_exact_patches_unconfirmed", "whole_client_materials_incomplete"],
            "FFmpeg reference bundle falsely claims corresponding source or has unknown limitations")
    def bundle_file(relative_name):
        relative_path = PurePosixPath(relative_name)
        require(isinstance(relative_name, str) and relative_path.parts
                and not relative_path.is_absolute()
                and all(part not in (".", "..") for part in relative_path.parts)
                and "\\" not in relative_name, "invalid FFmpeg bundle member path")
        path = evidence_path(str(bundle_path.parent.joinpath(*relative_path.parts)))
        require(path.is_relative_to(bundle_path.parent), "FFmpeg bundle member escapes root")
        return path
    audit_ref = bundle["ffmpegAudit"]
    require(isinstance(audit_ref, dict) and set(audit_ref) == {"path", "sha256", "bundleRelativePath", "copySha256"}
            and isinstance(audit_ref["sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", audit_ref["sha256"])
            and audit_ref["copySha256"] == audit_ref["sha256"],
            "exact FFmpeg audit reference required")
    audit_path = evidence_path(audit_ref["path"])
    require(sha(audit_path) == audit_ref["sha256"]
            and sha(bundle_file(audit_ref["bundleRelativePath"])) == audit_ref["sha256"],
            "FFmpeg audit changed")
    audit = read_json(audit_path)
    require(audit.get("originalMax", {}).get("p14ImagesMatchCab") == {
                location: {name: True for name in PUBLIC_BETA_FFMPEG_NAMES}
                for location in ("p14Main", "p14Service")},
            "FFmpeg audit does not establish the original MAX MSI binary chain")
    runtime_ref = bundle["runtimeManifest"]
    require(isinstance(runtime_ref, dict) and set(runtime_ref) == {"bundleRelativePath", "sha256", "imageCount"}
            and runtime_ref["imageCount"] == 10
            and isinstance(runtime_ref["sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", runtime_ref["sha256"]),
            "exact ten-image FFmpeg runtime manifest required")
    runtime_manifest_path = bundle_file(runtime_ref["bundleRelativePath"])
    require(sha(runtime_manifest_path) == runtime_ref["sha256"], "FFmpeg runtime manifest changed")
    runtime_manifest = read_json(runtime_manifest_path)
    require(runtime_manifest.get("schema") == "valmax.ffmpeg-runtime-reference.v1"
            and runtime_manifest.get("imageCount") == 10
            and runtime_manifest.get("exactCorrespondingSourceProven") is False
            and isinstance(runtime_manifest.get("images"), list)
            and len(runtime_manifest["images"]) == 10,
            "FFmpeg runtime manifest has unknown or incomplete scope")
    runtime_images = {entry["runtimePath"]: entry for entry in runtime_manifest["images"]}
    require(len(runtime_images) == 10, "duplicate FFmpeg runtime image in reference bundle")
    signature_ref = bundle["signatureVerification"]
    require(isinstance(signature_ref, dict) and set(signature_ref) == {"bundleRelativePath", "sha256"}
            and isinstance(signature_ref["sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", signature_ref["sha256"]),
            "pinned FFmpeg signature verification record required")
    signature_path = bundle_file(signature_ref["bundleRelativePath"])
    require(sha(signature_path) == signature_ref["sha256"], "FFmpeg signature verification record changed")
    signatures = read_json(signature_path)
    fingerprint = "FCF986EA15E6E293A5644F10B4322F04D67658D8"
    require(signatures.get("schema") == "valmax.ffmpeg-upstream-signatures.v1"
            and signatures.get("expectedFingerprintFromOfficialDownloadPage") == fingerprint
            and {entry.get("version") for entry in signatures.get("signatures", [])} == {"7.1.3", "7.1.5"}
            and all(entry.get("signingKeyFingerprint") == fingerprint
                    and isinstance(entry.get("validSigStatus"), str)
                    and entry["validSigStatus"].startswith("[GNUPG:] VALIDSIG " + fingerprint)
                    for entry in signatures["signatures"]),
            "FFmpeg upstream source signatures are not verified")
    configurations = []
    for location, prefix in (("p14Main", "mod/"), ("p14Service", "mod/max-service/")):
        images = audit.get("images", {}).get(location, {})
        libraries = audit.get("capabilityProbes", {}).get(location, {}).get("libraries", {})
        require(isinstance(images, dict) and isinstance(libraries, dict)
                and PUBLIC_BETA_FFMPEG_NAMES <= set(images) and PUBLIC_BETA_FFMPEG_NAMES <= set(libraries),
                "FFmpeg audit does not cover every staged DLL")
        for name in sorted(PUBLIC_BETA_FFMPEG_NAMES):
            runtime_path = prefix + name
            row, image, library = inventory.get(runtime_path), images[name], libraries[name]
            require(isinstance(row, dict) and row.get("sha256") == image.get("sha256")
                    and row.get("size") == image.get("sizeBytes"),
                    "staged FFmpeg DLL differs from its pinned audit: " + runtime_path)
            configuration = library.get("configuration")
            require(library.get("license") == "LGPL version 2.1 or later"
                    and isinstance(configuration, str) and configuration
                    and "--enable-gpl" not in configuration and "--enable-nonfree" not in configuration,
                    "FFmpeg configuration or license is not covered by this BETA evidence")
            reference = runtime_images.get(runtime_path)
            require(isinstance(reference, dict)
                    and reference.get("sha256") == row["sha256"]
                    and reference.get("bytes") == row["size"]
                    and reference.get("configuration") == configuration
                    and reference.get("reportedLicense") == library["license"]
                    and reference.get("productVersion") == image.get("productVersion")
                    and reference.get("matchesLocalMaxMsiCab") is True
                    and reference.get("exactCorrespondingSourceProven") is False,
                    "FFmpeg runtime bundle inventory differs from exact staged DLL")
            configurations.append({"runtimePath": runtime_path, "sha256": row["sha256"],
                                   "productVersion": image["productVersion"],
                                   "configuration": configuration, "license": library["license"]})
    bundles = bundle["sourceBundles"]
    require(isinstance(bundles, list) and len(bundles) == 2, "FFmpeg 7.1.3 and 7.1.5 source bundles required")
    source_paths, source_rows = {}, []
    for entry in bundles:
        required_source = {"version", "officialUrl", "path", "bundleRelativePath", "bytes", "sha256",
                           "signatureOfficialUrl", "signaturePath", "signatureSha256", "signatureVerified",
                           "signingKeyFingerprint", "correspondenceToValMAXDllsProven"}
        require(isinstance(entry, dict) and set(entry) == required_source
                and entry["version"] in ("7.1.3", "7.1.5") and entry["version"] not in source_paths,
                "unknown or duplicate FFmpeg source bundle")
        name = "ffmpeg-" + entry["version"] + ".tar.xz"
        require(entry["officialUrl"] == "https://ffmpeg.org/releases/" + name
                and entry["bundleRelativePath"] == "sources/" + name
                and entry["signatureOfficialUrl"] == entry["officialUrl"] + ".asc"
                and entry["signatureVerified"] is True
                and entry["signingKeyFingerprint"] == fingerprint
                and entry["correspondenceToValMAXDllsProven"] is False
                and type(entry["bytes"]) is int and entry["bytes"] > 0
                and isinstance(entry["sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                and isinstance(entry["signatureSha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", entry["signatureSha256"]),
                "FFmpeg source bundle identity must name its official release")
        source = evidence_path(entry["path"])
        signature = evidence_path(entry["signaturePath"])
        require(source.name == name and ordinary(source).st_size == entry["bytes"]
                and sha(source) == entry["sha256"]
                and sha(bundle_file(entry["bundleRelativePath"])) == entry["sha256"]
                and signature.name == name + ".asc"
                and sha(signature) == entry["signatureSha256"]
                and sha(bundle_file(entry["bundleRelativePath"] + ".asc")) == entry["signatureSha256"],
                "FFmpeg source bundle bytes differ from materials manifest")
        with tarfile.open(source, "r:xz") as archive:
            member = archive.getmember("ffmpeg-" + entry["version"] + "/libavcodec/version.h")
            require(member.isfile() and member.size > 0, "FFmpeg source bundle has no versioned source tree")
        source_paths[entry["version"]] = {"source": source, "signature": signature}
        location_key = "path" if ffmpeg_source_mode == "inline" else "assetMember"
        prefix = "materials/FFmpeg/" if ffmpeg_source_mode == "inline" else "sources/"
        source_rows.append({"version": entry["version"], location_key: prefix + name,
                            "size": entry["bytes"], "sha256": entry["sha256"],
                            "signature": {location_key: prefix + name + ".asc",
                                          "size": ordinary(signature).st_size,
                                          "sha256": entry["signatureSha256"]}})
    require(set(source_paths) == {"7.1.3", "7.1.5"}, "both FFmpeg source versions required")
    source_rows.sort(key=lambda row: row["version"])
    config_bytes = (json.dumps({"schema": 1, "exactCorrespondingSourceProven": False,
                                "ffmpegAuditSha256": audit_ref["sha256"],
                                "libraries": configurations}, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf8")
    config_row = {"path" if ffmpeg_source_mode == "inline" else "assetMember":
                  "materials/FFmpeg/build-configurations.json" if ffmpeg_source_mode == "inline"
                  else "build-configurations.json", "size": len(config_bytes),
                  "sha256": hashlib.sha256(config_bytes).hexdigest()}
    asset_plan = None
    if ffmpeg_source_mode == "separate-asset":
        asset_plan = ffmpeg_source_asset_plan(subject, source_paths, source_rows, config_row,
            config_bytes, {"ffmpegReferenceBundleManifestSha256": bundle_ref["sha256"],
                           "ffmpegAuditSha256": audit_ref["sha256"],
                           "ffmpegRuntimeManifestSha256": runtime_ref["sha256"],
                           "ffmpegSignatureVerificationSha256": signature_ref["sha256"]})
    summary = {"verified": True, "materialsReceiptSha256": binding["sha256"],
               "assemblyReceiptSha256": assembly_receipt_sha,
               "evidenceSha256": sha(evidence_file),
               "ffmpegReferenceBundleManifestSha256": bundle_ref["sha256"],
               "ffmpegAuditSha256": audit_ref["sha256"],
               "ffmpegRuntimeManifestSha256": runtime_ref["sha256"],
               "ffmpegSignatureVerificationSha256": signature_ref["sha256"],
               "sourceBundles": source_rows, "buildConfigurations": config_row,
               "knownLimitations": [{"id": item["id"], "reason": item["statusText"]}
                                    for item in material_limitations],
               "exactCorrespondingSourceProven": False, "completeForWholeClient": False}
    if asset_plan is not None:
        summary["ffmpegSourceDistribution"] = asset_plan["receipt"]
    if qt_source_delivery:
        summary["qtSourceDistribution"] = qt_source_delivery
    return {"summary": summary, "sourcePaths": source_paths, "configBytes": config_bytes,
            "ffmpegSourceMode": ffmpeg_source_mode, "ffmpegSourceAsset": asset_plan,
            "auditPath": audit_path, "evidencePath": evidence_file,
            "bundlePath": bundle_path, "runtimeManifestPath": runtime_manifest_path,
            "signatureVerificationPath": signature_path}


def ffmpeg_source_availability(asset):
    validate_ffmpeg_source_distribution(asset, asset["subject"])
    return (
        "Материалы FFmpeg находятся в обязательном отдельном архиве " + asset["assetName"] +
        ". SHA-256: " + asset["assetSha256"] + ". Скачайте его вместе с установщиком из одного выпуска. "
        "Соответствие исходников конкретным DLL пока не доказано.\n"
        "FFmpeg materials are in the required separate archive " + asset["assetName"] +
        ". SHA-256: " + asset["assetSha256"] + ". Download it alongside Setup from the same release. "
        "Exact source correspondence for the bundled DLLs remains unproven.\n"
    ).encode("utf-8-sig")


def copy_beta_materials(payload, plan, output=None):
    summary = plan["summary"]
    source_mode = plan.get("ffmpegSourceMode", "inline")
    require(source_mode in ("inline", "separate-asset"), "unknown FFmpeg source copying mode")
    require(sha(plan["auditPath"]) == summary["ffmpegAuditSha256"]
            and sha(plan["evidencePath"]) == summary["evidenceSha256"]
            and sha(plan["bundlePath"]) == summary["ffmpegReferenceBundleManifestSha256"]
            and sha(plan["runtimeManifestPath"]) == summary["ffmpegRuntimeManifestSha256"]
            and sha(plan["signatureVerificationPath"]) == summary["ffmpegSignatureVerificationSha256"],
            "public BETA materials evidence changed before copying")
    for row in summary["sourceBundles"]:
        for kind, item in (("source", row), ("signature", row["signature"])):
            source = plan["sourcePaths"][row["version"]][kind]
            require(ordinary(source).st_size == item["size"] and sha(source) == item["sha256"],
                    "FFmpeg source or signature changed before copying")
            if source_mode == "inline":
                target = payload / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                require(not target.exists(), "new FFmpeg source or signature target required")
                shutil.copyfile(source, target)
                require(ordinary(target).st_size == item["size"] and sha(target) == item["sha256"]
                        and sha(source) == item["sha256"], "FFmpeg source or signature copy mismatch")
    config = summary["buildConfigurations"]
    require(len(plan["configBytes"]) == config["size"]
            and hashlib.sha256(plan["configBytes"]).hexdigest() == config["sha256"],
            "FFmpeg configuration changed before copying")
    if source_mode == "inline":
        require(summary.get("ffmpegSourceDistribution") is None,
                "inline FFmpeg package claims an external source asset")
        target = payload / config["path"]
        require(not target.exists(), "new FFmpeg configuration target required")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(plan["configBytes"])
        require(ordinary(target).st_size == config["size"] and sha(target) == config["sha256"],
                "FFmpeg configuration copy mismatch")
    else:
        asset_plan = plan.get("ffmpegSourceAsset")
        asset = summary.get("ffmpegSourceDistribution")
        require(output is not None and isinstance(asset_plan, dict)
                and asset_plan.get("receipt") == asset and isinstance(asset_plan.get("bytes"), bytes),
                "required separate FFmpeg source asset plan is missing")
        validate_ffmpeg_source_distribution(asset, asset["subject"])
        require(not (payload / "materials/FFmpeg").exists()
                and not (payload / "source-assets").exists(),
                "FFmpeg source materials and source-assets must be outside the slim installer")
        folder = output / "source-assets"
        folder.mkdir(exist_ok=True)
        ordinary(folder, directory=True)
        target = folder / asset["assetName"]
        require(not target.exists(), "new separate FFmpeg source asset required")
        target.write_bytes(asset_plan["bytes"])
        verify_ffmpeg_source_asset(target, asset, summary)
        notice = payload / FFMPEG_SOURCE_INFO
        require(not notice.exists(), "new FFmpeg source availability notice required")
        notice.write_bytes(ffmpeg_source_availability(asset))
        plan["ffmpegSourceAssetCopy"] = {"path": str(target), "name": asset["assetName"],
                                         "size": asset["assetBytes"], "sha256": asset["assetSha256"]}
        plan["ffmpegSourceAvailability"] = {"path": FFMPEG_SOURCE_INFO, "sha256": sha(notice)}
    info = payload / PUBLIC_BETA_MATERIAL_INFO
    require(not info.exists(), "new public BETA materials summary required")
    info.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf8")
    return {"path": PUBLIC_BETA_MATERIAL_INFO, "sha256": sha(info)}


def packaging_inputs(runtime_path, qml_path, launcher_proof, *, preflight_only=False):
    runtime, qml = owned(runtime_path), owned(qml_path)
    proof_path = launcher_proof if launcher_proof.is_absolute() else Path.cwd() / launcher_proof
    build = read_json(evidence_path(str(proof_path)))
    require(build.get("compiled") is True, "compiled launcher receipt required")
    webview2_mode = build.get("webview2Mode", "fixed")
    require(webview2_mode in ("fixed", "evergreen"), "unknown launcher WebView2 packaging mode")
    qt_source_mode = build.get("qtSourceMode", "inline")
    require(qt_source_mode in ("inline", "separate-asset"), "unknown launcher Qt source packaging mode")
    rows = pins.inventory(runtime, webview2_mode, qt_source_mode)
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
    pin_receipt = read_json(pin_path)
    require(pin_receipt["files"] == rows and pin_receipt.get("webview2Mode", "fixed") == webview2_mode
            and pin_receipt.get("qtSourceMode", "inline") == qt_source_mode,
            "launcher and runtime inventory differ (WebView2 or Qt source mode)")
    hook_identity = hook_target_identity(runtime, rows)
    material_binding = bound_materials(runtime, rows, hook_identity["assemblyReceiptSha256"])
    assembly = read_json(runtime.parent / "assembly-manifest.json")
    require(assembly.get("webview2Mode", "fixed") == webview2_mode
            and assembly.get("qtSourceMode", "inline") == qt_source_mode,
            "assembled runtime and launcher packaging modes differ")
    assembly_rows = [{"path": row["path"], "size": row["bytes"], "sha256": row["sha256"]}
                     for row in assembly.get("inventory", [])]
    require(len(assembly_rows) == len(rows)
            and sorted(assembly_rows, key=lambda row: row["path"]) ==
                sorted(rows, key=lambda row: row["path"]),
            "assembled runtime inventory differs from pinned launcher bytes")
    qt_source_asset = bound_qt_source_asset(runtime, rows, assembly, material_binding, qt_source_mode)
    closure = dependency_closure(runtime, rows, launcher)
    return {"runtime": runtime, "qml": qml, "rows": rows, "launcher": launcher,
            "webview2Mode": webview2_mode, "qtSourceMode": qt_source_mode,
            "qtSourceAsset": qt_source_asset,
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
                and ({key for key, _ in CURRENT_RELEASE_BLOCKERS}
                     | {MAX_RIGHTS_ATTESTATION_BLOCKER[0]}) <= set(ids),
                "local preview accepts only the known unresolved product work")
        validate_subject(report.get("subject"))
        require_preview_version(report["subject"]["version"])
        return
    if mode == "public-beta":
        material_limitations = beta_material_limitations(report.get("webview2Mode", "fixed"),
                                                        report.get("qtSourceMode", "inline"))
        ffmpeg_source_mode = report.get("ffmpegSourceMode", "inline")
        require(ffmpeg_source_mode in ("inline", "separate-asset"),
                "unknown FFmpeg source packaging mode")
        require(report.get("distributionMode") == mode and report.get("releaseEligible") is True
                and report.get("betaCoreTested") is True and report.get("featureRuntimeTested") is False
                and report.get("evidenceVerified") is True, "public BETA requires passing installed core smoke without a stable feature claim")
        require(report.get("blockers") == [] and report.get("wholeClientMaterialsVerified") is False
                and report.get("materialsDocumentedForBeta") is True
                and report.get("maxRightsAttested") is True, "public BETA has unresolved distribution blockers")
        subject = report.get("subject")
        validate_subject(subject)
        require_preview_version(subject["version"])
        artifact = report.get("artifactValidation", {})
        require(artifact.get("verified") is True and artifact.get("identity") ==
                {key: subject[key] for key in ARTIFACT_IDENTITY_KEYS}
                and isinstance(artifact.get("materialsReceiptSha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", artifact["materialsReceiptSha256"]),
                "public BETA requires exact artifact and bound materials snapshot")
        beta_materials = report.get("betaMaterials")
        require(isinstance(beta_materials, dict) and beta_materials.get("verified") is True
                and beta_materials.get("materialsReceiptSha256") == artifact["materialsReceiptSha256"]
                and beta_materials.get("assemblyReceiptSha256") ==
                    artifact.get("hookTargetIdentity", {}).get("assemblyReceiptSha256")
                and beta_materials.get("completeForWholeClient") is False
                and beta_materials.get("exactCorrespondingSourceProven") is False
                and beta_materials.get("knownLimitations") ==
                    [{"id": item["id"], "reason": item["statusText"]}
                     for item in material_limitations]
                and isinstance(beta_materials.get("evidenceSha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", beta_materials["evidenceSha256"]),
                "public BETA materials are not bound to this exact assembled runtime")
        if ffmpeg_source_mode == "separate-asset":
            validate_ffmpeg_source_distribution(beta_materials.get("ffmpegSourceDistribution"), subject)
        else:
            require(beta_materials.get("ffmpegSourceDistribution") is None,
                    "inline public BETA claims a separate FFmpeg source asset")
        sources = beta_materials.get("sourceBundles")
        location_key = "path" if ffmpeg_source_mode == "inline" else "assetMember"
        prefix = "materials/FFmpeg/" if ffmpeg_source_mode == "inline" else "sources/"
        require(isinstance(sources, list) and len(sources) == 2
                and all(isinstance(row, dict) for row in sources)
                and [row.get("version") for row in sources] == ["7.1.3", "7.1.5"]
                and all(row.get(location_key) == prefix + "ffmpeg-" + row["version"] + ".tar.xz"
                        and type(row.get("size")) is int and row["size"] > 0
                        and isinstance(row.get("sha256"), str)
                        and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                        and isinstance(row.get("signature"), dict)
                        and row["signature"].get(location_key) == row[location_key] + ".asc"
                        and type(row["signature"].get("size")) is int
                        and row["signature"]["size"] > 0
                        and isinstance(row["signature"].get("sha256"), str)
                        and re.fullmatch(r"[0-9a-f]{64}", row["signature"]["sha256"])
                        for row in sources), "both pinned FFmpeg source bundles required for public BETA")
        require(all(isinstance(beta_materials.get(key), str)
                    and re.fullmatch(r"[0-9a-f]{64}", beta_materials[key])
                    for key in ("ffmpegReferenceBundleManifestSha256", "ffmpegAuditSha256",
                                "ffmpegRuntimeManifestSha256", "ffmpegSignatureVerificationSha256")),
                "pinned FFmpeg reference and verification records required for public BETA")
        config = beta_materials.get("buildConfigurations")
        require(isinstance(config, dict) and config.get(location_key) == (
                    "materials/FFmpeg/build-configurations.json" if ffmpeg_source_mode == "inline"
                    else "build-configurations.json")
                and type(config.get("size")) is int and config["size"] > 0
                and isinstance(config.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", config["sha256"]),
                "pinned FFmpeg build configurations required for public BETA")
        require(report.get("nativeQmlCompatibility", {}).get("verifiedNoLegacyReferences") is True,
                "public BETA requires verified native QML without legacy references")
        require(isinstance(report.get("installedSmokeSha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", report["installedSmokeSha256"])
                and report.get("evidenceManifestSha256") == report["installedSmokeSha256"],
                "public BETA requires a pinned installed-client smoke receipt")
        require(report.get("knownLimitations") ==
                ([dict(item) for item in PUBLIC_BETA_LIMITATIONS]
                 + [{"id": item["id"], "reason": item["statusText"]}
                    for item in material_limitations]),
                "public BETA must disclose its accepted limitations")
        return
    if mode == "user-release" or installer:
        require(report["releaseEligible"] is True, "complete user release is blocked: " + ", ".join(item["id"] for item in report["blockers"]))
    if mode == "user-release":
        require(report.get("maxRightsAttested") is True,
                "publisher MAX rights attestation is required for a user release")
    require(not installer or mode == "user-release", "developer staging cannot compile a user installer")


def require_preview_version(version):
    require(isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+-native\.preview\.\d+", version),
            "local preview version must visibly use -native.preview.<number>")


def preview_information(version, webview2_mode="fixed", qt_source_mode="inline", qt_source_asset=None):
    require_preview_version(version)
    require(webview2_mode in ("fixed", "evergreen") and qt_source_mode in ("inline", "separate-asset"),
            "unknown local preview packaging mode")
    evergreen_ru = ("Для вкладки бизнеса и мини-приложений нужен Microsoft Edge WebView2 Evergreen Runtime. Если его нет, мастер установит его через интернет до обновления ValMAX; без сети понадобится официальный автономный установщик Microsoft.\n"
                    if webview2_mode == "evergreen" else "")
    evergreen_en = ("The business tab and mini apps need Microsoft Edge WebView2 Evergreen Runtime. If missing, Setup installs it online before upgrading ValMAX; offline use requires Microsoft's standalone installer.\n"
                    if webview2_mode == "evergreen" else "")
    asset_ru = asset_en = ""
    if qt_source_mode == "separate-asset":
        require(isinstance(qt_source_asset, dict)
                and qt_source_asset.get("mode") == "separate-asset"
                and qt_source_asset.get("manifestDistribution") in ("local-prototype-only", "public-beta-asset")
                and qt_source_asset.get("availabilityDraftIncluded") is
                    (qt_source_asset["manifestDistribution"] == "local-prototype-only")
                and isinstance(qt_source_asset.get("assetName"), str)
                and PurePosixPath(qt_source_asset["assetName"]).name == qt_source_asset["assetName"]
                and isinstance(qt_source_asset.get("assetSha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", qt_source_asset["assetSha256"]),
                "local preview requires exact Qt source asset identity")
        asset_ru = ("Исходники Qt вынесены в отдельный локальный ZIP " + qt_source_asset["assetName"] +
                    " (SHA-256 " + qt_source_asset["assetSha256"] + "). Публикация этого файла вместе с установщиком пока не подтверждена.\n")
        asset_en = ("Qt sources are in a separate local ZIP " + qt_source_asset["assetName"] +
                    " (SHA-256 " + qt_source_asset["assetSha256"] + "). Publication of this file alongside the installer is unverified.\n")
    return (
        "ValMAX — локальная нативная тестовая сборка\nВерсия: " + version + "\n\n"
        "Эта сборка предназначена для ручной проверки перенесённых функций. Полный перенос ещё не завершён.\n\n"
        + evergreen_ru + asset_ru + ("\n" if evergreen_ru or asset_ru else "")
        + "Известные ограничения:\n"
        "• Сохранение удалённых сообщений: бета; пузыри в переписке показывают локально сохранённый текст. Новые записи с ключом порядка показывают исходное время, старые записи без него имеют приблизительное положение. Доступны только уже полученные сообщения после включения и успешной проверки нативного хранилища. Прежние удаления не восстановить.\n"
        "• Экспорт чата: бета; доступная история, фото, видео и голосовые скачиваются с ограничениями, полнота всей серверной истории не подтверждена.\n"
        "• Режим призрака и защита уведомлений: готовность до первых фоновых действий не подтверждена.\n"
        "• Полная проверка установленного клиента и всех функций ещё не завершена.\n\n"
        "Предыдущая установка сохраняется рядом как резервная копия. Папка аккаунтов MXMOD и Windows credentials\n"
        "не переносятся и не очищаются установщиком. Не запускайте старый uninstaller из резервной копии.\n\n"
        "Это локальная preview-сборка, а не готовый публичный релиз. Весь список ограничений сохранён в native-package.json.\n\n"
        "ValMAX — local native preview\nVersion: " + version + "\n\n"
        "This build is for manual testing of migrated features. Migration is incomplete.\n"
        + evergreen_en + asset_en + ("\n" if evergreen_en or asset_en else "")
        + "Deleted-message retention is BETA. Selected-chat bubbles show locally saved text. New records with an order key show their original time; older records without it remain approximate. Only already received messages after enable and a successful native-store check are available. Earlier deletions cannot be recovered.\n"
        "Chat export is BETA; available history and media are downloaded with limits. Complete server history and protection readiness before the first background activity are unverified.\n"
        "Complete installed-client testing is outstanding. The previous installation is preserved beside the new one.\n"
        "Setup does not migrate or clear MXMOD accounts or Windows credentials. Do not run the old uninstaller from the backup.\n"
        "This is a local preview, not a complete public release. See native-package.json for the preserved blocker list.\n"
    ).encode("utf-8-sig")


def qt_source_availability(asset):
    require(isinstance(asset, dict) and asset.get("mode") == "separate-asset"
            and asset.get("manifestDistribution") in ("local-prototype-only", "public-beta-asset")
            and asset.get("availabilityDraftIncluded") is
                (asset["manifestDistribution"] == "local-prototype-only")
            and isinstance(asset.get("assetName"), str)
            and PurePosixPath(asset["assetName"]).name == asset["assetName"]
            and isinstance(asset.get("assetSha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", asset["assetSha256"]),
            "verified separate Qt source asset required")
    prototype = asset.get("manifestDistribution") == "local-prototype-only"
    ru_delivery = ("Это локальная проверка упаковки. Архив лежит в source-assets рядом с payload; публичная публикация не подтверждена.\n"
                   if prototype else "На странице релиза проверьте, что файл опубликован рядом с установщиком, и сверьте SHA-256 перед использованием.\n")
    en_delivery = ("This is a local packaging proof. The asset is in source-assets beside payload; public publication is unverified.\n"
                   if prototype else "On the release page, verify that this file is published beside the installer and check its SHA-256 before use.\n")
    return (
        "Исходники Qt 6.11.2 для этой сборки находятся в отдельном ZIP-файле, а не внутри установщика.\n"
        "Файл: " + asset["assetName"] + "\nSHA-256: " + asset["assetSha256"] + "\n"
        + ru_delivery +
        "Полнота применимых лицензий и публикация отдельного файла проверяются отдельно.\n\n"
        "Qt 6.11.2 corresponding sources are in a separate ZIP asset, not inside this installer.\n"
        "File: " + asset["assetName"] + "\nSHA-256: " + asset["assetSha256"] + "\n"
        + en_delivery +
        "Complete applicable license scope and publication of this separate file require independent review.\n"
    ).encode("utf-8-sig")


def beta_information(version, webview2_mode="fixed", qt_source_mode="inline", qt_source_asset=None,
                     ffmpeg_source_mode="inline", ffmpeg_source_asset=None):
    require_preview_version(version)
    require(ffmpeg_source_mode in ("inline", "separate-asset"),
            "unknown FFmpeg source BETA notice mode")
    material_limitations = beta_material_limitations(webview2_mode, qt_source_mode)
    material_ru = "".join("• " + item["noticeRu"] + "\n" for item in material_limitations)
    material_en = "".join("• " + item["noticeEn"] + "\n" for item in material_limitations)
    evergreen_ru = ("• Для вкладки бизнеса и мини-приложений нужен Microsoft Edge WebView2 Evergreen Runtime. Если его нет, мастер установит Runtime через интернет; без сети нужен официальный автономный установщик Microsoft.\n"
                    if webview2_mode == "evergreen" else "")
    evergreen_en = ("Mini apps and the business tab need Microsoft Edge WebView2 Evergreen Runtime. If missing, Setup installs the Runtime online; offline use requires Microsoft's standalone installer.\n"
                    if webview2_mode == "evergreen" else "")
    asset_ru = asset_en = ""
    if qt_source_mode == "separate-asset":
        require(isinstance(qt_source_asset, dict)
                and qt_source_asset.get("manifestDistribution") == "public-beta-asset"
                and qt_source_asset.get("availabilityDraftIncluded") is False
                and isinstance(qt_source_asset.get("assetName"), str)
                and PurePosixPath(qt_source_asset["assetName"]).name == qt_source_asset["assetName"]
                and isinstance(qt_source_asset.get("assetSha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", qt_source_asset["assetSha256"]),
                "separate Qt source asset identity required for BETA notice")
        asset_ru = ("Исходники Qt 6.11.2 должны быть опубликованы отдельным файлом " + qt_source_asset["assetName"] +
                    " рядом с установщиком. SHA-256: " + qt_source_asset["assetSha256"] + ". Проверьте его наличие на странице релиза.\n")
        asset_en = ("Qt 6.11.2 sources must be published as a separate file " + qt_source_asset["assetName"] +
                    " beside the installer. SHA-256: " + qt_source_asset["assetSha256"] + ". Verify its presence on the release page.\n")
    if ffmpeg_source_mode == "separate-asset":
        require(isinstance(ffmpeg_source_asset, dict),
                "separate FFmpeg source asset identity required for BETA notice")
        validate_ffmpeg_source_distribution(ffmpeg_source_asset, ffmpeg_source_asset["subject"])
        require(ffmpeg_source_asset["subject"]["version"] == version,
                "FFmpeg source asset belongs to another BETA version")
        ffmpeg_ru = ("Архивы upstream FFmpeg, подписи и конфигурации сборок находятся в обязательном отдельном файле "
                     + ffmpeg_source_asset["assetName"] + " (SHA-256 " + ffmpeg_source_asset["assetSha256"] +
                     "). Проверьте его публикацию рядом с установщиком. Точное соответствие исходников DLL из MAX остаётся недоказанным.\n\n")
        ffmpeg_en = ("Upstream FFmpeg archives, signatures and build configurations are in the required separate file "
                     + ffmpeg_source_asset["assetName"] + " (SHA-256 " + ffmpeg_source_asset["assetSha256"] +
                     "). Check that it is published beside Setup. Exact source correspondence for the MAX DLL builds remains unproven.\n")
    else:
        require(ffmpeg_source_asset is None, "inline FFmpeg BETA notice claims an external asset")
        ffmpeg_ru = ("Архивы upstream FFmpeg и конфигурации сборок находятся в materials/FFmpeg. "
                     "Точное соответствие исходников DLL из MAX остаётся недоказанным.\n\n")
        ffmpeg_en = ("Upstream FFmpeg source archives and build configurations are under materials/FFmpeg. "
                     "Exact source correspondence for the MAX DLL builds remains unproven.\n")
    return (
        "ValMAX — публичная BETA-сборка\nВерсия: " + version + "\n\n"
        "Это рабочая тестовая версия. Проверен базовый сценарий установленного клиента; полная проверка всех функций ещё не завершена. Возможны ошибки.\n\n"
        "Известные ограничения:\n"
        "• Сохранение удалённых сообщений: доступны только сообщения, уже полученные клиентом после включения захвата. Новые записи с ключом порядка показывают исходное время; старые записи могут стоять приблизительно. Более ранние удаления восстановить нельзя.\n"
        "• Экспорт чата: история может быть неполной, поскольку сервер не подтвердил доступность всех старых сообщений. Фото, видео и голосовые выгружаются по мере доступности; отдельные вложения могут завершиться ошибкой. Перед использованием архива проверьте отчёт экспорта.\n"
        "• Режим призрака и защита уведомлений: готовность до самого первого фонового действия установленного клиента пока не подтверждена.\n"
        + evergreen_ru +
        "• Полная проверка всех функций, необходимая для стабильного релиза, ещё не завершена.\n\n"
        "Материалы сторонних компонентов — известные ограничения:\n" + material_ru + "\n"
        + ffmpeg_ru
        + asset_ru +
        "Папка аккаунтов MXMOD и Windows credentials не переносятся и не очищаются установщиком. Предыдущая установка сохраняется рядом как резервная копия; не запускайте её старый uninstaller.\n"
        "Ограничения и результаты проверок указаны в native-package.json.\n\n"
        "ValMAX — public BETA build\nVersion: " + version + "\n\n"
        "This is a working test release. Basic installed-client use was checked; the full feature regression is still pending and errors are possible.\n"
        "Deleted-message retention covers only messages received after capture was enabled. New records have original-order timestamps; older records may be approximate. Earlier deletions cannot be recovered.\n"
        "Chat export may be incomplete because the server has not confirmed all older history. Photos, videos and voice messages are downloaded when available; individual attachments may fail. Check the export result before relying on an archive.\n"
        "Ghost and notification protection before the first background event remains unverified. The full stable-release regression is pending.\n"
        + evergreen_en +
        "Third-party materials — known limitations:\n" + material_en + "\n"
        + ffmpeg_en
        + asset_en +
        "Setup does not migrate or clear MXMOD accounts or Windows credentials. The previous installation is preserved beside the new one; do not run its old uninstaller.\n"
        "See native-package.json for limitations and evidence.\n"
    ).encode("utf-8-sig")


def write_release_state(payload, manifest, report):
    digest = sha(payload / "native-package.json")
    require(manifest["distributionMode"] in ("user-release", "developer-staging", "local-preview", "public-beta"), "invalid native release marker mode")
    qt_source_mode = manifest.get("qtSourceMode", "inline")
    ffmpeg_source_mode = manifest.get("ffmpegSourceMode", "inline")
    require(qt_source_mode in ("inline", "separate-asset"), "unknown Qt source marker mode")
    require(ffmpeg_source_mode in ("inline", "separate-asset")
            and (ffmpeg_source_mode == "inline" or manifest["distributionMode"] == "public-beta"),
            "unknown or inapplicable FFmpeg source marker mode")
    if qt_source_mode == "separate-asset":
        asset = manifest.get("qtSourceDistribution")
        require(isinstance(asset, dict) and asset.get("mode") == "separate-asset"
                and asset == report.get("artifactValidation", {}).get("qtSourceAsset")
                and manifest.get("qtSourceAvailability") ==
                    {"path": QT_SOURCE_INFO, "sha256": sha(payload / QT_SOURCE_INFO)},
                "separate Qt source asset notice or byte binding changed")
    else:
        require(manifest.get("qtSourceDistribution") is None
                and manifest.get("qtSourceAvailability") is None,
                "inline Qt marker claims an external source asset")
    if ffmpeg_source_mode == "separate-asset":
        ffmpeg_asset = manifest.get("ffmpegSourceDistribution")
        validate_ffmpeg_source_distribution(ffmpeg_asset, report["subject"])
        require(ffmpeg_asset == report.get("betaMaterials", {}).get("ffmpegSourceDistribution")
                and manifest.get("ffmpegSourceAvailability") ==
                    {"path": FFMPEG_SOURCE_INFO, "sha256": sha(payload / FFMPEG_SOURCE_INFO)}
                and not (payload / "materials/FFmpeg").exists()
                and not (payload / "source-assets").exists(),
                "separate FFmpeg source notice or byte binding changed")
        ffmpeg_asset_path = payload.parent / "source-assets" / ffmpeg_asset["assetName"]
        verify_ffmpeg_source_asset(ffmpeg_asset_path, ffmpeg_asset, manifest["betaMaterials"])
    else:
        require(manifest.get("ffmpegSourceDistribution") is None
                and manifest.get("ffmpegSourceAvailability") is None,
                "inline FFmpeg package claims an external source asset")
    marker = ('; Generated by the native packager; verified installed functionality is separate from compilation.\n'
              '#define NativeDistributionMode "' + manifest["distributionMode"] + '"\n'
              '#define NativeWebView2Mode "' + manifest.get("webview2Mode", "fixed") + '"\n'
              '#define NativeQtSourceMode "' + qt_source_mode + '"\n'
              '#define NativeFFmpegSourceMode "' + ffmpeg_source_mode + '"\n'
              '#define NativeReleaseEligible ' + str(int(report["releaseEligible"] is True)) + '\n'
              '#define NativeReleaseBlockerCount ' + str(len(report["blockers"])) + '\n'
              '#define NativePackageManifestSha256 "' + digest + '"\n')
    if manifest.get("webview2Mode", "fixed") == "evergreen":
        bootstrapper = manifest.get("webview2Bootstrapper")
        require(bootstrapper == webview2_bootstrapper_pin(),
                "Evergreen bootstrapper release pin changed before installer compilation")
        marker += ('#define NativeWebView2BootstrapperSha256 "' + bootstrapper["sha256"] + '"\n')
    else:
        require(manifest.get("webview2Bootstrapper") is None,
                "Fixed WebView2 package claims an Evergreen bootstrapper")
    if qt_source_mode == "separate-asset":
        marker += ('#define NativeQtSourceAssetSha256 "' + asset["assetSha256"] + '"\n'
                   '#define NativeQtSourceInfoSha256 "' + manifest["qtSourceAvailability"]["sha256"] + '"\n')
    if ffmpeg_source_mode == "separate-asset":
        marker += ('#define NativeFFmpegSourceAssetSha256 "' + ffmpeg_asset["assetSha256"] + '"\n'
                   '#define NativeFFmpegSourceInfoSha256 "' +
                   manifest["ffmpegSourceAvailability"]["sha256"] + '"\n')
    if manifest["distributionMode"] == "local-preview":
        require_distribution("local-preview", report)
        require(manifest.get("localPreview") is True and manifest.get("previewInformation") == {
            "path": LOCAL_PREVIEW_INFO, "sha256": sha(payload / LOCAL_PREVIEW_INFO)}, "preview information changed")
        require_preview_version(manifest["version"])
        marker += ('#define NativePreviewVersion "' + manifest["version"] + '"\n'
                   '#define NativePreviewInfoSha256 "' + manifest["previewInformation"]["sha256"] + '"\n')
    elif manifest["distributionMode"] == "public-beta":
        require_distribution("public-beta", report)
        require(manifest.get("publicBeta") is True and manifest.get("betaCoreTested") is True
                and manifest.get("featureRuntimeTested") is False
                and manifest.get("wholeClientMaterialsVerified") is False
                and manifest.get("materialsDocumentedForBeta") is True
                and manifest.get("maxRightsAttested") is True
                and manifest.get("knownLimitations") == report["knownLimitations"]
                and manifest.get("installedSmokeSha256") == report["installedSmokeSha256"]
                and manifest.get("betaMaterials") == report["betaMaterials"]
                and manifest.get("betaMaterialsInformation") == {
                    "path": PUBLIC_BETA_MATERIAL_INFO, "sha256": sha(payload / PUBLIC_BETA_MATERIAL_INFO)}
                and manifest.get("betaInformation") == {
                    "path": PUBLIC_BETA_INFO, "sha256": sha(payload / PUBLIC_BETA_INFO)},
                "public BETA evidence or information changed")
        require(read_json(payload / PUBLIC_BETA_MATERIAL_INFO) == manifest["betaMaterials"],
                "public BETA materials summary differs from manifest")
        if ffmpeg_source_mode == "inline":
            for row in (*manifest["betaMaterials"]["sourceBundles"],
                        *(item["signature"] for item in manifest["betaMaterials"]["sourceBundles"]),
                        manifest["betaMaterials"]["buildConfigurations"]):
                target = payload / row["path"]
                require(ordinary(target).st_size == row["size"] and sha(target) == row["sha256"],
                        "public BETA source or configuration changed before installer compilation")
        require_preview_version(manifest["version"])
        marker += ('#define NativeBetaVersion "' + manifest["version"] + '"\n'
                   '#define NativeBetaCoreTested 1\n'
                   '#define NativeBetaInfoSha256 "' + manifest["betaInformation"]["sha256"] + '"\n'
                   '#define NativeBetaMaterialsInfoSha256 "' + manifest["betaMaterialsInformation"]["sha256"] + '"\n'
                   '#define NativeBetaSource713Sha256 "' + manifest["betaMaterials"]["sourceBundles"][0]["sha256"] + '"\n'
                   '#define NativeBetaSource715Sha256 "' + manifest["betaMaterials"]["sourceBundles"][1]["sha256"] + '"\n'
                   '#define NativeBetaSource713SignatureSha256 "' + manifest["betaMaterials"]["sourceBundles"][0]["signature"]["sha256"] + '"\n'
                   '#define NativeBetaSource715SignatureSha256 "' + manifest["betaMaterials"]["sourceBundles"][1]["signature"]["sha256"] + '"\n'
                   '#define NativeBetaBuildConfigSha256 "' + manifest["betaMaterials"]["buildConfigurations"]["sha256"] + '"\n')
    elif manifest["distributionMode"] == "user-release":
        require(manifest.get("maxRightsAttested") is True and report.get("maxRightsAttested") is True,
                "user release must bind publisher rights attestation")
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
    parser.add_argument("--evergreen-bootstrapper", type=Path,
                        help="Pinned, Microsoft-signed online Evergreen bootstrapper; required for Evergreen packaging.")
    parser.add_argument("--distribution", choices=("user-release", "developer-staging", "local-preview", "public-beta"), default="user-release")
    parser.add_argument("--release-evidence", type=Path, help="Full feature manifest for user-release, installed core smoke receipt for public-beta.")
    parser.add_argument("--beta-materials-evidence", type=Path,
                        help="Exact assembled-materials and FFmpeg source/config bundle receipt for public-beta.")
    parser.add_argument("--ffmpeg-source-mode", choices=("inline", "separate-asset"), default="inline",
                        help="Keep FFmpeg source materials in Setup, or emit a required separate public-BETA ZIP asset.")
    parser.add_argument("--max-rights-attested", action="store_true",
                        help="Publisher attests MAX rights holder permission for this public distribution; no confidential document is stored.")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--preflight-report", type=Path)
    args = parser.parse_args()
    require(args.version is None or re.fullmatch(r"\d+\.\d+\.\d+(?:[.-][A-Za-z0-9.]+)?", args.version), "invalid product version")
    require(args.preflight_only or args.version is not None, "product version is required for packaging")
    if args.distribution == "local-preview":
        require_preview_version(args.version)
        require(args.release_evidence is None, "local preview does not consume full-release evidence")
    if args.distribution == "public-beta":
        require_preview_version(args.version)
    require(not args.max_rights_attested or args.distribution in ("public-beta", "user-release"),
            "MAX rights attestation is only accepted for public distribution")
    require(args.beta_materials_evidence is None or args.distribution == "public-beta",
            "BETA materials evidence is only accepted for public-beta")
    require(args.ffmpeg_source_mode == "inline" or args.distribution == "public-beta",
            "separate FFmpeg source asset is only accepted for public-beta")
    require(not args.preflight_only or args.iscc is None, "preflight never compiles an installer")
    require(args.preflight_report is not None or args.output is not None, "preflight report or output directory required")
    require(args.preflight_only or args.output is not None, "packaging output required")
    inputs, artifact_error = None, None
    if all((args.runtime, args.qml_build, args.launcher_proof)):
        try:
            inputs = packaging_inputs(args.runtime, args.qml_build, args.launcher_proof, preflight_only=args.preflight_only)
        except (ValueError, OSError, KeyError, TypeError, SyntaxError, zipfile.BadZipFile) as error:
            artifact_error = str(error)
    else:
        artifact_error = "Assembled runtime, generated QML and matching launcher receipt are required."
    artifact_identity = None if inputs is None else {"launcherSha256": inputs["launcherHash"],
        "runtimeInventorySha256": inventory_digest(inputs["rows"]), "qmlProofSha256": inputs["qmlResult"]["proofSha256"]}
    subject = None if artifact_identity is None or args.version is None else {"version": args.version, **artifact_identity}
    legacy_resources = [] if inputs is None else inputs["qmlResult"].get("legacyResources", [])
    beta_plan, beta_materials_error = None, None
    if args.distribution == "public-beta":
        try:
            require(inputs is not None and subject is not None, "exact assembled BETA subject is unavailable")
            beta_plan = beta_materials_documentation(
                inputs.get("materialBinding"), inputs["rows"],
                inputs["hookTargetIdentity"]["assemblyReceiptSha256"],
                subject, args.beta_materials_evidence,
                inputs.get("webview2Mode", "fixed"), inputs.get("qtSourceMode", "inline"),
                args.ffmpeg_source_mode)
        except (ValueError, OSError, KeyError, TypeError, tarfile.TarError, zipfile.BadZipFile) as error:
            beta_materials_error = str(error)
    report = release_preflight(subject, args.release_evidence, args.distribution,
                               artifact_identity=artifact_identity,
                               materials=None if inputs is None or inputs.get("materialBinding") is None
                               else inputs["materialBinding"]["status"],
                               artifact_verified=inputs is not None,
                               qml_compatibility_verified=inputs is not None and not legacy_resources,
                               max_rights_attested=args.max_rights_attested,
                               beta_materials=None if beta_plan is None else beta_plan["summary"],
                               beta_materials_error=beta_materials_error,
                               webview2_mode="fixed" if inputs is None else inputs.get("webview2Mode", "fixed"),
                               qt_source_mode="inline" if inputs is None else inputs.get("qtSourceMode", "inline"),
                               ffmpeg_source_mode=args.ffmpeg_source_mode,
                               qml_proof_path=None if inputs is None else inputs["qml"] / "native-qml-proof.json")
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
    report["artifactValidation"]["qtSourceAsset"] = (None if inputs is None or inputs.get("qtSourceAsset") is None
                                                        else inputs["qtSourceAsset"]["receipt"])
    report["artifactValidation"]["ffmpegSourceAsset"] = (None if beta_plan is None or
                                                            beta_plan.get("ffmpegSourceAsset") is None
                                                            else beta_plan["ffmpegSourceAsset"]["receipt"])
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
    webview2_mode = inputs.get("webview2Mode", "fixed")
    if webview2_mode == "evergreen":
        require(args.evergreen_bootstrapper is not None,
                "Evergreen packaging requires --evergreen-bootstrapper (pinned Microsoft EXE)")
        bootstrapper_source = owned(args.evergreen_bootstrapper)
        bootstrapper_pin = verify_webview2_bootstrapper(bootstrapper_source)
    else:
        require(args.evergreen_bootstrapper is None,
                "Fixed WebView2 package must not include an Evergreen bootstrapper")
        bootstrapper_source = bootstrapper_pin = None
    runtime, qml, rows, launcher = inputs["runtime"], inputs["qml"], inputs["rows"], inputs["launcher"]
    launcher_hash, qml_result, closure = inputs["launcherHash"], inputs["qmlResult"], inputs["closure"]
    ancillary = ancillary_inventory()
    required_bytes = sum(row["size"] for row in rows) + launcher.stat().st_size + sum(row["size"] for row in ancillary)
    if inputs.get("qtSourceAsset") is not None:
        required_bytes += inputs["qtSourceAsset"]["receipt"]["assetBytes"]
    if bootstrapper_pin is not None:
        required_bytes += bootstrapper_pin["size"]
    if beta_plan is not None:
        if args.ffmpeg_source_mode == "separate-asset":
            required_bytes += beta_plan["ffmpegSourceAsset"]["receipt"]["assetBytes"]
        else:
            required_bytes += sum(row["size"] for row in beta_plan["summary"]["sourceBundles"])
            required_bytes += sum(row["signature"]["size"] for row in beta_plan["summary"]["sourceBundles"])
            required_bytes += beta_plan["summary"]["buildConfigurations"]["size"]
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
    webview2_mode, qt_source_mode = inputs.get("webview2Mode", "fixed"), inputs.get("qtSourceMode", "inline")
    require(pins.inventory(payload / "runtime", webview2_mode, qt_source_mode) == rows
            and pins.inventory(runtime, webview2_mode, qt_source_mode) == rows, "runtime changed during packaging")
    qt_asset = inputs.get("qtSourceAsset")
    qt_asset_copy = qt_info = None
    bootstrapper_copy = None
    if bootstrapper_pin is not None:
        prerequisite_folder = output / "installer-prerequisites"
        prerequisite_folder.mkdir()
        target = prerequisite_folder / bootstrapper_pin["name"]
        require(not target.exists() and sha(bootstrapper_source) == bootstrapper_pin["sha256"],
                "Evergreen bootstrapper changed before packaging")
        shutil.copyfile(bootstrapper_source, target)
        require(ordinary(target).st_size == bootstrapper_pin["size"]
                and sha(target) == bootstrapper_pin["sha256"],
                "Evergreen bootstrapper copy differs from release pin")
        bootstrapper_copy = {"path": str(target), "size": bootstrapper_pin["size"],
                             "sha256": bootstrapper_pin["sha256"],
                             "signerThumbprint": bootstrapper_pin["signerThumbprint"]}
    if qt_asset is not None:
        asset = qt_asset["receipt"]
        asset_folder = output / "source-assets"
        asset_folder.mkdir()
        target = asset_folder / asset["assetName"]
        require(not target.exists() and sha(qt_asset["archive"]) == asset["assetSha256"],
                "Qt source asset changed before packaging")
        shutil.copyfile(qt_asset["archive"], target)
        require(ordinary(target).st_size == asset["assetBytes"]
                and sha(target) == asset["assetSha256"]
                and sha(qt_asset["archive"]) == asset["assetSha256"],
                "separate Qt source asset copy differs")
        qt_asset_copy = {"path": str(target), "name": asset["assetName"],
                         "size": asset["assetBytes"], "sha256": asset["assetSha256"]}
        info = payload / QT_SOURCE_INFO
        info.write_bytes(qt_source_availability(asset))
        qt_info = {"path": QT_SOURCE_INFO, "sha256": sha(info)}
    copy_ancillary(payload, ancillary)
    preview = args.distribution == "local-preview"
    public_beta = args.distribution == "public-beta"
    preview_info = beta_info = None
    beta_material_info = copy_beta_materials(payload, beta_plan, output) if public_beta else None
    ffmpeg_asset_copy = None if beta_plan is None else beta_plan.get("ffmpegSourceAssetCopy")
    ffmpeg_info = None if beta_plan is None else beta_plan.get("ffmpegSourceAvailability")
    if preview:
        (payload / LOCAL_PREVIEW_INFO).write_bytes(preview_information(
            args.version, webview2_mode, qt_source_mode,
            None if qt_asset is None else qt_asset["receipt"]))
        preview_info = {"path": LOCAL_PREVIEW_INFO, "sha256": sha(payload / LOCAL_PREVIEW_INFO)}
    if public_beta:
        (payload / PUBLIC_BETA_INFO).write_bytes(beta_information(
            args.version, webview2_mode, qt_source_mode,
            None if qt_asset is None else qt_asset["receipt"],
            args.ffmpeg_source_mode,
            None if beta_plan is None or beta_plan.get("ffmpegSourceAsset") is None
            else beta_plan["ffmpegSourceAsset"]["receipt"]))
        beta_info = {"path": PUBLIC_BETA_INFO, "sha256": sha(payload / PUBLIC_BETA_INFO)}
    manifest = {"schema": 1, "nativeOnly": True, "version": args.version, "webview2Mode": webview2_mode,
                "webview2Bootstrapper": bootstrapper_pin,
                "qtSourceMode": qt_source_mode,
                "qtSourceDistribution": None if qt_asset is None else qt_asset["receipt"],
                "qtSourceAvailability": qt_info,
                "ffmpegSourceMode": args.ffmpeg_source_mode,
                "ffmpegSourceDistribution": None if beta_plan is None or
                    beta_plan.get("ffmpegSourceAsset") is None else beta_plan["ffmpegSourceAsset"]["receipt"],
                "ffmpegSourceAvailability": ffmpeg_info,
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
    if public_beta:
        manifest.update(publicBeta=True, betaCoreTested=report["betaCoreTested"],
                        materialsDocumentedForBeta=report["materialsDocumentedForBeta"],
                        betaMaterials=report["betaMaterials"], betaMaterialsInformation=beta_material_info,
                        installedSmokeSha256=report["installedSmokeSha256"],
                        maxRightsAttested=report["maxRightsAttested"],
                        knownLimitations=report["knownLimitations"], betaInformation=beta_info)
    elif args.distribution == "user-release":
        manifest["maxRightsAttested"] = report["maxRightsAttested"]
    (payload / "native-package.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    receipt = {"schema": 1, "packaged": True, "nativeOnly": True, "installerCompiled": False,
               "webview2Mode": webview2_mode, "qtSourceMode": qt_source_mode,
               "ffmpegSourceMode": args.ffmpeg_source_mode,
               "webview2Bootstrapper": bootstrapper_copy,
               "qtSourceAsset": qt_asset_copy,
               "qtSourceDistribution": None if qt_asset is None else qt_asset["receipt"],
               "ffmpegSourceAsset": ffmpeg_asset_copy,
               "ffmpegSourceDistribution": manifest["ffmpegSourceDistribution"],
               "ffmpegSourceAvailability": ffmpeg_info,
               "payload": str(payload), "manifestSha256": sha(payload / "native-package.json"),
               "installedClientChanged": False, "featureRuntimeTested": report["featureRuntimeTested"],
               "wholeClientMaterialsVerified": report["wholeClientMaterialsVerified"],
               "materialsReceiptSha256": report["artifactValidation"]["materialsReceiptSha256"],
               "ancillaryFiles": ancillary,
               "distributionMode": args.distribution, "releaseEligible": report["releaseEligible"],
               "preflightReport": str(report_path), "preflightReportSha256": sha(report_path)}
    if preview:
        receipt.update(localPreview=True, previewInformation=preview_info)
    if public_beta:
        receipt.update(publicBeta=True, betaCoreTested=report["betaCoreTested"],
                       materialsDocumentedForBeta=report["materialsDocumentedForBeta"],
                       betaMaterials=report["betaMaterials"], betaMaterialsInformation=beta_material_info,
                       installedSmokeSha256=report["installedSmokeSha256"],
                       maxRightsAttested=report["maxRightsAttested"],
                       knownLimitations=report["knownLimitations"], betaInformation=beta_info)
    elif args.distribution == "user-release":
        receipt["maxRightsAttested"] = report["maxRightsAttested"]
    receipt.update(write_release_state(payload, manifest, report))
    if args.iscc:
        ordinary(args.iscc)
        require(sha(launcher) == launcher_hash and sha(payload / "ValMAX.exe") == launcher_hash, "launcher changed before Inno compilation")
        command = [str(args.iscc), "/Qp", "/DPayload=" + str(payload), "/DOutput=" + str(output / "release"),
                   "/DProductVersion=" + args.version, str(TEMPLATE)]
        if qt_asset_copy is not None:
            require(sha(Path(qt_asset_copy["path"])) == qt_asset_copy["sha256"],
                    "separate Qt source ZIP changed before Inno compilation")
            command.insert(-1, "/DQtSourceAsset=" + qt_asset_copy["path"])
        if ffmpeg_asset_copy is not None:
            verify_ffmpeg_source_asset(Path(ffmpeg_asset_copy["path"]),
                                       manifest["ffmpegSourceDistribution"], manifest["betaMaterials"])
            command.insert(-1, "/DFFmpegSourceAsset=" + ffmpeg_asset_copy["path"])
        if bootstrapper_copy is not None:
            require(sha(Path(bootstrapper_copy["path"])) == bootstrapper_pin["sha256"],
                    "Evergreen bootstrapper changed before Inno compilation")
            command.insert(-1, "/DWebView2Bootstrapper=" + bootstrapper_copy["path"])
        with (output / "inno.log").open("xb") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1800,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        require(result.returncode == 0, "Inno compilation failed")
        setup = output / "release" / ("ValMAX-" + args.version + "-Setup.exe")
        require(setup.is_file(), "installer missing")
        receipt.update(installerCompiled=True, installer=str(setup), installerSha256=sha(setup))
    if qt_asset_copy is not None:
        require(ordinary(Path(qt_asset_copy["path"])).st_size == qt_asset_copy["size"]
                and sha(Path(qt_asset_copy["path"])) == qt_asset_copy["sha256"]
                and sha(qt_asset["archive"]) == qt_asset_copy["sha256"]
                and sha(payload / QT_SOURCE_INFO) == qt_info["sha256"],
                "separate Qt source asset or availability notice changed before final receipt")
    if ffmpeg_asset_copy is not None:
        verify_ffmpeg_source_asset(Path(ffmpeg_asset_copy["path"]),
                                   manifest["ffmpegSourceDistribution"], manifest["betaMaterials"])
        require(sha(payload / FFMPEG_SOURCE_INFO) == ffmpeg_info["sha256"],
                "separate FFmpeg source availability notice changed before final receipt")
    if bootstrapper_copy is not None:
        require(ordinary(Path(bootstrapper_copy["path"])).st_size == bootstrapper_pin["size"]
                and sha(Path(bootstrapper_copy["path"])) == bootstrapper_pin["sha256"]
                and sha(bootstrapper_source) == bootstrapper_pin["sha256"],
                "Evergreen bootstrapper changed before final receipt")
    (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
