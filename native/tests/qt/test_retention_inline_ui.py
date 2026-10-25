"""Render the inline retention component in an owned synthetic Qt ListView.

The fixture uses the real QML transport with a fake Bridge. It never starts
MAX, reads a user profile, or treats its footer as an original message place.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine


ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "native/tests/qt/retention_inline.qml"
INLINE = ROOT / "native/qt/MessageRetentionInline.qml"
TRANSPORT = ROOT / "native/qt/RetentionTransport.qml"

FAKE_BRIDGE = """pragma Singleton
import QtQml
QtObject {
    property string runtimeState: "ready"
    readonly property bool runtimeAvailable: runtimeState === "ready"
    property int sequence: 0
    property var requests: []
    signal operationFinished(string id, string response)
    function invoke(raw) {
        const id = "inline-" + (++sequence);
        requests = requests.concat([{id: id, value: JSON.parse(raw)}]);
        return {accepted: true, requestId: id, error: ""};
    }
    function success(index, result) {
        operationFinished(requests[index].id,
            JSON.stringify({schema: 1, ok: true, result: result}));
    }
}
"""
FAKE_ICON = """import QtQuick
Item {
    property url source
    property color fillColor
    property int imageWidth
    property int imageHeight
}
"""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path, default=Path("E:/ValMAXBuild/build"))
    parser.add_argument("--report", type=Path,
                        default=Path("E:/ValMAXBuild/analysis/retention-inline-ui-proof.json"))
    args = parser.parse_args()
    args.build_root.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication([])

    with tempfile.TemporaryDirectory(prefix="retention-inline-", dir=args.build_root) as folder:
        fixture = Path(folder)
        shutil.copy2(HARNESS, fixture / HARNESS.name)
        shutil.copy2(INLINE, fixture / INLINE.name)
        module = fixture / "qml/ValMAX/Native"
        module.mkdir(parents=True)
        (module / "qmldir").write_text(
            "module ValMAX.Native\n"
            "singleton Bridge 1.0 Bridge.qml\n"
            "singleton RetentionTransport 1.0 RetentionTransport.qml\n", "utf8")
        (module / "Bridge.qml").write_text(FAKE_BRIDGE, "utf8")
        shutil.copy2(TRANSPORT, module / TRANSPORT.name)
        ui_kit = fixture / "qml/UiKitModule"
        ui_kit.mkdir(parents=True)
        (ui_kit / "qmldir").write_text(
            "module UiKitModule\nSvgIcon 1.0 SvgIcon.qml\n", "utf8")
        (ui_kit / "SvgIcon.qml").write_text(FAKE_ICON, "utf8")

        engine = QQmlEngine()
        engine.addImportPath(str(fixture / "qml"))
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(fixture / HARNESS.name)))
        if component.isError():
            raise AssertionError("QML parse: " + "; ".join(e.toString() for e in component.errors()))
        root = component.create()
        if root is None:
            raise AssertionError("QML construction: " + "; ".join(e.toString() for e in component.errors()))
        deadline = time.monotonic() + 8
        while not root.property("finished") and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        if not root.property("finished"):
            raise AssertionError("offscreen inline fixture timed out")
        failure = root.property("failure")
        if failure:
            loader = root.findChild(QObject, "retentionInlineTestLoader")
            inline = loader.property("item") if loader is not None else None
            state = {"step": root.property("step"), "checks": root.property("checks"),
                     "requests": root.property("requestCount"),
                     "sawRetained": root.property("sawRetained"),
                     "sawRequest": root.property("sawBridgeRequest"),
                     "loader": None if loader is None else {
                         "item": loader.property("item") is not None},
                     "inline": None if inline is None else {
                         "account": inline.property("accountId"),
                         "chat": inline.property("chatId"),
                         "active": inline.property("active"),
                         "ready": inline.property("backendReady"),
                         "displayed": inline.property("displayedCount")}}
            raise AssertionError(f"{failure}; {state}")
        checks = root.property("checks")
        assert isinstance(checks, int) and checks >= 25, checks
        root.deleteLater()
        app.processEvents()

    result = {
        "schema": 1, "verified": True, "checks": checks,
        "sourceHashes": {str(path.relative_to(ROOT)): digest(path)
                         for path in (HARNESS, INLINE, TRANSPORT)},
        "syntheticOnly": True, "realClientStarted": False,
        "realProfilesRead": False, "networkUsed": False,
        "footerIsApproximateSection": True,
        "originalMessagePositionProven": False,
        "scope": "Real inline QML and transport in one offscreen ListView with fake Bridge and fake SvgIcon stub; bounded layout, plain text, paging, scope abort, minimize geometry and unchanged refresh",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
