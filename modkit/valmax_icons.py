"""Pure build-time ValMAX SVG payloads; no native/profile/network operations."""
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ICON_IDS = ("privacy", "ghost", "mdata", "quick-replies", "accounts", "chat-export", "business")
ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets/valmax-icons"
PREFIX = ":/valmax/icons/"


def resource_payloads(directory=None):
    """Return seven bounded, hash-verified SVG resources for the RCC builder.

    This function does not register resources in a native process. The caller
    retains normal baseline/SHA/byte/reversibility guards for its RCC extension.
    """
    directory = Path(directory) if directory is not None else ASSET_ROOT
    catalog = json.loads((directory / "catalog.json").read_text(encoding="utf8"))
    if catalog.get("schema") != 1 or catalog.get("grid") != 24 or catalog.get("background") != "transparent":
        raise ValueError("Unsupported ValMAX icon catalog")
    rows = catalog.get("icons")
    if not isinstance(rows, list) or tuple(row.get("id") for row in rows) != ICON_IDS:
        raise ValueError("Incomplete ValMAX icon catalog")
    result = {}
    for row in rows:
        identifier = row["id"]; expected_path = "icons/" + identifier + ".svg"
        if row.get("file") != expected_path or row.get("resource") != PREFIX + identifier + ".svg":
            raise ValueError("Unexpected ValMAX icon path")
        data = (directory / expected_path).read_bytes()
        if not 64 <= len(data) <= 32768 or hashlib.sha256(data).hexdigest() != row.get("sha256"):
            raise ValueError("ValMAX icon hash/size mismatch")
        if b"<!" in data or re.search(br"\b(?:href|style|filter|mask|onload|onclick)\s*=", data, re.I):
            raise ValueError("Unsupported active/external SVG content")
        root = ET.fromstring(data)
        if root.tag != "{http://www.w3.org/2000/svg}svg" or root.attrib.get("viewBox") != "0 0 24 24" or root.attrib.get("fill") != "none":
            raise ValueError("Unexpected SVG grid/background")
        if not list(root) or any(child.tag != "{http://www.w3.org/2000/svg}path" or child.attrib.get("fill") != "currentColor" or "stroke" in child.attrib for child in root):
            raise ValueError("Only native-compatible monochrome filled paths allowed")
        result[row["resource"]] = data
    return result
