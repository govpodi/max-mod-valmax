// Inline native settings component. QtQuick.Dialogs is imported as Dialogs.
Component {
    id: settingsModMData
    SettingsBaseItem {
        id: mdataPage
        property var selfComponent: settingsModMData
        title: "MData"
        onBackClicked: { if (pendingRequest === null) root.backClicked(); }
        property bool available: false
        property string operationState: "idle"
        property string statusText: "Проверка сервиса MData…"
        property bool hasError: false
        property var pendingRequest: null
        property var activeDialog: null
        property string exportScope: "all"
        property var selectedExportAccounts: []
        property var modelExportAccountIds: []
        readonly property string currentExportAccountId: currentAccountId()
        readonly property bool busy: pendingRequest !== null || operationState === "waiting_for_exit" || operationState === "working"
        readonly property real gap: UiKit.DesignSystem.SpacingCommon._M
        readonly property real sectionGap: UiKit.DesignSystem.SpacingCommon._2XL
        function errorText(code) {
            var messages = {
                file_exists: "Файл уже существует. Выбери другое имя.",
                no_accounts: "В MAX Mod пока нет аккаунтов для переноса входа.",
                invalid_profile: "Данные MAX Mod не прошли проверку.",
                destination_not_empty: "Не удалось добавить аккаунты к текущим данным MAX Mod.",
                authentication_failed: "Пароль не подошёл или MData повреждён.",
                invalid_archive: "Выбранный файл не является поддерживаемой MData.",
                invalid_payload: "Содержимое MData не прошло проверку.",
                unsupported_version: "Версия MData не поддерживается этой сборкой.",
                policy_violation: "MData не соответствует правилам безопасного переноса входа.",
                invalid_password: "Проверь длину и правильность пароля.",
                unsafe_path: "Выбери локальный файл с расширением .mdata.",
                unsupported_path: "Выбранное расположение файла не поддерживается.",
                invalid_request: "Запрос не прошёл проверку. Попробуй ещё раз.",
                io_error: "Не удалось прочитать или сохранить MData.",
                credential_io: "Не удалось перенести данные входа из хранилища Windows.",
                crypto_unavailable: "Модуль шифрования недоступен.",
                too_large: "Размер MData превышает допустимый предел.",
                client_still_running: "MAX Mod или его служба не завершились. Проверь состояние приложения перед повторной попыткой.",
                busy: "Другая операция MData ещё выполняется.",
                account_limit: "Можно добавить не больше трёх аккаунтов. Освободи место для нового аккаунта.",
                profile_changed: "Данные MAX Mod изменились. Повтори операцию.",
                source_changed: "Данные MAX Mod изменились во время создания копии. Повтори операцию.",
                profile_collision: "Этот аккаунт уже есть в MAX Mod. Текущие данные сохранены.",
                recovery_required: "Сначала восстанови резервную копию через ярлык восстановления MData.",
                legacy_add_only: "Файл .maxsession поддерживает только добавление аккаунтов.",
                unsupported_credential: "MData содержит неподдерживаемый тип данных входа Windows.",
                insufficient_space: "Недостаточно места на диске профиля MAX Mod или выбранного файла. Освободи место и повтори.",
                already_present: "Эти аккаунты уже есть в MAX Mod.",
                backup_failed: "Не удалось создать резервную копию текущих данных. Восстановление отменено.",
                restart_failed: "Операция завершена, но автоматический запуск не удался. Открой MAX Mod обычным ярлыком.",
                rollback_failed: "Возникла ошибка восстановления. Не повторяй загрузку до проверки резервной копии."
            };
            return messages[code] || "MData не удалось обработать. Проверь файл, пароль и состояние MAX Mod.";
        }
        function resultText(result) {
            var messages = {
                exported: "MData создана.",
                restored: "Данные из MData восстановлены.",
                added: "Аккаунты из MData добавлены.",
                already_present: "Аккаунты из MData уже добавлены."
            };
            return messages[result] || "Последняя операция MData завершена.";
        }
        function showError(message, dialog) {
            hasError = true; statusText = message;
            if (dialog) { dialog.clearSecrets(); dialog.errorText = message; }
        }
        function validAccountId(value) {
            return typeof value === "string" && /^[1-9][0-9]{0,18}$/.test(value) && (value.length < 19 || value <= "9223372036854775807");
        }
        function currentAccountId() {
            try { return String(QmlUtils.contactIdString(root.selfInfo.id)); }
            catch (_) { return ""; }
        }
        function currentAccountName() {
            try { return String(root.selfInfo.name).slice(0, 128); }
            catch (_) { return ""; }
        }
        function exportAccountSelected(id) {
            if (exportScope !== "one" && exportScope !== "subset") return false;
            return Array.isArray(selectedExportAccounts) && selectedExportAccounts.some(function(row) { return row.id === id; });
        }
        function rememberExportAccount(id) {
            if (!validAccountId(id)) return;
            var ids = modelExportAccountIds.slice();
            ids.push(id);
            modelExportAccountIds = ids;
        }
        function forgetExportAccount(id) {
            var ids = modelExportAccountIds.slice();
            var index = ids.indexOf(id);
            if (index >= 0) ids.splice(index, 1);
            modelExportAccountIds = ids;
        }
        function toggleExportAccount(id, name) {
            if (busy || !validAccountId(id)) return;
            var rows = (exportScope === "one" || exportScope === "subset") && Array.isArray(selectedExportAccounts) ? selectedExportAccounts.slice() : [];
            var index = rows.findIndex(function(row) { return row.id === id; });
            if (index >= 0) {
                if (rows.length === 1) return;
                rows.splice(index, 1);
            } else {
                if (rows.length === 3) return;
                rows.push({id: id, name: name});
            }
            selectedExportAccounts = rows;
            exportScope = rows.length === 1 ? "one" : "subset";
        }
        function selectedExportIds() {
            if (exportScope === "all") return [];
            if (exportScope === "current") return validAccountId(currentExportAccountId) ? [currentExportAccountId] : null;
            if (exportScope !== "one" && exportScope !== "subset") return null;
            var rows = selectedExportAccounts;
            if (!Array.isArray(rows) || rows.length < 1 || rows.length > 3
                    || (exportScope === "one") !== (rows.length === 1)) return null;
            var ids = [];
            for (var j = 0; j < rows.length; ++j) {
                var selectedId = rows[j] && rows[j].id;
                if (!validAccountId(selectedId) || modelExportAccountIds.indexOf(selectedId) < 0 || ids.indexOf(selectedId) >= 0) return null;
                ids.push(selectedId);
            }
            return ids;
        }
        function unicodeLength(value, limit) {
            // QJSEngine's Array.from(string) iterates UTF-16 code units in
            // this runtime. Count valid surrogate pairs explicitly instead.
            if (value.length > limit * 2) return limit + 1;
            var count = 0;
            for (var i = 0; i < value.length; ++i) {
                var code = value.charCodeAt(i);
                if (code >= 0xd800 && code <= 0xdbff) {
                    var next = value.charCodeAt(++i);
                    if (!(next >= 0xdc00 && next <= 0xdfff)) return limit + 1;
                } else if (code >= 0xdc00 && code <= 0xdfff) return limit + 1;
                if (++count > limit) return count;
            }
            return count;
        }
        function validPreview(value) {
            if (!value || value.schema !== 1 || value.kind !== "login" || typeof value.fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(value.fingerprint) || typeof value.profiles !== "number" || Math.floor(value.profiles) !== value.profiles || value.profiles < 1 || value.profiles > 3 || !Array.isArray(value.accounts) || value.accounts.length !== value.profiles) return false;
            var seen = {};
            for (var i = 0; i < value.accounts.length; ++i) {
                var row = value.accounts[i];
                if (!row || !validAccountId(row.accountId) || seen[row.accountId] || typeof row.displayName !== "string" || unicodeLength(row.displayName, 256) > 256 || typeof row.shortName !== "string" || unicodeLength(row.shortName, 32) > 32 || /[\u0000-\u001f\u007f-\u009f\u2028-\u202e\u2066-\u2069]/.test(row.displayName + row.shortName)) return false;
                seen[row.accountId] = true;
            }
            return true;
        }
        function openPasswordDialog(action, file) {
            if (busy || !available || activeDialog !== null) return;
            var selected = action === "export" ? selectedExportIds() : [];
            if (selected === null) { showError("Выбери аккаунт для MData заново.", null); return; }
            var accountName = action !== "export" ? "" : exportScope === "current" ? currentAccountName() : exportScope === "one" ? selectedExportAccounts[0].name : "";
            activeDialog = passwordDialogComponent.createObject(Overlay.overlay, {transferAction: action, selectedFile: file, exportAccountIds: selected.slice(), exportAccountName: accountName});
            activeDialog.open();
        }
        function submit(dialog) {
            if (busy || !available) return;
            var legacy = /\.maxsession$/i.test(dialog.selectedFile);
            if (!/^file:\/\/\//.test(dialog.selectedFile) || !/\.mdata$/i.test(dialog.selectedFile) && !(dialog.transferAction === "import" && legacy)) { showError("Выбери локальный файл с расширением .mdata" + (dialog.transferAction === "import" ? " или .maxsession." : "."), dialog); return; }
            var password = dialog.transferAction === "import" && dialog.previewReady ? dialog.previewSecret : dialog.secret;
            var bytes;
            try { bytes = encodeURIComponent(password).replace(/%[0-9a-f]{2}|./gi, "x").length; }
            catch (ignored) { bytes = -1; }
            if (bytes < 12 || bytes > 1024 || password.indexOf("\u0000") >= 0) { password = ""; showError("Используй длинный пароль: от 12 до 1024 байт UTF-8; для латинских букв минимум 12 символов.", dialog); return; }
            if (dialog.transferAction === "export" && dialog.secret !== dialog.confirmation) { showError("Пароли не совпадают. Введи их заново.", dialog); return; }
            var action = dialog.transferAction;
            var route = "/mdata";
            if (action === "import" && !dialog.previewReady) {
                dialog.previewSecret = password; dialog.secret = ""; dialog.confirmation = "";
                if (legacy) {
                    dialog.legacyPreview = true; dialog.previewReady = true;
                    statusText = "Старый формат: список аккаунтов недоступен. Подтверди добавление отдельно.";
                    password = ""; return;
                }
                var previewBody = JSON.stringify({schema: 1, file: dialog.selectedFile, password: password});
                password = ""; request("POST", previewBody, "inspect", dialog, "/mdata/inspect"); previewBody = "";
                return;
            }
            var body;
            if (action === "import" && !dialog.legacyPreview) {
                if (!dialog.previewReady || !/^[0-9a-f]{64}$/.test(dialog.previewFingerprint)) { password = ""; showError("Проверь MData заново перед входом.", dialog); return; }
                route = "/mdata/import-confirmed";
                body = JSON.stringify({schema: 1, file: dialog.selectedFile, password: password, fingerprint: dialog.previewFingerprint});
            } else if (action === "export" && dialog.exportAccountIds.length > 0) {
                route = "/mdata/export-selected";
                body = JSON.stringify({schema: 1, file: dialog.selectedFile, password: password, accountIds: dialog.exportAccountIds});
            } else {
                body = JSON.stringify({schema: 1, action: action, file: dialog.selectedFile, password: password, mode: action === "export" ? "restore" : "add"});
            }
            password = "";
            dialog.clearSecrets();
            request("POST", body, action, dialog, route);
            body = "";
        }
        function chooseImport() { if (!busy && available) loadFileDialog.open(); }
        function refreshStatus() { request("GET", "", "", null); }
        function request(method, body, action, dialog, route) {
            if (pendingRequest !== null) return;
            var xhr = new XMLHttpRequest();
            pendingRequest = xhr;
            hasError = false;
            if (method === "POST") statusText = action === "inspect" ? "Проверка MData и списка аккаунтов…" : "Подготовка MData…";
            requestWatchdog.interval = method === "POST" ? 30000 : 7000;
            requestWatchdog.restart();
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE || pendingRequest !== xhr) return;
                pendingRequest = null;
                requestWatchdog.stop();
                try {
                    var value = JSON.parse(xhr.responseText);
                    if (method === "POST" && action === "inspect") {
                        if (xhr.status !== 200) {
                            if (value && value.schema === 1 && value.error === "unsupported_version" && dialog && dialog.previewSecret.length > 0) {
                                dialog.legacyPreview = true; dialog.previewReady = true; dialog.errorText = "";
                                statusText = "Старый формат: список аккаунтов недоступен. Подтверди добавление отдельно.";
                                return;
                            }
                            showError(errorText(value ? value.error : "invalid_archive"), dialog); return;
                        }
                        if (!validPreview(value) || !dialog || dialog.previewSecret.length === 0 || activeDialog !== dialog) throw new Error("preview");
                        dialog.previewAccounts = value.accounts; dialog.previewFingerprint = value.fingerprint; dialog.previewReady = true; dialog.legacyPreview = false; dialog.errorText = "";
                        statusText = "MData проверена. Подтверди вход в перечисленные аккаунты.";
                        return;
                    }
                    if (!value || value.schema !== 1 || ["idle", "waiting_for_exit", "working", "done", "error"].indexOf(value.state) < 0) throw new Error("schema");
                    if (method === "GET") {
                        if (typeof value.available !== "boolean") throw new Error("available");
                        available = value.available;
                        operationState = value.state;
                        if (xhr.status !== 200 || value.state === "error") { showError(errorText(value.error), activeDialog); return; }
                        if (!available) { showError("Сервис MData недоступен. Запусти MAX Mod обычным ярлыком.", activeDialog); return; }
                        statusText = value.state === "done" ? resultText(value.result) : value.state === "waiting_for_exit" || value.state === "working" ? "Операция MData выполняется…" : "Создай MData для переноса входа или войди с готовой MData.";
                        return;
                    }
                    if (xhr.status !== 200 || value.state === "error") { operationState = "error"; showError(errorText(value.error), dialog); return; }
                    if (value.accepted !== true || value.action !== action) throw new Error("ack");
                    if (action === "export") {
                        if (value.state !== "done" || value.result !== "exported" || typeof value.profiles !== "number" || Math.floor(value.profiles) !== value.profiles || value.profiles < 1 || value.profiles > 3) throw new Error("export ack");
                        operationState = "done";
                        statusText = resultText(value.result);
                        if (dialog) { dialog.clearSecrets(); dialog.close(); }
                        return;
                    }
                    if (action !== "import" || value.state !== "waiting_for_exit") throw new Error("import ack");
                    // Only an acknowledged import requires closing MAX.
                    operationState = "waiting_for_exit";
                    statusText = "MAX Mod перезапускается для MData…";
                    if (dialog) { dialog.clearSecrets(); dialog.close(); }
                    Qt.callLater(function() { appDelegate.closeMessenger(); });
                } catch (ignored) { showError("Операция не подтверждена. MAX Mod остаётся открыт; повторного запроса нет.", dialog); }
            };
            xhr.open(method, "http://127.0.0.1:18973" + (route || "/mdata"));
            if (method === "POST") { xhr.setRequestHeader("Content-Type", "application/json"); xhr.setRequestHeader("X-Max-Mod-Local-Write", "mdata-v1"); }
            xhr.send(body || "");
        }
        Component.onCompleted: refreshStatus()
        Component.onDestruction: {
            if (activeDialog) { activeDialog.clearSecrets(); activeDialog.close(); }
            if (pendingRequest !== null) { pendingRequest.onreadystatechange = function() {}; pendingRequest.abort(); }
        }
        Timer {
            id: recoveryPoll
            interval: 2000; repeat: true
            running: mdataPage.visible && (!mdataPage.available || mdataPage.operationState === "waiting_for_exit" || mdataPage.operationState === "working")
            onTriggered: { if (mdataPage.pendingRequest === null) mdataPage.refreshStatus(); }
        }
        Timer {
            id: requestWatchdog
            onTriggered: {
                var xhr = mdataPage.pendingRequest; mdataPage.pendingRequest = null;
                if (xhr !== null) { xhr.onreadystatechange = function() {}; xhr.abort(); }
                mdataPage.showError("Сервис не ответил вовремя. Проверь результат перед новой попыткой; MAX Mod остаётся открыт.", mdataPage.activeDialog);
            }
        }
        component MDataText: Label {
            property int typography: Typography.FontUtil.Styles.Body
            font: Typography.FontUtil.getFont(typography, Typography.FontUtil.Sizes.Default)
            lineHeight: Typography.FontUtil.getLineHeight(typography, Typography.FontUtil.Sizes.Default)
            lineHeightMode: Text.FixedHeight
            color: mdataPage.maxwellColorTokens.colorTextPrimary
            textFormat: Text.PlainText; wrapMode: Text.Wrap
        }
        component MDataButton: Button {
            implicitHeight: Math.max(UiKit.DesignSystem.SizeBase.px48, contentItem.implicitHeight + 2 * mdataPage.gap)
            horizontalPadding: mdataPage.gap
            contentItem: MDataText { text: parent.text; color: parent.enabled ? mdataPage.maxwellColorTokens.colorTextThemed : mdataPage.maxwellColorTokens.colorTextTertiary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { color: mdataPage.maxwellColorTokens.colorBackgroundCard; radius: UiKit.DesignSystem.BorderRadiusCommon._L }
        }
        Dialogs.FileDialog {
            id: createFileDialog
            title: "Создать MData"
            fileMode: Dialogs.FileDialog.SaveFile
            defaultSuffix: "mdata"
            nameFilters: ["MData (*.mdata)"]
            Component.onCompleted: selectedFile = Qt.url("MData.mdata")
            onAccepted: mdataPage.openPasswordDialog("export", selectedFile.toString())
        }
        Dialogs.FileDialog {
            id: loadFileDialog
            title: "Войти с MData"
            fileMode: Dialogs.FileDialog.OpenFile
            nameFilters: ["MData и файл сессии (*.mdata *.maxsession)"]
            onAccepted: mdataPage.openPasswordDialog("import", selectedFile.toString())
        }
        Component {
            id: passwordDialogComponent
            UiKit.BaseModalDialog {
                id: passwordDialog
                objectName: "mdataPasswordDialog"
                property string transferAction
                property string selectedFile
                readonly property string restoreMode: "add"
                property string secret: ""
                property string confirmation: ""
                property string errorText: ""
                property var exportAccountIds: []
                property string exportAccountName: ""
                property string previewSecret: ""
                property bool previewReady: false
                property bool legacyPreview: false
                property var previewAccounts: []
                property string previewFingerprint: ""
                onSelectedFileChanged: clearSecrets()
                chatAppearance: mdataPage.maxwellColorTokens
                minimumWidth: Math.min(400, parent ? parent.width - 2 * margins : 400)
                margins: UiKit.DesignSystem.SpacingCommon._L
                title: transferAction === "export" ? "Создать MData" : "Войти с MData"
                subtitle: transferAction === "export" ? "Только данные входа MAX Mod, включая его токены из Windows. Переписка, медиа и кэш не копируются. MAX Mod остаётся открыт." : !previewReady ? "Сначала проверим MData и покажем аккаунты. Вход выполняется только после отдельного подтверждения." : legacyPreview ? "Это старый формат. Список аккаунтов до добавления недоступен. Текущие аккаунты останутся; MAX Mod перезапустится." : "Будут добавлены перечисленные аккаунты. Текущие аккаунты останутся; MAX Mod перезапустится."
                confirmText: transferAction === "export" ? "Создать MData" : !previewReady ? "Проверить MData" : legacyPreview ? "Добавить без предпросмотра" : "Войти с MData"
                cancelText: mdataPage.pendingRequest === null ? "Отмена" : ""
                confirmButtonEnabled: mdataPage.pendingRequest === null
                confirmButtonLoading: mdataPage.pendingRequest !== null
                closePolicy: mdataPage.pendingRequest !== null ? Popup.NoAutoClose : Popup.CloseOnEscape | Popup.CloseOnPressOutside
                function clearSecrets() { previewReady = false; previewSecret = ""; previewAccounts = []; previewFingerprint = ""; legacyPreview = false; secret = ""; confirmation = ""; }
                Component.onDestruction: clearSecrets()
                onConfirmClicked: mdataPage.submit(passwordDialog)
                onCancelClicked: { if (mdataPage.pendingRequest === null) close(); }
                onCloseClicked: { if (mdataPage.pendingRequest === null) close(); }
                onClosed: {
                    clearSecrets();
                    if (mdataPage.activeDialog === passwordDialog) mdataPage.activeDialog = null;
                    Qt.callLater(destroy);
                }
                dialogContentItem: ColumnLayout {
                    enabled: mdataPage.pendingRequest === null
                    spacing: mdataPage.gap
                    TextField {
                        objectName: "mdataPassword"
                        visible: !passwordDialog.previewReady
                        Layout.fillWidth: true
                        text: passwordDialog.secret
                        onTextChanged: passwordDialog.secret = text
                        placeholderText: passwordDialog.transferAction === "export" ? "Пароль для MData" : "Пароль выбранной MData"
                        echoMode: TextInput.Password
                        inputMethodHints: Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
                        font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
                        color: mdataPage.maxwellColorTokens.colorTextPrimary
                        placeholderTextColor: mdataPage.maxwellColorTokens.colorTextTertiary
                        padding: mdataPage.gap
                        background: Rectangle { color: mdataPage.maxwellColorTokens.colorBackgroundCard; radius: UiKit.DesignSystem.BorderRadiusCommon._S; border.color: mdataPage.maxwellColorTokens.colorDividerPrimary }
                    }
                    TextField {
                        objectName: "mdataPasswordConfirmation"
                        visible: passwordDialog.transferAction === "export"
                        Layout.fillWidth: true
                        text: passwordDialog.confirmation
                        onTextChanged: passwordDialog.confirmation = text
                        placeholderText: "Повтори пароль"
                        echoMode: TextInput.Password
                        inputMethodHints: Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
                        font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
                        color: mdataPage.maxwellColorTokens.colorTextPrimary
                        placeholderTextColor: mdataPage.maxwellColorTokens.colorTextTertiary
                        padding: mdataPage.gap
                        background: Rectangle { color: mdataPage.maxwellColorTokens.colorBackgroundCard; radius: UiKit.DesignSystem.BorderRadiusCommon._S; border.color: mdataPage.maxwellColorTokens.colorDividerPrimary }
                    }
                    MDataText { Layout.fillWidth: true; visible: passwordDialog.transferAction === "export"; text: passwordDialog.exportAccountIds.length === 0 ? "Все сохранённые аккаунты" : passwordDialog.exportAccountIds.length === 1 ? "Выбранный аккаунт: " + (passwordDialog.exportAccountName || "Без имени") + "\nID: " + passwordDialog.exportAccountIds[0] : "Выбрано аккаунтов: " + passwordDialog.exportAccountIds.length + "\nID: " + passwordDialog.exportAccountIds.join(", "); typography: Typography.FontUtil.Styles.Description; color: mdataPage.maxwellColorTokens.colorTextSecondary }
                    Repeater {
                        model: passwordDialog.previewAccounts
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true; spacing: 4
                            MDataText { Layout.fillWidth: true; text: modelData.displayName || modelData.shortName || "Аккаунт без имени"; maximumLineCount: 3; elide: Text.ElideRight; typography: Typography.FontUtil.Styles.BodyStrong }
                            MDataText { Layout.fillWidth: true; text: "ID: " + modelData.accountId; typography: Typography.FontUtil.Styles.Description; color: mdataPage.maxwellColorTokens.colorTextSecondary }
                        }
                    }
                    MDataText { Layout.fillWidth: true; visible: passwordDialog.errorText.length > 0; text: passwordDialog.errorText; color: mdataPage.maxwellColorTokens.colorTextAttention; typography: Typography.FontUtil.Styles.Description }
                }
            }
        }
        contentLoaderItem: UiKit.MeFlickable {
            id: mdataScroll
            objectName: "mdataScroll"
            contentWidth: width; contentHeight: mdataScrollContent.implicitHeight + 2 * mdataPage.sectionGap
            flickableDirection: Flickable.VerticalFlick
            ScrollBar.vertical: UiKit.MeScrollBar {}
            ColumnLayout {
                id: mdataScrollContent
                y: mdataPage.sectionGap
                width: mdataScroll.width; spacing: mdataPage.gap
                MDataText { objectName: "mdataDescription"; Layout.fillWidth: true; text: "MData переносит только вход в аккаунты. Переписка, файлы и кэш в неё не входят."; typography: Typography.FontUtil.Styles.Description; color: mdataPage.maxwellColorTokens.colorTextSecondary }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: mdataPage.gap
                    visible: exportAccounts.count > 0
                    MDataText { Layout.fillWidth: true; text: "Какие аккаунты перенести"; typography: Typography.FontUtil.Styles.BodyStrong }
                    RowLayout {
                        Layout.fillWidth: true; spacing: mdataPage.gap
                        MDataButton { objectName: "mdataExportAll"; Layout.fillWidth: true; text: "Все" + (mdataPage.exportScope === "all" ? " · выбрано" : ""); enabled: !mdataPage.busy; onClicked: mdataPage.exportScope = "all" }
                        MDataButton { objectName: "mdataExportCurrent"; Layout.fillWidth: true; text: "Текущий" + (mdataPage.exportScope === "current" ? " · выбран" : ""); enabled: !mdataPage.busy && mdataPage.validAccountId(mdataPage.currentExportAccountId); onClicked: mdataPage.exportScope = "current" }
                    }
                    Repeater {
                        id: exportAccounts
                        model: typeof application !== "undefined" && application.accountModel ? application.accountModel : null
                        onItemAdded: function(index, item) { mdataPage.rememberExportAccount(item.localId); }
                        onItemRemoved: function(index, item) { mdataPage.forgetExportAccount(item.localId); }
                        delegate: MDataButton {
                            required property var dependency
                            required property var accountId
                            readonly property string localId: String(QmlUtils.contactIdString(dependency.commonDependency.selfInfo.id))
                            readonly property string nativeName: String(dependency.commonDependency.selfInfo.name).slice(0, 128)
                            objectName: "mdataExportAccount_" + localId
                            Layout.fillWidth: true
                            text: nativeName + (mdataPage.exportAccountSelected(localId) ? " · выбран" : "")
                            enabled: !mdataPage.busy && mdataPage.validAccountId(localId)
                            onClicked: mdataPage.toggleExportAccount(localId, nativeName)
                        }
                    }
                }
                MDataButton { objectName: "createMData"; Layout.fillWidth: true; text: "Создать MData"; enabled: mdataPage.available && !mdataPage.busy; onClicked: createFileDialog.open() }
                MDataButton { objectName: "loadMData"; Layout.fillWidth: true; text: "Войти с MData"; enabled: mdataPage.available && !mdataPage.busy; onClicked: loadFileDialog.open() }
                MDataText { objectName: "mdataStatus"; Layout.fillWidth: true; text: mdataPage.statusText; typography: Typography.FontUtil.Styles.Description; color: mdataPage.hasError ? mdataPage.maxwellColorTokens.colorTextAttention : mdataPage.maxwellColorTokens.colorTextSecondary }
            }
        }
    }
}
