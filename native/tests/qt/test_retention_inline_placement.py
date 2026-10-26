"""Synthetic ordered inline QML proof with exact generated-source hashes.

The real range, bubble, and transport components run in offscreen Qt. A small
QObject-backed history model and fake native Bridge contain only test rows.
The generated client HistoryListView is checked by hash and pinned structure;
the installed client is a separate required smoke test.
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
from PySide6.QtCore import (QAbstractListModel, QByteArray, QObject, Property,
                            QModelIndex, QPoint, QPointF, Qt, QUrl, Slot)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "native/tests/qt/retention_inline_placement.qml"
RANGE = ROOT / "native/qt/MessageRetentionRange.qml"
BUBBLE = ROOT / "native/qt/MessageRetentionBubble.qml"
TRANSPORT = ROOT / "native/qt/RetentionTransport.qml"
HISTORY_RESOURCE = ":/qt/qml/OneMeUi/HistoryListView.qml"
RANGE_RESOURCE = ":/valmax/MessageRetentionRange.qml"
BUBBLE_RESOURCE = ":/valmax/MessageRetentionBubble.qml"

FAKE_BRIDGE = """pragma Singleton
import QtQml
QtObject {
    property string runtimeState: "ready"
    readonly property bool runtimeAvailable: runtimeState === "ready"
    property int sequence: 0
    property var requests: []
    signal operationFinished(string id, string response)
    function invoke(raw) {
        const id = "range-" + (++sequence);
        requests = requests.concat([{id: id, value: JSON.parse(raw)}]);
        return {accepted: true, requestId: id, error: ""};
    }
    function success(index, result) {
        operationFinished(requests[index].id,
            JSON.stringify({schema: 1, ok: true, result: result}));
    }
    function failure(index, code) {
        operationFinished(requests[index].id,
            JSON.stringify({schema: 1, ok: false, error: code}));
    }
}
"""
FAKE_QML_UTILS = """pragma Singleton
import QtQml
QtObject {
    function messageIdString(value) { return String(value); }
    function toDateTime(value) {
        return value && typeof value.ms === "string"
            ? new Date(Number(value.ms)) : new Date(NaN);
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


class SyntheticTimestamp(QObject):
    def __init__(self, milliseconds: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._milliseconds = milliseconds

    @Property(str, constant=True)
    def ms(self) -> str:
        return self._milliseconds


class SyntheticMessage(QObject):
    def __init__(self, message_id: str, milliseconds: str,
                 parent: QObject | None = None, delayed: bool = False) -> None:
        super().__init__(parent)
        self._message_id = message_id
        self._timestamp = SyntheticTimestamp(milliseconds, self)
        self._delayed = delayed

    @Property(str, constant=True)
    def messageId(self) -> str:
        return self._message_id

    @Property(QObject, constant=True)
    def time(self) -> QObject:
        return self._timestamp

    @Property(bool, constant=True)
    def isDelayed(self) -> bool:
        return self._delayed


class SyntheticContext(QObject):
    def __init__(self, message_id: str, milliseconds: str,
                 parent: QObject | None = None, delayed: bool = False) -> None:
        super().__init__(parent)
        self._message = SyntheticMessage(message_id, milliseconds, self, delayed)

    @Slot(result=QObject)
    def get(self) -> QObject:
        return self._message


class SyntheticHistoryModel(QAbstractListModel):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # The model exposes stock-named section/flag roles. Role 0x101 is
        # deliberately numeric, so a hardcoded assumption that it is a day
        # string fails; its exact number is not asserted for the real client.
        # Times are local epoch ms on consecutive days.
        self._first_ms = int(time.mktime((2024, 7, 3, 12, 0, 0, 0, 0, -1)) * 1000)
        second_ms = int(time.mktime((2024, 7, 4, 12, 0, 0, 0, 0, -1)) * 1000)
        first_day = str(int(time.mktime((2024, 7, 3, 0, 0, 0, 0, 0, -1)) * 1000))
        second_day = str(int(time.mktime((2024, 7, 4, 0, 0, 0, 0, 0, -1)) * 1000))
        self._values = [("101", str(self._first_ms), first_day),
                        ("103", str(self._first_ms), first_day),
                        ("104", str(second_ms), second_day),
                        ("106", str(second_ms + 200), second_day)]
        self._contexts = [SyntheticContext(value[0], value[1], self)
                          for value in self._values]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._values)

    def roleNames(self) -> dict[int, QByteArray]:
        return {0x100: QByteArray(b"messageContext"),
                0x101: QByteArray(b"messageChainedFlag"),
                0x102: QByteArray(b"selectionContext"),
                0x103: QByteArray(b"messageDayStartTime")}

    def data(self, index: QModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._values):
            return None
        if role == 0x100:
            return self._contexts[index.row()]
        if role == 0x101:
            return 0
        if role == 0x103:
            return self._values[index.row()][2]
        return None

    @Slot()
    def replaceMiddle(self) -> None:
        replacement_ms = str(self._first_ms + 50)
        self._values[1] = ("109", replacement_ms, self._values[0][2])
        self._contexts[1] = SyntheticContext("109", replacement_ms, self)
        row = self.index(1, 0)
        self.dataChanged.emit(row, row, [0x100, 0x101, 0x103])

    @Slot(bool)
    def setMiddleDelayed(self, delayed: bool) -> None:
        self._contexts[1] = SyntheticContext("109", str(self._first_ms + 50), self, delayed)
        row = self.index(1, 0)
        self.dataChanged.emit(row, row, [0x100])


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_generated(proof_path: Path) -> dict[str, str]:
    generator_proof = json.loads(proof_path.read_text("utf8"))
    if generator_proof.get("verified") is not True or not generator_proof.get("retentionRangeIntegrated"):
        raise AssertionError("generated QML fixture proof is missing or failed")
    fixture = Path(generator_proof["fixture"]) / "native"
    native_proof = json.loads((fixture / "native-qml-proof.json").read_text("utf8"))
    history = fixture / "HistoryListView.qml"
    source = history.read_text("utf8")
    if native_proof["sourceHashes"][HISTORY_RESOURCE] != digest(history):
        raise AssertionError("generated HistoryListView hash mismatch")
    for resource, name in ((RANGE_RESOURCE, "MessageRetentionRange.qml"),
                           (BUBBLE_RESOURCE, "MessageRetentionBubble.qml")):
        packed = fixture / "icons/inputs/valmax" / name
        if native_proof["extraResourceHashes"][resource] != digest(packed):
            raise AssertionError("packed retention component hash mismatch")
        source_path = RANGE if resource == RANGE_RESOURCE else BUBBLE
        if digest(packed) != digest(source_path):
            raise AssertionError("generated retention component is stale")
    required = (
        "delegate: Item {", 'objectName: "valmaxRetentionHistoryRow"',
        "required property var messageContext", "required property int index",
        "property alias isHighlighted: messageDelegate.isHighlighted",
        "height: valmaxGhostBefore.implicitHeight + messageDelegate.height + valmaxGhostAfter.implicitHeight",
        "messageContext: valmaxHistoryRow.messageContext",
        "delegateY: valmaxHistoryRow.y + y",
        "valmaxRetentionRange.rowsBeforeIndex[String(valmaxHistoryRow.index)]",
        "valmaxRetentionRange.rowsByIndex[String(valmaxHistoryRow.index)]",
        "ValMAX.MessageRetentionBubble {",
        "valmaxRetentionRange.loadMore()",
        "valmaxRetentionRange.nextWindow()",
        "valmaxRetentionRange.previousWindow()",
        'objectName: "valmaxRetentionRangeNextBefore"',
        'objectName: "valmaxRetentionRangeNextAfter"',
        'objectName: "valmaxRetentionRangePreviousBefore"',
        'objectName: "valmaxRetentionRangePreviousAfter"',
        "onClicked: valmaxRetentionRange.scanVisible(true)",
        'objectName: "valmaxRetentionApproximateBubbles"',
        'objectName: "valmaxRetentionEmptyChatBubbles"',
        'active: valmaxHistoryRow.index === listView.count - 1 &&',
    )
    if any(part not in source for part in required):
        raise AssertionError("generated inline wrapper structure changed")
    if 'valmaxRetentionInlineFooter' in source or 'Сохранённые удаления ·' in source:
        raise AssertionError("separate saved-deletions section remained")
    if source.count("HistoryMessageDelegate {") != 1 or "delegate: HistoryMessageDelegate {" in source:
        raise AssertionError("stock HistoryMessageDelegate ownership changed")
    return {"generatedHistoryListViewSha256": digest(history),
            "packedRangeQmlSha256": native_proof["extraResourceHashes"][RANGE_RESOURCE],
            "packedBubbleQmlSha256": native_proof["extraResourceHashes"][BUBBLE_RESOURCE],
            "packedBundleSha256": native_proof["generatedArtifacts"]["icons/icon-pack-plus-valmax.rcc"]}


def write_fixture(fixture: Path) -> None:
    shutil.copy2(HARNESS, fixture / HARNESS.name)
    valmax = fixture / "qml/ValMAX"
    valmax.mkdir(parents=True)
    (valmax / "qmldir").write_text(
        "module ValMAX\nMessageRetentionRange 1.0 MessageRetentionRange.qml\n"
        "MessageRetentionBubble 1.0 MessageRetentionBubble.qml\n", "utf8")
    shutil.copy2(RANGE, valmax / RANGE.name)
    shutil.copy2(BUBBLE, valmax / BUBBLE.name)
    native = valmax / "Native"
    native.mkdir()
    (native / "qmldir").write_text(
        "module ValMAX.Native\nsingleton Bridge 1.0 Bridge.qml\n"
        "singleton RetentionTransport 1.0 RetentionTransport.qml\n", "utf8")
    (native / "Bridge.qml").write_text(FAKE_BRIDGE, "utf8")
    shutil.copy2(TRANSPORT, native / TRANSPORT.name)
    utils = fixture / "qml/QmlSingletonesModule"
    utils.mkdir()
    (utils / "qmldir").write_text(
        "module QmlSingletonesModule\nsingleton QmlUtils 1.0 QmlUtils.qml\n", "utf8")
    (utils / "QmlUtils.qml").write_text(FAKE_QML_UTILS, "utf8")
    ui_kit = fixture / "qml/UiKitModule"
    ui_kit.mkdir()
    (ui_kit / "qmldir").write_text("module UiKitModule\nSvgIcon 1.0 SvgIcon.qml\n", "utf8")
    (ui_kit / "SvgIcon.qml").write_text(FAKE_ICON, "utf8")


def verify_pointer_isolation(root: QObject, app: QGuiApplication) -> bool:
    for _ in range(40):
        app.processEvents()
        time.sleep(0.003)
    def visual_descendants(item: QQuickItem) -> list[QQuickItem]:
        children = item.childItems()
        return children + [nested for child in children
                           for nested in visual_descendants(child)]

    items = visual_descendants(root.contentItem())
    bubbles = [item for item in items
               if item.objectName() == "valmaxRetainedInlineBubble"]
    native_items = [item for item in items
                    if item.objectName() == "retentionPlacementNativeItem"]
    if len(bubbles) != 1 or len(native_items) != 4:
        raise AssertionError(f"final account bubble/native item count is wrong: "
                             f"{len(bubbles)}/{len(native_items)}")
    bubble = bubbles[0]
    center = bubble.mapToScene(QPointF(bubble.width() / 2, bubble.height() / 2))
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(center.x()), round(center.y())))
    app.processEvents()
    if root.property("nativeClicks") != 0:
        raise AssertionError("ghost click reached a stock native message handler")
    native = native_items[2]
    native_center = native.mapToScene(QPointF(30, native.height() / 2))
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(native_center.x()), round(native_center.y())))
    app.processEvents()
    if root.property("nativeClicks") != 1:
        raise AssertionError("stock native message handler did not receive its own click")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path, default=Path("E:/ValMAXBuild/build"))
    parser.add_argument("--generator-proof", type=Path,
                        default=Path("E:/ValMAXBuild/analysis/retention-inline-generator-proof.json"))
    parser.add_argument("--report", type=Path,
                        default=Path("E:/ValMAXBuild/analysis/retention-inline-placement-proof.json"))
    args = parser.parse_args()
    generated_hashes = check_generated(args.generator_proof)
    args.build_root.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication([])
    with tempfile.TemporaryDirectory(prefix="retention-placement-", dir=args.build_root) as folder:
        fixture = Path(folder)
        write_fixture(fixture)
        engine = QQmlEngine()
        engine.addImportPath(str(fixture / "qml"))
        model = SyntheticHistoryModel(engine)
        engine.rootContext().setContextProperty("historyModel", model)
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(fixture / HARNESS.name)))
        if component.isError():
            raise AssertionError("placement QML parse: " + "; ".join(e.toString() for e in component.errors()))
        root = component.create()
        if root is None:
            raise AssertionError("placement QML construction: " + "; ".join(e.toString() for e in component.errors()))
        deadline = time.monotonic() + 12
        while not root.property("finished") and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.004)
        if not root.property("finished"):
            raise AssertionError(f"placement fixture timeout at step {root.property('step')}")
        failure = root.property("failure")
        if failure:
            raise AssertionError(f"{failure}; step {root.property('step')}")
        checks = root.property("checks")
        if not isinstance(checks, int) or checks < 20 or not root.property("sawCustomTimestamp"):
            raise AssertionError("placement cases incomplete")
        pointer_isolated = verify_pointer_isolation(root, app)
        root.close()
        root.deleteLater()
        app.processEvents()

    result = {
        "schema": 1, "verified": True, "syntheticOnly": True,
        "liveExactPositionProven": False, "realClientStarted": False,
        "realProfilesRead": False, "networkUsed": False,
        "checks": checks + int(pointer_isolated),
        "cases": ["same-key pair ordering", "native collision deduplication",
                  "before-first and after-last placement", "local cross-day label",
                  "numeric non-day role and validated original time display",
                  "implausible and unsafe timestamp fallback",
                  "custom timestamp conversion and delayed guard",
                  "bounded paging and empty page",
                  "interior dataChanged reanchor", "account switch abort",
                  "wrapper geometry and unchanged native indices", "pointer isolation"],
        "hashes": generated_hashes | {
            "placementHarnessSha256": digest(HARNESS),
            "rangeSourceSha256": digest(RANGE),
            "bubbleSourceSha256": digest(BUBBLE),
            "transportSourceSha256": digest(TRANSPORT),
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf8")
    print(json.dumps({"verified": result["verified"], "syntheticOnly": True,
                      "checks": result["checks"], "report": str(args.report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
