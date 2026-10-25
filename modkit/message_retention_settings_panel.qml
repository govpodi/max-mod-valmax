// Native settings; saved preference and confirmed runtime are separate.
Component {
    id: settingsValMAXRetention
    SettingsBaseItem {
        id: retentionPage
        property var selfComponent: settingsValMAXRetention
        title: "Удалённые сообщения"
        onBackClicked: root.backClicked()
        property string revision: ""
        property bool savedEnabled: false
        property bool editorEnabled: false
        property bool loaded: false
        property var runtime: null
        property bool runtimeTrusted: false
        property var pendingRequest: null
        property string pendingMethod: ""
        property bool needsRefresh: false
        property bool hasError: false
        property bool closing: false
        property string statusText: "Загрузка настроек…"
        readonly property bool dirty: loaded && editorEnabled !== savedEnabled
        readonly property bool saving: pendingRequest !== null && pendingMethod === "POST"
        readonly property bool confirmedActive: runtimeTrusted && runtime !== null && runtime.error === null && runtime.ready && runtime.applied && runtime.enabled && savedEnabled
        readonly property string runtimeTitle: !runtimeTrusted || runtime === null ? "Состояние фильтра неизвестно" : runtime.error !== null ? "Сохранение не подтверждено" : !runtime.ready ? "Фильтр не подключён" : !runtime.applied || runtime.enabled !== savedEnabled ? "Ожидание применения" : confirmedActive ? "Сохранение включено" : "Сохранение новых удалений выключено"
        readonly property string runtimeDetail: !runtimeTrusted || runtime === null ? "Нажми «Обновить», чтобы проверить связь с помощником ValMAX." : runtime.error !== null ? "Нативный фильтр сообщил об ошибке. Сохранение новых удалений сейчас не подтверждено." : !runtime.ready ? "Для сохранения нужен работающий помощник ValMAX. Запусти приложение обычным ярлыком." : !runtime.applied || runtime.enabled !== savedEnabled ? "Настройка сохранена. Работающий клиент ещё не подтвердил её применение." : confirmedActive ? "Клиент подтвердил: уже полученные сообщения сохраняются при наблюдаемых удалениях." : "Ранее сохранённые сообщения и значки корзины остаются."
        readonly property real gap: UiKit.DesignSystem.SpacingCommon._M
        readonly property real sectionGap: UiKit.DesignSystem.SpacingCommon._2XL
        readonly property real radius: UiKit.DesignSystem.BorderRadiusCommon._L
        function fail(message) { hasError = true; statusText = message; }
        function validResponse(value) {
            if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join("|") !== "enabled|revision|runtime|schema" || value.schema !== 1 || typeof value.revision !== "string" || !/^[0-9a-f]{64}$/.test(value.revision) || typeof value.enabled !== "boolean") return false;
            const r = value.runtime;
            if (!r || typeof r !== "object" || Array.isArray(r) || Object.keys(r).sort().join("|") !== "applied|enabled|error|historySyncVerified|ready|retainedEvents|retainedMessages" || typeof r.ready !== "boolean" || typeof r.applied !== "boolean" || typeof r.enabled !== "boolean" || typeof r.historySyncVerified !== "boolean" || !(r.error === null || typeof r.error === "string" && r.error.length <= 128) || r.applied && !r.ready) return false;
            for (const count of [r.retainedEvents, r.retainedMessages]) if (!Number.isSafeInteger(count) || count < 0 || count > 1000000000) return false;
            return true;
        }
        function abortPending() {
            retentionSettingsWatchdog.stop();
            const xhr = pendingRequest; pendingRequest = null; pendingMethod = "";
            if (xhr !== null) { xhr.onreadystatechange = function() {}; try { xhr.abort(); } catch (_) {} }
        }
        function request(method, body, quiet) {
            if (closing || pendingRequest !== null || ["GET", "POST"].indexOf(method) < 0) return;
            let xhr;
            try { xhr = new XMLHttpRequest(); } catch (_) { runtimeTrusted = false; fail("Помощник ValMAX сейчас недоступен."); return; }
            pendingRequest = xhr; pendingMethod = method;
            if (!quiet) { hasError = false; statusText = method === "POST" ? "Сохранение…" : "Обновление настроек…"; }
            retentionSettingsWatchdog.interval = method === "POST" ? 10000 : 7000;
            retentionSettingsWatchdog.restart();
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE || pendingRequest !== xhr) return;
                pendingRequest = null; pendingMethod = ""; retentionSettingsWatchdog.stop();
                if (closing) return;
                if (xhr.status === 409) { needsRefresh = true; runtimeTrusted = false; fail("Настройки изменились. Нажми «Обновить», затем повтори сохранение. Твой выбор оставлен в форме."); return; }
                if (xhr.status !== 200) { runtimeTrusted = false; if (method === "POST") needsRefresh = true; fail("Помощник ValMAX не ответил. Нажми «Обновить», чтобы проверить состояние. Твой выбор оставлен в форме."); return; }
                try {
                    if (typeof xhr.responseText !== "string" || xhr.responseText.length > 16384) throw new Error("size");
                    const value = JSON.parse(xhr.responseText);
                    if (!validResponse(value)) throw new Error("schema");
                    const keepDraft = dirty && method !== "POST";
                    savedEnabled = value.enabled; revision = value.revision; runtime = value.runtime; runtimeTrusted = true; loaded = true;
                    if (!keepDraft) editorEnabled = savedEnabled;
                    if (method === "POST") { needsRefresh = false; hasError = false; statusText = "Настройка сохранена на этом компьютере."; }
                    else if (!quiet) { needsRefresh = false; hasError = false; statusText = keepDraft ? "Состояние обновлено. Несохранённый выбор оставлен в форме." : "Состояние обновлено."; }
                    else if (hasError && !needsRefresh) { hasError = false; statusText = "Связь с помощником восстановлена."; }
                } catch (_) { runtimeTrusted = false; if (method === "POST") needsRefresh = true; fail("Ответ помощника не прошёл проверку. Твой выбор оставлен в форме."); }
            };
            try {
                xhr.open(method, "http://127.0.0.1:18973/message-retention/" + (method === "POST" ? "settings" : "status"));
                if (method === "POST") { xhr.setRequestHeader("Content-Type", "application/json"); xhr.setRequestHeader("X-Max-Mod-Local-Write", "message-retention-v1"); }
                xhr.send(body || "");
            } catch (_) { abortPending(); runtimeTrusted = false; if (method === "POST") needsRefresh = true; fail("Помощник ValMAX сейчас недоступен. Твой выбор оставлен в форме."); }
        }
        function save() {
            if (!loaded || !runtimeTrusted || pendingRequest !== null || !dirty || needsRefresh) return;
            request("POST", JSON.stringify({schema: 1, revision: revision, enabled: editorEnabled}), false);
        }
        Component.onCompleted: request("GET", "", false)
        Component.onDestruction: { closing = true; abortPending(); retentionSettingsPoll.stop(); }
        Timer {
            id: retentionSettingsPoll; objectName: "retentionSettingsPoll"
            interval: 2000; repeat: true; running: retentionPage.visible && !retentionPage.closing
            onTriggered: retentionPage.request("GET", "", true)
        }
        Timer {
            id: retentionSettingsWatchdog; objectName: "retentionSettingsWatchdog"
            interval: 7000
            onTriggered: {
                const wasPost = retentionPage.pendingMethod === "POST";
                retentionPage.abortPending(); retentionPage.runtimeTrusted = false;
                if (wasPost) retentionPage.needsRefresh = true;
                retentionPage.fail("Ответ не получен. Нажми «Обновить», чтобы проверить состояние. Твой выбор оставлен в форме.");
            }
        }
        component RetentionText: Label {
            property int typography: Typography.FontUtil.Styles.Body
            font: Typography.FontUtil.getFont(typography, Typography.FontUtil.Sizes.Default)
            lineHeight: Typography.FontUtil.getLineHeight(typography, Typography.FontUtil.Sizes.Default)
            lineHeightMode: Text.FixedHeight; textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: retentionPage.maxwellColorTokens.colorTextPrimary
        }
        component RetentionButton: Button {
            font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
            implicitHeight: Math.max(UiKit.DesignSystem.SizeBase.px48, contentItem.implicitHeight + 2 * retentionPage.gap)
            padding: retentionPage.gap
            contentItem: RetentionText { text: parent.text; color: parent.enabled ? retentionPage.maxwellColorTokens.colorTextThemed : retentionPage.maxwellColorTokens.colorTextTertiary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: retentionPage.radius; color: retentionPage.maxwellColorTokens.colorBackgroundCard }
        }
        contentLoaderItem: UiKit.MeFlickable {
            id: retentionSettingsScroll
            objectName: "retentionSettingsScroll"
            contentWidth: width; contentHeight: retentionScrollContent.implicitHeight + 2 * retentionPage.sectionGap
            flickableDirection: Flickable.VerticalFlick
            ScrollBar.vertical: UiKit.MeScrollBar {}
            ColumnLayout {
                id: retentionScrollContent
                y: retentionPage.sectionGap
                width: retentionSettingsScroll.width; spacing: retentionPage.sectionGap
                RetentionText { Layout.fillWidth: true; text: "Общий для всех аккаунтов ValMAX"; typography: Typography.FontUtil.Styles.BodyStrong }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: UiKit.DesignSystem.SpacingCommon._S
                    RetentionText { objectName: "retentionRuntimeTitle"; Layout.fillWidth: true; text: retentionPage.runtimeTitle; typography: Typography.FontUtil.Styles.BodyStrong; color: retentionPage.confirmedActive ? retentionPage.maxwellColorTokens.colorTextPositive : retentionPage.maxwellColorTokens.colorTextSecondary }
                    RetentionText { objectName: "retentionRuntimeDetail"; Layout.fillWidth: true; text: retentionPage.runtimeDetail; typography: Typography.FontUtil.Styles.Description; color: retentionPage.maxwellColorTokens.colorTextSecondary }
                    RetentionText { objectName: "retentionExperimental"; Layout.fillWidth: true; visible: !retentionPage.runtimeTrusted || !retentionPage.runtime || !retentionPage.runtime.historySyncVerified; text: "Бета: входящий текст сохраняется при включённой функции и работающем обработчике. Удаления, которые обработчик не увидел, не восстанавливаются; ранее сохранённые сообщения остаются."; typography: Typography.FontUtil.Styles.Description; color: retentionPage.maxwellColorTokens.colorTextAttention }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: retentionPage.gap
                    RetentionText { Layout.fillWidth: true; text: "Сохранять удалённые сообщения"; typography: Typography.FontUtil.Styles.BodyStrong }
                    UiKit.MeSwitcher { objectName: "retentionEnabled"; enabled: retentionPage.loaded && !retentionPage.saving; checked: retentionPage.editorEnabled; Accessible.name: "Сохранять удалённые сообщения"; onToggled: retentionPage.editorEnabled = checked }
                }
                RetentionText { Layout.fillWidth: true; text: "Сохраняются уже полученные сообщения. Удалённое до получения восстановить нельзя."; typography: Typography.FontUtil.Styles.Description; color: retentionPage.maxwellColorTokens.colorTextSecondary }
                RetentionText { Layout.fillWidth: true; text: "Значок корзины показывает наблюдаемое удаление. Выключение не убирает ранее сохранённые сообщения и их отметки."; typography: Typography.FontUtil.Styles.Description; color: retentionPage.maxwellColorTokens.colorTextSecondary }
                RetentionText { objectName: "retentionCounts"; Layout.fillWidth: true; visible: retentionPage.runtimeTrusted && retentionPage.runtime !== null; text: retentionPage.runtime && retentionPage.runtime.retainedEvents !== null && retentionPage.runtime.retainedMessages !== null ? "Отмечено удалений: " + retentionPage.runtime.retainedEvents + ". Сообщений сохранено: " + retentionPage.runtime.retainedMessages + "." : "Общее количество удалений и сохранённых сообщений недоступно."; typography: Typography.FontUtil.Styles.Description; color: retentionPage.maxwellColorTokens.colorTextSecondary }
                RetentionText { objectName: "retentionDraftStatus"; Layout.fillWidth: true; visible: retentionPage.dirty; text: "Есть несохранённые изменения."; typography: Typography.FontUtil.Styles.Description; color: retentionPage.maxwellColorTokens.colorTextAttention }
                RetentionText { objectName: "retentionStatus"; Layout.fillWidth: true; text: retentionPage.statusText; typography: Typography.FontUtil.Styles.Description; color: retentionPage.hasError ? retentionPage.maxwellColorTokens.colorTextAttention : retentionPage.maxwellColorTokens.colorTextSecondary }
                Flow {
                    Layout.fillWidth: true; spacing: retentionPage.gap
                    RetentionButton { objectName: "saveRetentionSettings"; text: "Сохранить"; enabled: retentionPage.loaded && retentionPage.runtimeTrusted && retentionPage.pendingRequest === null && retentionPage.dirty && !retentionPage.needsRefresh; onClicked: retentionPage.save() }
                    RetentionButton { objectName: "refreshRetentionSettings"; text: "Обновить"; enabled: retentionPage.pendingRequest === null; onClicked: retentionPage.request("GET", "", false) }
                }
            }
        }
    }
}
