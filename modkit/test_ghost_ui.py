"""Version-pinned page insertion; no native client, profile or API calls."""
from pathlib import Path
import unittest

from ghost_ui import transform_leading, transform_sources

NATIVE = Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi/LeadingSettingsBar.qml")


@unittest.skipUnless(NATIVE.is_file(), "Pinned decoded native resource unavailable")
class GhostTransformTests(unittest.TestCase):
    def setUp(self):
        self.source = NATIVE.read_text(encoding="utf8")

    def test_native_page_entry_and_existing_privacy_preserved(self):
        source = transform_leading(self.source)
        self.assertEqual(1, source.count("id: settingsValMAXGhost"))
        self.assertEqual(1, source.count("settingsPage: settingsValMAXGhost"))
        self.assertEqual(self.source.count("settingsPage: settingsPrivacy"), source.count("settingsPage: settingsPrivacy"))
        self.assertIn('iconSource: "qrc:/valmax/icons/ghost.svg"', source)
        self.assertIn('title: "Режим призрака"', source)
        self.assertIn('property var selfComponent: settingsValMAXGhost', source)
        self.assertIn('UiKit.MeSwitcher { objectName: "ghostEnabled"', source)
        self.assertIn('UiKit.MeCheckbox { objectName: "ghostHideRead"', source)

    def test_input_preserved_and_unrelated_resources_retained(self):
        sources = {"LeadingSettingsBar.qml": self.source, "Other.qml": "synthetic untouched"}
        result = transform_sources(sources)
        self.assertEqual(set(sources), set(result))
        self.assertEqual("synthetic untouched", result["Other.qml"])
        self.assertEqual(self.source, sources["LeadingSettingsBar.qml"])
        with self.assertRaises(ValueError):
            transform_sources({"Other.qml": "synthetic"})

    def test_duplicate_apply_and_drifted_native_anchors_rejected(self):
        with self.assertRaises(ValueError):
            transform_leading(transform_leading(self.source))
        for anchor in ('    Component {\n        id: settingsDev\n',
                       '                OpenPageSettingsCellImpl {\n                    title: qsTranslate("Settings", "Privacy")\n'):
            source = self.source.replace("\r\n", "\n")
            for replacement in ("", anchor + anchor):
                with self.subTest(anchor=anchor, duplicate=bool(replacement)):
                    with self.assertRaises(ValueError):
                        transform_leading(source.replace(anchor, replacement))

    def test_contract_has_four_capability_checked_filters_and_no_fake_controls(self):
        panel = Path(__file__).with_name("ghost_panel.qml").read_text(encoding="utf8")
        self.assertIn('"http://127.0.0.1:18973/ghost-settings"', panel)
        self.assertIn('"X-Max-Mod-Local-Write", "ghost-settings-v1"', panel)
        for forbidden in ("editorAutoOffline", "accountId", "sendPressed(", "auth", "localStorage"):
            self.assertNotIn(forbidden, panel)
        self.assertIn('objectName: "ghostHideOnline"', panel)
        self.assertIn('objectName: "ghostHideStories"', panel)
        self.assertIn('ghostPage.runtime.features.hideOnline', panel)
        self.assertIn('ghostPage.runtime.features.hideStories', panel)
        self.assertIn('interval: 2000; repeat: true', panel)
        self.assertIn('method === "POST" ? 10000 : 7000', panel)


if __name__ == "__main__":
    unittest.main()
