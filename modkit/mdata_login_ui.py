"""Native QR/phone entry reuses the existing MData transfer controller."""
from pathlib import Path
from quick_replies_ui import once


def transform_sources(sources):
    result = dict(sources)
    for name in ("PhoneNumberPage.qml", "QrCodePage.qml"):
        source = result[name]
        if "valmaxMDataEntry" in source:
            raise ValueError("MData login entry already inserted")
        source = once(source, "import QtQuick\n", "import QtQuick\nimport QtQuick.Dialogs as Dialogs\nimport OneMeUi as ModUi\n")
        panel = Path(__file__).with_name("mdata_panel.qml").read_text(encoding="utf8")
        panel = panel.replace("SettingsBaseItem {", "ModUi.SettingsBaseItem {", 1)
        panel = panel.replace("if (pendingRequest === null) root.backClicked();", "return;")
        controller = '''
    Loader {
        id: valmaxMDataEntry
        visible: false; width: 0; height: 0
        sourceComponent: settingsModMData
    }
'''
        source = once(source, "    id: root\n", "    id: root\n" + controller + panel + "\n")
        button = '''
        UiKit.MeAccentButton {
            objectName: "valmaxMDataLoginButton"
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: UiKit.DesignSystem.SpacingCommon._S
            text: "Войти с MData"
            mode: UiKit.DesignSystem.Mode.Link
            enabled: valmaxMDataEntry.item !== null && valmaxMDataEntry.item.available && !valmaxMDataEntry.item.busy
            onClicked: valmaxMDataEntry.item.chooseImport()
            Accessible.name: text
        }
'''
        # The button is a sibling of the native login footer, on a separate line.
        if name == "PhoneNumberPage.qml":
            anchor = '    }\n\n    Accessible.id: "WelcomePage"'
        else:
            anchor = '    }\n\n    Accessible.id: "QrCodePage"'
        source = once(source, anchor, button + anchor)
        result[name] = source
    return result
