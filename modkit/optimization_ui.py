"""Native optimization settings and SDK BETA labels; source-only transforms.

Apply after the existing feature transforms. No SDK type, native menu action,
runtime or profile is changed here. The shared generator keeps all URL, AOT,
baseline and reverse guards. Standalone chat-export content has its own helper.
"""
from pathlib import Path

MARKER = "// VALMAX_OPTIMIZATION_SETTINGS"
BETA_MARKER = "// VALMAX_BETA_SETTINGS_CELL"
MENU_MARKER = "// VALMAX_EXPORT_BETA"
PANEL_MARKER = "// VALMAX_EXPORT_PANEL_BETA"
RESOURCE_NAMES = ("LeadingSettingsBar.qml", "ContactMoreMenu.qml")
CONFIG_IMPORT = 'import "qrc:/valmax/optimization" as ValMAXOptimization\n'
FLAG_NAMES = ("enabled", "smoothWheel", "listScheduling", "listPrefetch", "readyCache", "historyCache", "historyBookkeeping", "photoDecodeResize", "windowPosition", "hiddenPageWork", "profileRows")


def once(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError("Unexpected optimization/BETA source anchor")
    return source.replace(anchor, replacement, 1)


BETA_CELL = '''    // VALMAX_BETA_SETTINGS_CELL
    component ValMAXBetaSettingsCellImpl: OpenPageSettingsCellImpl {
        id: betaSettingsCell
        Accessible.name: title + " · BETA, функция тестируется"
        contentItem: RowLayout {
            spacing: UiKit.DesignSystem.SpacingCommon._M
            UiKit.SvgIcon {
                Layout.alignment: Qt.AlignVCenter
                imageWidth: UiKit.DesignSystem.SizeBase.px24
                imageHeight: UiKit.DesignSystem.SizeBase.px24
                source: betaSettingsCell.iconSource
                fillColor: ClientAppearance.AppearanceTokens.colorIconSecondary
                asynchronous: false
                Accessible.role: Accessible.NoRole
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: UiKit.DesignSystem.SpacingCommon._S
                UiKit.MeText {
                    Layout.maximumWidth: Math.max(0, parent.width - betaSettingsLabel.implicitWidth - 2 * parent.spacing)
                    text: betaSettingsCell.title
                    typography: Typography.FontUtil.Styles.BodyStrong
                    maximumLineCount: 1
                    ignoreLineBreaks: true
                    elide: MeCustomText.Property.ElideRight
                    Accessible.role: Accessible.NoRole
                }
                UiKit.MeHighlightedText {
                    id: betaSettingsLabel
                    objectName: "valmaxBeta_" + betaSettingsCell.title
                    text: "BETA"
                    highlightedTrailing: true
                    textColor: ClientAppearance.AppearanceTokens.colorTextThemed
                    backgroundColor: ClientAppearance.AppearanceTokens.colorStatesBackgroundHighlighted
                    Accessible.name: "BETA · функция тестируется"
                }
                UiKit.SpacerItem {}
            }
            UiKit.SvgIcon {
                Layout.alignment: Qt.AlignVCenter
                imageWidth: UiKit.DesignSystem.SizeBase.px12
                imageHeight: UiKit.DesignSystem.SizeBase.px12
                source: "qrc:/icon_pack/chevron_right_mini.svg"
                fillColor: ClientAppearance.AppearanceTokens.colorIconMute
                asynchronous: false
                Accessible.role: Accessible.NoRole
            }
        }
    }

'''


def transform_leading(source, directory=None):
    if type(source) is not str or MARKER in source or BETA_MARKER in source:
        raise ValueError("Optimization settings require untouched feature output")
    source = source.replace("\r\n", "\n")
    if CONFIG_IMPORT not in source:
        # LeadingSettingsBar also contains a separately scoped inline QtQuick
        # component. Add the import to the document header, not that component.
        if not source.startswith("import QtQuick\n"):
            raise ValueError("Unexpected settings document import header")
        source = "import QtQuick\n" + CONFIG_IMPORT + source[len("import QtQuick\n"):]
    directory = Path(directory) if directory is not None else Path(__file__).parent
    panel = (directory / "optimization_panel.qml").read_text(encoding="utf8").replace("\r\n", "\n")
    source = once(source, "    Component {\n        id: settingsDev\n",
                  "    " + MARKER + "\n" + BETA_CELL + panel.rstrip() + "\n\n    Component {\n        id: settingsDev\n")
    for title in ("MData", "Аккаунты ValMAX", "Удалённые сообщения"):
        anchor = '                OpenPageSettingsCellImpl {\n                    title: "' + title + '"\n'
        source = once(source, anchor, anchor.replace("OpenPageSettingsCellImpl", "ValMAXBetaSettingsCellImpl", 1))
    entry = '''                ValMAXBetaSettingsCellImpl {
                    title: "Оптимизация"
                    iconSource: "qrc:/icon_pack/flash.svg"
                    settingsPage: settingsValMAXOptimization
                }

'''
    anchor = "                UiKit.MeLoader {\n                    id: accountSelector\n"
    return once(source, anchor, entry + anchor)


def transform_menu(source):
    if type(source) is not str or MENU_MARKER in source:
        raise ValueError("Export BETA requires untouched export-menu output")
    source = source.replace("\r\n", "\n")
    if "import QtQuick.Layouts\n" not in source:
        source = once(source, "import QtQuick\n", "import QtQuick\nimport QtQuick.Layouts\n")
    anchor = '''    // VALMAX_CHAT_EXPORT
    UiKit.ActionMenuItem {
        text: "Скачать чат"
'''
    item = '''    // VALMAX_CHAT_EXPORT
    UiKit.ActionMenuItem {
        id: valmaxExportBetaItem
        text: "Скачать чат"
        // VALMAX_EXPORT_BETA
        UiKit.MeHighlightedText {
            objectName: "valmaxChatExportBeta"
            parent: valmaxExportBetaItem.contentItem
            Layout.alignment: Qt.AlignVCenter
            text: "BETA"
            highlightedTrailing: true
            textColor: valmaxExportBetaItem.chatAppearance.colorTextThemed
            backgroundColor: valmaxExportBetaItem.chatAppearance.colorStatesBackgroundHighlighted
            Accessible.name: "BETA · функция тестируется"
        }
'''
    return once(source, anchor, item)


def transform_export_panel(source):
    """Replace the plain beta warning with the native SDK pill and same warning."""
    if type(source) is not str or PANEL_MARKER in source:
        raise ValueError("Export panel BETA requires untouched export panel")
    source = source.replace("\r\n", "\n")
    warning = '''            UiKit.MeText { objectName: "valmaxExportBetaWarning"; Layout.fillWidth: true; text: exportDialog.nativeApi2ExportAvailable && exportDialog.exportMode === "api2-text"
                ? "Бета: архив чата может быть неполным. Фото, видео и голосовые сохраняются, когда доступны (до 4 ГиБ на файл). Отдельный раздел содержит только входящие сообщения, полученные и локально сохранённые после включения функции; прежние и недоступные удаления не восстановятся. Возможны повторы и пропуски."
                : "Бета: архив чата может быть неполным. В режиме «История из клиента» сохраняются показанные клиентом сообщения и доступные вложения. Локально сохранённые удаления в этот архив не входят; возможны пропуски."; wrapMode: Text.WordWrap; color: exportDialog.colors.colorTextAttention; typography: ExportTypography.FontUtil.Styles.Description }
'''
    source = once(source, warning, "")
    anchor = '''        dialogContentItem: ColumnLayout {
            spacing: 8
'''
    return once(source, anchor, anchor + '''            // VALMAX_EXPORT_PANEL_BETA
            RowLayout {
                Layout.fillWidth: true
                spacing: UiKit.DesignSystem.SpacingCommon._S
                UiKit.MeHighlightedText {
                    objectName: "valmaxChatExportPanelBeta"
                    text: "BETA"
                    highlightedTrailing: true
                    textColor: exportDialog.colors.colorTextThemed
                    backgroundColor: exportDialog.colors.colorStatesBackgroundHighlighted
                    Accessible.name: "BETA · функция тестируется"
                }
                UiKit.MeText {
                    objectName: "valmaxExportBetaExplanation"
                    Layout.fillWidth: true
                    text: exportDialog.nativeApi2ExportAvailable && exportDialog.exportMode === "api2-text"
                        ? "Архив чата может быть неполным. Фото, видео и голосовые сохраняются, когда доступны (до 4 ГиБ на файл). Отдельный раздел содержит только входящие сообщения, полученные и локально сохранённые после включения функции; прежние и недоступные удаления не восстановятся. Возможны повторы и пропуски."
                        : "Архив чата может быть неполным. В режиме «История из клиента» сохраняются показанные клиентом сообщения и доступные вложения. Локально сохранённые удаления в этот архив не входят; возможны пропуски."
                    wrapMode: Text.WordWrap
                    typography: ExportTypography.FontUtil.Styles.Description
                    color: exportDialog.colors.colorTextSecondary
                }
            }
''')


def transform_sources(sources, directory=None):
    if type(sources) is not dict or any(name not in sources for name in RESOURCE_NAMES):
        raise ValueError("Optimization/BETA requires settings and export menu")
    result = dict(sources)
    result["LeadingSettingsBar.qml"] = transform_leading(sources["LeadingSettingsBar.qml"], directory)
    result["ContactMoreMenu.qml"] = transform_menu(sources["ContactMoreMenu.qml"])
    return result
