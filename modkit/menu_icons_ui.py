"""Bounded native SDK menu SVG warmup, in the owning MeWindow only."""
from __future__ import annotations

import json

MARKER = "// VALMAX_MENU_ICON_PREWARM"
# Sizes and color keys match bundled MenuItem.qml/Action.qml exactly.
ICON_SPECS = tuple(
    ("qrc:/icon_pack/" + name + ".svg", "colorIconSecondary", 20)
    for name in ("privacy", "forward", "search", "clear_history", "report", "eye_crossed", "eye",
                 "folder_add_to", "folder", "folder_add")
) + (("qrc:/valmax/icons/chat-export.svg", "colorIconSecondary", 20),) + tuple(
    ("qrc:/icon_pack/" + name + ".svg", "colorIconNegative", 20)
    for name in ("minus_round", "block", "delete")
) + (("qrc:/icon_pack/chevron_right.svg", "colorIconPrimary", 16),
     ("qrc:/icon_pack/check.svg", "colorIconSecondary", 16))


def prewarm_source():
    specs = [{"source": source, "colorKey": color, "size": size}
             for source,color,size in ICON_SPECS]
    return '''    // VALMAX_MENU_ICON_PREWARM
    Item {
        id: valmaxMenuIconPrewarm
        objectName: "valmaxMenuIconPrewarm"
        visible: false
        enabled: false
        width: 0
        height: 0
        readonly property var iconSpecs: SPECS
        Repeater {
            model: valmaxMenuIconPrewarm.iconSpecs
            delegate: UiKit.SvgIcon {
                required property var modelData
                source: modelData.source
                fillColor: ClientAppearance.AppearanceTokens[modelData.colorKey]
                imageWidth: modelData.size
                imageHeight: modelData.size
                asynchronous: false
            }
        }
    }
'''.replace("SPECS", json.dumps(specs,ensure_ascii=False,separators=(",",":")))


def transform_window(source):
    if type(source) is not str or MARKER in source:
        raise ValueError("Menu icon warmup requires an unmodified transform")
    source = source.replace("\r\n", "\n")
    anchor = "ApplicationWindow {\n    id: root\n"
    if source.count(anchor) != 1 or source.count("import UiKitModule as UiKit\n") != 1 or source.count("import ClientAppearanceModule as ClientAppearance\n") != 1:
        raise ValueError("Unexpected native MeWindow menu icon anchor/imports")
    return source.replace(anchor,anchor+"\n"+prewarm_source(),1)
