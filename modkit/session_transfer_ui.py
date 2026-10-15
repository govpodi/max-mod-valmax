"""Insert the native transfer page into the pinned settings source only."""
from pathlib import Path
from quick_replies_ui import once


def transform_leading(source: str) -> str:
    if "settingsModSessionTransfer" in source:
        raise ValueError("Transfer transform already applied")
    source = once(source, "import QtQuick\n", "import QtQuick\nimport QtQuick.Dialogs as Dialogs\n")
    panel = Path(__file__).with_name("session_transfer_panel.qml").read_text(encoding="utf8")
    source = once(source, "    Component {\n        id: settingsDev\n", panel + "\n    Component {\n        id: settingsDev\n")
    entry = '''                OpenPageSettingsCellImpl {
                    title: "Перенос аккаунтов"
                    iconSource: "qrc:/icon_pack/devices.svg"
                    settingsPage: settingsModSessionTransfer
                }

'''
    return once(source, '                OpenPageSettingsCellImpl {\n                    title: "Быстрые ответы"', entry + '                OpenPageSettingsCellImpl {\n                    title: "Быстрые ответы"')
