"""Render the generated Qt Quick recorder pulse offscreen without MAX/profile data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "modkit"))
from recording_indicator_ui import PULSE_QML  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()
    if args.image.exists() or not args.image.parent.is_dir():
        raise RuntimeError("fresh existing E: image parent required")
    os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                      QML_DISABLE_DISK_CACHE="1")
    from PySide6.QtCore import QObject, QPointF, QUrl, qInstallMessageHandler
    from PySide6.QtGui import QColor, QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest

    warnings: list[str] = []
    qInstallMessageHandler(lambda kind, context, message: warnings.append(str(message)))
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlEngine()
    engine.rootContext().setContextProperty("UiKit", {"DesignSystem": {"SizeBase": {"px16": 16}}})
    component = QQmlComponent(engine)
    component.setData(("import QtQuick\n" + PULSE_QML + "\n").encode("utf8"),
                      QUrl("qrc:/valmax/RecordingPulseTest.qml"))
    if component.isError():
        raise RuntimeError("recording pulse QML failed: " + "; ".join(str(e) for e in component.errors()))
    item = component.create()
    if item is None:
        raise RuntimeError("recording pulse QML did not instantiate")
    halo = item.findChild(QObject, "valmaxRecordingPulseHalo")
    dot = item.findChild(QObject, "valmaxRecordingPulseDot")
    if halo is None or dot is None or item.width() != 16 or item.height() != 16:
        raise RuntimeError("recording pulse geometry or children changed")
    if abs(halo.width() - 16 * 5 / 6) > 0.01 or dot.width() != 8:
        raise RuntimeError("recording pulse circle proportions changed")
    window = QQuickWindow()
    window.setColor(QColor("#ffffff"))
    window.resize(32, 32)
    item.setParentItem(window.contentItem())
    item.setPosition(QPointF(8, 8))
    window.show()
    QTest.qWait(120)
    first_scale = float(halo.scale())
    QTest.qWait(650)
    second_scale = float(halo.scale())
    if not 0.49 <= first_scale <= 1.01 or not 0.49 <= second_scale <= 1.01 or abs(second_scale - first_scale) < 0.08:
        raise RuntimeError("recording pulse did not animate within expected range")
    image = window.grabWindow()
    if image.isNull() or image.width() < 32 or image.height() < 32:
        raise RuntimeError("offscreen Qt Quick render did not produce an image")
    center = image.pixelColor(16, 16)
    if (center.red(), center.green(), center.blue()) != (255, 48, 60):
        raise RuntimeError("recording pulse rendered colour changed")
    if warnings:
        raise RuntimeError("unexpected Qt warnings: " + "; ".join(warnings[:5]))
    if not image.save(str(args.image)):
        raise RuntimeError("could not save synthetic recording indicator PNG")
    print(json.dumps({"rendered": True, "width": image.width(), "height": image.height(),
                      "centerRgb": [center.red(), center.green(), center.blue()],
                      "firstScale": round(first_scale, 3), "secondScale": round(second_scale, 3),
                      "warnings": 0, "image": str(args.image)}))
    window.close()
    app.quit()


if __name__ == "__main__":
    main()
