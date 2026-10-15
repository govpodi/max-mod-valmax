// Inline Component in LeadingSettingsBar; requires QtQuick.Dialogs as Dialogs.
Component {
    id: settingsModSessionTransfer
    SettingsBaseItem {
        id: transferPage
        property var selfComponent: settingsModSessionTransfer
        title: "Перенос аккаунтов"
        onBackClicked: root.backClicked()
        property bool available: false
        property string operationState: "idle"
        property string statusText: "Проверка локального сервиса…"
        property bool hasError: false
        property string exportFile: ""
        property string importFile: ""
        property var pendingRequest: null
        readonly property bool busy: pendingRequest !== null || operationState === "waiting_for_exit" || operationState === "importing"
        readonly property real gap: UiKit.DesignSystem.SpacingCommon._M
        readonly property real sectionGap: UiKit.DesignSystem.SpacingCommon._2XL
        readonly property real groupRadius: UiKit.DesignSystem.BorderRadiusCommon._L
        signal clearPasswords()
        function fail(message) { hasError = true; statusText = message; clearPasswords(); }
        function errorText(code) {
            var labels = {
                archive_exists: "Файл уже существует. Выбери другое имя — существующие копии не перезаписываются.",
                file_exists: "Файл уже существует. Выбери другое имя — существующие копии не перезаписываются.",
                profile_not_empty: "В MAX Mod уже есть аккаунты. Импорт разрешён только в пустой профиль и не заменяет существующие аккаунты.",
                profiles_exist: "В MAX Mod уже есть аккаунты. Импорт разрешён только в пустой профиль.",
                bad_password: "Пароль не подошёл или файл повреждён. Проверь выбранную копию и введи пароль заново.",
                wrong_password: "Пароль не подошёл или файл повреждён. Проверь выбранную копию и введи пароль заново.",
                invalid_archive: "Файл не является поддерживаемой копией MAX Mod или повреждён.",
                version_mismatch: "Версия копии не подходит этой сборке MAX Mod.",
                no_session: "Нет доступной авторизации MAX Mod для экспорта.",
                not_logged_in: "Сначала войди в аккаунт MAX Mod, затем создай копию.",
                busy: "Другая операция уже выполняется. Дождись её завершения.",
                io_error: "Не удалось прочитать или сохранить файл. Проверь путь и доступ к папке.",
                invalid_password: "Пароль слишком короткий или длинный. Введи другой пароль.",
                invalid_path: "Выбери локальный файл с расширением .maxsession.",
                no_accounts: "В MAX Mod нет аккаунтов для экспорта. Сначала выполни вход.",
                invalid_profile: "Профиль MAX Mod не прошёл проверку. Перенос не выполнен.",
                destination_not_empty: "В MAX Mod уже есть данные аккаунтов. Импорт разрешён только в пустой профиль; существующие данные не заменяются.",
                credential_io: "Не удалось обработать защищённые данные входа. Перенос не завершён.",
                unsafe_path: "Этот путь не разрешён. Выбери локальный файл с расширением .maxsession.",
                client_still_running: "MAX Mod ещё работает. Выбери «Выход» через значок MAX Mod в трее, затем повтори импорт.",
                unsupported_version: "Версия копии не поддерживается этой сборкой MAX Mod.",
                invalid_payload: "Содержимое копии не прошло проверку. Перенос не выполнен.",
                policy_violation: "Копия не соответствует правилам безопасного переноса. Изменения не применены.",
                too_large: "Размер выбранной копии превышает допустимый предел.",
                authentication_failed: "Пароль не подошёл или копия повреждена. Проверь файл и введи пароль заново.",
                crypto_unavailable: "Модуль шифрования недоступен. Перезапусти MAX Mod обычным ярлыком.",
                rollback_failed: "При восстановлении после ошибки возникла проблема. Не запускай повторный импорт; проверь сохранённый профиль.",
                invalid_request: "Запрос не прошёл проверку. Выбери файл и введи пароль заново.",
                unavailable: "Локальный сервис недоступен. Запусти MAX Mod обычным ярлыком."
            };
            return labels[code] || "Операцию выполнить не удалось. Проверь файл, пароль и состояние MAX Mod.";
        }
        function utf8Size(value) {
            try { return encodeURIComponent(value).replace(/%[0-9a-f]{2}|./gi, "x").length; }
            catch (ignored) { return -1; }
        }
        function displayFile(value) {
            if (!value) return "Файл не выбран";
            try { return decodeURIComponent(value.replace(/^file:\/\/\//, "")); }
            catch (ignored) { return "Выбран локальный файл"; }
        }
        function refreshStatus() { request("GET", "", ""); }
        function submit(action, file, password, confirmation) {
            if (!available || busy) return;
            if (!/^file:\/\/\//.test(file) || !/\.maxsession$/i.test(file)) { fail("Выбери локальный файл с расширением .maxsession."); return; }
            var size = utf8Size(password);
            if (size < 12) { fail("Пароль слишком короткий. Нужно не менее 12 байт UTF-8; для латинских букв это 12 символов."); return; }
            if (size > 1024) { fail("Пароль слишком длинный. Используй не более 1024 байт UTF-8."); return; }
            if (action === "export" && password !== confirmation) { fail("Пароли не совпадают. Введи их заново."); return; }
            if (action !== "export" && action !== "import") { fail("Неизвестная операция."); return; }
            var body = JSON.stringify({schema: 1, action: action, file: file, password: password});
            clearPasswords();
            request("POST", body, action);
            body = ""; password = ""; confirmation = "";
        }
        function request(method, body, action) {
            if (pendingRequest !== null) return;
            var xhr = new XMLHttpRequest();
            pendingRequest = xhr;
            hasError = false;
            if (method === "POST") statusText = action === "export" ? "Создание защищённой копии…" : "Подготовка импорта…";
            else if (operationState !== "waiting_for_exit" && operationState !== "importing") statusText = "Проверка локального сервиса…";
            requestWatchdog.interval = method === "POST" ? 30000 : 7000;
            requestWatchdog.restart();
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE || pendingRequest !== xhr) return;
                pendingRequest = null;
                requestWatchdog.stop();
                clearPasswords();
                try {
                    var value = JSON.parse(xhr.responseText);
                    if (!value || value.schema !== 1 || ["idle", "waiting_for_exit", "importing", "done", "error"].indexOf(value.state) < 0) throw new Error("schema");
                    if (method === "GET") {
                        if (typeof value.available !== "boolean") throw new Error("available");
                        available = value.available;
                    }
                    operationState = value.state;
                    if (xhr.status !== 200 || value.state === "error") { fail(errorText(value.error)); return; }
                    if (method === "GET" && !available) { fail(errorText("unavailable")); return; }
                    if (value.state === "waiting_for_exit") statusText = "Импорт подготовлен. Выбери «Выход» через значок MAX Mod в трее. После завершения клиента подожди 20 секунд и открой MAX Mod обычным ярлыком.";
                    else if (value.state === "importing") statusText = "Импорт выполняется. Дождись завершения, подожди 20 секунд, затем открой MAX Mod обычным ярлыком.";
                    else if (value.state === "done") statusText = method === "POST" && action === "export" ? "Защищённая копия создана в выбранном файле." : "Операция завершена.";
                    else statusText = "Локальный сервис готов. Автоматического экспорта или импорта нет.";
                } catch (ignored) { fail("Локальный сервис не ответил корректно. Проверь его запуск; операция не повторяется автоматически."); }
            };
            xhr.open(method, "http://127.0.0.1:18973/session-transfer");
            if (method === "POST") {
                xhr.setRequestHeader("Content-Type", "application/json");
                xhr.setRequestHeader("X-Max-Mod-Local-Write", "session-transfer-v1");
            }
            xhr.send(body || "");
        }
        Component.onCompleted: refreshStatus()
        onVisibleChanged: { if (!visible) clearPasswords(); }
        Component.onDestruction: {
            clearPasswords();
            if (pendingRequest !== null) { pendingRequest.onreadystatechange = function() {}; pendingRequest.abort(); }
        }
        Timer {
            id: requestWatchdog
            onTriggered: {
                var xhr = transferPage.pendingRequest;
                transferPage.pendingRequest = null;
                if (xhr !== null) { xhr.onreadystatechange = function() {}; xhr.abort(); }
                transferPage.fail("Сервис не ответил вовремя. Операция не повторяется автоматически. Проверь результат перед новой попыткой.");
            }
        }
        Timer { interval: 2000; repeat: true; running: transferPage.operationState === "waiting_for_exit" || transferPage.operationState === "importing"; onTriggered: transferPage.refreshStatus() }
        Dialogs.FileDialog {
            id: exportDialog
            title: "Сохранить защищённую копию MAX Mod"
            fileMode: Dialogs.FileDialog.SaveFile
            defaultSuffix: "maxsession"
            nameFilters: ["Копия MAX Mod (*.maxsession)"]
            onAccepted: transferPage.exportFile = selectedFile.toString()
        }
        Dialogs.FileDialog {
            id: importDialog
            title: "Выбрать копию MAX Mod"
            fileMode: Dialogs.FileDialog.OpenFile
            nameFilters: ["Копия MAX Mod (*.maxsession)"]
            onAccepted: transferPage.importFile = selectedFile.toString()
        }
        component TransferText: Label {
            property int typography: Typography.FontUtil.Styles.Body
            font: Typography.FontUtil.getFont(typography, Typography.FontUtil.Sizes.Default)
            lineHeight: Typography.FontUtil.getLineHeight(typography, Typography.FontUtil.Sizes.Default)
            lineHeightMode: Text.FixedHeight
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: transferPage.maxwellColorTokens.colorTextPrimary
        }
        component TransferButton: Button {
            implicitHeight: Math.max(UiKit.DesignSystem.SizeBase.px48, contentItem.implicitHeight + 2 * transferPage.gap)
            horizontalPadding: transferPage.gap
            contentItem: TransferText { text: parent.text; color: parent.enabled ? transferPage.maxwellColorTokens.colorTextThemed : transferPage.maxwellColorTokens.colorTextTertiary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { color: transferPage.maxwellColorTokens.colorBackgroundCard; radius: transferPage.groupRadius }
        }
        component PasswordField: TextField {
            echoMode: TextInput.Password
            inputMethodHints: Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
            padding: transferPage.gap
            font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
            color: transferPage.maxwellColorTokens.colorTextPrimary
            placeholderTextColor: transferPage.maxwellColorTokens.colorTextTertiary
            background: Rectangle { color: transferPage.maxwellColorTokens.colorBackgroundCard; radius: transferPage.groupRadius; border.color: transferPage.maxwellColorTokens.colorDividerPrimary }
        }
        contentLoaderItem: UiKit.MeFlickable {
            id: transferScroll
            objectName: "sessionTransferScroll"
            contentWidth: width; contentHeight: transferScrollContent.implicitHeight + 2 * transferPage.sectionGap
            flickableDirection: Flickable.VerticalFlick
            ScrollBar.vertical: UiKit.MeScrollBar {}
            Connections { target: transferPage; function onClearPasswords() { exportPassword.text = ""; exportConfirmation.text = ""; importPassword.text = ""; } }
            ColumnLayout {
                id: transferScrollContent
                y: transferPage.sectionGap
                width: transferScroll.width; spacing: transferPage.sectionGap
                TransferText { Layout.fillWidth: true; text: "Копия переносит вход в сохранённые телефонные аккаунты MAX Mod. Сообщения, файлы, настройки и вход в кабинет бизнеса в неё не входят. В копии есть данные для входа: храни файл и пароль отдельно и не передавай их другим людям."; color: transferPage.maxwellColorTokens.colorTextSecondary }
                TransferText { objectName: "sessionTransferStatus"; Layout.fillWidth: true; text: transferPage.statusText; typography: Typography.FontUtil.Styles.Description; color: transferPage.hasError ? transferPage.maxwellColorTokens.colorTextAttention : transferPage.maxwellColorTokens.colorTextSecondary }
                TransferButton { objectName: "refreshSessionTransfer"; Layout.fillWidth: true; text: "Проверить состояние"; enabled: transferPage.pendingRequest === null; onClicked: transferPage.refreshStatus() }
                ColumnLayout {
                    objectName: "sessionExportForm"
                    enabled: transferPage.available && !transferPage.busy
                    Layout.fillWidth: true; spacing: transferPage.gap
                    TransferText { Layout.fillWidth: true; text: "Экспорт"; typography: Typography.FontUtil.Styles.BodyStrong }
                    TransferText { Layout.fillWidth: true; text: "Создай пароль для копии. Он не сохраняется в настройках. Существующий файл не будет перезаписан."; typography: Typography.FontUtil.Styles.Description; color: transferPage.maxwellColorTokens.colorTextSecondary }
                    TransferButton { objectName: "chooseSessionExport"; Layout.fillWidth: true; text: "Выбрать файл для сохранения"; onClicked: exportDialog.open() }
                    TransferText { Layout.fillWidth: true; text: transferPage.displayFile(transferPage.exportFile); typography: Typography.FontUtil.Styles.Description; color: transferPage.maxwellColorTokens.colorTextSecondary; wrapMode: Text.WrapAnywhere }
                    PasswordField { id: exportPassword; objectName: "sessionExportPassword"; Layout.fillWidth: true; placeholderText: "Пароль для копии" }
                    PasswordField { id: exportConfirmation; objectName: "sessionExportConfirmation"; Layout.fillWidth: true; placeholderText: "Повтори пароль" }
                    TransferButton { objectName: "exportSession"; Layout.fillWidth: true; text: "Создать защищённую копию"; onClicked: transferPage.submit("export", transferPage.exportFile, exportPassword.text, exportConfirmation.text) }
                }
                ColumnLayout {
                    objectName: "sessionImportForm"
                    enabled: transferPage.available && !transferPage.busy
                    Layout.fillWidth: true; spacing: transferPage.gap
                    TransferText { Layout.fillWidth: true; text: "Импорт"; typography: Typography.FontUtil.Styles.BodyStrong }
                    TransferText { Layout.fillWidth: true; text: "Импорт разрешён только в пустой MAX Mod. Он не заменяет и не объединяет существующие аккаунты. После подготовки выбери «Выход» через значок MAX Mod в трее. Подожди 20 секунд после завершения клиента, затем открой его обычным ярлыком."; typography: Typography.FontUtil.Styles.Description; color: transferPage.maxwellColorTokens.colorTextSecondary }
                    TransferButton { objectName: "chooseSessionImport"; Layout.fillWidth: true; text: "Выбрать копию"; onClicked: importDialog.open() }
                    TransferText { Layout.fillWidth: true; text: transferPage.displayFile(transferPage.importFile); typography: Typography.FontUtil.Styles.Description; color: transferPage.maxwellColorTokens.colorTextSecondary; wrapMode: Text.WrapAnywhere }
                    PasswordField { id: importPassword; objectName: "sessionImportPassword"; Layout.fillWidth: true; placeholderText: "Пароль выбранной копии" }
                    TransferButton { objectName: "importSession"; Layout.fillWidth: true; text: "Подготовить импорт после выхода"; onClicked: transferPage.submit("import", transferPage.importFile, importPassword.text, "") }
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: transferPage.gap
                    TransferText { Layout.fillWidth: true; text: "MAX для бизнеса"; typography: Typography.FontUtil.Styles.BodyStrong }
                    TransferText { Layout.fillWidth: true; text: "Кабинет организации откроется на официальном сайте. Его вход не добавляет телефонный аккаунт в список MAX Mod."; typography: Typography.FontUtil.Styles.Description; color: transferPage.maxwellColorTokens.colorTextSecondary }
                    TransferButton { objectName: "openBusinessCabinet"; Layout.fillWidth: true; text: "Открыть кабинет организации"; onClicked: root.commonDependency.clickDispatcher.onUrlClicked("https://business.max.ru/self/") }
                }
            }
        }
    }
}
