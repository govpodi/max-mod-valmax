// Declaration fragment for the pinned BaseSendPanel, not a standalone QML type.
// Search is explicit; original file URLs never cross this QML boundary.
readonly property var modInlineRows: modNativeMedia.rows
readonly property bool modInlineBusy: modNativeMedia.busy
readonly property string modInlineNativeError: modNativeMedia.errorCode
readonly property int modInlineNativeRetry: modNativeMedia.retryAfterSeconds
property string modInlineStatus: ""
property string modInlineAction: "search"
property bool modInlineSearched: false
property bool modInlineResetting: false
property string modInlineRetryRoute: ""
property string modInlineRetryError: ""
property int modInlineRetrySeconds: 0
property double modInlineRetryDeadline: 0
property bool modInlineRetryCaptured: false
property string modInlineDismissedDraft: ""
readonly property string modInlineDraftKey: /^@(pic|vid)(?:\s[^\r\n]{0,160})?$/i.test(chatInput.text) ? chatInput.text : ""
readonly property bool modInlineVisible: textInputVisible && modInlineDraftKey.length > 0 && modInlineDismissedDraft !== modInlineDraftKey
readonly property string modInlineMode: modInlineDraftKey.substring(1, 4).toLowerCase()
readonly property string modInlineQuery: modInlineDraftKey.substring(4).trim()
onModInlineDraftKeyChanged: modInlineReset(false)
onModInlineVisibleChanged: { if (!modInlineVisible) modInlineReset(false); }
onModInlineNativeErrorChanged: modInlineUpdateStatus()
onModInlineNativeRetryChanged: modInlineUpdateStatus()
onModInlineBusyChanged: modInlineUpdateStatus()
onModInlineRowsChanged: modInlineUpdateStatus()
ValMAXNative.MediaComposer {
    id: modNativeMedia
    composer: root
    onDraftDispatched: function(caption) {
        root.modQuickReplySelected = null;
        root.setText(caption, []);
        chatInput.moveCursorToEnd();
        chatInput.forceActiveFocus();
    }
}
Connections {
    target: root
    function onVisibleChanged() { if (!root.visible) root.modInlineReset(false); }
    function onEnabledChanged() { if (!root.enabled) root.modInlineReset(false); }
}
function modInlineReset(accountOrChatChanged) {
    // The provider's complete cooldown remains owned by native code. This local
    // countdown is display/admission feedback only and never schedules a search.
    modInlineResetting = true;
    modInlineSearched = false;
    modNativeMedia.reset();
    modInlineStatus = "";
    modInlineResetting = false;
    if (accountOrChatChanged) modInlineDismissedDraft = "";
}
function modInlineClose() {
    modInlineDismissedDraft = chatInput.text;
    modInlineReset(false);
    chatInput.forceActiveFocus();
}
function modInlineError(code, route, retrySeconds) {
    if (code === "provider_busy") {
        var busy = route === "select" ? "Wikimedia Commons временно ограничила загрузку выбранного файла." : "Wikimedia Commons временно ограничила поиск.";
        return busy + (retrySeconds > 0 ? " Повтори через " + retrySeconds + " с." : " Попробуй позже.");
    }
    if (code === "provider_unavailable") return route === "select" ? "Не удалось загрузить выбранный файл из Wikimedia Commons. Выбери другой или попробуй ещё раз." : "Поиск в Wikimedia Commons недоступен. Попробуй ещё раз.";
    var messages = {
        invalid_input: "Введи от 2 до 160 символов после @pic или @vid.",
        search_too_fast: "Подожди две секунды перед новым поиском.",
        result_expired: "Результаты устарели. Нажми «Найти» ещё раз.",
        cancelled: "Загрузка отменена. Можно выбрать файл ещё раз.",
        file_too_large: "Файл слишком большой: максимум 25 МБ.",
        unsupported_media: "Файл не прошёл проверку формата. Выбери другой.",
        invalid_response: "Wikimedia Commons вернула неподходящий ответ. Попробуй другой файл или повтори поиск.",
        cache_full: "Временный кэш медиа заполнен. Файлы старше суток очищаются при следующем выборе.",
        storage_unavailable: "Не удалось сохранить временный файл ValMAX.",
        no_space: "На диске недостаточно места для выбранного файла.",
        cleanup_failed: "Не удалось освободить временный файл. Закрой использующие его программы и повтори позже.",
        download_busy: "Дождись загрузки выбранного файла.",
        busy: "Поиск медиа занят. Попробуй ещё раз через несколько секунд.",
        attachment_limit: "В черновике уже десять вложений. Сначала отправь или убери файлы."
    };
    return messages[code] || "Нативный поиск медиа недоступен. Перезапусти ValMAX обычным ярлыком.";
}
function modInlineUpdateStatus() {
    if (modInlineResetting || !modInlineSearched) return;
    if (modInlineBusy) {
        modInlineStatus = modInlineAction === "select" ? "Загружаем выбранный файл…" : "Ищем в Wikimedia Commons…";
        return;
    }
    var code = modNativeMedia.errorCode;
    if (code.length > 0) {
        modInlineRetryRoute = modInlineAction;
        modInlineRetryError = code;
        var seconds = modNativeMedia.retryAfterSeconds;
        if (code === "provider_busy" && seconds > 0 && seconds <= 86400 && !modInlineRetryCaptured) {
            modInlineRetryCaptured = true;
            modInlineRetryDeadline = Math.max(modInlineRetryDeadline, Date.now() + seconds * 1000);
            modInlineRetrySeconds = Math.max(0, Math.ceil((modInlineRetryDeadline - Date.now()) / 1000));
            modInlineRetryTimer.restart();
        }
        modInlineStatus = modInlineError(code, modInlineAction, modInlineRetrySeconds);
        return;
    }
    modInlineStatus = modInlineRows.length ? "Выбери файл: он добавится в черновик с подписью источника." : "Подходящих файлов не найдено. Попробуй другой запрос.";
}
function modInlineSearch() {
    if (!modInlineVisible || modInlineBusy || modInlineRetrySeconds > 0) return;
    modInlineAction = "search";
    modInlineSearched = true;
    if (!modNativeMedia.available) { modInlineStatus = "Добавление медиа недоступно в этом поле сообщения."; return; }
    modInlineRetryCaptured = false;
    modNativeMedia.search(modInlineMode, modInlineQuery);
    modInlineUpdateStatus();
}
function modInlineSelect(index) {
    if (!modInlineVisible || modInlineBusy || modInlineRetrySeconds > 0 || index < 0 || index >= modInlineRows.length) return;
    modInlineAction = "select";
    if (!modNativeMedia.available) { modInlineStatus = "Добавление медиа недоступно в этом поле сообщения."; return; }
    modInlineRetryCaptured = false;
    modNativeMedia.select(modInlineRows[index].id);
    modInlineUpdateStatus();
}
Timer {
    id: modInlineRetryTimer
    interval: 1000
    repeat: true
    onTriggered: {
        root.modInlineRetrySeconds = Math.max(0, Math.ceil((root.modInlineRetryDeadline - Date.now()) / 1000));
        if (root.modInlineSearched && !root.modInlineBusy && root.modInlineRetryError === "provider_busy")
            root.modInlineStatus = root.modInlineRetrySeconds > 0 ? root.modInlineError("provider_busy", root.modInlineRetryRoute, root.modInlineRetrySeconds) : "Ограничение ожидания закончилось. Повтори действие вручную.";
        if (root.modInlineRetrySeconds === 0) stop();
    }
}
