"""Bake original monoline geometry to native-compatible currentColor fills.

Only project assets are written. Requires Qt 6.11.2/PySide6; no native MAX.
The SVGs retain curves and transparent background, with no raster tracing.
"""
import hashlib
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainterPath, QPainterPathStroker

ROOT = Path(__file__).resolve().parents[1]


def make_path(operations):
    result = QPainterPath()
    for operation in operations:
        method, *values = operation
        if method == "M": result.moveTo(*values)
        elif method == "L": result.lineTo(*values)
        elif method == "C": result.cubicTo(*values)
        elif method == "Z": result.closeSubpath()
        else: raise ValueError("Unsupported original path command")
    return result


def number(value):
    return format(round(value, 4), ".4f").rstrip("0").rstrip(".") or "0"


def serialize(path):
    pieces = []; i = 0
    while i < path.elementCount():
        point = path.elementAt(i)
        if point.type == QPainterPath.ElementType.MoveToElement:
            pieces.append("M" + number(point.x) + " " + number(point.y)); i += 1
        elif point.type == QPainterPath.ElementType.LineToElement:
            pieces.append("L" + number(point.x) + " " + number(point.y)); i += 1
        elif point.type == QPainterPath.ElementType.CurveToElement:
            second, end = path.elementAt(i + 1), path.elementAt(i + 2)
            pieces.append("C" + " ".join(number(v) for v in (point.x, point.y, second.x, second.y, end.x, end.y))); i += 3
        else: raise ValueError("Invalid painter path")
    return " ".join(pieces)


def svg(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none">\n'
            + body + "\n</svg>\n").encode("utf8")


def main():
    source = ROOT / "source/geometry.json"; spec = json.loads(source.read_text(encoding="utf8"))
    assert spec["schema"] == 1 and spec["grid"] == 24 and spec["strokeWidth"] == 2
    stroker = QPainterPathStroker(); stroker.setWidth(2); stroker.setCapStyle(Qt.PenCapStyle.RoundCap); stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    (ROOT / "icons").mkdir(exist_ok=True); (ROOT / "prototypes").mkdir(exist_ok=True)
    records = []
    for icon in spec["icons"]:
        lines = QPainterPath(); filled = QPainterPath(); prototype = []
        for operations in icon.get("paths", []):
            path = make_path(operations); lines.addPath(path)
            prototype.append('<path stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="' + serialize(path) + '"/>')
        for x, y, radius in icon.get("rings", []):
            lines.addEllipse(x - radius, y - radius, radius * 2, radius * 2)
            prototype.append(f'<circle cx="{number(x)}" cy="{number(y)}" r="{number(radius)}" stroke="currentColor" stroke-width="2"/>')
        for operations in icon.get("fills", []):
            path = make_path(operations); filled.addPath(path)
            prototype.append('<path fill="currentColor" d="' + serialize(path) + '"/>')
        for x, y, radius in icon.get("dots", []):
            filled.addEllipse(x - radius, y - radius, radius * 2, radius * 2)
            prototype.append(f'<circle cx="{number(x)}" cy="{number(y)}" r="{number(radius)}" fill="currentColor"/>')
        outline = stroker.createStroke(lines); outline.addPath(filled); outline.setFillRule(Qt.FillRule.WindingFill)
        data = svg('<path fill="currentColor" fill-rule="nonzero" d="' + serialize(outline) + '"/>')
        path = ROOT / "icons" / (icon["id"] + ".svg"); path.write_bytes(data)
        (ROOT / "prototypes" / (icon["id"] + ".svg")).write_bytes(svg("\n".join(prototype)))
        bounds = outline.boundingRect()
        records.append({"id": icon["id"], "label": icon["label"], "meaning": icon["meaning"], "file": "icons/" + path.name,
                        "resource": ":/valmax/icons/" + path.name, "sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data), "bounds": [round(v, 4) for v in (bounds.left(), bounds.top(), bounds.right(), bounds.bottom())]})
    catalog = {"schema": 1, "grid": 24, "stroke_width": 2, "color": "currentColor", "background": "transparent",
               "geometry_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "icons": records}
    (ROOT / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    qrc = '<RCC version="1.0">\n  <qresource prefix="/valmax/icons">\n' + "".join('    <file alias="' + row["id"] + '.svg">' + row["file"] + '</file>\n' for row in records) + '  </qresource>\n</RCC>\n'
    (ROOT / "valmax-icons.qrc").write_text(qrc, encoding="utf8")
    print(json.dumps({"icons": len(records), "assets": str(ROOT), "total_svg_bytes": sum(row["bytes"] for row in records)}, ensure_ascii=False))


if __name__ == "__main__": main()
