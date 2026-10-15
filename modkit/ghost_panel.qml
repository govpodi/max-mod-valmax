// Native settings page. A saved preference is never runtime confirmation.
Component {
    id: settingsValMAXGhost
    SettingsBaseItem {
        id: ghostPage
        property var selfComponent: settingsValMAXGhost
        title: "Режим призрака"
        onBackClicked: root.backClicked()
        property string revision: ""
        property var savedSettings: ({enabled: false, hideTyping: true, hideRead: true, hideOnline: false, hideStories: false})
        property var runtime: null
        property bool runtimeTrusted: false
        property bool loaded: false
        property bool editorEnabled: false
        property bool editorHideTyping: true
        property bool editorHideRead: true
        property bool editorHideOnline: false
        property bool editorHideStories: false
        property var pendingRequest: null
        property string pendingMethod: ""
        property bool needsRefresh: false
        property bool hasError: false
        property string statusText: "Загрузка настроек…"
        readonly property bool dirty: loaded && (editorEnabled !== savedSettings.enabled || editorHideTyping !== savedSettings.hideTyping || editorHideRead !== savedSettings.hideRead || editorHideOnline !== savedSettings.hideOnline || editorHideStories !== savedSettings.hideStories)
        readonly property bool saving: pendingRequest !== null && pendingMethod === "POST"
        readonly property bool canConfigure: loaded && runtimeTrusted && runtime !== null && runtime.ready && runtime.features.hideTyping && runtime.features.hideRead
        readonly property bool confirmedActive: canConfigure && runtime.applied && runtime.state === "active"
        readonly property string runtimeTitle: !runtimeTrusted || runtime === null || !runtime.ready ? "Защита не подключена" : runtime.state === "pending" ? "Ожидание применения" : confirmedActive ? "Фильтры работают" : "Режим выключен"
        readonly property string runtimeDetail: !runtimeTrusted || runtime === null || !runtime.ready ? "Запусти ValMAX обычным ярлыком: для этих фильтров нужен работающий помощник мода." : runtime.state === "pending" ? "Настройки сохранены. Работающий клиент ещё не подтвердил их применение." : confirmedActive ? "Работающий клиент подтвердил сохранённые настройки для всех аккаунтов." : "Работающий клиент подтвердил: сохранённые фильтры выключены."
        readonly property real gap: UiKit.DesignSystem.SpacingCommon._M
        readonly property real sectionGap: UiKit.DesignSystem.SpacingCommon._2XL
        readonly property real radius: UiKit.DesignSystem.BorderRadiusCommon._L
        function fail(message) { hasError = true; statusText = message; }
        function resetDraft() { editorEnabled = savedSettings.enabled; editorHideTyping = savedSettings.hideTyping; editorHideRead = savedSettings.hideRead; editorHideOnline = savedSettings.hideOnline; editorHideStories = savedSettings.hideStories; }
        function validResponse(value) {
            if (!value || value.schema !== 1 || typeof value.revision !== "string" || !/^[0-9a-f]{64}$/.test(value.revision)) return false;
            var s = value.settings, r = value.runtime;
            var flags = ["hideTyping", "hideRead", "hideOnline", "hideStories"];
            if (!s || Array.isArray(s) || Object.keys(s).sort().join(",") !== "enabled,hideOnline,hideRead,hideStories,hideTyping" || typeof s.enabled !== "boolean") return false;
            if (!r || r.scope !== "all_accounts" || ["active", "off", "pending", "unavailable"].indexOf(r.state) < 0 || typeof r.ready !== "boolean" || typeof r.applied !== "boolean" || !r.features || !r.blocked) return false;
            for (var f = 0; f < flags.length; ++f) if (typeof s[flags[f]] !== "boolean" || typeof r.features[flags[f]] !== "boolean" || r.applied && s[flags[f]] && !r.features[flags[f]]) return false;
            var counters = ["typing", "read", "presence", "stories"];
            for (var i = 0; i < counters.length; ++i) { var n = r.blocked[counters[i]]; if (typeof n !== "number" || !isFinite(n) || Math.floor(n) !== n || n < 0 || n > 1000000) return false; }
            if (r.ready && (!r.features.hideTyping || !r.features.hideRead) || r.applied && !r.ready) return false;
            var active = r.applied && s.enabled && (s.hideTyping || s.hideRead || s.hideOnline || s.hideStories);
            return r.state === (active ? "active" : r.applied ? "off" : r.ready ? "pending" : "unavailable");
        }
        function request(method, body, quiet) {
            if (pendingRequest !== null) return;
            var xhr = new XMLHttpRequest(); pendingRequest = xhr; pendingMethod = method;
            if (!quiet) { hasError = false; statusText = method === "POST" ? "Сохранение…" : "Обновление настроек…"; }
            ghostWatchdog.interval = method === "POST" ? 10000 : 7000;
            ghostWatchdog.restart();
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE || ghostPage.pendingRequest !== xhr) return;
                ghostPage.pendingRequest = null; ghostPage.pendingMethod = ""; ghostWatchdog.stop();
                if (xhr.status === 409) { needsRefresh = true; fail("Настройки изменились. Нажми «Обновить», затем повтори сохранение. Твой выбор сохранён в форме."); return; }
                if (xhr.status !== 200) { runtimeTrusted = false; if (method === "POST") needsRefresh = true; fail("Помощник ValMAX не ответил. Нажми «Обновить», чтобы проверить состояние. Твой выбор сохранён в форме."); return; }
                try {
                    var value = JSON.parse(xhr.responseText);
                    if (!validResponse(value)) throw new Error("schema");
                    var keepDraft = dirty && method !== "POST";
                    savedSettings = value.settings; revision = value.revision; runtime = value.runtime; runtimeTrusted = true; loaded = true;
                    if (!keepDraft) resetDraft();
                    if (method === "POST") { needsRefresh = false; hasError = false; statusText = "Настройки сохранены на этом компьютере."; }
                    else if (!quiet) { needsRefresh = false; hasError = false; statusText = keepDraft ? "Настройки обновлены. Несохранённый выбор оставлен в форме." : "Настройки обновлены."; }
                    else if (hasError && !needsRefresh) { hasError = false; statusText = "Связь с помощником восстановлена."; }
                } catch (ignored) { runtimeTrusted = false; if (method === "POST") needsRefresh = true; fail("Ответ помощника не прошёл проверку. Твой выбор сохранён в форме."); }
            };
            xhr.open(method, "http://127.0.0.1:18973/ghost-settings");
            if (method === "POST") { xhr.setRequestHeader("Content-Type", "application/json"); xhr.setRequestHeader("X-Max-Mod-Local-Write", "ghost-settings-v1"); }
            xhr.send(body || "");
        }
        function save() {
            if (!canConfigure || pendingRequest !== null || !dirty || needsRefresh) return;
            request("POST", JSON.stringify({schema: 1, revision: revision, settings: {enabled: editorEnabled, hideTyping: editorHideTyping, hideRead: editorHideRead, hideOnline: editorHideOnline, hideStories: editorHideStories}}), false);
        }
        Component.onCompleted: request("GET", "", false)
        Component.onDestruction: { if (pendingRequest !== null) { pendingRequest.onreadystatechange = function() {}; pendingRequest.abort(); } }
        Timer {
            id: ghostPoll; objectName: "ghostPoll"
            interval: 2000; repeat: true; running: ghostPage.visible
            onTriggered: ghostPage.request("GET", "", true)
        }
        Timer {
            id: ghostWatchdog; objectName: "ghostWatchdog"
            interval: 7000
            onTriggered: {
                var xhr = ghostPage.pendingRequest, wasPost = ghostPage.pendingMethod === "POST";
                ghostPage.pendingRequest = null; ghostPage.pendingMethod = "";
                if (xhr !== null) { xhr.onreadystatechange = function() {}; xhr.abort(); }
                ghostPage.runtimeTrusted = false;
                if (wasPost) ghostPage.needsRefresh = true;
                ghostPage.fail("Ответ не получен за " + (wasPost ? "10" : "7") + " секунд. Нажми «Обновить», чтобы проверить состояние. Твой выбор сохранён в форме.");
            }
        }
        component GhostText: Label {
            property int typography: Typography.FontUtil.Styles.Body
            font: Typography.FontUtil.getFont(typography, Typography.FontUtil.Sizes.Default)
            lineHeight: Typography.FontUtil.getLineHeight(typography, Typography.FontUtil.Sizes.Default)
            lineHeightMode: Text.FixedHeight; textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: ghostPage.maxwellColorTokens.colorTextPrimary
        }
        component GhostButton: Button {
            font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
            implicitHeight: Math.max(UiKit.DesignSystem.SizeBase.px48, contentItem.implicitHeight + 2 * ghostPage.gap)
            padding: ghostPage.gap
            contentItem: GhostText { text: parent.text; color: parent.enabled ? ghostPage.maxwellColorTokens.colorTextThemed : ghostPage.maxwellColorTokens.colorTextTertiary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: ghostPage.radius; color: ghostPage.maxwellColorTokens.colorBackgroundCard }
        }
        contentLoaderItem: UiKit.MeFlickable {
            id: ghostScroll; objectName: "ghostScroll"
            contentWidth: width; contentHeight: ghostScrollContent.implicitHeight + 2 * ghostPage.sectionGap
            flickableDirection: Flickable.VerticalFlick
            ScrollBar.vertical: UiKit.MeScrollBar {}
            ColumnLayout {
                id: ghostScrollContent
                y: ghostPage.sectionGap
                width: ghostScroll.width; spacing: ghostPage.sectionGap
                GhostText { Layout.fillWidth: true; text: "Общий для всех аккаунтов ValMAX"; typography: Typography.FontUtil.Styles.BodyStrong }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: UiKit.DesignSystem.SpacingCommon._S
                    GhostText { objectName: "ghostRuntimeTitle"; Layout.fillWidth: true; text: ghostPage.runtimeTitle; typography: Typography.FontUtil.Styles.BodyStrong; color: ghostPage.confirmedActive ? ghostPage.maxwellColorTokens.colorTextPositive : ghostPage.maxwellColorTokens.colorTextSecondary }
                    GhostText { objectName: "ghostRuntimeDetail"; Layout.fillWidth: true; text: ghostPage.runtimeDetail; typography: Typography.FontUtil.Styles.Description; color: ghostPage.maxwellColorTokens.colorTextSecondary }
                    GhostText { Layout.fillWidth: true; visible: ghostPage.canConfigure; text: ghostPage.runtime ? "Остановлено: «печатает» — " + ghostPage.runtime.blocked.typing + "; прочтений — " + ghostPage.runtime.blocked.read + "; просмотров историй — " + ghostPage.runtime.blocked.stories + ". Запросов без активного статуса — " + ghostPage.runtime.blocked.presence + "." : ""; typography: Typography.FontUtil.Styles.Description; color: ghostPage.maxwellColorTokens.colorTextSecondary }
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: ghostPage.gap
                    enabled: ghostPage.canConfigure && !ghostPage.saving
                    RowLayout {
                        Layout.fillWidth: true
                        GhostText { Layout.fillWidth: true; text: "Включить режим призрака"; typography: Typography.FontUtil.Styles.BodyStrong }
                        UiKit.MeSwitcher { objectName: "ghostEnabled"; checked: ghostPage.editorEnabled; Accessible.name: "Включить режим призрака"; onToggled: ghostPage.editorEnabled = checked }
                    }
                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: ghostPage.maxwellColorTokens.colorDividerPrimary }
                    RowLayout {
                        Layout.fillWidth: true
                        GhostText { Layout.fillWidth: true; text: "Не отправлять «печатает»" }
                        UiKit.MeCheckbox { objectName: "ghostHideTyping"; checked: ghostPage.editorHideTyping; Accessible.name: "Не отправлять «печатает»"; onToggled: ghostPage.editorHideTyping = checked }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        GhostText { Layout.fillWidth: true; text: "Не отмечать сообщения прочитанными" }
                        UiKit.MeCheckbox { objectName: "ghostHideRead"; checked: ghostPage.editorHideRead; Accessible.name: "Не отмечать сообщения прочитанными"; onToggled: ghostPage.editorHideRead = checked }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        enabled: ghostPage.runtime !== null && ghostPage.runtime.features.hideOnline
                        GhostText { Layout.fillWidth: true; text: "Не отправлять статус «в сети»" }
                        UiKit.MeCheckbox { objectName: "ghostHideOnline"; checked: ghostPage.editorHideOnline; Accessible.name: "Не отправлять статус «в сети»"; onToggled: ghostPage.editorHideOnline = checked }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        enabled: ghostPage.runtime !== null && ghostPage.runtime.features.hideStories
                        GhostText { Layout.fillWidth: true; text: "Не отмечать истории просмотренными" }
                        UiKit.MeCheckbox { objectName: "ghostHideStories"; checked: ghostPage.editorHideStories; Accessible.name: "Не отмечать истории просмотренными"; onToggled: ghostPage.editorHideStories = checked }
                    }
                }
                GhostText { Layout.fillWidth: true; text: "При скрытии прочтения счётчики непрочитанных могут не сбрасываться."; typography: Typography.FontUtil.Styles.Description; color: ghostPage.maxwellColorTokens.colorTextSecondary }
                GhostText { Layout.fillWidth: true; text: "Онлайн-статус изменяется со следующим служебным запросом или подключением. Сервер может некоторое время показывать прежний статус. Другие открытые официальные клиенты могут показывать тебя в сети."; typography: Typography.FontUtil.Styles.Description; color: ghostPage.maxwellColorTokens.colorTextSecondary }
                GhostText { Layout.fillWidth: true; text: "Реакции на истории отправляются обычным способом: автор увидит твою реакцию. Фильтры экспериментальные; подтверждение помощника означает подключение фильтров, а не гарантию невидимости на сервере."; typography: Typography.FontUtil.Styles.Description; color: ghostPage.maxwellColorTokens.colorTextSecondary }
                GhostText { objectName: "ghostDraftStatus"; Layout.fillWidth: true; visible: ghostPage.dirty; text: "Есть несохранённые изменения."; typography: Typography.FontUtil.Styles.Description; color: ghostPage.maxwellColorTokens.colorTextAttention }
                GhostText { objectName: "ghostStatus"; Layout.fillWidth: true; text: ghostPage.statusText; typography: Typography.FontUtil.Styles.Description; color: ghostPage.hasError ? ghostPage.maxwellColorTokens.colorTextAttention : ghostPage.maxwellColorTokens.colorTextSecondary }
                Flow {
                    Layout.fillWidth: true; spacing: ghostPage.gap
                    GhostButton { objectName: "saveGhostSettings"; text: "Сохранить"; enabled: ghostPage.canConfigure && ghostPage.pendingRequest === null && ghostPage.dirty && !ghostPage.needsRefresh; onClicked: ghostPage.save() }
                    GhostButton { objectName: "cancelGhostSettings"; text: "Отменить изменения"; enabled: ghostPage.canConfigure && !ghostPage.saving && ghostPage.dirty; onClicked: { ghostPage.resetDraft(); ghostPage.hasError = false; ghostPage.statusText = "Изменения отменены."; } }
                    GhostButton { objectName: "reloadGhostSettings"; text: "Обновить"; enabled: ghostPage.pendingRequest === null; onClicked: ghostPage.request("GET", "", false) }
                }
            }
        }
    }
}
