"""Synthetic inventory checks for Fixed and system Evergreen packaging."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "native" / "launcher"))
from launcher_generate_pins import EVERGREEN_REQUIRED, FIXED_REQUIRED, REQUIRED, generate, inventory


def check_rejected(root: Path, mode: str, reason: str) -> None:
    try:
        inventory(root, mode)
    except ValueError as exc:
        if reason not in str(exc):
            raise AssertionError(f"wrong rejection: {exc}") from exc
        return
    raise AssertionError(f"inventory accepted invalid {mode} fixture")


def write_files(root: Path, paths: set[str]) -> None:
    for relative in paths:
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"owned-synthetic-fixture")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="webview2-modes-", dir=ROOT / "analysis") as tmp:
        fixture = Path(tmp)
        runtime = fixture / "runtime"
        write_files(runtime, REQUIRED)

        check_rejected(runtime, "fixed", "required native payload component missing")
        check_rejected(runtime, "evergreen", "required native payload component missing")

        write_files(runtime, EVERGREEN_REQUIRED)
        evergreen_header = fixture / "evergreen.h"
        evergreen_receipt = fixture / "evergreen.json"
        generate(runtime, evergreen_header, evergreen_receipt, "evergreen")
        assert "kUseEvergreenWebView2=true" in evergreen_header.read_text(encoding="utf8")
        assert json.loads(evergreen_receipt.read_text(encoding="utf8"))["webview2Mode"] == "evergreen"

        write_files(runtime, FIXED_REQUIRED)
        check_rejected(runtime, "evergreen", "unexpected runtime branch")
        fixed_header = fixture / "fixed.h"
        fixed_receipt = fixture / "fixed.json"
        generate(runtime, fixed_header, fixed_receipt)
        assert "kUseEvergreenWebView2=false" in fixed_header.read_text(encoding="utf8")
        assert json.loads(fixed_receipt.read_text(encoding="utf8"))["webview2Mode"] == "fixed"

        (runtime / "mod" / "WebView2Loader.dll").unlink()
        check_rejected(runtime, "evergreen", "unexpected runtime branch")
        (runtime / "webview2" / "msedgewebview2.exe").unlink()
        (runtime / "webview2").rmdir()
        check_rejected(runtime, "evergreen", "required native payload component missing")
    print("webview2_mode_pin_fixtures_passed")


if __name__ == "__main__":
    main()
