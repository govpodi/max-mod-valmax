"""Exercise 202 synthetic deleted bubbles through real bounded in-chat QML windows.

This fixture uses only a fake Bridge and synthetic history model. It verifies
the generated production wrapper by hash, then drives the actual range,
transport and bubble components in offscreen Qt. Installed-client behavior is
a separate smoke test.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile
import time

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtCore import QUrl

from test_retention_inline_placement import (
    ROOT, RANGE, BUBBLE, TRANSPORT, SyntheticHistoryModel,
    check_generated, digest, write_fixture,
)


HARNESS = ROOT / "native/tests/qt/retention_window_paging.qml"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path,
                        default=Path("E:/ValMAXBuild/build/retention-window-test"))
    parser.add_argument("--generator-proof", type=Path,
                        default=Path("E:/ValMAXBuild/analysis/retention-window-generator-proof.json"))
    parser.add_argument("--report", type=Path,
                        default=Path("E:/ValMAXBuild/analysis/retention-window-paging-proof.json"))
    args = parser.parse_args()
    generated_hashes = check_generated(args.generator_proof)
    args.build_root.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication([])
    with tempfile.TemporaryDirectory(prefix="retention-window-", dir=args.build_root) as folder:
        fixture = Path(folder)
        write_fixture(fixture)
        shutil.copy2(HARNESS, fixture / HARNESS.name)
        engine = QQmlEngine()
        engine.addImportPath(str(fixture / "qml"))
        model = SyntheticHistoryModel(engine)
        engine.rootContext().setContextProperty("historyModel", model)
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(fixture / HARNESS.name)))
        if component.isError():
            raise AssertionError("window QML parse: "
                                 + "; ".join(error.toString() for error in component.errors()))
        root = component.create()
        if root is None:
            raise AssertionError("window QML construction: "
                                 + "; ".join(error.toString() for error in component.errors()))
        deadline = time.monotonic() + 45
        while not root.property("finished") and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.004)
        if not root.property("finished"):
            raise AssertionError(f"window fixture timeout at step {root.property('step')}")
        failure = root.property("failure")
        if failure:
            raise AssertionError(f"{failure}; step {root.property('step')}")
        checks = root.property("checks")
        if not isinstance(checks, int) or checks < 60 or not root.property("sawTailLimit"):
            raise AssertionError("202-row window cases incomplete")
        root.close()
        root.deleteLater()
        app.processEvents()

    result = {
        "schema": 1, "verified": True, "syntheticOnly": True,
        "realClientStarted": False, "realProfilesRead": False, "networkUsed": False,
        "fixtureDeletedRows": 202, "maxDisplayedRows": 200, "checks": checks,
        "cases": ["partial page at 199 and one-row boundary request",
                  "202 rows ordered in two in-chat windows",
                  "previous and known next cursor navigation",
                  "periodic refresh stays on the visible window",
                  "failed window navigation preserves visible rows and retries",
                  "stale cursor clears prior windows and restarts from the first page",
                  "late old-account response is ignored",
                  "account switch and UI component restart isolate cursors and bubbles"],
        "hashes": generated_hashes | {
            "windowHarnessSha256": digest(HARNESS),
            "rangeSourceSha256": digest(RANGE),
            "bubbleSourceSha256": digest(BUBBLE),
            "transportSourceSha256": digest(TRANSPORT),
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf8")
    print(json.dumps({"verified": True, "syntheticOnly": True,
                      "checks": checks, "report": str(args.report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
