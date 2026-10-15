// Local preferences page inserted into the native LeadingSettingsBar resource.
Component {
    id: settingsValMAXAccounts
    SettingsBaseItem {
        id: accountPreferencesPage
        property var selfComponent: settingsValMAXAccounts
        title: "Аккаунты ValMAX"
        onBackClicked: root.backClicked()
        property var accounts: []
        property string revision: ""
        property bool loaded: false
        property var pendingRequest: null
        property string selectedAccountId: String(QmlUtils.contactIdString(root.selfInfo.id))
        property string selectedNativeName: root.selfInfo.name
        property string editorName: ""
        property string editorColor: ""
        property bool editorWorkEnabled: false
        property var editorDays: [1, 2, 3, 4, 5]
        property string editorStart: "09:00"
        property string editorEnd: "18:00"
        property var editorGhost: ({hideRead: false, hideStoryRead: false, hideTyping: false, hideOnline: false, autoOffline: false})
        property bool dirty: false
        property bool hasError: false
        property string statusText: "Загрузка настроек аккаунтов…"
        readonly property var accountPalette: ["", "#0088ff", "#8b5cf6", "#22a06b", "#e58b17", "#dc5260", "#d35ca5", "#149ca1"]
        readonly property var colorNames: ["Без цвета", "Синий", "Фиолетовый", "Зелёный", "Оранжевый", "Красный", "Розовый", "Бирюзовый"]
        readonly property real gap: UiKit.DesignSystem.SpacingCommon._M
        readonly property real sectionGap: UiKit.DesignSystem.SpacingCommon._2XL
        readonly property real radius: UiKit.DesignSystem.BorderRadiusCommon._L
        function error(message) { hasError = true; statusText = message; }
        function rowFor(id) {
            for (var i = 0; i < accounts.length; ++i) if (accounts[i].accountId === id) return accounts[i];
            return null;
        }
        function displayFor(id, nativeName) { var row = rowFor(id); return row && row.displayName.length ? row.displayName : nativeName; }
        function loadSelection() {
            var row = rowFor(selectedAccountId);
            editorName = row ? row.displayName : "";
            editorColor = row ? row.color : "";
            editorWorkEnabled = row ? row.workHours.enabled : false;
            editorDays = row ? row.workHours.days.slice() : [1, 2, 3, 4, 5];
            editorStart = row ? row.workHours.start : "09:00";
            editorEnd = row ? row.workHours.end : "18:00";
            editorGhost = row ? JSON.parse(JSON.stringify(row.ghost)) : {hideRead: false, hideStoryRead: false, hideTyping: false, hideOnline: false, autoOffline: false};
            dirty = false;
        }
        function selectAccount(id, name) {
            if (id === selectedAccountId) return;
            if (dirty) { error("Сохрани или отмени изменения перед выбором другого аккаунта."); return; }
            selectedAccountId = id; selectedNativeName = name; loadSelection(); hasError = false;
        }
        function setDay(day, checked) {
            var next = editorDays.slice(); var index = next.indexOf(day);
            if (checked && index < 0) next.push(day);
            if (!checked && index >= 0) next.splice(index, 1);
            next.sort(); editorDays = next; dirty = true;
        }
        function validResponse(value) {
            if (!value || value.schema !== 1 || !/^[0-9a-f]{64}$/.test(value.revision) || !Array.isArray(value.accounts) || value.accounts.length > 32) return false;
            var seen = {};
            for (var i = 0; i < value.accounts.length; ++i) {
                var row = value.accounts[i]; var hours = row ? row.workHours : null;
                if (!row || typeof row.accountId !== "string" || !/^[1-9][0-9]{0,18}$/.test(row.accountId) || seen[row.accountId] || typeof row.displayName !== "string" || row.displayName.replace(/[\ud800-\udbff][\udc00-\udfff]/g, "x").length > 64 || accountPalette.indexOf(row.color) < 0 || !hours || typeof hours.enabled !== "boolean" || !Array.isArray(hours.days) || hours.days.length > 7 || !/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(hours.start) || !/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(hours.end) || hours.start === hours.end || !row.ghost) return false;
                seen[row.accountId] = true;
                var daySeen = {};
                for (var j = 0; j < hours.days.length; ++j) { var day = hours.days[j]; if (typeof day !== "number" || Math.floor(day) !== day || day < 1 || day > 7 || daySeen[day]) return false; daySeen[day] = true; }
                if (hours.enabled && !hours.days.length) return false;
                var ghostKeys = ["hideRead", "hideStoryRead", "hideTyping", "hideOnline", "autoOffline"];
                for (var k = 0; k < ghostKeys.length; ++k) if (typeof row.ghost[ghostKeys[k]] !== "boolean") return false;
            }
            return true;
        }
        function request(method, body) {
            if (pendingRequest !== null) return;
            var xhr = new XMLHttpRequest(); pendingRequest = xhr;
            hasError = false; statusText = method === "POST" ? "Сохранение…" : "Загрузка настроек аккаунтов…";
            requestWatchdog.restart();
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE || pendingRequest !== xhr) return;
                pendingRequest = null; requestWatchdog.stop();
                if (xhr.status === 409) { error("Настройки изменились. Нажми «Обновить» и повтори сохранение. Текст формы сохранён."); return; }
                if (xhr.status === 400) { error("Проверь название, дни недели и время. Изменения формы сохранены."); return; }
                if (xhr.status !== 200) { error("Настройки недоступны. Запусти ValMAX обычным ярлыком и повтори."); return; }
                try {
                    var value = JSON.parse(xhr.responseText);
                    if (!validResponse(value)) throw new Error("schema");
                    accounts = value.accounts; revision = value.revision; loaded = true;
                    if (method === "POST" || !dirty) loadSelection();
                    statusText = method === "POST" ? "Настройки этого аккаунта сохранены на компьютере." : dirty ? "Настройки обновлены. Несохранённые изменения формы сохранены." : "Выбери аккаунт, который хочешь настроить.";
                } catch (ignored) { error("Ответ хранилища не прошёл проверку. Изменения формы сохранены."); }
            };
            xhr.open(method, "http://127.0.0.1:18973/account-settings");
            if (method === "POST") { xhr.setRequestHeader("Content-Type", "application/json"); xhr.setRequestHeader("X-Max-Mod-Local-Write", "account-settings-v1"); }
            xhr.send(body || "");
        }
        function save() {
            if (!loaded || pendingRequest !== null) return;
            if (Array.from(editorName).length > 64 || /[\u0000-\u001f\u007f-\u009f\u2028-\u202e\u2066-\u2069]/.test(editorName)) { error("Название: до 64 символов, без переносов строк."); return; }
            var timePattern = /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/;
            if (!timePattern.test(editorStart) || !timePattern.test(editorEnd) || editorStart === editorEnd || editorWorkEnabled && editorDays.length === 0) { error("Выбери дни недели и разное время начала и окончания в формате ЧЧ:ММ."); return; }
            var next = [];
            for (var i = 0; i < accounts.length; ++i) if (accounts[i].accountId !== selectedAccountId) next.push(accounts[i]);
            next.push({accountId: selectedAccountId, displayName: editorName.trim(), color: editorColor, workHours: {enabled: editorWorkEnabled, days: editorDays.slice(), start: editorStart, end: editorEnd}, ghost: editorGhost});
            if (next.length > 32) { error("Сохранено слишком много старых настроек аккаунтов."); return; }
            request("POST", JSON.stringify({schema: 1, revision: revision, accounts: next}));
        }
        Component.onCompleted: request("GET", "")
        Component.onDestruction: { if (pendingRequest !== null) { pendingRequest.onreadystatechange = function() {}; pendingRequest.abort(); } }
        Timer {
            id: requestWatchdog; interval: 7000
            onTriggered: {
                var xhr = accountPreferencesPage.pendingRequest; accountPreferencesPage.pendingRequest = null;
                if (xhr !== null) { xhr.onreadystatechange = function() {}; xhr.abort(); }
                accountPreferencesPage.error("Ответ не получен. Нажми «Обновить», чтобы проверить сохранение. Изменения формы сохранены.");
            }
        }
        component AccountText: Label {
            property int typography: Typography.FontUtil.Styles.Body
            font: Typography.FontUtil.getFont(typography, Typography.FontUtil.Sizes.Default)
            lineHeight: Typography.FontUtil.getLineHeight(typography, Typography.FontUtil.Sizes.Default)
            lineHeightMode: Text.FixedHeight; textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: accountPreferencesPage.maxwellColorTokens.colorTextPrimary
        }
        component AccountButton: Button {
            font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
            implicitHeight: Math.max(UiKit.DesignSystem.SizeBase.px48, contentItem.implicitHeight + 2 * accountPreferencesPage.gap)
            padding: accountPreferencesPage.gap
            contentItem: AccountText { text: parent.text; color: parent.enabled ? accountPreferencesPage.maxwellColorTokens.colorTextThemed : accountPreferencesPage.maxwellColorTokens.colorTextTertiary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: accountPreferencesPage.radius; color: accountPreferencesPage.maxwellColorTokens.colorBackgroundCard }
        }
        component AccountField: TextField {
            selectByMouse: true; padding: accountPreferencesPage.gap
            font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
            color: accountPreferencesPage.maxwellColorTokens.colorTextPrimary
            placeholderTextColor: accountPreferencesPage.maxwellColorTokens.colorTextTertiary
            background: Rectangle { radius: accountPreferencesPage.radius; color: accountPreferencesPage.maxwellColorTokens.colorBackgroundCard; border.color: accountPreferencesPage.maxwellColorTokens.colorDividerPrimary }
        }
        contentLoaderItem: UiKit.MeFlickable {
            id: accountScroll; objectName: "accountPreferencesScroll"
            contentWidth: width; contentHeight: accountScrollContent.implicitHeight + 2 * accountPreferencesPage.sectionGap
            flickableDirection: Flickable.VerticalFlick
            ScrollBar.vertical: UiKit.MeScrollBar {}
            ColumnLayout {
                id: accountScrollContent
                y: accountPreferencesPage.sectionGap
                width: accountScroll.width; spacing: accountPreferencesPage.sectionGap
                AccountText { Layout.fillWidth: true; text: "У каждого аккаунта — своё название, цвет и часы уведомлений. Название видно только в ValMAX; публичный профиль остаётся прежним."; color: accountPreferencesPage.maxwellColorTokens.colorTextSecondary }
                AccountText { Layout.fillWidth: true; text: "Настраиваемый аккаунт"; typography: Typography.FontUtil.Styles.BodyStrong }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: UiKit.DesignSystem.SpacingCommon._S
                    Repeater {
                        model: application.accountModel
                        delegate: AccountButton {
                            required property var dependency
                            required property var accountId
                            readonly property string localId: String(QmlUtils.contactIdString(dependency.commonDependency.selfInfo.id))
                            Layout.fillWidth: true
                            enabled: accountPreferencesPage.loaded && accountPreferencesPage.pendingRequest === null
                            text: accountPreferencesPage.displayFor(localId, dependency.commonDependency.selfInfo.name) + (localId === accountPreferencesPage.selectedAccountId ? " · выбран" : "")
                            onClicked: accountPreferencesPage.selectAccount(localId, dependency.commonDependency.selfInfo.name)
                            Accessible.name: text
                        }
                    }
                }
                AccountText { objectName: "accountPreferencesStatus"; Layout.fillWidth: true; text: accountPreferencesPage.statusText; typography: Typography.FontUtil.Styles.Description; color: accountPreferencesPage.hasError ? accountPreferencesPage.maxwellColorTokens.colorTextAttention : accountPreferencesPage.maxwellColorTokens.colorTextSecondary }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: accountPreferencesPage.gap
                    enabled: accountPreferencesPage.loaded && accountPreferencesPage.pendingRequest === null
                    AccountText { Layout.fillWidth: true; text: accountPreferencesPage.selectedNativeName; typography: Typography.FontUtil.Styles.BodyStrong }
                    AccountText { text: "Название на этом компьютере"; typography: Typography.FontUtil.Styles.Description; color: accountPreferencesPage.maxwellColorTokens.colorTextSecondary }
                    AccountField { objectName: "accountLocalName"; Layout.fillWidth: true; text: accountPreferencesPage.editorName; placeholderText: "Личный, Работа, ИП…"; maximumLength: 128; onTextEdited: { accountPreferencesPage.editorName = text; accountPreferencesPage.dirty = true; } }
                    AccountText { text: "Цвет аккаунта"; typography: Typography.FontUtil.Styles.Description; color: accountPreferencesPage.maxwellColorTokens.colorTextSecondary }
                    Flow {
                        Layout.fillWidth: true; spacing: accountPreferencesPage.gap
                        Repeater {
                            model: accountPreferencesPage.accountPalette
                            delegate: Button {
                                required property int index
                                required property string modelData
                                objectName: "accountColor_" + index
                                implicitWidth: 44; implicitHeight: 44; padding: 7
                                Accessible.name: accountPreferencesPage.colorNames[index]
                                ToolTip.visible: hovered; ToolTip.text: accountPreferencesPage.colorNames[index]
                                onClicked: { accountPreferencesPage.editorColor = modelData; accountPreferencesPage.dirty = true; }
                                background: Rectangle { radius: width / 2; color: "transparent"; border.width: accountPreferencesPage.editorColor === modelData ? 2 : 0; border.color: accountPreferencesPage.maxwellColorTokens.colorTextThemed }
                                contentItem: Rectangle { radius: width / 2; color: modelData || accountPreferencesPage.maxwellColorTokens.colorBackgroundCard; border.width: modelData.length ? 0 : 1; border.color: accountPreferencesPage.maxwellColorTokens.colorDividerPrimary; Label { anchors.centerIn: parent; visible: !modelData.length; text: "×"; color: accountPreferencesPage.maxwellColorTokens.colorTextSecondary } }
                            }
                        }
                    }
                    Rectangle { Layout.fillWidth: true; Layout.topMargin: accountPreferencesPage.gap; Layout.bottomMargin: accountPreferencesPage.gap; implicitHeight: 1; color: accountPreferencesPage.maxwellColorTokens.colorDividerPrimary }
                    RowLayout {
                        Layout.fillWidth: true
                        AccountText { Layout.fillWidth: true; text: "Рабочие часы"; typography: Typography.FontUtil.Styles.BodyStrong }
                        UiKit.MeSwitcher { objectName: "accountWorkHoursEnabled"; checked: accountPreferencesPage.editorWorkEnabled; onToggled: { accountPreferencesPage.editorWorkEnabled = checked; accountPreferencesPage.dirty = true; } }
                    }
                    AccountText { Layout.fillWidth: true; text: "Расписание отключает уведомления вне выбранных часов и сохраняет ручное отключение. Используется местное время компьютера. Применяется, пока ValMAX работает."; color: accountPreferencesPage.maxwellColorTokens.colorTextSecondary; typography: Typography.FontUtil.Styles.Description }
                    Flow {
                        Layout.fillWidth: true; spacing: UiKit.DesignSystem.SpacingCommon._2XS
                        enabled: accountPreferencesPage.editorWorkEnabled
                        Repeater {
                            model: ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                            delegate: Button {
                                required property int index
                                required property string modelData
                                text: modelData; checkable: true; checked: accountPreferencesPage.editorDays.indexOf(index + 1) >= 0
                                implicitWidth: 44; implicitHeight: 40; padding: 7
                                onToggled: accountPreferencesPage.setDay(index + 1, checked)
                                font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default)
                                contentItem: AccountText { text: parent.text; typography: Typography.FontUtil.Styles.Description; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: !parent.enabled ? accountPreferencesPage.maxwellColorTokens.colorTextTertiary : parent.checked ? accountPreferencesPage.maxwellColorTokens.colorTextThemed : accountPreferencesPage.maxwellColorTokens.colorTextSecondary }
                                background: Rectangle { radius: UiKit.DesignSystem.BorderRadiusCommon._S; color: accountPreferencesPage.maxwellColorTokens.colorBackgroundCard; border.width: parent.checked && parent.enabled ? 1 : 0; border.color: accountPreferencesPage.maxwellColorTokens.colorTextThemed }
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; enabled: accountPreferencesPage.editorWorkEnabled; spacing: accountPreferencesPage.gap
                        AccountText { text: "С" }
                        AccountField { objectName: "accountWorkStart"; Layout.fillWidth: true; text: accountPreferencesPage.editorStart; placeholderText: "09:00"; maximumLength: 5; inputMethodHints: Qt.ImhTime; onTextEdited: { accountPreferencesPage.editorStart = text; accountPreferencesPage.dirty = true; } }
                        AccountText { text: "до" }
                        AccountField { objectName: "accountWorkEnd"; Layout.fillWidth: true; text: accountPreferencesPage.editorEnd; placeholderText: "18:00"; maximumLength: 5; inputMethodHints: Qt.ImhTime; onTextEdited: { accountPreferencesPage.editorEnd = text; accountPreferencesPage.dirty = true; } }
                    }
                    AccountText { Layout.fillWidth: true; visible: accountPreferencesPage.editorWorkEnabled; text: "Если окончание раньше начала, смена продолжается на следующий день. Если вручную включить уведомления вне расписания, они останутся включены до следующей смены режима."; color: accountPreferencesPage.maxwellColorTokens.colorTextSecondary; typography: Typography.FontUtil.Styles.Description }
                }
                Flow {
                    Layout.fillWidth: true; spacing: accountPreferencesPage.gap
                    AccountButton { objectName: "saveAccountPreferences"; text: "Сохранить"; enabled: accountPreferencesPage.loaded && accountPreferencesPage.pendingRequest === null && accountPreferencesPage.dirty; onClicked: accountPreferencesPage.save() }
                    AccountButton { text: "Отменить изменения"; enabled: accountPreferencesPage.loaded && accountPreferencesPage.pendingRequest === null && accountPreferencesPage.dirty; onClicked: { accountPreferencesPage.loadSelection(); accountPreferencesPage.hasError = false; accountPreferencesPage.statusText = "Изменения отменены."; } }
                    AccountButton { objectName: "reloadAccountPreferences"; text: "Обновить"; enabled: accountPreferencesPage.pendingRequest === null; onClicked: accountPreferencesPage.request("GET", "") }
                }
            }
        }
    }
}
