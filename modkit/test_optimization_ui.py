"""Fail-closed source, schema and native SDK BETA-insertion contracts."""
from pathlib import Path
import unittest

import optimization_ui as target

HERE = Path(__file__).parent
NATIVE = Path('D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi')


def leading_fixture():
    from quick_replies_ui import transform_sources
    from mdata_ui import transform_leading
    from account_preferences_ui import transform_sources as accounts
    from message_retention_ui import transform_leading as retention
    names = ('LeadingSettingsBar.qml', 'BaseSendPanel.qml', 'MessageTextInput.qml', 'BaseMessageSendPanel.qml', 'AccountsStackView.qml', 'SettingsAccountSelectorDelegate.qml', 'ProfileInfoCell.qml', 'AccountButton.qml')
    values = {name: (NATIVE / name).read_text('utf8') for name in names}
    values = transform_sources(values)
    values['LeadingSettingsBar.qml'] = transform_leading(values['LeadingSettingsBar.qml'])
    values = accounts(values)
    return retention(values['LeadingSettingsBar.qml'])


class OptimizationUiTests(unittest.TestCase):
    def test_settings_entry_badges_preserve_clean_titles_and_navigation(self):
        source = leading_fixture(); result = target.transform_leading(source)
        self.assertEqual(1, result.count('id: settingsValMAXOptimization'))
        self.assertEqual(1, result.count('settingsPage: settingsValMAXOptimization'))
        for title in ('MData', 'Аккаунты ValMAX', 'Удалённые сообщения', 'Оптимизация'):
            self.assertIn('ValMAXBetaSettingsCellImpl {\n                    title: "' + title + '"', result)
            self.assertNotIn('title: "' + title + ' BETA"', result)
        for value in ('settingsPage: settingsModMData', 'settingsPage: settingsValMAXAccounts', 'settingsPage: settingsValMAXRetention'):
            self.assertEqual(source.count(value), result.count(value))
        self.assertIn('component ValMAXBetaSettingsCellImpl: OpenPageSettingsCellImpl', result)
        self.assertIn('UiKit.MeHighlightedText {', result)
        self.assertIn('qrc:/icon_pack/flash.svg', result)
        self.assertEqual(1, result.count(target.CONFIG_IMPORT))
        with self.assertRaises(ValueError): target.transform_leading(result)

    def test_stable_candidate_removes_settings_and_optimization_badges(self):
        source = leading_fixture()
        beta = target.transform_leading(source)
        self.assertEqual(beta, target.transform_leading(source, release_channel="beta"))
        stable = target.transform_leading(source, release_channel="stable-candidate")
        self.assertIn(target.RETENTION_BETA_NOTICE, beta)
        self.assertNotIn(target.RETENTION_BETA_NOTICE, stable)
        self.assertIn(target.RETENTION_STABLE_NOTICE, stable)
        self.assertEqual(beta.count('visible: !retentionPage.runtimeTrusted || !retentionPage.runtime || !retentionPage.runtime.historySyncVerified'),
                         stable.count('visible: !retentionPage.runtimeTrusted || !retentionPage.runtime || !retentionPage.runtime.historySyncVerified'))
        self.assertNotIn(target.BETA_MARKER, stable)
        self.assertNotIn("ValMAXBetaSettingsCellImpl", stable)
        self.assertNotIn('objectName: "optimizationBeta"', stable)
        self.assertNotIn('Accessible.name: "BETA · функция тестируется"', stable)
        self.assertIn('objectName: "optimizationBeta"', beta)
        for title in ("MData", "Аккаунты ValMAX", "Удалённые сообщения", "Оптимизация"):
            self.assertIn('OpenPageSettingsCellImpl {\n                    title: "' + title + '"', stable)
            self.assertIn('ValMAXBetaSettingsCellImpl {\n                    title: "' + title + '"', beta)
        for value in ('settingsPage: settingsModMData', 'settingsPage: settingsValMAXAccounts',
                      'settingsPage: settingsValMAXRetention', 'settingsPage: settingsValMAXOptimization'):
            self.assertEqual(beta.count(value), stable.count(value))
        self.assertEqual(beta.count('id: settingsValMAXOptimization'), stable.count('id: settingsValMAXOptimization'))
        self.assertEqual(beta.count(target.CONFIG_IMPORT), stable.count(target.CONFIG_IMPORT))
        with self.assertRaises(ValueError): target.transform_leading(stable)
        with self.assertRaises(ValueError): target.transform_leading(source, release_channel="stable")
        with self.assertRaises(ValueError): target.transform_leading(source.replace(target.RETENTION_BETA_NOTICE, target.RETENTION_STABLE_NOTICE), release_channel="stable-candidate")

    def test_stable_candidate_hides_export_badges_but_keeps_partial_history_warning(self):
        from chat_export_ui import transform
        from chat_lock_ui import transform_export_panel
        sources = {'LeadingSettingsBar.qml': leading_fixture(),
                   'ContactMoreMenu.qml': transform((NATIVE / 'ContactMoreMenu.qml').read_text('utf8'))}
        beta = target.transform_sources(sources)
        stable = target.transform_sources(sources, release_channel="stable-candidate")
        self.assertIn('objectName: "valmaxChatExportBeta"', beta['ContactMoreMenu.qml'])
        self.assertIn(target.STABLE_MENU_MARKER, stable['ContactMoreMenu.qml'])
        self.assertNotIn('objectName: "valmaxChatExportBeta"', stable['ContactMoreMenu.qml'])
        self.assertNotIn('Accessible.name: "BETA · функция тестируется"', stable['ContactMoreMenu.qml'])
        self.assertEqual(beta['ContactMoreMenu.qml'].count('text: "Скачать чат"'),
                         stable['ContactMoreMenu.qml'].count('text: "Скачать чат"'))
        panel_source = transform_export_panel((HERE / 'chat_export_panel.qml').read_text('utf8'))
        beta_panel = target.transform_export_panel(panel_source)
        stable_panel = target.transform_export_panel(panel_source, release_channel="stable-candidate")
        self.assertIn('objectName: "valmaxChatExportPanelBeta"', beta_panel)
        self.assertIn(target.STABLE_PANEL_MARKER, stable_panel)
        self.assertIn('Архив чата может быть неполным.', stable_panel)
        self.assertIn('Фото, видео и голосовые сохраняются, когда доступны', stable_panel)
        self.assertIn('Отдельный раздел содержит только входящие сообщения', stable_panel)
        self.assertIn('Локально сохранённые удаления в этот архив не входят', stable_panel)
        self.assertIn('objectName: "valmaxExportLimitations"', stable_panel)
        self.assertNotIn('objectName: "valmaxChatExportPanelBeta"', stable_panel)
        self.assertNotIn('Accessible.name: "BETA · функция тестируется"', stable_panel)
        self.assertNotIn('Бета', stable_panel)
        self.assertNotIn('бета-', stable_panel)
        self.assertIn('text: "История и медиа" +', stable_panel)
        self.assertEqual(panel_source.count('function '), stable_panel.count('function '))
        with self.assertRaises(ValueError): target.transform_export_panel(stable_panel, release_channel="stable-candidate")
        with self.assertRaises(ValueError): target.transform_menu(stable['ContactMoreMenu.qml'], release_channel="stable-candidate")
        with self.assertRaises(ValueError): target.transform_sources(sources, release_channel="stable")
        with self.assertRaises(ValueError): target.transform_menu(sources['ContactMoreMenu.qml'], release_channel="stable")
        with self.assertRaises(ValueError): target.transform_export_panel(panel_source, release_channel="stable")

    def test_badge_menu_preserves_native_actions_and_content_layout(self):
        from chat_export_ui import transform
        native = (NATIVE / 'ContactMoreMenu.qml').read_text('utf8')
        source = transform(native); result = target.transform_menu(source)
        for value in ('onTriggered:', 'root.context.clearHistory(', 'root.contactBlocked()', 'root.stopBot()', 'deleteDialog.createObject(', 'root.finished()'):
            self.assertEqual(source.count(value), result.count(value))
        self.assertIn('parent: valmaxExportBetaItem.contentItem', result)
        self.assertNotIn('contentItem: RowLayout', result)
        self.assertEqual(1, result.count('text: "Скачать чат"'))
        with self.assertRaises(ValueError): target.transform_menu(result)
        with self.assertRaises(ValueError): target.transform_menu(native)

    def test_export_panel_keeps_business_logic_and_adds_sdk_badge(self):
        from chat_lock_ui import transform_export_panel
        source = transform_export_panel((HERE / 'chat_export_panel.qml').read_text('utf8'))
        result = target.transform_export_panel(source)
        self.assertIn('objectName: "valmaxChatExportPanelBeta"', result)
        self.assertIn('Архив чата может быть неполным. Фото, видео и голосовые сохраняются, когда доступны (до 4 ГиБ на файл).', result)
        self.assertIn('Отдельный раздел содержит только входящие сообщения, полученные и локально сохранённые после включения функции', result)
        self.assertIn('text: exportDialog.nativeApi2ExportAvailable && exportDialog.exportMode === "api2-text"', result)
        self.assertIn('Локально сохранённые удаления в этот архив не входят; возможны пропуски.', result)
        self.assertIn('Возможны повторы и пропуски.', result)
        self.assertNotIn('objectName: "valmaxExportBetaWarning"', result)
        self.assertEqual(source.count('function '), result.count('function '))
        self.assertEqual(source.count('title: "Скачать чат"'), result.count('title: "Скачать чат"'))
        self.assertEqual(source.count('nativeChatId'), result.count('nativeChatId'))
        with self.assertRaises(ValueError): target.transform_export_panel(result)

    def test_sources_missing_anchor_and_double_application_fail_closed(self):
        from chat_export_ui import transform
        sources = {'LeadingSettingsBar.qml': leading_fixture(), 'ContactMoreMenu.qml': transform((NATIVE / 'ContactMoreMenu.qml').read_text('utf8')), 'Unrelated.qml': 'untouched'}
        result = target.transform_sources(sources)
        self.assertEqual(set(sources), set(result)); self.assertEqual('untouched', result['Unrelated.qml'])
        for name in target.RESOURCE_NAMES:
            values = dict(sources); del values[name]
            with self.assertRaises(ValueError): target.transform_sources(values)
        values = dict(sources); values['LeadingSettingsBar.qml'] += '                OpenPageSettingsCellImpl {\n                    title: "MData"\n'
        with self.assertRaises(ValueError): target.transform_sources(values)
        with self.assertRaises(ValueError): target.transform_sources(result)

    def test_panel_all_flags_and_restart_semantics(self):
        source = (HERE / 'optimization_panel.qml').read_text('utf8')
        for key in target.FLAG_NAMES:
            self.assertEqual(1, source.count('flag: "' + key + '"'))
        self.assertIn('appliedSettings', source)
        self.assertIn('ValMAXOptimization.OptimizationConfig.loaded', source)
        self.assertIn('ValMAXOptimization.OptimizationConfig.failureCode', source)
        self.assertIn('sameSettings(clientSettings, appliedSettings)', source)
        self.assertIn('!clientSettingsVerified ? "Настройки клиента не подтверждены"', source)
        self.assertIn('request("POST", JSON.stringify({schema: 1, revision: revision, settings:', source)
        self.assertIn('"X-Max-Mod-Local-Write", "optimization-settings-v1"', source)
        self.assertIn('полностью', source.lower()); self.assertIn('трее → «Выход»', source)
        self.assertNotIn('Qt.quit', source); self.assertNotIn('restartClient(', source)
        self.assertIn('const keepDraft = dirty && method !== "POST"', source)
        self.assertIn('contentLoaderItem: UiKit.MeFlickable {', source)
        self.assertIn('ScrollBar.vertical: UiKit.MeScrollBar {}', source)
        self.assertIn('flickableDirection: Flickable.VerticalFlick', source)
        self.assertIn('(flag !== "listPrefetch" || optimizationPage.editorSettings.listScheduling)', source)
        self.assertNotIn('flag !== "readyCache"', source)
        self.assertNotIn('Нужна постепенная отрисовка списков.', source)


if __name__ == '__main__': unittest.main()
