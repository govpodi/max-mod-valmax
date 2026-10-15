// Inserted as an inline settings Component by ui_privacy.py.
Component {
    id: settingsModPrivacy
    SettingsBaseItem {
        id: modPrivacyPage
        property var selfComponent: settingsModPrivacy
        title: "Приватность ValMAX"
        onBackClicked: root.backClicked()
        property var report: null
        property string statusText: "Запусти MAX Mod с диагностикой, чтобы увидеть наблюдения."
        property var pendingRequest: null
        readonly property var observation: report ? report.observation || null : null
        readonly property var dashboard: report ? report.dashboard || null : null
        readonly property bool observing: observation !== null && observation.active === true
        readonly property color primaryText: maxwellColorTokens.colorTextPrimary
        readonly property color secondaryText: maxwellColorTokens.colorTextSecondary
        readonly property color cardSurface: maxwellColorTokens.colorBackgroundCard
        readonly property color lineColor: maxwellColorTokens.colorDividerPrimary
        readonly property color accentColor: maxwellColorTokens.colorTextThemed
        readonly property color successColor: maxwellColorTokens.colorTextPositive
        readonly property color warningColor: maxwellColorTokens.colorTextAttention
        readonly property real groupRadius: UiKit.DesignSystem.BorderRadiusCommon._L
        readonly property real innerPadding: UiKit.DesignSystem.SpacingCommon._M
        readonly property real sectionSpacing: UiKit.DesignSystem.SpacingCommon._2XL
        function count(value) {
            if (typeof value !== "number" || !isFinite(value) || value < 0) return "—";
            return String(Math.floor(value)).replace(/\B(?=(\d{3})+(?!\d))/g, "\u202f");
        }
        function metric(key) { return count(dashboard ? dashboard[key] : null); }
        function quantity(value, one, few, many) {
            var text = count(value);
            if (text === "—") return text + " " + many;
            var n = Math.floor(value), ending = n % 100 >= 11 && n % 100 <= 14 ? many : n % 10 === 1 ? one : n % 10 >= 2 && n % 10 <= 4 ? few : many;
            return text + " " + ending;
        }
        function typeCount() { return dashboard && dashboard.requestTypesComplete === false ? "не менее " + metric("requestTypes") + " типов" : quantity(dashboard ? dashboard.requestTypes : null, "тип", "типа", "типов"); }
        function bytes(value) {
            if (typeof value !== "number" || !isFinite(value) || value < 0) return "—";
            var units = ["Б", "КБ", "МБ", "ГБ"], index = 0;
            while (value >= 1024 && index < units.length - 1) { value /= 1024; index++; }
            return (index === 0 ? Math.floor(value) : value.toFixed(value < 10 ? 1 : 0)) + " " + units[index];
        }
        function duration() {
            if (!observation || typeof observation.seconds !== "number") return "Время наблюдения неизвестно";
            var value = Math.max(0, Math.floor(observation.seconds));
            return (value >= 60 ? Math.floor(value / 60) + " мин " : "") + (value % 60) + " с наблюдения";
        }
        function errorTotal() {
            var result = 0, errors = report ? report.errors || {} : {};
            for (var key in errors) if (typeof errors[key] === "number" && errors[key] > 0) result += errors[key];
            return result;
        }
        function protectedCategory(ids) {
            if (!report || report.verified !== true) return false;
            var rows = report.rules || [];
            for (var i = 0; i < ids.length; ++i) {
                var found = false;
                for (var j = 0; j < rows.length; ++j) if (rows[j].id === ids[i] && rows[j].state === "enabled") found = true;
                if (!found) return false;
            }
            return true;
        }
        function refreshStatus() {
            if (pendingRequest !== null) return;
            var request = new XMLHttpRequest();
            pendingRequest = request;
            requestWatchdog.restart();
            request.onreadystatechange = function() {
                if (request.readyState !== XMLHttpRequest.DONE || pendingRequest !== request) return;
                pendingRequest = null;
                requestWatchdog.stop();
                try {
                    if (request.status !== 200) throw new Error("unavailable");
                    var value = JSON.parse(request.responseText);
                    if (value.schema !== 1 || typeof value.verified !== "boolean") throw new Error("schema");
                    report = value;
                    statusText = value.verified ? "Патчи сборки подтверждены проверкой файлов." : "Проверка файлов сборки не пройдена.";
                } catch (error) {
                    report = null;
                    statusText = "Нет данных. Запусти MAX Mod с диагностикой.";
                }
            };
            request.open("GET", "http://127.0.0.1:18973/status");
            request.send();
        }
        Component.onDestruction: {
            if (pendingRequest !== null) {
                pendingRequest.onreadystatechange = function() {};
                pendingRequest.abort();
            }
        }
        Timer {
            id: requestWatchdog
            interval: 7000
            onTriggered: {
                var request = modPrivacyPage.pendingRequest;
                modPrivacyPage.pendingRequest = null;
                if (request !== null) { request.onreadystatechange = function() {}; request.abort(); }
                modPrivacyPage.report = null;
                modPrivacyPage.statusText = "Диагностика не ответила. Проверь её запуск.";
            }
        }
        Timer {
            objectName: "privacyRefreshTimer"
            interval: 2000; repeat: true; triggeredOnStart: true
            running: modPrivacyPage.visible
            onTriggered: modPrivacyPage.refreshStatus()
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
            property var rules: []
            readonly property bool confirmed: modPrivacyPage.protectedCategory(rules)
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
                DashboardText { Layout.fillWidth: true; text: protectionRow.confirmed ? "Защита включена" : "Не подтверждено"; color: protectionRow.confirmed ? modPrivacyPage.successColor : modPrivacyPage.warningColor; typography: Typography.FontUtil.Styles.Description }
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
                DashboardText { Layout.fillWidth: true; text: "ValMAX ограничивает встроенную диагностику MAX. Здесь показаны виды данных, для которых проверена защита этой сборки."; color: modPrivacyPage.secondaryText }
                DashboardText { objectName: "privacyVerification"; Layout.fillWidth: true; text: modPrivacyPage.statusText; color: modPrivacyPage.report && modPrivacyPage.report.verified ? modPrivacyPage.successColor : modPrivacyPage.warningColor; typography: Typography.FontUtil.Styles.Description }
                ProtectionRow { heading: "Отчёты об ошибках и сбоях"; rules: ["tracer_main", "tracer_service"]; detail: "Отключены вызовы встроенного средства отправки отчётов в приложении и службе звонков." }
                ProtectionRow { heading: "Статистика использования"; rules: ["ui_statistics", "mytracker"]; detail: "Отключены статистика интерфейса и создание модуля MyTracker в службе звонков. Это проверка патчей, а не подсчёт сетевых блокировок." }
                ProtectionRow { heading: "Личные данные в журналах"; rules: ["log_masking", "error_logs"]; detail: "Включена штатная маскировка журналов. Автоматическое прикрепление журналов к отчётам отключено." }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: observationContent.implicitHeight + 2 * modPrivacyPage.innerPadding
                    color: modPrivacyPage.cardSurface; radius: modPrivacyPage.groupRadius
                    ColumnLayout {
                        id: observationContent
                        x: modPrivacyPage.innerPadding; y: modPrivacyPage.innerPadding
                        width: parent.width - 2 * modPrivacyPage.innerPadding
                        spacing: UiKit.DesignSystem.SpacingCommon._S
                        DashboardText { Layout.fillWidth: true; text: modPrivacyPage.observing ? "Наблюдение работает" : "Нет активного наблюдения"; typography: Typography.FontUtil.Styles.BodyStrong }
                        DashboardText { objectName: "tracerCardValue"; Layout.fillWidth: true; text: modPrivacyPage.observing ? modPrivacyPage.count(modPrivacyPage.observation.blockedCalls) : "—"; color: modPrivacyPage.accentColor; typography: Typography.FontUtil.Styles.BodyStrong }
                        DashboardText { Layout.fillWidth: true; text: "Обращений службы звонков к отключённой диагностике за этот запуск. Это вызовы модуля, а не число сетевых угроз."; color: modPrivacyPage.secondaryText; typography: Typography.FontUtil.Styles.Description }
                        DashboardText { visible: modPrivacyPage.observing; Layout.fillWidth: true; text: modPrivacyPage.duration(); color: modPrivacyPage.secondaryText; typography: Typography.FontUtil.Styles.Description }
                        DashboardText { visible: modPrivacyPage.errorTotal() > 0; Layout.fillWidth: true; text: "Часть наблюдения недоступна. Показания могут быть неполными."; color: modPrivacyPage.warningColor; typography: Typography.FontUtil.Styles.Description }
                    }
                }
                DashboardText { Layout.fillWidth: true; text: "Сервис MAX по-прежнему видит адрес подключения и сведения, необходимые для работы аккаунта. Защита диагностики не скрывает сообщения от их получателя и не делает аккаунт анонимным."; color: modPrivacyPage.secondaryText; typography: Typography.FontUtil.Styles.Description }
            }
        }
    }
}
