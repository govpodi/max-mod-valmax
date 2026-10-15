"""Render original ValMAX SVGs with QtSvg; no MAX or desktop automation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1] / "modkit"))


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output", required=True, type=Path); args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1")
    fonts = output / "fonts"; fonts.mkdir(exist_ok=True)
    for name in ("arial.ttf", "arialbd.ttf"):
        shutil.copyfile(Path("C:/Windows/Fonts") / name, fonts / name)
    os.environ["QT_QPA_FONTDIR"] = str(fonts)
    from PySide6.QtCore import QByteArray, QRectF, Qt, qInstallMessageHandler, QtMsgType, qVersion
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from valmax_icons import resource_payloads
    warnings = []
    def handler(kind, _context, text):
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg): warnings.append(text)
    previous = qInstallMessageHandler(handler); app = QGuiApplication([])
    payloads = resource_payloads(ROOT); catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf8"))
    def render(data, size, color="#000000"):
        renderer = QSvgRenderer(QByteArray(data.replace(b"currentColor", color.encode())))
        assert renderer.isValid()
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied); image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image); painter.setRenderHint(QPainter.RenderHint.Antialiasing); renderer.render(painter, QRectF(0, 0, size, size)); painter.end()
        return image
    checks = []
    for row in catalog["icons"]:
        data = payloads[row["resource"]]; sizes = []
        for size in (24, 32, 48, 72):
            image = render(data, size); points = [(x, y) for y in range(size) for x in range(size) if image.pixelColor(x, y).alpha()]
            assert points and len(points) < size * size * .65
            assert all(image.pixelColor(x, y).alpha() == 0 for x, y in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)))
            bounds = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
            assert bounds[0] > 0 and bounds[1] > 0 and bounds[2] < size - 1 and bounds[3] < size - 1, (row["id"], size, bounds)
            path = output / (row["id"] + f"-{size}.png"); assert image.save(str(path))
            sizes.append({"size": size, "alpha_pixel_count": len(points), "alpha_bounds": bounds, "transparent_png": str(path)})
        prototype = (ROOT / "prototypes" / (row["id"] + ".svg")).read_bytes()
        # Baked paths and editable monolines must describe the same silhouette.
        a, b = render(data, 192), render(prototype, 192)
        differences = sum(abs(a.pixelColor(x, y).alpha() - b.pixelColor(x, y).alpha()) for y in range(192) for x in range(192)) / (192 * 192 * 255)
        assert differences < .012, (row["id"], differences)
        checks.append({"id": row["id"], "sizes": sizes, "prototype_mean_alpha_difference": round(differences, 6)})
    index = json.loads(Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_index.json").read_text())
    baseline = Path("D:/CodexLabs/max-mod-26.35.1/runtime/baseline/max.exe").read_bytes()
    assert hashlib.sha256(baseline).hexdigest() == "d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182"
    native = []
    for name in ("privacy", "profile", "reply", "devices", "case"):
        entry = next(r for r in index["files"] if r["path"] == ":/icon_pack/" + name + ".svg")
        data = baseline[entry["payload_raw"]:entry["payload_raw"] + entry["length"]]
        if entry["flags"] & 1: data = zlib.decompress(data[4:])
        native.append((name, data))
    sheet = QImage(1120, 840, QImage.Format.Format_ARGB32_Premultiplied); sheet.fill(QColor("#ffffff"))
    painter = QPainter(sheet); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    def text(x, y, value, color="#22262b", size=15, bold=False):
        font = QFont("Arial"); font.setPixelSize(size); font.setBold(bold); painter.setFont(font); painter.setPen(QColor(color)); painter.drawText(x, y, value)
    text(24, 30, "ValMAX · иконки настроек", size=22, bold=True)
    text(24, 56, "Сетка 24 px · линия 2 px · прозрачные SVG · цвет берётся из темы", color="#60666f")
    text(24, 92, "Штатные MAX (сравнение пропорций)", bold=True)
    for i, (name, data) in enumerate(native):
        x = 330 + i * 150; painter.drawImage(x, 75, render(data, 24, "#6b6f75")); painter.drawImage(x + 35, 66, render(data, 48, "#6b6f75")); text(x, 137, name, "#60666f", 13)
    themes = ((160, "Светлая тема", "#edeef2", "#646970"), (385, "Тёмная тема", "#17212a", "#a7b0ba"), (610, "Выбранный раздел", "#eaf4ff", "#0088ff"))
    for top, title, background, foreground in themes:
        painter.fillRect(QRectF(0, top, 1120, 220), QColor(background)); text(24, top + 32, title, foreground, 17, True)
        for i, row in enumerate(catalog["icons"]):
            x = 32 + i * 157; data = payloads[row["resource"]]
            painter.drawImage(x + 22, top + 65, render(data, 24, foreground)); painter.drawImage(x + 64, top + 53, render(data, 48, foreground))
            text(x, top + 132, row["label"], foreground, 14)
            text(x + 22, top + 162, "24", foreground, 12); text(x + 76, top + 162, "48", foreground, 12)
    painter.end(); sheet_path = output / "valmax-icons-contact-sheet.png"; assert sheet.save(str(sheet_path))
    qInstallMessageHandler(previous); assert not warnings, warnings
    report = {"qt_version": qVersion(), "icons": checks, "warnings": warnings, "contact_sheet": str(sheet_path),
              "max_started": False, "real_profile_accessed": False, "native_reference_icons": [name for name, _ in native],
              "notes": ["QtSvg renders transparent currentColor paths at 24/32/48/72 px; outlines stay inside canvas.", "Editable 2px round-cap monoline prototypes and baked native-compatible fills agree at 192px.", "Contact sheet backgrounds are only comparison surfaces; every source SVG and individual PNG has transparency."]}
    (output / "icons-qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"report": str(output / "icons-qa.json"), "sheet": str(sheet_path), "icons": len(checks), "warnings": warnings}, ensure_ascii=False))


if __name__ == "__main__": main()
