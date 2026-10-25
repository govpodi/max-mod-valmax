"""Bounded read-only distribution-material inventory; no legal release opinion.

Frozen runtime is never modified. Small original texts and current official
Microsoft Fixed Version terms are retained in a new D materials directory.
Existing large source/runtime archives are referenced by their exact hashes,
not copied or downloaded again. No MAX/installer/browser/target is run.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import urllib.request
import zipfile

import pefile
from assemble_native_runtime import inventory, ordinary, read_json, relative, require, sha

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "analysis/native-migration-v1"
SMALL_LIMIT = 2 << 20


def save_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as target: target.write(data)
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class PlainText(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.parts = []
    def handle_data(self, data): self.parts.append(data)
    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "h1", "h2", "h3", "br"}: self.parts.append("\n")


def official(url, destination):
    require(url.startswith(("https://developer.microsoft.com/", "https://learn.microsoft.com/", "https://edgecdn-embza6g8cacagcbn.z01.azurefd.net/")), "official Microsoft material source required")
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(SMALL_LIMIT + 1)
        final = response.url
        content_type = response.headers.get("Content-Type")
        modified = response.headers.get("Last-Modified")
    require(len(data) <= SMALL_LIMIT, "small legal/provenance text limit")
    return data, {**save_new(destination, data), "sourceUrl": url, "finalUrl": final, "contentType": content_type, "lastModified": modified}


def pe_version(path):
    image = pefile.PE(str(path), fast_load=True)
    try:
        image.parse_data_directories(directories=[2])
        values = getattr(image, "VS_FIXEDFILEINFO", [])
        if not values: return None
        info = values[0]
        return ".".join(map(str, (info.FileVersionMS >> 16, info.FileVersionMS & 65535,
                                   info.FileVersionLS >> 16, info.FileVersionLS & 65535)))
    finally:
        image.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-proof", type=Path, default=REPORTS / "native-developer-staging-final-20261021.json")
    parser.add_argument("--gum-proof", type=Path, default=Path("D:/ValMAXBuild/materials/frida-gum-audit-20261021/frida-gum-materials-inventory.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    require(args.output.is_absolute() and args.output.resolve().is_relative_to(Path("D:/ValMAXBuild/materials").resolve())
            and not args.output.exists(), "new owned materials directory required")
    require(args.report.parent.resolve() == REPORTS.resolve() and not args.report.exists(), "new audit report required")
    staging = read_json(args.staging_proof)
    runtime = Path(staging["runtime"])
    require(inventory(runtime) == staging["inventory"], "frozen runtime differs from receipt")
    output = args.output
    output.mkdir(parents=True)
    collected, external, by_digest = [], [], {}
    legal_rows = [row for row in staging["inventory"] if row["path"].startswith(("mod/licenses/", "transfer/licenses/"))
                  or (row["path"].startswith("webview2/") and re.search(r"license|notice|terms|credits", row["path"], re.I))]
    for row in legal_rows:
        source = runtime / relative(row["path"])
        require(sha(source) == row["sha256"], "staged material changed")
        if row["bytes"] > SMALL_LIMIT:
            external.append({**row, "source": str(source), "reason": "Retained in frozen runtime; not duplicated in bounded small-material collection."})
            continue
        if row["sha256"] in by_digest:
            collected.append({**row, "collectedPath": by_digest[row["sha256"]], "source": str(source), "deduplicated": True})
        else:
            copied = save_new(output / "existing" / relative(row["path"]), source.read_bytes())
            require(copied["sha256"] == row["sha256"], "material copy mismatch")
            by_digest[row["sha256"]] = copied["path"]
            collected.append({**row, "collectedPath": copied["path"], "source": str(source), "deduplicated": False})
    zip_materials = []
    for name in ("webview-third-party-notices.zip", "qtwebview-6.11.2-valmax-source.zip"):
        source = runtime / "mod/licenses/WebView" / name
        with zipfile.ZipFile(source) as archive:
            require(archive.testzip() is None, "existing WebView archive damaged")
            for member in archive.infolist():
                if member.is_dir() or not re.search(r"license|copying|notice|BUILD\.txt$", member.filename, re.I): continue
                relative(member.filename)
                require(member.file_size <= SMALL_LIMIT, "archive legal text bound")
                data = archive.read(member)
                zip_materials.append({**save_new(output / "existing-archive-texts" / name.removesuffix(".zip") / relative(member.filename), data),
                                      "archive": str(source), "archiveSha256": sha(source), "archiveMember": member.filename})
    # Original SDK license/NOTICE identity is separately bound to its exact NuGet.
    sdk_root = Path("C:/ValMAXBuild/deps/webview2-build")
    sdk_inputs = read_json(sdk_root / "sources.json")
    sdk_package = next(row for row in sdk_inputs["inputs"] if row["name"].endswith(".nupkg"))
    require(sha(sdk_root / sdk_package["name"]) == sdk_package["sha256"], "SDK input archive changed")
    sdk_documents = []
    for name in ("LICENSE.txt", "NOTICE.txt"):
        path = sdk_root / "microsoft-sdk" / name
        sdk_documents.append({"name": name, "source": str(path), "sha256": sha(path), "bytes": path.stat().st_size,
                              "nugetUrl": sdk_package["url"], "nugetSha256": sdk_package["sha256"], "version": "1.0.4258.31"})
    # Read-only MSI tables, no installation/custom actions. Preserve the actual
    # placeholder instead of inventing a MAX license or using ValMAX MIT.
    layout = read_json(Path("D:/CodexLabs/max-mod-26.35.1/analysis/restore_layout_report.json"))
    msi_path = Path(layout["msi"])
    require(sha(msi_path) == layout["msi_sha256"], "baseline MSI changed")
    module_path = Path("D:/CodexLabs/max-mod-26.35.1/modkit/restore_layout.py")
    spec = importlib.util.spec_from_file_location("material_readonly_msi", module_path)
    msi_reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(msi_reader)
    database = msi_reader.MsiDatabase(msi_path)
    try:
        controls = database.query("Control", ["Dialog_", "Control", "Text"])
        license_control = [row for row in controls if row["Dialog_"] == "LicenseAgreementDlg" and row["Control"] == "LicenseText"]
        require(len(license_control) == 1, "one pinned MSI license control required")
        rtf = license_control[0]["Text"].encode("utf8")
        placeholder = save_new(output / "MAX/installer-LicenseText-PLACEHOLDER.rtf", rtf)
        binary_names = [row["Name"] for row in database.query("Binary", ["Name"])]
        stream_names = [row["Name"] for row in database.query("_Streams", ["Name"])]
    finally:
        database.close()
    max_materials = {"baselineProductVersion": layout["product_version"], "msi": str(msi_path), "msiSha256": layout["msi_sha256"],
                     "readerSource": str(module_path), "readerSourceSha256": sha(module_path),
                     "licenseControl": {"table": "Control", "dialog": "LicenseAgreementDlg", "control": "LicenseText", "text": placeholder},
                     "licenseControlIsPlaceholder": b"Some license here" in rtf,
                     "binaryTableNames": binary_names, "streamNames": stream_names,
                     "runtimeStandaloneNoticeFiles": [], "actualMaxLegalTextIdentified": False,
                     "scope": "Observed exact pinned baseline MSI/resources, not an inference of licensing terms or rights."}
    # Obtain the current official fixed-version dialog text, not the unrelated
    # Microsoft website Terms of Use, Evergreen terms or SDK license.
    webview = output / "Microsoft-WebView2-Fixed"
    page_url = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/"
    _, page = official(page_url, webview / "download-page.html")
    component_url = "https://edgecdn-embza6g8cacagcbn.z01.azurefd.net/shared/edgeweb/_nuxt/CNXrhzNQ.js"
    component_bytes, component = official(component_url, webview / "download-dialog-component.js")
    require(b"/microsoft-edge/api/eula/webview2?locale=" in component_bytes and b"&fixed=true" in component_bytes, "official dialog endpoint provenance changed")
    api_url = "https://developer.microsoft.com/microsoft-edge/api/eula/webview2?locale=en-us&fixed=true"
    api_body, api = official(api_url, webview / "fixed-license-response.json")
    data = json.loads(api_body)
    require(data.get("locale") == "en-us" and isinstance(data.get("fixedHtml"), str)
            and "MICROSOFT EDGE WEBVIEW2 RUNTIME (FIXED VERSION)" in data["fixedHtml"], "wrong Microsoft license product/mode")
    fixed_html = save_new(webview / "Microsoft-Edge-WebView2-Runtime-Fixed-Version.html", data["fixedHtml"].encode("utf8"))
    plain = PlainText(); plain.feed(data["fixedHtml"])
    text = re.sub(r"[ \t]+", " ", "".join(plain.parts)); text = re.sub(r"\n\s*\n+", "\n\n", text).strip() + "\n"
    fixed_text = save_new(webview / "Microsoft-Edge-WebView2-Runtime-Fixed-Version.txt", text.encode("utf8"))
    _, privacy_reference = official("https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/data-privacy", webview / "official-license-dialog-reference.html")
    _, distribution_reference = official("https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution", webview / "official-distribution-reference.html")
    fixed_materials = {"runtimeVersion": staging["fixedWebView2Version"], "mode": "Fixed Version", "sdk": sdk_documents,
                       "downloadPage": page, "officialDialogComponent": component, "officialDialogResponse": api,
                       "originalHtml": fixed_html, "convenienceText": fixed_text,
                       "officialReferences": [privacy_reference, distribution_reference],
                       "termsAccepted": False, "historicalEditionForRuntime155Confirmed": False,
                       "scope": "Current unversioned official Fixed Version Runtime legal dialog captured on checkedAt. No historical-edition or legal sufficiency conclusion."}
    gum = read_json(args.gum_proof)
    gum_copy = save_new(output / "Frida-Gum/frida-gum-materials-inventory.json", args.gum_proof.read_bytes())
    for row in gum["officialLegalTexts"]:
        source = Path(row["path"])
        require(sha(source) == row["sha256"], "Gum text changed")
        save_new(output / "Frida-Gum" / source.name, source.read_bytes())
    qt_root = [row for row in staging["inventory"] if row["path"].startswith("mod/Qt6") and row["path"].endswith(".dll")]
    unavailable_groups = {"qtlottie": "Qt6Lottie", "qtmultimedia": "Qt6Multimedia", "qtpositioning": "Qt6Positioning", "qtsvg": "Qt6Svg", "qtwebchannel": "Qt6WebChannel"}
    missing_qt_materials = [{"moduleFromBinaryName": module, "runtimeImages": [r for r in qt_root if Path(r["path"]).name.startswith(prefix)],
                             "status": "matching module source/notice provenance not established by the shipped materials",
                             "officialModuleCatalog": "https://download.qt.io/archive/qt/6.11/6.11.2/submodules/"}
                            for module, prefix in unavailable_groups.items()]
    vc_provenance = read_json(runtime / "transfer/licenses/Microsoft/provenance.json")
    helper_vc = {r["path"].lower(): r for r in vc_provenance["runtimeFiles"]}
    vc_runtime = [{**row, "fileVersion": pe_version(runtime / relative(row["path"])),
                   "matchesHelperVcArchiveIdentity": helper_vc.get(Path(row["path"]).name.lower(), {}).get("sha256") == row["sha256"]}
                  for row in staging["inventory"] if row["path"].startswith("mod/") and "/" not in row["path"][4:]
                  and Path(row["path"]).name.lower().startswith(("msvcp", "vcruntime", "concrt")) and row["path"].endswith(".dll")]
    other_candidates = [row for row in staging["inventory"] if row["path"].startswith("mod/") and "/" not in row["path"][4:]
                        and re.search(r"^(?:avcodec-|avformat-|avutil-|swresample-|libcrypto-|libssl-|WinSparkle)", Path(row["path"]).name, re.I)]
    gaps = [
        {"id": "max_legal_text", "status": "unknown", "detail": "No standalone MAX notice/license in baseline; exact MSI license field is a placeholder. Actual MAX terms/redistribution materials have not been identified."},
        {"id": "remaining_qt_module_materials", "status": "not_established", "detail": "Matching source/license/third-party provenance not established for the explicitly listed Lottie/Multimedia/Positioning/Svg/WebChannel images."},
        {"id": "baseline_third_party_materials", "status": "not_established", "detail": "The listed original MAX FFmpeg/OpenSSL/WinSparkle candidates have no matching full-version materials manifest; unrelated Qt/helper texts are not substituted."},
        {"id": "baseline_vc_archive_binding", "status": "not_established", "detail": "VC license documents are present, but the exact helper VC archive hashes do not bind all main MAX CRT DLLs. Main CRT versions/hashes are listed separately."},
        {"id": "webview2_historical_edition", "status": "unknown", "detail": "Current official Fixed Version terms collected separately; historical edition applicable when version155.0.4283.60 was obtained is not established. Complete embedded credits text was not extracted by running the browser."},
        {"id": "gum_full_transitive_materials", "status": "unknown", "detail": "Tagged COPYING is collected separately. Local devkit lacks notices; tagged COPYING.LIB unavailable; full transitive material closure and direct privacy-module applicability remain unestablished."},
    ]
    input_refs = {str(args.staging_proof): sha(args.staging_proof), str(args.gum_proof): sha(args.gum_proof),
                  str(sdk_root / "sources.json"): sha(sdk_root / "sources.json")}
    result = {"schema": 1, "checkedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "readOnlyRuntimeAudit": True, "runtime": str(runtime), "runtimeInventorySha256": staging["runtimeInventorySha256"],
              "inputReceipts": input_refs, "stagedMaterialFiles": legal_rows, "stagedMaterialFileCount": len(legal_rows),
              "stagedMaterialBytes": sum(r["bytes"] for r in legal_rows), "smallCopies": collected, "largeExistingReferences": external,
              "webViewZipTexts": zip_materials, "max": max_materials, "microsoftWebView2": fixed_materials,
              "gumProofCopy": gum_copy, "gumUnknownMaterials": gum["unknownMaterials"],
              "qtModulesWithFullSourceArchivesPresent": ["qtbase", "qtdeclarative", "qtserialport", "qtwebengine", "qtwebview (modified backend source zip)"],
              "qtRootBinaryInventory": qt_root, "qtMaterialsNotEstablished": missing_qt_materials,
              "mainVcRuntime": vc_runtime, "otherBaselineBinaryCandidates": other_candidates, "gaps": gaps,
              "legalReleaseOpinion": False, "distributionReadyAssessed": False, "runtimeWritten": False,
              "targetsExecuted": False, "newRuntimeOrLargeSourceDownloads": False, "toolSourceSha256": sha(Path(__file__)),
              "scope": "Exact observed material inventory/provenance and explicit unknowns only; not permission to release or a complete legal compliance assessment."}
    for source, digest in input_refs.items(): require(sha(Path(source)) == digest, "audit input receipt changed")
    manifest_path = output / "materials-audit.json"
    save_new(manifest_path, json.dumps(result, indent=2).encode("utf8") + b"\n")
    save_new(args.report, json.dumps(result, indent=2).encode("utf8") + b"\n")
    print(json.dumps({"verifiedInventory": True, "materials": str(output), "stagedMaterialFiles": len(legal_rows),
                      "uniqueSmallCopies": len(by_digest), "newMicrosoftFixedTermsSha256": fixed_html["sha256"],
                      "gapIds": [r["id"] for r in gaps], "legalReleaseOpinion": False}))


if __name__ == "__main__": main()
