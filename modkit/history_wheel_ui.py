"""Pinned history-only bounded ordinary wheel admission; analysis candidate.

Apply after current stage003 history cache, scroll bookkeeping and retention.
Native WheelHandler/pagination, read filters and bubbles remain byte-exact.
"""
import hashlib
from pathlib import Path
RESOURCE = ":/qt/qml/OneMeUi/HistoryListView.qml"
RESOURCES = (RESOURCE,)
WHEEL_RESOURCE = ":/valmax/ValMAXWheelAdmission.qml"
SOURCE_SHA256 = "b3917edef153c8967f0ae9db8888dacd8041d1aaa7d78b8cb4ce13de25951b0f"
WHEEL_SHA256 = "ebbe2f04284ab5486dc0855ecee67dd812d73f189a67a7f6db48903261495217"
EDITS = [('import QtQuick.Layouts\n', 'import QtQuick.Layouts\nimport "qrc:/valmax" as ValMAX\n'), ('            id: listView\n', '            id: listView\n\n            // VALMAX_HISTORY_WHEEL_ADMISSION\n            ValMAX.ValMAXWheelAdmission {\n                id: valmaxHistoryWheel\n                view: listView\n                pixelsPerNotch: Math.max(0, Qt.styleHints.wheelScrollLines) * 36\n                pageActive: root.isActiveChat && root.isHistoryPanelActive\n                owner: root.chatHistoryContext.get()\n                accountOwner: root.commonDependency\n                externalControlActive: scrollbar.pressed || listView.scrollingFromDelegate || newMessageScrollAnimation.running\n                supportedGeometry: listView.orientation === ListView.Vertical &&\n                    listView.verticalLayoutDirection === ListView.TopToBottom &&\n                    listView.snapMode === ListView.NoSnap &&\n                    listView.highlightRangeMode !== ListView.StrictlyEnforceRange\n                onMovementStarted: scrollbar.active = true\n                onMovementEnded: {\n                    currentDateHideTimer.restart();\n                    scrollbar.active = listView.moving || (scrollbar.interactive && (scrollbar.pressed || scrollbar.hovered));\n                }\n            }\n            Connections {\n                target: listView\n                function onModelChanged() { valmaxHistoryWheel.cancel(); }\n            }\n            Connections {\n                target: listView.model\n                function onModelReset() { valmaxHistoryWheel.cancel(); }\n            }\n            Connections {\n                target: root.chatPropertiesContext.get()\n                function onChatIdChanged() { valmaxHistoryWheel.cancel(); }\n            }\n            Connections {\n                target: scrollbar\n                function onActiveChanged() {\n                    if (valmaxHistoryWheel.moving && !scrollbar.active) scrollbar.active = true;\n                }\n            }\n'), ('(currentDateHideTimer.running || listView.moving)', '(currentDateHideTimer.running || listView.moving || valmaxHistoryWheel.moving)'), ('                Connections {\n                    target: listView\n\n                    function onMovementStarted() {\n                        historyMenuHandler.close();\n                    }', '                Connections {\n                    target: valmaxHistoryWheel\n                    function onMovementStarted() { historyMenuHandler.close(); }\n                }\n\n                Connections {\n                    target: listView\n\n                    function onMovementStarted() {\n                        historyMenuHandler.close();\n                    }'), ('            Keys.onPressed: event => {\n', '            Keys.onPressed: event => {\n                valmaxHistoryWheel.cancel();\n')]

def checked(source):
    if type(source) is not str:
        raise ValueError("History wheel requires text")
    source = source.replace("\r\n", "\n")
    if hashlib.sha256(source.encode("utf8")).hexdigest() != SOURCE_SHA256:
        raise ValueError("History wheel version/source/order mismatch")
    return source

def wheel_source():
    source = Path(__file__).with_name("ValMAXWheelAdmission.qml").read_bytes()
    if hashlib.sha256(source).hexdigest() != WHEEL_SHA256:
        raise ValueError("History wheel component/source mismatch")
    return source.decode("utf8")

def transform(source):
    source = checked(source)
    for old, new in EDITS:
        if source.count(old) != 1:
            raise ValueError("History wheel anchor drift")
        source = source.replace(old, new, 1)
    return source

def reverse(source):
    for old, new in reversed(EDITS):
        if source.count(new) != 1:
            raise ValueError("History wheel output drift")
        source = source.replace(new, old, 1)
    return checked(source)

def transform_sources(sources):
    if type(sources) is not dict or "HistoryListView.qml" not in sources:
        raise ValueError("History wheel resource is required")
    result = dict(sources)
    result["HistoryListView.qml"] = transform(result["HistoryListView.qml"])
    return result
