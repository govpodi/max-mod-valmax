"""Read-only pinned native routing contracts; no browser or account access."""
from pathlib import Path
import re
import unittest

from business_ui import REQUIRED_RESOURCES, transform_leading, transform_sources

NATIVE = Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi")


@unittest.skipUnless(all((NATIVE / name).is_file() for name in REQUIRED_RESOURCES), "Pinned decoded native resources unavailable")
class BusinessTransformsTests(unittest.TestCase):
    def setUp(self):
        self.sources = {name: (NATIVE / name).read_text(encoding="utf8") for name in REQUIRED_RESOURCES}

    def test_preserves_input_and_unrelated_sources(self):
        self.sources["Other.qml"] = "untouched synthetic resource"
        result = transform_sources(self.sources)
        self.assertEqual(set(self.sources), set(result))
        self.assertEqual("untouched synthetic resource", result["Other.qml"])
        self.assertNotIn("settingsValMAXBusiness", self.sources["LeadingSettingsBar.qml"])

    def test_enum_appends_business_and_retains_existing_numbers(self):
        source = transform_sources(self.sources)["LeadingPanel.qml"]
        body = re.search(r"enum Page\s*\{([^}]+)\}", source).group(1)
        self.assertEqual(["Chats", "Calls", "Contacts", "Settings", "Business"], [name.strip() for name in body.split(",")])

    def test_business_entry_opens_internal_component_instead_of_external_dispatch(self):
        leading = transform_leading(self.sources["LeadingSettingsBar.qml"])
        self.assertEqual(1, leading.count("id: settingsValMAXBusiness"))
        self.assertNotIn("settingsPage: settingsValMAXBusiness", leading)
        self.assertIn("property alias pageValMAXBusiness: settingsValMAXBusiness", leading)
        self.assertNotIn("clickDispatcher.onUrlClicked(root.commonDependency.serverParameters.settingsBusiness)", leading)
        self.assertNotIn("Qt.openUrlExternally", Path(__file__).with_name("business_panel.qml").read_text(encoding="utf8"))

    def test_side_button_routes_through_native_stack_and_restores_settings(self):
        result = transform_sources(self.sources)
        self.assertIn('Accessible.id: "ValMAXBusinessSideButton"', result["SideMenu.qml"])
        self.assertIn("onClicked: root.openBusinessClicked()", result["SideMenu.qml"])
        self.assertIn("listBar.openBusiness();", result["LeadingPanel.qml"])
        self.assertIn("currentItem.pageValMAXBusiness", result["LeadingListBar.qml"])
        self.assertIn("if (root.selectedPage === LeadingPanel.Page.Business) root.selectedPage = LeadingPanel.Page.Settings;", result["LeadingPanel.qml"])

    def test_desktop_uses_full_width_pane_and_mobile_open_settings_state(self):
        messenger = transform_sources(self.sources)["MessengerView.qml"]
        self.assertIn("readonly property bool isOpenSettingsPage: selectedPage === LeadingPanel.Page.Settings || selectedPage === LeadingPanel.Page.Business", messenger)
        self.assertIn('name: "valmaxBusinessWorkspace"', messenger)
        self.assertIn("when: !windowState.isOneFrame && leadingPanel.selectedPage === LeadingPanel.Page.Business", messenger)
        self.assertIn("SplitView.maximumWidth: windowState.leftSideMenuWidth", messenger)
        self.assertIn("showListBar: false", messenger)
        self.assertIn('name: "oneFrameHideListBar"', messenger)

    def test_engine_import_is_lazy_and_does_not_require_missing_webengine(self):
        panel = Path(__file__).with_name("business_panel.qml").read_text(encoding="utf8")
        self.assertIn("Qt.createQmlObject(`", panel)
        self.assertIn("import QtWebView", panel)
        self.assertNotIn("QtWebEngine", panel)
        self.assertIn('businessHome: "https://business.max.ru/self/"', panel)
        self.assertNotIn("code_challenge", panel)
        self.assertNotIn("redirect_state", panel)
        self.assertNotIn("XMLHttpRequest", panel)
        self.assertNotIn("deleteAllCookies", panel)

    def test_source_transform_is_atomic_on_missing_or_ambiguous_anchor(self):
        for name in REQUIRED_RESOURCES:
            sources = dict(self.sources); del sources[name]
            with self.assertRaises(ValueError): transform_sources(sources)
        modified = dict(self.sources); modified["SideMenu.qml"] += "\n        UiKit.VerticalSpacer {}\n"
        with self.assertRaises(ValueError): transform_sources(modified)
        self.assertNotIn("Page.Business", self.sources["LeadingPanel.qml"])

    def test_double_apply_is_rejected(self):
        with self.assertRaises(ValueError): transform_sources(transform_sources(self.sources))


if __name__ == "__main__":
    unittest.main()
