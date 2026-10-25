"""Build-only immutable pins for the final runtime/ tree. Never executes it.

This inventory proves bytes, not feature readiness or dependency closure. The
full-package builder remains responsible for those checks before distribution.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat

MUTABLE = "mod/optimization-active.json"
REQUIRED = {
    "mod/max.exe", "mod/max-service/max-service.exe", "mod/max-service/config.dll", "mod/valmax_core.dll",
    "mod/valmax_privacy.dll", "mod/valmax_ghost.dll", "mod/valmax_notifications.dll",
    "mod/valmax-private-notification-f2bb5bbaca678eca.png",
    "mod/qml/ValMAX/Native/valmaxnativeplugin.dll", "mod/qml/ValMAX/Native/qmldir",
    "mod/qml/ValMAX/Native/SettingsTransport.qml", "mod/qml/ValMAX/Native/ChatLockTransport.qml",
    "mod/qml/ValMAX/Native/SupportTransport.qml", "mod/qml/ValMAX/Native/MDataTransport.qml",
    "mod/qml/ValMAX/Native/MDataTransferEntry.qml", "mod/qml/ValMAX/Native/ExportMediaOwner.qml",
    "mod/qml/ValMAX/Native/ExportTransport.qml",
    "mod/plugins/platforms/qwindows.dll", "mod/plugins/webview/qtwebview_webview2.dll",
    "webview2/msedgewebview2.exe", "transfer/valmax-transfer.exe", "transfer/valmax_core.dll",
}

def require(value, reason):
    if not value:
        raise ValueError(reason)

def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def inventory(root):
    require(root.is_absolute() and root.name == "runtime", "explicit runtime directory required")
    require(all(part not in (".", "..") for part in root.parts), "ambiguous runtime path")
    for parent in (root, *root.parents):
        require(not parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, "reparse ancestor")
    require(root.is_dir(), "runtime directory missing")
    rows, directories, seen, total = [], set(), set(), 0
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        require(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, "reparse payload entry")
        relative = path.relative_to(root).as_posix()
        require(relative.casefold() not in seen, "case-alias payload entry")
        seen.add(relative.casefold())
        require(all(part and part not in (".", "..") and not part.endswith((".", " "))
                    and not any(c in part for c in '<>:"|?*\\\0')
                    and not re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                    and part.casefold() not in {"mxmod", "oneme", ".mxmod-helper-temp"}
                    for part in path.relative_to(root).parts), "unsafe payload component")
        require(relative.split("/", 1)[0] in {"mod", "webview2", "transfer"}, "unexpected runtime branch")
        if stat.S_ISDIR(info.st_mode):
            directories.add(relative)
            continue
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "nonregular or hardlinked payload")
        require(path.suffix.lower() not in {".py", ".pyc", ".pyd", ".mdata", ".dmp", ".pdb", ".lib"}, "development/private payload file")
        if relative == MUTABLE:
            require(info.st_size <= 4096, "oversized mutable snapshot")
            continue
        total += info.st_size
        require(info.st_size <= 1 << 34 and total <= 8 << 30 and len(rows) < 16384, "payload bound")
        rows.append({"path": relative, "size": info.st_size, "sha256": file_hash(path)})
    paths = {row["path"] for row in rows}
    require(REQUIRED <= paths, "required native payload component missing")
    derived = set()
    for relative in paths:
        parent = Path(relative).parent
        while parent.as_posix() != ".":
            derived.add(parent.as_posix())
            parent = parent.parent
    require(directories == derived and len(directories) <= 8192, "empty/untracked payload directory")
    return rows

def generate(root, output, receipt):
    rows = inventory(root)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    lines = ["// Build-generated complete runtime inventory; no runtime path/hash override.",
             "#pragma once", '#include "preflight.h"', "namespace valmax::launcher::pins {",
             f'inline constexpr char kInventorySha256[]="{digest}";', "inline constexpr PayloadPin kFiles[]={"]
    lines += [f'{{L{json.dumps(r["path"], ensure_ascii=True)},{r["size"]}ull,"{r["sha256"]}"}},' for r in rows]
    lines += ["};", "}"]
    output.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    # CMake may regenerate after a dependency changes; inputs are checked again
    # by the final packager. This is build output, never a runtime config file.
    output.write_text(text, encoding="utf8")
    receipt.write_text(json.dumps({"schema": 1, "runtimeRoot": str(root), "files": rows,
        "inventorySha256": digest, "mutableFile": MUTABLE,
        "generatorSha256": file_hash(Path(__file__)), "dependencyClosureVerified": False,
        "runtimeExecuted": False}, indent=2) + "\n", encoding="utf8")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    generate(args.runtime_root, args.output, args.receipt)
