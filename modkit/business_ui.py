"""Internal business tab in native navigation; no auth or web data is read.

Qt WebView2 storage is application-local in the Qt 6.11 backend. This is one
ValMAX web session, not an additional phone account or a separate session per
phone account. Native runtime isolation must be verified by the root launcher.
The engine loads lazily only when the tab opens; QtWebEngine is not required.
"""
from pathlib import Path

from quick_replies_ui import once

REQUIRED_RESOURCES = ("LeadingSettingsBar.qml", "LeadingListBar.qml", "LeadingPanel.qml", "SideMenu.qml", "MessengerView.qml")


def transform_leading(source, directory=None):
    if "settingsValMAXBusiness" in source:
        raise ValueError("Business page transform already applied")
    directory = Path(directory) if directory is not None else Path(__file__).parent
    source = source.replace("\r\n", "\n")
    source = once(source, "    property alias pageSettingsDev: settingsDev\n", "    property alias pageSettingsDev: settingsDev\n    property alias pageValMAXBusiness: settingsValMAXBusiness\n")
    anchor = "    Component {\n        id: settingsDev\n"
    source = once(source, anchor, (directory / "business_panel.qml").read_text(encoding="utf8") + "\n" + anchor)
    original = '''                OpenPageSettingsCell {
                    visible: root.settingsForBusinessVisible
                    Layout.fillWidth: true
                    title: qsTranslate("Settings", "%1 for business").arg(QmlUtils.productFriendlyName)
                    iconSource: "qrc:/icon_pack/case.svg"
                    onClicked: {
                        root.commonDependency.clickDispatcher.onUrlClicked(root.commonDependency.serverParameters.settingsBusiness);
                        root.settingsUserProfileContext.get().profileButtonClicked("max_for_business");
                    }
                }

                HorizontalSeparatorImpl {
                    visible: root.settingsForBusinessVisible
                }
'''
    # The dedicated side tab owns routing and full-width layout. Remove the
    # old settings link rather than opening the same page in a narrow pane.
    replacement = ""
    return once(source, original, replacement)


def transform_sources(sources, directory=None):
    if any(name not in sources for name in REQUIRED_RESOURCES):
        raise ValueError("Business tab requires all native navigation resources")
    if any("ValMAXBusiness" in sources[name] or "Page.Business" in sources[name] for name in REQUIRED_RESOURCES):
        raise ValueError("Business tab transform already applied")
    result = dict(sources)
    result["LeadingSettingsBar.qml"] = transform_leading(sources["LeadingSettingsBar.qml"], directory)
    bar = sources["LeadingListBar.qml"].replace("\r\n", "\n")
    result["LeadingListBar.qml"] = once(bar, "    function openDevSettings() {\n", '''    function openBusiness() {
        state = "settings";
        root.openSettingsPageClicked(currentItem.pageValMAXBusiness, undefined);
    }

    function openDevSettings() {
''')
    leading = sources["LeadingPanel.qml"].replace("\r\n", "\n")
    leading = once(leading, "        Contacts,\n        Settings\n", "        Contacts,\n        Settings,\n        Business\n")
    leading = once(leading, "if (root.selectedPage === LeadingPanel.Page.Settings) {", "if (root.selectedPage === LeadingPanel.Page.Settings || root.selectedPage === LeadingPanel.Page.Business) {")
    leading = once(leading, "    function openSettings() {\n", "    function openSettings() {\n        if (root.selectedPage === LeadingPanel.Page.Business) root.closeSettingsPageClicked();\n")
    leading = once(leading, "            onOpenSettingsClicked: {\n", '''            onOpenBusinessClicked: {
                if (root.selectedPage === LeadingPanel.Page.Business) return;
                root.selectedPage = LeadingPanel.Page.Business;
                listBar.openBusiness();
                root.closeMainPanelViewIfNeeded();
            }
            onOpenSettingsClicked: {
                if (root.selectedPage === LeadingPanel.Page.Business) root.closeSettingsPageClicked();
''')
    result["LeadingPanel.qml"] = once(leading, "                onCloseSettingsPageClicked: root.closeSettingsPageClicked()", '''                onCloseSettingsPageClicked: {
                    if (root.selectedPage === LeadingPanel.Page.Business) root.selectedPage = LeadingPanel.Page.Settings;
                    root.closeSettingsPageClicked();
                }''')
    menu = sources["SideMenu.qml"].replace("\r\n", "\n")
    menu = once(menu, "    signal openSettingsClicked\n", "    signal openSettingsClicked\n    signal openBusinessClicked\n")
    side_button = '''        SectionButton {
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: root.buttonWidth
            isSelected: root.isSelectedPage(LeadingPanel.Page.Business)
            text: "Бизнес"
            iconSource: "qrc:/valmax/icons/business.svg"
            iconFillColor: isSelected ? root.activeIcon : root.normalIcon
            textFillColor: isSelected ? root.activeText : root.normalText
            onClicked: root.openBusinessClicked()
            Accessible.id: "ValMAXBusinessSideButton"
        }

        FixedSpacer {}

'''
    result["SideMenu.qml"] = once(menu, "        UiKit.VerticalSpacer {}\n", side_button + "        UiKit.VerticalSpacer {}\n")
    messenger = sources["MessengerView.qml"].replace("\r\n", "\n")
    messenger = once(messenger, "readonly property bool isOpenSettingsPage: selectedPage === LeadingPanel.Page.Settings", "readonly property bool isOpenSettingsPage: selectedPage === LeadingPanel.Page.Settings || selectedPage === LeadingPanel.Page.Business")
    anchor = '            states: [\n                State {\n                    name: "oneFrameHideListBar"'
    replacement = '''            states: [
                State {
                    name: "valmaxBusinessWorkspace"
                    when: !windowState.isOneFrame && leadingPanel.selectedPage === LeadingPanel.Page.Business
                    PropertyChanges {
                        target: leadingPanel
                        SplitView.minimumWidth: windowState.leftSideMenuWidth
                        SplitView.preferredWidth: windowState.leftSideMenuWidth
                        SplitView.maximumWidth: windowState.leftSideMenuWidth
                        showListBar: false
                    }
                },
                State {
                    name: "oneFrameHideListBar"'''
    result["MessengerView.qml"] = once(messenger, anchor, replacement)
    return result
