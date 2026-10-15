"""Source-only MData page insertion; keeps the existing native resource URLs."""
from pathlib import Path
from quick_replies_ui import once


def transform_leading(source):
    if "settingsModMData" in source:
        raise ValueError("MData source already inserted")
    if "import QtQuick.Dialogs as Dialogs" not in source:
        source = once(source, "import QtQuick\n", "import QtQuick\nimport QtQuick.Dialogs as Dialogs\n")
    component = Path(__file__).with_name("mdata_panel.qml").read_text(encoding="utf8")
    source = once(source, "    Component {\n        id: settingsDev\n", component + "\n    Component {\n        id: settingsDev\n")
    entry = '''                OpenPageSettingsCellImpl {
                    title: "MData"
                    iconSource: "qrc:/valmax/icons/mdata.svg"
                    settingsPage: settingsModMData
                }

'''
    return once(source, '                OpenPageSettingsCellImpl {\n                    title: "Быстрые ответы"', entry + '                OpenPageSettingsCellImpl {\n                    title: "Быстрые ответы"')
