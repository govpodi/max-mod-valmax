"""Native help/diagnostic page; apply after the feature settings transforms."""
from pathlib import Path

MARKER = "// VALMAX_SUPPORT_DIAGNOSTICS"


def transform_leading(source, directory=None):
    from quick_replies_ui import once
    if type(source) is not str or MARKER in source:
        raise ValueError("Support settings require untouched feature output")
    source = source.replace("\r\n", "\n")
    if "import QtQuick.Dialogs as Dialogs\n" not in source:
        source = once(source, "import QtQuick\n", "import QtQuick\nimport QtQuick.Dialogs as Dialogs\n")
    directory = Path(directory) if directory is not None else Path(__file__).parent
    panel = (directory / "support_panel.qml").read_text(encoding="utf8").replace("\r\n", "\n")
    source = once(source, "    Component {\n        id: settingsDev\n",
        "    " + MARKER + "\n" + panel.rstrip() + "\n\n    Component {\n        id: settingsDev\n")
    entry = '''                OpenPageSettingsCellImpl {
                    title: "Помощь и диагностика"
                    iconSource: "qrc:/icon_pack/info.svg"
                    settingsPage: settingsValMAXSupport
                }

'''
    return once(source, "                UiKit.MeLoader {\n                    id: accountSelector\n",
        entry + "                UiKit.MeLoader {\n                    id: accountSelector\n")
