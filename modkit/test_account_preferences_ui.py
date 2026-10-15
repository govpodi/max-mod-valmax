"""Version-pinned source-transform contracts; no native processes or accounts."""
from pathlib import Path
import unittest

from account_preferences_ui import REQUIRED_RESOURCES, OPTIONAL_RESOURCES, transform_sources


NATIVE = Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi")


@unittest.skipUnless(all((NATIVE / name).is_file() for name in REQUIRED_RESOURCES), "Pinned decoded native resources unavailable")
class AccountPreferencesTransformTests(unittest.TestCase):
    def setUp(self):
        self.sources = {name: (NATIVE / name).read_text(encoding="utf8") for name in (*REQUIRED_RESOURCES, *OPTIONAL_RESOURCES)}

    def test_all_input_resources_retained_and_unrelated_source_untouched(self):
        self.sources["Unrelated.qml"] = "untouched synthetic content"
        result = transform_sources(self.sources)
        self.assertEqual(set(self.sources), set(result))
        self.assertEqual(self.sources["Unrelated.qml"], result["Unrelated.qml"])
        self.assertNotIn("settingsValMAXAccounts", self.sources["LeadingSettingsBar.qml"])

    def test_page_and_named_entry_use_existing_native_container(self):
        source = transform_sources(self.sources)["LeadingSettingsBar.qml"]
        self.assertEqual(1, source.count("id: settingsValMAXAccounts"))
        self.assertEqual(1, source.count("settingsPage: settingsValMAXAccounts"))
        self.assertIn('"http://127.0.0.1:18973/account-settings"', source)
        self.assertIn('"X-Max-Mod-Local-Write", "account-settings-v1"', source)
        self.assertNotIn('text: "Режим призрака"', source)

    def test_scheduler_is_inside_native_account_view_and_adds_no_layout_page(self):
        source = transform_sources(self.sources)["AccountsStackView.qml"]
        self.assertEqual(self.sources["AccountsStackView.qml"].count("Ui.MessengerView {"), source.count("Ui.MessengerView {"))
        self.assertEqual(self.sources["AccountsStackView.qml"].count("    Repeater {"), source.count("    Repeater {"))
        self.assertLess(source.index("Ui.MessengerView {"), source.index("id: modAccountView"))
        self.assertIn("dependency.leadingPanelDependency.settings.notificationContext", source)
        self.assertIn("modSchedulePreviousMuted", source)
        self.assertIn("modReleaseScheduleMute()", source)
        self.assertNotIn("activeAccountDependency", source)
        self.assertNotIn('xhr.open("POST"', source)

    def test_account_labels_use_exact_id_strings_and_keep_native_fallback(self):
        result = transform_sources(self.sources)
        for name in ("SettingsAccountSelectorDelegate.qml", "ProfileInfoCell.qml", "AccountButton.qml"):
            with self.subTest(resource=name):
                source = result[name]
                self.assertIn("QmlUtils.contactIdString(commonDependency.selfInfo.id)", source)
                self.assertIn("root.modAccountAlias ||", source)
                self.assertIn("root.modAccountColor", source)
                self.assertIn('xhr.open("GET"', source)
                self.assertNotIn('xhr.open("POST"', source)

    def test_optional_footer_can_be_omitted_and_double_apply_fails(self):
        del self.sources["AccountButton.qml"]
        result = transform_sources(self.sources)
        self.assertEqual(set(self.sources), set(result))
        with self.assertRaises(ValueError): transform_sources(result)

    def test_missing_resource_and_ambiguous_native_anchor_fail_closed(self):
        for name in REQUIRED_RESOURCES:
            with self.subTest(resource=name):
                sources = dict(self.sources); del sources[name]
                with self.assertRaises(ValueError): transform_sources(sources)
        sources = dict(self.sources)
        sources["ProfileInfoCell.qml"] += "\n            avatarSize: root.avatarImageSize\n"
        with self.assertRaises(ValueError): transform_sources(sources)


if __name__ == "__main__":
    unittest.main()
