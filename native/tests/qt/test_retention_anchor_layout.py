"""Offscreen synthetic proof for message delegate ghost layout mechanics."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine


ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "native/tests/qt/retention_anchor_layout.qml"


class SyntheticHistoryModel(QAbstractListModel):
    def rowCount(self, parent=QModelIndex()):
        return 3

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and 0 <= index.row() < 3 and role == 0x100:
            return f"synthetic-{index.row()}"
        return None

    def roleNames(self):
        return {0x100: QByteArray(b"messageContext")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path,
                        default=Path("E:/ValMAXBuild/analysis/retention-anchor-layout-proof.json"))
    args = parser.parse_args()
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlEngine()
    engine.rootContext().setContextProperty("testModel", SyntheticHistoryModel(engine))
    model_probe = QQmlComponent(engine)
    model_probe.setData(b"""import QtQml
QtObject {
    property bool passed: false
    Component.onCompleted: {
        passed = typeof testModel.index === "function"
            && typeof testModel.data === "function"
            && testModel.data(testModel.index(1, 0), 0x100) === "synthetic-1"
    }
}
""", QUrl.fromLocalFile(str(HARNESS)))
    if model_probe.isError():
        raise AssertionError("model probe parse: " + "; ".join(e.toString() for e in model_probe.errors()))
    probed = model_probe.create()
    if probed is None or not probed.property("passed"):
        raise AssertionError("QAbstractItemModel.index/data role 0x100 unavailable to QML")
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(HARNESS)))
    if component.isError():
        raise AssertionError("QML parse: " + "; ".join(e.toString() for e in component.errors()))
    root = component.create()
    if root is None:
        raise AssertionError("QML construction: " + "; ".join(e.toString() for e in component.errors()))
    deadline = time.monotonic() + 4
    while not root.property("finished") and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    if not root.property("finished"):
        raise AssertionError(f"layout timeout at step {root.property('step')}")
    failure = root.property("failure")
    if failure:
        raise AssertionError(f"{failure}; step {root.property('step')}")
    checks = root.property("checks")
    assert isinstance(checks, int) and checks >= 7, checks
    root.deleteLater()
    app.processEvents()
    result = {
        "schema": 1, "verified": True, "checks": checks,
        "sourceSha256": hashlib.sha256(HARNESS.read_bytes()).hexdigest(),
        "syntheticOnly": True, "nativeHistoryDelegateLoaded": False,
        "modelIndexStable": True, "abstractModelRandomAccessQml": True,
        "directContentItemGrowsDelegate": False,
        "scope": "Synthetic direct reparent, wrapper, and bottomPadding geometry in real offscreen Qt ListView; pointer safety of native delegate not proven",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
