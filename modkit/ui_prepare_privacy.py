"""Prepare a version-pinned native QML privacy panel without editing MAX binaries.

Only writes source, patch specifications and static evidence. The optional
ui_resource_patch helper prepares the RCC/PE changes; applying them belongs to
the normal copy-only builder, followed by a separate synthetic-profile test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import pprint
import zlib


BASELINE_SHA256 = "d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182"
RESOURCE_PATH = ":/qt/qml/OneMeUi/LeadingSettingsBar.qml"
PAYLOAD_OFFSET = 0x2FACFEE
PAYLOAD_LENGTH = 2683


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare_source(binary: bytes, panel: bytes) -> tuple[bytes, dict]:
    if sha(binary) != BASELINE_SHA256:
        raise ValueError("Unsupported or modified max.exe; exact baseline required")
    payload = binary[PAYLOAD_OFFSET:PAYLOAD_OFFSET + PAYLOAD_LENGTH]
    original = zlib.decompress(payload[4:])
    if len(original) != int.from_bytes(payload[:4], "big"):
        raise ValueError("QCompress source length mismatch")
    source = original.decode("utf-8").replace("\r\n", "\n")
    anchor = '''                OpenPageSettingsCellImpl {
                    title: qsTranslate("Settings", "Privacy")'''
    if source.count(anchor) != 1:
        raise ValueError("Privacy navigation anchor is not unique")
    button = '''                OpenPageSettingsCellImpl {
                    title: "Приватность мода"
                    iconSource: "qrc:/valmax/icons/privacy.svg"
                    settingsPage: settingsModPrivacy
                    Accessible.name: title
                }

'''
    source = source.replace(anchor, button + anchor)
    component_anchor = "    Component {\n        id: settingsDev\n"
    if source.count(component_anchor) != 1:
        raise ValueError("Settings component anchor is not unique")
    component = panel.decode("utf-8").replace("\r\n", "\n").strip()
    component = "\n".join("    " + line if line else "" for line in component.splitlines())
    source = source.replace(component_anchor, component + "\n\n" + component_anchor)
    result = source.encode("utf-8")
    # This optional compact form preserves every newline and every string. It
    # only removes leading/trailing indentation, so JavaScript ASI is unchanged.
    compact = "\n".join(line.strip() for line in source.splitlines()).encode("utf-8")
    variants = {"original": original, "candidate": result, "compact_candidate": compact}
    sizes = {name: {"source_bytes": len(value), "qcompress_bytes": 4 + len(zlib.compress(value, 9)),
                    "sha256_source": sha(value)} for name, value in variants.items()}
    chosen = compact if sizes["compact_candidate"]["qcompress_bytes"] <= PAYLOAD_LENGTH else result
    evidence = {"resource_path": RESOURCE_PATH, "baseline_sha256": BASELINE_SHA256,
                "original_payload_raw": hex(PAYLOAD_OFFSET), "original_budget": PAYLOAD_LENGTH,
                "variants": sizes, "fits_original_slot": 4 + len(zlib.compress(chosen, 9)) <= PAYLOAD_LENGTH,
                "source_sha256": sha(chosen), "status_endpoint": "http://127.0.0.1:18973/status",
                "counter_semantics": "blockedCalls=null means no observation; numeric 0 is displayed only with observation.active=true",
                "limitations": ["Static preparation only; QML runtime and accessibility require a synthetic-profile test.",
                                "The panel does not implement hooks or claim complete traffic coverage.",
                                "Rule state and observed blocked-call count are separate fields.",
                                "Successful local diagnostics schema validation is not a cryptographic authentication mechanism."]}
    return chosen, evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="Exact immutable baseline max.exe")
    parser.add_argument("--output", type=Path, required=True, help="Directory for generated source and evidence")
    parser.add_argument("--panel", type=Path, default=Path(__file__).with_name("ui_privacy_panel.qml"))
    parser.add_argument("--emit-spec", type=Path, help="Write PATCH_FILES Python module; no binaries are modified")
    args = parser.parse_args()
    binary = args.baseline.read_bytes()
    source, evidence = prepare_source(binary, args.panel.read_bytes())
    args.output.mkdir(parents=True, exist_ok=True)
    source_file = args.output / "LeadingSettingsBar.qml"
    source_file.write_bytes(source)
    evidence["source_file"] = str(source_file.resolve())
    if args.emit_spec:
        from ui_resource_patch import make_resource_spec
        spec = make_resource_spec(binary, RESOURCE_PATH, source)
        args.emit_spec.write_text('"""Generated version-pinned privacy settings UI; do not edit patch bytes by hand."""\n\nPATCH_FILES = '
                                  + pprint.pformat([spec], width=110, sort_dicts=False) + "\n", encoding="utf-8")
        evidence["spec_file"] = str(args.emit_spec.resolve())
        evidence["patch_count"] = len(spec["patches"])
    (args.output / "privacy_panel_static.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
