// Saved choices apply on the next complete ValMAX launch.
Component {
    id: settingsValMAXOptimization
    SettingsBaseItem {
        id: optimizationPage
        property var selfComponent: settingsValMAXOptimization
        title: "Оптимизация"
        onBackClicked: root.backClicked()
        readonly property var flagNames: ["enabled", "smoothWheel", "listScheduling", "listPrefetch", "readyCache", "historyCache", "historyBookkeeping", "photoDecodeResize", "windowPosition", "hiddenPageWork", "profileRows"]
        property string revision: ""
        property var savedSettings: null
        property var appliedSettings: null
        property var editorSettings: ({enabled: true, smoothWheel: true, listScheduling: true, listPrefetch: true, readyCache: true, historyCache: true, historyBookkeeping: true, photoDecodeResize: true, windowPosition: true, hiddenPageWork: true, profileRows: true})
        property bool restartRequired: false
        property bool available: false
        property bool loaded: false
        property bool trusted: false
        property bool needsRefresh: false
        property bool hasError: false
        property bool closing: false
        property var pendingRequest: null
        property string pendingMethod: ""
        property string statusText: "Загрузка настроек…"
        readonly property bool dirty: loaded && !sameSettings(editorSettings, savedSettings)
        readonly property bool saving: pendingRequest !== null && pendingMethod === "POST"
        readonly property bool canConfigure: loaded && trusted && available && !saving && !closing
        readonly property bool clientConfigLoaded: ValMAXOptimization.OptimizationConfig.loaded
        readonly property string clientConfigFailure: ValMAXOptimization.OptimizationConfig.failureCode
        readonly property var clientSettings: ValMAXOptimization.OptimizationConfig.snapshot()
        readonly property bool clientSettingsVerified: clientConfigLoaded && clientConfigFailure === "" && validSettings(clientSettings)
        readonly property bool backendMatchesClient: clientSettingsVerified && sameSettings(clientSettings, appliedSettings)
        readonly property bool clientNeedsRestart: loaded && trusted && (!clientSettingsVerified || !backendMatchesClient || restartRequired || !sameSettings(savedSettings, clientSettings))
        readonly property real gap: UiKit.DesignSystem.SpacingCommon._M
        readonly property real sectionGap: UiKit.DesignSystem.SpacingCommon._2XL
        readonly property string restartInstruction: "Полностью закрой ValMAX через значок в трее → «Выход», затем открой обычным ярлыком. Закрытие окна крестиком не завершает клиент."
        readonly property string runtimeTitle: !trusted || !loaded ? "Состояние неизвестно" : !clientSettingsVerified ? "Настройки клиента не подтверждены" : !backendMatchesClient ? "Состояние клиента отличается" : clientNeedsRestart ? "Нужен перезапуск ValMAX" : clientSettings.enabled ? "Настройки применены" : "Оптимизация выключена"
        readonly property string runtimeDetail: !trusted || !loaded ? "Нажми «Обновить», чтобы проверить сохранённые настройки." : !clientSettingsVerified ? "Клиент не смог загрузить параметры запуска и использует исходные настройки ускорения. Сохранённый выбор сейчас не подтверждён. " + restartInstruction : !backendMatchesClient ? "Параметры работающего клиента отличаются от данных помощника. Сохранённый выбор можно оставить; для его применения нужен полный перезапуск. " + restartInstruction : clientNeedsRestart ? "Выбор сохранён. " + restartInstruction : clientSettings.enabled ? "Работающий клиент подтвердил сохранённые параметры оптимизации." : "Работающий клиент подтвердил: дополнительные ускорения выключены."
        function validSettings(value) {
            if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length !== flagNames.length) return false;
            for (let i = 0; i < flagNames.length; ++i) if (typeof value[flagNames[i]] !== "boolean") return false;
            return true;
        }
        function sameSettings(left, right) {
            if (!validSettings(left) || !validSettings(right)) return false;
            for (let i = 0; i < flagNames.length; ++i) if (left[flagNames[i]] !== right[flagNames[i]]) return false;
            return true;
        }
        function copySettings(value) {
            const result = {};
            for (let i = 0; i < flagNames.length; ++i) result[flagNames[i]] = value[flagNames[i]];
            return result;
        }
        function setFlag(name, value) {
            if (!canConfigure || flagNames.indexOf(name) < 0 || typeof value !== "boolean") return;
            const result = copySettings(editorSettings); result[name] = value; editorSettings = result;
        }
        function resetDraft() { if (savedSettings !== null) editorSettings = copySettings(savedSettings); }
        function fail(message) { hasError = true; statusText = message; }
        function validResponse(value) {
            if (!value || typeof value !== "object" || Array.isArray(value) || value.schema !== 1 || typeof value.revision !== "string" || !/^[0-9a-f]{64}$/.test(value.revision)) return false;
            return validSettings(value.settings) && validSettings(value.appliedSettings) && typeof value.restartRequired === "boolean" && typeof value.available === "boolean";
        }
        function abortPending() {
            optimizationWatchdog.stop();
            const xhr = pendingRequest; pendingRequest = null; pendingMethod = "";
            if (xhr !== null) { xhr.onreadystatechange = function() {}; try { xhr.abort(); } catch (_) {} }
        }
        function request(method, body, quiet) {
            if (closing || pendingRequest !== null || ["GET", "POST"].indexOf(method) < 0) return;
            let xhr;
            try { xhr = new XMLHttpRequest(); } catch (_) { trusted = false; fail("Помощник ValMAX сейчас недоступен."); return; }
            pendingRequest = xhr; pendingMethod = method;
            if (!quiet) { hasError = false; statusText = method === "POST" ? "Сохранение…" : "Обновление настроек…"; }
            optimizationWatchdog.interval = method === "POST" ? 10000 : 7000; optimizationWatchdog.restart();
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE || pendingRequest !== xhr) return;
                pendingRequest = null; pendingMethod = ""; optimizationWatchdog.stop();
                if (closing) return;
                if (xhr.status === 409) { needsRefresh = true; trusted = false; fail("Настройки изменились. Нажми «Обновить», затем повтори сохранение. Твой выбор оставлен в форме."); return; }
                if (xhr.status !== 200) { trusted = false; if (method === "POST") needsRefresh = true; fail("Помощник ValMAX не ответил. Нажми «Обновить». Твой выбор оставлен в форме."); return; }
                try {
                    if (typeof xhr.responseText !== "string" || xhr.responseText.length > 16384) throw new Error("size");
                    const value = JSON.parse(xhr.responseText);
                    if (!validResponse(value)) throw new Error("schema");
                    const keepDraft = dirty && method !== "POST";
                    savedSettings = value.settings; appliedSettings = value.appliedSettings; revision = value.revision;
                    restartRequired = value.restartRequired; available = value.available; trusted = true; loaded = true;
                    if (!keepDraft) resetDraft();
                    if (method === "POST") { needsRefresh = false; hasError = false; statusText = clientNeedsRestart ? "Настройки сохранены. Изменения применятся после полного перезапуска." : "Настройки сохранены."; }
                    else if (!quiet) { needsRefresh = false; hasError = false; statusText = keepDraft ? "Состояние обновлено. Несохранённый выбор оставлен в форме." : "Состояние обновлено."; }
                    else if (hasError && !needsRefresh) { hasError = false; statusText = "Связь с помощником восстановлена."; }
                } catch (_) { trusted = false; if (method === "POST") needsRefresh = true; fail("Ответ помощника не прошёл проверку. Твой выбор оставлен в форме."); }
            };
            try {
                xhr.open(method, "http://127.0.0.1:18973/optimization-settings");
                if (method === "POST") { xhr.setRequestHeader("Content-Type", "application/json"); xhr.setRequestHeader("X-Max-Mod-Local-Write", "optimization-settings-v1"); }
                xhr.send(body || "");
            } catch (_) { abortPending(); trusted = false; if (method === "POST") needsRefresh = true; fail("Помощник ValMAX сейчас недоступен. Твой выбор оставлен в форме."); }
        }
        function save() {
            if (!canConfigure || pendingRequest !== null || !dirty || needsRefresh) return;
            request("POST", JSON.stringify({schema: 1, revision: revision, settings: copySettings(editorSettings)}), false);
        }
        Component.onCompleted: request("GET", "", false)
        Component.onDestruction: { closing = true; optimizationPoll.stop(); abortPending(); }
        Timer { id: optimizationPoll; objectName: "optimizationPoll"; interval: 3000; repeat: true; running: optimizationPage.visible && !optimizationPage.closing && optimizationPage.loaded && optimizationPage.clientNeedsRestart; onTriggered: optimizationPage.request("GET", "", true) }
        Timer { id: optimizationWatchdog; objectName: "optimizationWatchdog"; interval: 7000; onTriggered: { const wasPost = optimizationPage.pendingMethod === "POST"; optimizationPage.abortPending(); optimizationPage.trusted = false; if (wasPost) optimizationPage.needsRefresh = true; optimizationPage.fail("Ответ не получен. Нажми «Обновить». Твой выбор оставлен в форме."); } }
        component OptimizationText: Label {
            property int typography: Typography.FontUtil.Styles.Body
            font: Typography.FontUtil.getFont(typography, Typography.FontUtil.Sizes.Default)
            lineHeight: Typography.FontUtil.getLineHeight(typography, Typography.FontUtil.Sizes.Default)
            lineHeightMode: Text.FixedHeight; textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: optimizationPage.maxwellColorTokens.colorTextPrimary
        }
        component OptimizationOption: ColumnLayout {
            required property string flag
            required property string caption
            required property string explanation
            property bool master: false
            spacing: UiKit.DesignSystem.SpacingCommon._S
            RowLayout {
                Layout.fillWidth: true; spacing: optimizationPage.gap
                OptimizationText { Layout.fillWidth: true; text: caption; typography: Typography.FontUtil.Styles.BodyStrong }
                UiKit.MeSwitcher {
                    objectName: "optimization_" + flag
                    enabled: optimizationPage.canConfigure && (master || optimizationPage.editorSettings.enabled) && (flag !== "listPrefetch" || optimizationPage.editorSettings.listScheduling)
                    checked: optimizationPage.editorSettings[flag]
                    Accessible.name: caption
                    onToggled: optimizationPage.setFlag(flag, checked)
                }
            }
            OptimizationText { Layout.fillWidth: true; text: explanation; typography: Typography.FontUtil.Styles.Description; color: optimizationPage.maxwellColorTokens.colorTextSecondary }
        }
        component OptimizationButton: Button {
            font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
            implicitHeight: Math.max(UiKit.DesignSystem.SizeBase.px48, contentItem.implicitHeight + 2 * optimizationPage.gap)
            padding: optimizationPage.gap
            contentItem: OptimizationText { text: parent.text; color: parent.enabled ? optimizationPage.maxwellColorTokens.colorTextThemed : optimizationPage.maxwellColorTokens.colorTextTertiary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: UiKit.DesignSystem.BorderRadiusCommon._L; color: optimizationPage.maxwellColorTokens.colorBackgroundCard }
        }
        contentLoaderItem: UiKit.MeFlickable {
            id: optimizationScroll
            objectName: "optimizationScroll"
            contentWidth: width; contentHeight: optimizationScrollContent.implicitHeight + 2 * optimizationPage.sectionGap
            flickableDirection: Flickable.VerticalFlick
            ScrollBar.vertical: UiKit.MeScrollBar {}
            ColumnLayout {
                id: optimizationScrollContent
                y: optimizationPage.sectionGap
                width: Math.max(0, optimizationScroll.width - optimizationPage.gap); spacing: optimizationPage.sectionGap
                RowLayout {
                    Layout.fillWidth: true; spacing: optimizationPage.gap
                    OptimizationText { text: "Для всех аккаунтов на этом компьютере"; Layout.fillWidth: true; typography: Typography.FontUtil.Styles.Description; color: optimizationPage.maxwellColorTokens.colorTextSecondary }
                    UiKit.MeHighlightedText { objectName: "optimizationBeta"; text: "BETA"; highlightedTrailing: true; textColor: optimizationPage.maxwellColorTokens.colorTextThemed; backgroundColor: optimizationPage.maxwellColorTokens.colorStatesBackgroundHighlighted; Accessible.name: "BETA · функция тестируется" }
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: UiKit.DesignSystem.SpacingCommon._S
                    OptimizationText { objectName: "optimizationRuntimeTitle"; Layout.fillWidth: true; text: optimizationPage.runtimeTitle; typography: Typography.FontUtil.Styles.BodyStrong; color: optimizationPage.clientNeedsRestart ? optimizationPage.maxwellColorTokens.colorTextAttention : optimizationPage.maxwellColorTokens.colorTextPrimary }
                    OptimizationText { objectName: "optimizationRuntimeDetail"; Layout.fillWidth: true; text: optimizationPage.runtimeDetail; typography: Typography.FontUtil.Styles.Description; color: optimizationPage.maxwellColorTokens.colorTextSecondary }
                }
                OptimizationOption { Layout.fillWidth: true; flag: "enabled"; caption: "Включить оптимизацию"; explanation: "Общий переключатель дополнительных ускорений ValMAX. Выбранные ниже пункты сохраняются и при выключении."; master: true }
                OptimizationText { Layout.fillWidth: true; text: "Прокрутка и списки"; typography: Typography.FontUtil.Styles.BodyStrong }
                OptimizationOption { Layout.fillWidth: true; flag: "smoothWheel"; caption: "Плавная прокрутка колесом"; explanation: "Ограничивает резкие скачки в переписке и профиле, чтобы клиент успевал отрисовать содержимое." }
                OptimizationOption { Layout.fillWidth: true; flag: "listScheduling"; caption: "Постепенная отрисовка списков"; explanation: "Готовит строки чатов, контактов и звонков небольшими порциями. Во время быстрого движения может показывать заглушки." }
                OptimizationOption { Layout.fillWidth: true; flag: "listPrefetch"; caption: "Предзагрузка соседних строк"; explanation: "Заранее готовит четыре строки за видимой областью. Работает вместе с постепенной отрисовкой списков." }
                OptimizationOption { Layout.fillWidth: true; flag: "readyCache"; caption: "Сохранять готовые строки"; explanation: "Оставляет рядом с экраном уже готовые имена, аватары и подписи. При сворачивании окна они не создаются заново." }
                OptimizationText { Layout.fillWidth: true; text: "Переписка и фото"; typography: Typography.FontUtil.Styles.BodyStrong }
                OptimizationOption { Layout.fillWidth: true; flag: "historyCache"; caption: "Ограничивать запас сообщений в памяти"; explanation: "Сохраняет небольшой запас за пределами экрана, чтобы длинная переписка не удерживала слишком много элементов." }
                OptimizationOption { Layout.fillWidth: true; flag: "historyBookkeeping"; caption: "Сократить повторные расчёты в чате"; explanation: "Объединяет повторяющиеся обновления видимых сообщений во время прокрутки." }
                OptimizationOption { Layout.fillWidth: true; flag: "photoDecodeResize"; caption: "Меньше перезагрузок фото при растягивании"; explanation: "Изменяет размер фото сразу, а новую детализацию подготавливает после короткой паузы в изменении окна." }
                OptimizationText { Layout.fillWidth: true; text: "Окно и вкладки"; typography: Typography.FontUtil.Styles.BodyStrong }
                OptimizationOption { Layout.fillWidth: true; flag: "windowPosition"; caption: "Плавное перемещение окна"; explanation: "Реже сохраняет положение окна во время перетаскивания. Размер окна продолжает обновляться сразу." }
                OptimizationOption { Layout.fillWidth: true; flag: "hiddenPageWork"; caption: "Меньше работы в скрытых вкладках"; explanation: "Приостанавливает лишние обновления скрытых экранов. Доставка сообщений продолжает работать." }
                OptimizationOption { Layout.fillWidth: true; flag: "profileRows"; caption: "Стабильные строки информации о пользователе"; explanation: "Обновляет только изменившиеся поля профиля, чтобы не создавать весь список заново." }
                OptimizationText { objectName: "optimizationDraftStatus"; Layout.fillWidth: true; visible: optimizationPage.dirty; text: "Есть несохранённые изменения."; typography: Typography.FontUtil.Styles.Description; color: optimizationPage.maxwellColorTokens.colorTextAttention }
                OptimizationText { objectName: "optimizationStatus"; Layout.fillWidth: true; text: optimizationPage.statusText; typography: Typography.FontUtil.Styles.Description; color: optimizationPage.hasError ? optimizationPage.maxwellColorTokens.colorTextAttention : optimizationPage.maxwellColorTokens.colorTextSecondary }
                Flow {
                    Layout.fillWidth: true; spacing: optimizationPage.gap
                    OptimizationButton { objectName: "saveOptimizationSettings"; text: "Сохранить"; enabled: optimizationPage.canConfigure && optimizationPage.pendingRequest === null && optimizationPage.dirty && !optimizationPage.needsRefresh; onClicked: optimizationPage.save() }
                    OptimizationButton { objectName: "resetOptimizationSettings"; text: "Отменить изменения"; enabled: optimizationPage.canConfigure && optimizationPage.dirty; onClicked: { optimizationPage.resetDraft(); optimizationPage.hasError = false; optimizationPage.statusText = "Изменения отменены."; } }
                    OptimizationButton { objectName: "refreshOptimizationSettings"; text: "Обновить"; enabled: optimizationPage.pendingRequest === null; onClicked: optimizationPage.request("GET", "", false) }
                }
            }
        }
    }
}
