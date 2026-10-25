// Native-only inline settings component. No HTTP or packet observation.
Component {
    id: settingsModPrivacy
    SettingsBaseItem {
        id: modPrivacyPage
        property var selfComponent: settingsModPrivacy
        title: "Приватность ValMAX"
        onBackClicked: root.backClicked()
        readonly property var report: ValMAXNative.Bridge.privacyReport
        readonly property bool verified: validateReport(report)
        readonly property bool serviceVerified: verified && report.serviceProcessVerified === true
        readonly property string verificationText: verificationMessage(report)
        readonly property color verificationColor: serviceVerified ? successColor : warningColor
        readonly property color primaryText: maxwellColorTokens.colorTextPrimary
        readonly property color secondaryText: maxwellColorTokens.colorTextSecondary
        readonly property color cardSurface: maxwellColorTokens.colorBackgroundCard
        readonly property color successColor: maxwellColorTokens.colorTextPositive
        readonly property color warningColor: maxwellColorTokens.colorTextAttention
        readonly property real groupRadius: UiKit.DesignSystem.BorderRadiusCommon._L
        readonly property real innerPadding: UiKit.DesignSystem.SpacingCommon._M
        readonly property real sectionSpacing: UiKit.DesignSystem.SpacingCommon._2XL
        function validateReport(value) {
            if (!value || value.schema !== 1 || value.state !== "verified"
                || value.buildVerified !== true || value.currentProcessVerified !== true
                || typeof value.serviceProcessVerified !== "boolean" || value.error !== ""
                || !value.groups || value.groups.length !== 6) return false;
            if (value.serviceProcessVerified) {
                if (value.serviceError !== "") return false;
            } else if (["service_not_running", "module_not_loaded", "io_error", "memory_mismatch",
                        "file_mismatch", "not_verified", "invalid_status"].indexOf(value.serviceError) < 0) return false;
            var ids = ["tracer_main", "tracer_service", "ui_statistics", "mytracker", "log_masking", "error_logs"];
            for (var i = 0; i < ids.length; ++i) {
                var state = (i === 1 || i === 3) && !value.serviceProcessVerified ? "verified_package" : "verified_loaded";
                if (!value.groups[i] || value.groups[i].id !== ids[i] || value.groups[i].state !== state) return false;
            }
            return true;
        }
        function groupState(id) {
            if (!verified) return "unavailable";
            for (var i = 0; i < report.groups.length; ++i)
                if (report.groups[i].id === id) return report.groups[i].state;
            return "unavailable";
        }
        function stateLabel(state) {
            if (state === "verified_loaded") return "Проверено в работающем процессе";
            if (state === "verified_package") {
                if (report.serviceError === "service_not_running") return "Патч есть в сборке; служба не найдена";
                if (report.serviceError === "module_not_loaded") return "Патч есть в сборке; модуль службы не загружен";
                if (report.serviceError === "memory_mismatch") return "Патч есть в сборке; код службы не совпал";
                if (report.serviceError === "io_error") return "Патч есть в сборке; проверка службы недоступна";
                return "Патч есть в сборке; служба не подтверждена";
            }
            return "Проверка недоступна";
        }
        function verificationMessage(value) {
            if (!validateReport(value)) return "Проверка не завершена или недоступна. Защита этих точек пока не подтверждена.";
            if (value.serviceProcessVerified) return "Файлы сборки и изменённые участки кода приложения и службы звонков проверены в работающих процессах.";
            if (value.serviceError === "service_not_running") return "Файлы сборки и код приложения проверены. Работающая служба звонков этой копии сейчас не найдена.";
            if (value.serviceError === "module_not_loaded") return "Файлы сборки и код приложения проверены. Обязательный модуль службы звонков не загружен; защита службы не подтверждена.";
            if (value.serviceError === "memory_mismatch") return "Файлы сборки и код приложения проверены. Код работающей службы звонков не совпал с проверенной сборкой; защита службы не подтверждена.";
            if (value.serviceError === "io_error") return "Файлы сборки и код приложения проверены. Не удалось получить доступ к службе звонков или её модулям; защита службы не подтверждена.";
            return "Файлы сборки и код приложения проверены. Подтверждение службы звонков недоступно.";
        }
        component DashboardText: Label {
            property int typography: Typography.FontUtil.Styles.Body
            color: modPrivacyPage.primaryText
            font: Typography.FontUtil.getFont(typography, Typography.FontUtil.Sizes.Default)
            lineHeight: Typography.FontUtil.getLineHeight(typography, Typography.FontUtil.Sizes.Default)
            lineHeightMode: Text.FixedHeight
            textFormat: Text.PlainText; wrapMode: Text.Wrap
        }
        component ProtectionRow: Rectangle {
            id: protectionRow
            property string heading
            property string detail
            property string rule
            readonly property string verificationState: modPrivacyPage.groupState(rule)
            Layout.fillWidth: true
            implicitHeight: protectionContent.implicitHeight + 2 * modPrivacyPage.innerPadding
            color: modPrivacyPage.cardSurface
            radius: modPrivacyPage.groupRadius
            ColumnLayout {
                id: protectionContent
                x: modPrivacyPage.innerPadding; y: modPrivacyPage.innerPadding
                width: parent.width - 2 * modPrivacyPage.innerPadding
                spacing: UiKit.DesignSystem.SpacingCommon._S
                DashboardText { Layout.fillWidth: true; text: protectionRow.heading; typography: Typography.FontUtil.Styles.BodyStrong }
                DashboardText { Layout.fillWidth: true; text: modPrivacyPage.stateLabel(protectionRow.verificationState); color: protectionRow.verificationState === "verified_loaded" ? modPrivacyPage.successColor : modPrivacyPage.warningColor; typography: Typography.FontUtil.Styles.Description }
                DashboardText { Layout.fillWidth: true; text: protectionRow.detail; color: modPrivacyPage.secondaryText; typography: Typography.FontUtil.Styles.Description }
            }
        }
        contentLoaderItem: UiKit.MeFlickable {
            id: modPrivacyScroll
            objectName: "privacyScroll"
            contentWidth: width; contentHeight: modPrivacyContent.implicitHeight + 2 * modPrivacyPage.sectionSpacing
            flickableDirection: Flickable.VerticalFlick
            ScrollBar.vertical: UiKit.MeScrollBar {}
            ColumnLayout {
                id: modPrivacyContent
                y: modPrivacyPage.sectionSpacing
                width: modPrivacyScroll.width
                spacing: modPrivacyPage.innerPadding
                DashboardText { Layout.fillWidth: true; text: "Меньше служебных данных"; typography: Typography.FontUtil.Styles.BodyStrong }
                DashboardText { Layout.fillWidth: true; text: "В этой сборке изменены перечисленные точки сбора данных. Нативный модуль проверяет файлы и код приложения; отдельный наблюдатель не нужен."; color: modPrivacyPage.secondaryText }
                DashboardText { objectName: "privacyVerification"; Layout.fillWidth: true; text: modPrivacyPage.verificationText; color: modPrivacyPage.verificationColor; typography: Typography.FontUtil.Styles.Description }
                ProtectionRow { rule: "tracer_main"; heading: "Отчёты о сбоях приложения"; detail: "Отключены проверяемые вызовы встроенной диагностики Tracer в основном приложении." }
                ProtectionRow { rule: "tracer_service"; heading: "Отчёты о сбоях службы звонков"; detail: modPrivacyPage.serviceVerified ? "Изменённые участки кода Tracer проверены в работающей службе звонков." : "В файле службы отключены вызовы Tracer; статус работающего процесса указан выше." }
                ProtectionRow { rule: "ui_statistics"; heading: "Статистика интерфейса"; detail: "Отключены проверяемые точки начала сессии аналитики и отправки накопленной статистики интерфейса." }
                ProtectionRow { rule: "mytracker"; heading: "MyTracker"; detail: modPrivacyPage.serviceVerified ? "Изменённый участок кода MyTracker проверен в работающей службе звонков." : "В файле службы звонков отключено создание MyTracker; статус работающего процесса указан выше." }
                ProtectionRow { rule: "log_masking"; heading: "Личные данные в журналах"; detail: "Включена штатная маскировка журналов: проверены настройки приложения и файл настроек службы." }
                ProtectionRow { rule: "error_logs"; heading: "Отправка журналов ошибок"; detail: "Отключена настройка отправки журналов ошибок в приложении и файле настроек службы." }
                DashboardText { objectName: "privacyUnknownScope"; Layout.fillWidth: true; text: "Здесь показаны только перечисленные локальные настройки. Сервис MAX по-прежнему видит адрес подключения и данные, необходимые для работы аккаунта."; color: modPrivacyPage.secondaryText; typography: Typography.FontUtil.Styles.Description }
            }
        }
    }
}
