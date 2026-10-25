"""Run the generated native window's interaction gate in an owned Qt fixture."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QMetaObject, QObject, Property, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "modkit"))
from startup_interaction_ui import OVERLAY, READINESS


class Bridge(QObject):
    runtimeStateChanged = Signal()
    ghostRuntimeStateChanged = Signal()
    notificationRuntimeStateChanged = Signal()

    def __init__(self):
        super().__init__()
        self._runtime = "opening"
        self._ghost = "unavailable"
        self._notification = "unavailable"

    @Property(str, notify=runtimeStateChanged)
    def runtimeState(self):
        return self._runtime

    @Property(str, notify=ghostRuntimeStateChanged)
    def ghostRuntimeState(self):
        return self._ghost

    @Property(str, notify=notificationRuntimeStateChanged)
    def notificationRuntimeState(self):
        return self._notification

    def set(self, *, runtime=None, ghost=None, notification=None):
        for name, value, signal in (
            ("_runtime", runtime, self.runtimeStateChanged),
            ("_ghost", ghost, self.ghostRuntimeStateChanged),
            ("_notification", notification, self.notificationRuntimeStateChanged),
        ):
            if value is not None and getattr(self, name) != value:
                setattr(self, name, value)
                signal.emit()


class Delegate(QObject):
    def __init__(self):
        super().__init__()
        self.closed = False

    @Slot()
    def closeMessenger(self):
        self.closed = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    source = args.source.read_text("utf8")
    assert source.count(READINESS) == source.count(OVERLAY) == 1
    assert source.count("enabled: root.valmaxInteractionPermitted") == 1
    assert source.index(OVERLAY) > source.index("    StackView {\n        id: stack")

    app = QGuiApplication([])
    bridge, delegate = Bridge(), Delegate()
    engine = QQmlEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.rootContext().setContextProperty("appDelegate", delegate)
    fixture = (
        "import QtQuick\nimport QtQuick.Controls\n"
        "ApplicationWindow {\n id: root\n width: 640; height: 480; visible: true\n"
        " color: '#202020'\n readonly property var valmaxNativeBridge: bridge\n"
        " property bool isClosingState: false\n"
        " property bool isLoginState: false\n"
        " property QtObject application: QtObject { property QtObject accountModel: QtObject { property int count: 1 } }\n"
        + READINESS
        + "    StackView { id: stack; objectName: 'stack'; anchors.fill: parent; enabled: root.valmaxInteractionPermitted }\n"
        + OVERLAY + "}\n"
    )
    component = QQmlComponent(engine)
    component.setData(fixture.encode("utf8"), args.source.as_uri())
    if component.isError():
        raise AssertionError("QML fixture failed: " + "; ".join(error.toString() for error in component.errors()))
    root = component.create()
    if root is None:
        raise AssertionError("QML fixture returned no root")
    app.processEvents()
    stack = root.findChild(QObject, "stack")
    cover = root.findChild(QObject, "valmaxStartupInteractionCover")
    status = root.findChild(QObject, "valmaxStartupGateStatus")
    ghost_status = root.findChild(QObject, "valmaxStartupGateGhostStatus")
    notification_status = root.findChild(QObject, "valmaxStartupGateNotificationStatus")
    close = root.findChild(QObject, "valmaxStartupGateClose")
    assert all(item is not None for item in (stack, cover, status, ghost_status,
                                             notification_status, close))
    checks = 0

    account_model = root.property("application").property("accountModel")

    def expect(ready, permitted, visible, label):
        nonlocal checks
        app.processEvents()
        assert root.property("valmaxInteractionHostsReady") is ready, label
        assert root.property("valmaxInteractionPermitted") is permitted, label + " permitted"
        assert stack.property("enabled") is permitted, label + " stack"
        assert cover.property("visible") is visible, label + " overlay"
        checks += 4

    def expect_status(runtime, ghost, notification, label):
        nonlocal checks
        app.processEvents()
        for item, expected in ((status, runtime), (ghost_status, ghost),
                               (notification_status, notification)):
            assert expected in item.property("text"), label + " " + expected
            checks += 1

    expect(False, False, True, "opening with account")
    expect_status("Открываем", "подтверждения нет", "подтверждения нет", "opening")
    account_model.setProperty("count", 0)
    expect(False, True, False, "first sign-in available")
    account_model.setProperty("count", 1)
    root.setProperty("isLoginState", True)
    expect(False, True, False, "additional account sign-in available")
    root.setProperty("isLoginState", False)
    expect(False, False, True, "account surface gated again")
    bridge.set(runtime="ready", ghost="off", notification="ready")
    expect(True, True, False, "acknowledged off")
    expect_status("подтверждения", "выключен", "подтверждена", "acknowledged off")
    bridge.set(ghost="active")
    expect(True, True, False, "acknowledged active")
    expect_status("подтверждения", "включён", "подтверждена", "acknowledged active")
    bridge.set(notification="unavailable")
    expect(False, False, True, "notification lost")
    expect_status("подтверждения", "включён", "подтверждения нет", "notification lost")
    bridge.set(ghost="pending", notification="ready")
    expect(False, False, True, "unknown ghost state cannot open account surface")
    expect_status("подтверждения", "подтверждения нет", "подтверждена", "unknown ghost")
    bridge.set(runtime="error")
    expect(False, False, True, "runtime error")
    expect_status("не открылись", "подтверждения нет", "подтверждена", "runtime error")
    bridge.set(runtime="unconfigured")
    expect(False, False, True, "clean first start without native config")
    expect_status("не открылись", "подтверждения нет", "подтверждена", "unconfigured")
    # Even in a failed state the cover's close action reaches the app delegate.
    assert QMetaObject.invokeMethod(close, "clicked")
    app.processEvents()
    assert delegate.closed
    checks += 2
    account_model.setProperty("count", 0)
    expect(False, True, False, "first sign-in after host error")
    account_model.setProperty("count", 1)
    root.setProperty("isClosingState", True)
    expect(False, False, False, "closing")
    root.close()
    root.deleteLater()
    app.processEvents()
    result = {"schema": 1, "verified": True, "checks": checks,
              "scope": "Generated native MeWindow source and owned offscreen Qt QML fixture",
              "nativeBackgroundStartupProven": False, "realProfilesRead": False,
              "installedClientChanged": False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
