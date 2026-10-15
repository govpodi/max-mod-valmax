"""Support page insertion and explicit-save source contracts."""
from pathlib import Path
import unittest

import support_ui as target

HERE = Path(__file__).parent


class SupportUiTests(unittest.TestCase):
    def fixture(self):
        return 'import QtQuick\n    Component {\n        id: settingsDev\n    }\n                UiKit.MeLoader {\n                    id: accountSelector\n                }\n'

    def test_page_and_entry_insert_once_preserving_other_source(self):
        source = self.fixture(); result = target.transform_leading(source)
        self.assertEqual(1, result.count('id: settingsValMAXSupport'))
        self.assertEqual(1, result.count('settingsPage: settingsValMAXSupport'))
        self.assertEqual(1, result.count('import QtQuick.Dialogs as Dialogs'))
        self.assertEqual(1, result.count('id: settingsDev'))
        with self.assertRaises(ValueError): target.transform_leading(result)

    def test_missing_and_duplicated_anchors_fail_closed(self):
        for source in ('import QtQuick\n', self.fixture() + self.fixture()):
            with self.assertRaises(ValueError): target.transform_leading(source)

    def test_dialog_acceptance_is_the_only_save_dispatch_and_no_polling(self):
        source = (HERE / 'support_panel.qml').read_text('utf8')
        self.assertEqual(1, source.count('supportPage.request("POST", selectedFile.toString())'))
        self.assertIn('onAccepted: supportPage.request("POST", selectedFile.toString())', source)
        self.assertNotIn('onRejected:', source)
        self.assertNotIn('repeat: true', source)
        self.assertIn('"X-Max-Mod-Local-Write", "support-diagnostics-v1"', source)
        self.assertIn('JSON.stringify({schema: 1, file: file})', source)
        self.assertIn('TextEdit.PlainText', source)
        self.assertIn('ScrollBar.vertical: UiKit.MeScrollBar {}', source)


if __name__ == '__main__': unittest.main()
