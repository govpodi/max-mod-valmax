// Explicit local report; no automatic upload or raw log collection.
Component {
    id: settingsValMAXSupport
    SettingsBaseItem {
        id: supportPage
        property var selfComponent: settingsValMAXSupport
        title: "Помощь и диагностика"
        onBackClicked: root.backClicked()
        property var report: null
        property var pendingRequest: null
        property bool hasError: false
        property bool showReport: false
        property string statusText: "Загрузка состава отчёта…"
        readonly property real gap: UiKit.DesignSystem.SpacingCommon._M
        readonly property real sectionGap: UiKit.DesignSystem.SpacingCommon._2XL
        readonly property real groupRadius: UiKit.DesignSystem.BorderRadiusCommon._L
        function fail(message) { hasError = true; statusText = message; }
        function request(method, file) {
            if (pendingRequest !== null) return;
            var xhr = new XMLHttpRequest();
            pendingRequest = xhr; hasError = false;
            statusText = method === "POST" ? "Сохранение отчёта…" : "Загрузка состава отчёта…";
            requestWatchdog.restart();
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE || pendingRequest !== xhr) return;
                pendingRequest = null; requestWatchdog.stop();
                try {
                    var value = JSON.parse(xhr.responseText);
                    if (!value || value.schema !== 1) throw new Error("schema");
                    if (xhr.status !== 200) {
                        fail(value.error === "file_exists" ? "Файл уже существует. Выбери новое имя." : value.error === "unsafe_path" ? "Выбери новый локальный файл .json в доступной папке." : "Не удалось подготовить или сохранить отчёт. Повтори попытку.");
                        return;
                    }
                    if (method === "GET") {
                        if (!value.report || value.report.schema !== 1) throw new Error("report");
                        report = value.report;
                        statusText = "Состав отчёта готов. Сохранение и отправка выполняются только по твоему выбору.";
                    } else {
                        if (value.saved !== true) throw new Error("saved");
                        statusText = "Отчёт сохранён в выбранном файле. Просмотри его и приложи к описанию ошибки вручную.";
                    }
                } catch (ignored) { fail("Локальный помощник недоступен. Опиши ошибку без отчёта или перезапусти ValMAX обычным ярлыком."); }
            };
            xhr.open(method, "http://127.0.0.1:18973/support-diagnostics");
            if (method === "POST") {
                xhr.setRequestHeader("Content-Type", "application/json");
                xhr.setRequestHeader("X-Max-Mod-Local-Write", "support-diagnostics-v1");
                xhr.send(JSON.stringify({schema: 1, file: file}));
            } else xhr.send();
        }
        Component.onCompleted: request("GET", "")
        Component.onDestruction: {
            if (pendingRequest !== null) { pendingRequest.onreadystatechange = function() {}; pendingRequest.abort(); }
        }
        Timer {
            id: requestWatchdog
            interval: 10000
            onTriggered: {
                var xhr = supportPage.pendingRequest; supportPage.pendingRequest = null;
                if (xhr !== null) { xhr.onreadystatechange = function() {}; xhr.abort(); }
                supportPage.fail("Помощник не ответил вовремя. Проверь выбранную папку перед повторным сохранением.");
            }
        }
        Dialogs.FileDialog {
            id: saveSupportDialog
            title: "Сохранить отчёт ValMAX"
            fileMode: Dialogs.FileDialog.SaveFile
            defaultSuffix: "json"
            nameFilters: ["Отчёт ValMAX (*.json)"]
            onAccepted: supportPage.request("POST", selectedFile.toString())
        }
        component SupportText: Label {
            property int typography: Typography.FontUtil.Styles.Body
            font: Typography.FontUtil.getFont(typography, Typography.FontUtil.Sizes.Default)
            lineHeight: Typography.FontUtil.getLineHeight(typography, Typography.FontUtil.Sizes.Default)
            lineHeightMode: Text.FixedHeight
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: supportPage.maxwellColorTokens.colorTextPrimary
        }
        component SupportButton: Button {
            implicitHeight: Math.max(UiKit.DesignSystem.SizeBase.px48, contentItem.implicitHeight + 2 * supportPage.gap)
            horizontalPadding: supportPage.gap
            contentItem: SupportText { text: parent.text; color: parent.enabled ? supportPage.maxwellColorTokens.colorTextThemed : supportPage.maxwellColorTokens.colorTextTertiary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { color: supportPage.maxwellColorTokens.colorBackgroundCard; radius: supportPage.groupRadius }
        }
        contentLoaderItem: UiKit.MeFlickable {
            id: supportScroll
            objectName: "supportDiagnosticsScroll"
            contentWidth: width; contentHeight: supportContent.implicitHeight + 2 * supportPage.sectionGap
            flickableDirection: Flickable.VerticalFlick
            ScrollBar.vertical: UiKit.MeScrollBar {}
            ColumnLayout {
                id: supportContent
                y: supportPage.sectionGap; width: supportScroll.width
                spacing: supportPage.gap
                SupportText { Layout.fillWidth: true; text: "Что не получилось?"; typography: Typography.FontUtil.Styles.BodyStrong }
                SupportText { Layout.fillWidth: true; text: "В сообщении об ошибке напиши, что хотел сделать, куда нажал, что ожидал и что произошло. Укажи, повторяется ли проблема. Если ValMAX не запускается, отчёт не обязателен." }
                SupportButton { objectName: "openValMAXBugReport"; Layout.fillWidth: true; text: "Сообщить об ошибке"; onClicked: Qt.openUrlExternally("https://github.com/govpodi/max-mod-valmax/issues/new?template=bug_report.yml") }
                SupportText { Layout.fillWidth: true; text: "Локальный отчёт"; typography: Typography.FontUtil.Styles.BodyStrong }
                SupportText { Layout.fillWidth: true; text: "В файл входят только версии программ, известный код состояния запуска, переключатели режима призрака и оптимизации, общие счётчики и коды ошибок наблюдения. Нет переписки, аккаунтов, ID, номеров, путей, адресов подключения, токенов, сырых логов и дампов."; color: supportPage.maxwellColorTokens.colorTextSecondary }
                SupportText { Layout.fillWidth: true; text: "Отчёт никуда не отправляется автоматически. Выбери новый файл, просмотри его и прикрепи к ошибке вручную. null означает, что сведений нет. Счётчики меняются в текущем запуске; сохранённые переключатели могут требовать перезапуска."; color: supportPage.maxwellColorTokens.colorTextSecondary; typography: Typography.FontUtil.Styles.Description }
                SupportText { objectName: "supportDiagnosticsStatus"; Layout.fillWidth: true; text: supportPage.statusText; color: supportPage.hasError ? supportPage.maxwellColorTokens.colorTextAttention : supportPage.maxwellColorTokens.colorTextSecondary }
                SupportButton { objectName: "saveSupportDiagnostics"; Layout.fillWidth: true; text: "Сохранить отчёт…"; enabled: supportPage.pendingRequest === null && supportPage.report !== null; onClicked: saveSupportDialog.open() }
                SupportButton { objectName: "previewSupportDiagnostics"; Layout.fillWidth: true; text: supportPage.showReport ? "Скрыть состав отчёта" : "Посмотреть состав отчёта"; enabled: supportPage.report !== null; onClicked: supportPage.showReport = !supportPage.showReport }
                SupportButton { objectName: "refreshSupportDiagnostics"; Layout.fillWidth: true; text: "Обновить сведения"; enabled: supportPage.pendingRequest === null; onClicked: supportPage.request("GET", "") }
                TextArea {
                    objectName: "supportDiagnosticsPreview"
                    Layout.fillWidth: true
                    visible: supportPage.showReport
                    readOnly: true; selectByMouse: true; textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.WrapAnywhere
                    text: supportPage.report === null ? "" : JSON.stringify(supportPage.report, null, 2)
                    color: supportPage.maxwellColorTokens.colorTextPrimary
                    padding: supportPage.gap
                    font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default)
                    background: Rectangle { color: supportPage.maxwellColorTokens.colorBackgroundCard; radius: supportPage.groupRadius }
                }
                SupportText { Layout.fillWidth: true; text: "Перед публикацией проверь текст и снимки экрана. Не добавляй личные данные, архивы профиля или переписку."; color: supportPage.maxwellColorTokens.colorTextSecondary; typography: Typography.FontUtil.Styles.Description }
            }
        }
    }
}
