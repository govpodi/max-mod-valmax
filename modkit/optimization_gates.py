"""Final, reversible stage-009 feature gates; keep prior pinned generators exact.

This layer changes performance choices only. PIN masks, input identity checks,
owned popup lifetimes, deletion retention and compiled bubbles stay enabled.
The configuration is one immutable local-file snapshot per QML engine.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

IMPORT = 'import "qrc:/valmax/optimization" as ValMAXOptimization\n'
CONFIG_RESOURCE = ":/valmax/optimization/OptimizationConfig.qml"
QMLDIR_RESOURCE = ":/valmax/optimization/qmldir"
KEYS = ("enabled", "smoothWheel", "listScheduling", "listPrefetch", "readyCache",
        "historyCache", "historyBookkeeping", "photoDecodeResize", "windowPosition",
        "hiddenPageWork", "profileRows")
PINS = {
    "MeWindow.qml": "464515ec0c01dfb7d534ef50ece32932d72c80aa247b3e0fd989b691ff202874",
    "HistoryListView.qml": "a06f3736856bbc0121d4313c05ad164f79a2e3c559ea6f07061c53d5c1b1c1d3",
    "HistoryPreviewCell.qml": "0d025ce46afddfa3c0594fa1abce6147c08837f4f9d5795cc32ec2ab7ed28309",
    "ChatListView.qml": "cd333fdf9abf229d6c62de1f629e346cc264fb5ac72191ef4fc7707a61ff244f",
    "ContactListView.qml": "23ebf53cde19d26e744061e7a261b5a80019c06b72205a340f01faebe72484fd",
    "CallsListView.qml": "2613c0b75755034a986eeea3b36ea733f5481e621a068adce5c19f8df889a055",
    "SidebarPanel.qml": "3fe7f39381f727a8fa6f34296dfbaa30f7960c4fdd839c28716c0bb7779f7f3f",
    "SidebarContactBrief.qml": "9a404197d4369810d1b180b80bca38c9d518dc7a78340372051283f53b74d366",
    "BaseLeadingListHeader.qml": "f2fbe01030c080a2b52b1bcb8006b2222e020b22f81db70a741f5a5fe42da90b",
    "LeadingChatsBar.qml": "836402a67fafb1af70e8af2e3e6084af839d31cffc843913f78cec1335fa32f7",
    "PresenceTextStatusNew.qml": "7a7ca59a358725d8dfc17ae02e892a40eca49a82704af20fabadd159c233cfd6",
    "ChatDelegate.qml": "62ac1942fb12cdd75349c2b0f4e0c741b1445dfbcb796a7a326a9bfa3cc1ec09",
}
EXTRA_PINS = {
    ":/valmax/ValMAXAsyncListRow.qml": "7a8d88412a67243d6994187d45c5bb904d0c651cae01a0ecc78ac8edd92920c1",
    ":/valmax/ValMAXListWorkQueue.qml": "f6ba5494db3cc93b2b372a22e04d5c4fb69f72a0db956f2094695d069e83e1dc",
    ":/valmax/ValMAXWheelAdmission.qml": "ebbe2f04284ab5486dc0855ecee67dd812d73f189a67a7f6db48903261495217",
}


def flag(key: str) -> str:
    if key not in KEYS:
        raise ValueError("Unknown optimization key")
    return f'ValMAXOptimization.OptimizationConfig.isEnabled("{key}")'


def _once(source, before, after, count=1):
    if source.count(before) != count:
        raise ValueError("Optimization gate source anchor drift")
    return source.replace(before, after)


def _checked(source, expected):
    if type(source) is not str:
        raise ValueError("Optimization gate requires source text")
    source = source.replace("\r\n", "\n")
    if hashlib.sha256(source.encode("utf8")).hexdigest() != expected:
        raise ValueError("Optimization gate stage/order/source mismatch")
    return source


def _with_import(source):
    return _once(source, "import QtQuick\n", "import QtQuick\n" + IMPORT)


def transform(source: str, name: str) -> str:
    """Transform only exact final stage-009 text, after every prior generator."""
    if name not in PINS:
        raise ValueError("Unknown optimization resource")
    source = _with_import(_checked(source, PINS[name]))
    if name == "MeWindow.qml":
        for coordinate in ("x", "y"):
            source = _once(source,
                f"    on{coordinate.upper()}Changed: if (valmaxWindowPositionTimer) valmaxWindowPositionTimer.restart()\n",
                f"    on{coordinate.upper()}Changed: {{\n"
                f"        if ({flag('windowPosition')}) {{\n"
                "            if (valmaxWindowPositionTimer) valmaxWindowPositionTimer.restart();\n"
                f"        }} else windowState.{coordinate} = {coordinate};\n    }}\n")
    elif name == "HistoryListView.qml":
        source = _once(source,
            "cacheBuffer: Math.max(500, Math.min(valmaxLargestDelegate, height * 2))",
            f"cacheBuffer: {flag('historyCache')} ? Math.max(500, Math.min(valmaxLargestDelegate, height * 2)) : valmaxLargestDelegate")
        source = _once(source, "Qt.callLater(root.updateLastVisible);",
            f"if ({flag('historyBookkeeping')}) Qt.callLater(root.updateLastVisible);\n"
            "                    else root.updateLastVisible();", 2)
    elif name == "HistoryPreviewCell.qml":
        source = _once(source,
            "if (valmaxDecodeInitialized && valmaxPhotoDecodeTimer)\n                    valmaxPhotoDecodeTimer.restart();",
            "if (valmaxDecodeInitialized && valmaxPhotoDecodeTimer) {\n"
            f"                    if ({flag('photoDecodeResize')}) valmaxPhotoDecodeTimer.restart();\n"
            "                    else valmaxCommitDecodeSize();\n                }")
        source = _once(source, "            asynchronous: true\n            retainWhileLoading:",
            f"            asynchronous: {flag('photoDecodeResize')}\n            retainWhileLoading:")
        source = _once(source, "retainWhileLoading: source.toString() === valmaxDecodedSource.toString()",
            f"retainWhileLoading: {flag('photoDecodeResize')} && source.toString() === valmaxDecodedSource.toString()")
        source = _once(source, "visible: status === Image.Ready || (status === Image.Loading && source.toString() === valmaxDecodedSource.toString())",
            f"visible: !{flag('photoDecodeResize')} || status === Image.Ready || (status === Image.Loading && source.toString() === valmaxDecodedSource.toString())")
        source = _once(source, "sourceSize: valmaxDecodeSize", f"sourceSize: {flag('photoDecodeResize')} ? valmaxDecodeSize : root.sourceSize")
    elif name in ("ChatListView.qml", "ContactListView.qml", "CallsListView.qml"):
        row_height = {"ChatListView.qml": "valmaxPrefetchRowHeight", "ContactListView.qml": "root.valmaxContactRowHeight",
                      "CallsListView.qml": "root.valmaxCallRowHeight"}[name]
        source = _once(source, f"cacheBuffer: Math.max(320, 4 * {row_height})",
            f"cacheBuffer: {flag('listScheduling')} && {flag('listPrefetch')} ? Math.max(320, 4 * {row_height}) : 320")
        if name == "ChatListView.qml":
            source = _once(source, "reuseItems: true", f"reuseItems: {flag('listScheduling')}")
    elif name == "SidebarPanel.qml":
        # The common wheel component owns its on/off path; import is enough to
        # verify this concrete parent viewport remains part of the contract.
        pass
    elif name == "SidebarContactBrief.qml":
        source = _once(source, "                const desired = rows;\n",
            "                const desired = rows;\n"
            f"                if (!{flag('profileRows')}) {{\n"
            "                    valmaxStableRows.clear();\n"
            "                    for (let at = 0; at < desired.length; ++at) valmaxStableRows.append(desired[at]);\n"
            "                    return;\n                }\n")
    elif name in ("BaseLeadingListHeader.qml", "LeadingChatsBar.qml"):
        property_name = "valmaxHeaderActive" if name == "BaseLeadingListHeader.qml" else "valmaxChatsPageActive"
        old = f"readonly property bool {property_name}: root.visible && root.Window.window !== null\n        && root.Window.window.visible && root.Window.window.visibility !== Window.Minimized"
        new = f"readonly property bool {property_name}: !{flag('hiddenPageWork')} || (root.visible && root.Window.window !== null\n        && root.Window.window.visible && root.Window.window.visibility !== Window.Minimized)"
        source = _once(source, old, new)
    elif name == "PresenceTextStatusNew.qml":
        source = _once(source,
            "readonly property bool valmaxVisualActive: root.visible && root.enabled && root.Window.window &&\n        root.Window.window.visible && root.Window.window.visibility !== Window.Minimized",
            f"readonly property bool valmaxVisualActive: !{flag('hiddenPageWork')} || (root.visible && root.enabled && root.Window.window &&\n        root.Window.window.visible && root.Window.window.visibility !== Window.Minimized)")
    elif name == "ChatDelegate.qml":
        # PIN/pool guards stay unconditional, including with master disabled.
        source = _once(source,
            "readonly property bool valmaxVisualActive: root.visible && root.enabled && !root.valmaxPooled && !root.valmaxChatLockMask &&\n        root.Window.window && root.Window.window.visible && root.Window.window.visibility !== Window.Minimized",
            "readonly property bool valmaxVisualActive: root.enabled && !root.valmaxPooled && !root.valmaxChatLockMask &&\n"
            f"        (!{flag('hiddenPageWork')} || (root.visible && root.Window.window && root.Window.window.visible && root.Window.window.visibility !== Window.Minimized))")
    return source


def transform_sources(sources: dict[str, str]) -> dict[str, str]:
    if type(sources) is not dict or not PINS.keys() <= sources.keys():
        raise ValueError("Optimization gates require the final native resources")
    result = dict(sources)
    for name in PINS:
        result[name] = transform(result[name], name)
    return result


def reverse(source: str, original: str, name: str) -> str:
    original = _checked(original, PINS[name])
    if type(source) is not str or source.replace("\r\n", "\n") != transform(original, name):
        raise ValueError("Optimization gate output drift")
    return original


def transform_extra(source: str, resource: str) -> str:
    if resource not in EXTRA_PINS:
        raise ValueError("Unknown optimization helper resource")
    source = _with_import(_checked(source, EXTRA_PINS[resource]))
    if resource.endswith("ValMAXAsyncListRow.qml"):
        source = _once(source, "    property int prefetchRows: 4\n",
            f"    property int prefetchRows: {flag('listScheduling')} && {flag('listPrefetch')} ? 4 : 0\n")
        source = _once(source, "readonly property bool valmaxRetainReady: cacheEligible &&",
            f"readonly property bool valmaxRetainReady: {flag('readyCache')} && cacheEligible &&")
        source = _once(source, "property bool deferNewLoads: view ? view.moving : false",
            f"property bool deferNewLoads: {flag('listScheduling')} && view ? view.moving : false")
        source = _once(source, "        if (workQueue) {\n            grace.stop();",
            f"        if (workQueue && {flag('listScheduling')}) {{\n            grace.stop();")
        source = _once(source, "if (workQueue) workQueue.request(root, true);",
            f"if (workQueue && {flag('listScheduling')}) workQueue.request(root, true);")
        source = _once(source, "        asynchronous: true\n", f"        asynchronous: {flag('listScheduling')}\n")
        source = _once(source, "visible: root.eligible && !root.ready", f"visible: {flag('listScheduling')} && root.eligible && !root.ready")
    elif resource.endswith("ValMAXListWorkQueue.qml"):
        source = _once(source, "readonly property bool viewActive: pageActive &&",
            f"readonly property bool viewActive: {flag('listScheduling')} && pageActive &&")
        source = _once(source, "        if (!view) return;\n", f"        if (!view || !{flag('listScheduling')}) return;\n")
    else:
        source = _once(source, "readonly property bool allowed: pageActive &&",
            f"readonly property bool allowed: {flag('smoothWheel')} && pageActive &&")
        source = _once(source, "        if (presentationWindow)\n",
            f"        if (presentationWindow && {flag('smoothWheel')})\n")
    return source


def transform_extra_resources(resources: dict[str, bytes]) -> dict[str, bytes]:
    if type(resources) is not dict or not EXTRA_PINS.keys() <= resources.keys():
        raise ValueError("Optimization gates require the three shared helpers")
    result = dict(resources)
    for resource in EXTRA_PINS:
        value = result[resource]
        if type(value) is not bytes:
            raise ValueError("Optimization helper resources require bytes")
        result[resource] = transform_extra(value.decode("utf8"), resource).encode("utf8")
    result.update(configuration_resources())
    return result


def configuration_resources() -> dict[str, bytes]:
    return {CONFIG_RESOURCE: Path(__file__).with_name("OptimizationConfig.qml").read_bytes(),
            QMLDIR_RESOURCE: Path(__file__).with_name("optimization_qmldir").read_bytes()}
