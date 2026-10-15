"""Build partial privacy variants for guarded debugging, without changing specs.

This does not run MAX. Each output is a fresh build with the usual complete source
SHA256, expected-byte, reverse-validation, containment and destination guards.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import sys

import build_mod
import privacy


CASES = ("mask_logs", "error_logs", "config", "tracer", "tracer_main",
         "tracer_service", "core", "full")


def select_privacy(case: str) -> list[dict]:
    if case not in CASES:
        raise ValueError(f"Unknown privacy diagnostic case: {case}")

    def selected(identifier: str) -> bool:
        if case == "full":
            return True
        if case == "mask_logs":
            return identifier.startswith("mask_logs")
        if case == "error_logs":
            return identifier.startswith("no_error_logs_to_crash_server")
        if case == "config":
            return identifier.startswith(("mask_logs", "no_error_logs_to_crash_server"))
        if case == "tracer_main":
            return identifier.startswith("no_tracer_") and identifier.endswith("_root")
        if case == "tracer_service":
            return identifier.startswith("no_tracer_") and identifier.endswith("_service")
        if case == "tracer":
            return identifier.startswith("no_tracer_")
        # Always keep both analytics coroutine patches together.
        return identifier.startswith("no_analytics_")

    result = []
    for specification in deepcopy(privacy.PATCH_FILES):
        specification["patches"] = [entry for entry in specification["patches"]
                                      if selected(entry["id"])]
        if specification["patches"]:
            result.append(specification)
    if not result:
        raise ValueError("The selected diagnostic variant contains no patches")
    return result


def build_variant(baseline: Path, output: Path, case: str) -> dict:
    partial = ModuleType("privacy")
    partial.PATCH_FILES = select_privacy(case)
    with patch.dict(sys.modules, {"privacy": partial}):
        manifest = build_mod.build(baseline, output, ["accounts", "isolation", "privacy"])
    manifest["diagnostic_privacy_case"] = case
    manifest_path = output / "mod-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    build_mod.verify(manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=CASES, required=True)
    args = parser.parse_args()
    manifest = build_variant(args.baseline, args.output, args.case)
    print(json.dumps({"destination":manifest["destination"],
                      "diagnostic_privacy_case":args.case,
                      "patches":sum(len(item["patches"]) for item in manifest["files"])}))


if __name__ == "__main__":
    main()
