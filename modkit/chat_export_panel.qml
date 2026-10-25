import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQml.Models as ExportModels
import QtQuick.Dialogs as ExportDialogs
import UiKitModule as UiKit
import ClientAppearanceModule as ExportAppearance
import TypographyModule as ExportTypography
import PreviewGridModule as ExportPreview
import QmlSingletonesModule
import ru.oneme.components 1.0
// Inline, explicitly user-started export. Native references never cross HTTP.
    UiKit.BaseModalDialog {
        id: exportDialog
        objectName: "valmaxChatExportDialog"
        required property var commonDependency
        required property var nativeChatId
        required property var nativeChatProperties
        required property string chatTitle
        property var selectedScope: null
        property bool scopeArmed: false
        property bool scopeInvalidated: false
        property bool nativeHistoryPreviewAvailable: false
        property bool nativeApi2ExportAvailable: false
        property string exportMode: "client"
        property int api2WindowsFetched: 0
        property int api2MediaProcessed: 0
        property int api2MediaTotal: 0
        property string api2Stage: "history"
        property bool api2StopPending: false
        property bool api2Pacing: false
        property double api2PauseUntil: 0
        property int api2PauseRemainingMs: 0
        property double exportStartedAt: 0
        property int elapsedSeconds: 0
        property bool previewRequested: false
        property bool previewBusy: false
        property var pendingPreviewRequest: null
        property string previewStatusText: ""
        signal exportFinished()
        property bool closedByUser: false
        property bool cancelRequested: false
        property bool activeNative: false
        property var terminalResult: null
        property string failureText: ""
        property var accountDependency: null
        property var seedContext: null
        property var historyContext: null
        property var api2HistoryContext: null
        property var activeBubble: null
        property var pendingRequest: null
        property var nativeDownloadRequest: null
        property var audioBuffer: null
        property int audioOffset: 0
        property var limits: ({maxPages: 20000, maxMessages: 1000000, maxAttachments: 1000000, maxPageMessages: 200, maxPageBytes: 2097152, maxAudioBytes: 67108864, maxChunkBytes: 49152})
        property var sentIds: ({})
        property var nativeTasks: ({})
        property var mediaQueue: []
        property var currentMedia: null
        property var pendingMessages: []
        property string jobId: ""
        property string jobToken: ""
        property string phase: "idle"
        property string statusText: "Сообщения чата и доступные вложения. Неотправленные сообщения не входят в экспорт."
        property string htmlPath: ""
        property string htmlFileURL: ""
        property string stopReason: ""
        property string direction: "backward"
        property int messageCount: 0
        property int attachmentCount: 0
        property int mediaCount: 0
        property int omissionCount: 0
        property int pagesFetched: 0
        property int sequence: 0
        property int historyRevision: 0
        property int requestRevision: -1
        property int noProgress: 0
        property double waitStarted: 0
        property double changedAt: 0
        property double proofStarted: 0
        property bool awaitingHistory: false
        property bool sawHistoryChange: false
        property bool initialHistoryReady: false
        property bool backwardEnd: false
        property bool forwardEnd: false
        property bool historicalEndProven: false
        property bool hasError: false
        readonly property bool busy: previewBusy || ["seed", "history", "history-proof", "page", "media", "finishing", "rendering", "api2-start", "api2-seed", "api2-next", "cancelling"].indexOf(phase) >= 0
        readonly property string progressStageText: phase === "cancelling" ? "Остановка экспорта…"
            : exportMode === "api2-text"
                ? (phase === "api2-start" || phase === "api2-seed" ? "Шаг 1/3 · Подготовка"
                    : api2Stage === "media" ? "Шаг 3/3 · Вложения и создание HTML"
                    : "Шаг 2/3 · История и вложения")
                : (phase === "seed" ? "Шаг 1/3 · Подготовка"
                    : phase === "finishing" || phase === "rendering"
                        ? "Шаг 3/3 · Создание HTML"
                        : "Шаг 2/3 · История и файлы")
        readonly property string ownId: String(QmlUtils.contactIdString(commonDependency.selfInfo.id))
        readonly property string chatIdText: String(QmlUtils.chatIdString(nativeChatId))
        onCommonDependencyChanged: if (scopeArmed) invalidateSelectedScope()
        onNativeChatIdChanged: if (scopeArmed) invalidateSelectedScope()
        onNativeChatPropertiesChanged: if (scopeArmed) invalidateSelectedScope()
        onSelectedScopeChanged: if (scopeArmed) invalidateSelectedScope()
        Component.onCompleted: scopeArmed = true
        readonly property var history: getContext(historyContext)
        readonly property var seedHistory: getContext(seedContext)
        readonly property bool moreBackward: history ? history.hasMoreBackward : false
        readonly property bool moreForward: history ? history.hasMoreForward : false
        onMoreBackwardChanged: historyUpdated()
        onMoreForwardChanged: historyUpdated()
        readonly property var downloader: commonDependency ? commonDependency.mediaDownloader : null
        readonly property var colors: ExportAppearance.AppearanceTokens
        chatAppearance: colors
        minimumWidth: Math.min(520, parent ? parent.width - 32 : 520)
        margins: UiKit.DesignSystem.SpacingCommon._L
        title: "Скачать чат"
        subtitle: chatTitle
        confirmText: busy ? "" : htmlFileURL ? "Открыть экспорт" : "Выбрать папку"
        cancelText: api2StopPending ? "Закрыть" : busy ? "Остановить" : "Закрыть"
        confirmButtonEnabled: !busy
        closePolicy: busy ? Popup.NoAutoClose : Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onConfirmClicked: {
            if (htmlFileURL) Qt.openUrlExternally(htmlFileURL);
            else if (validSelectedScope()) destinationDialog.open();
            else invalidateSelectedScope();
        }
        onCancelClicked: { if (api2StopPending) close(); else if (previewBusy) abortPreview(); else if (busy) cancelExport(); else close(); }
        onCloseClicked: { if (api2StopPending) close(); else if (previewBusy) abortPreview(); else if (busy) cancelExport(); else close(); }
        onClosed: {
            closedByUser = true;
            if (previewBusy) abortPreview();
            if (busy && !cancelRequested) cancelExport();
            if (api2StopPending || !activeNative && !busy) dispose();
        }
        onOpened: if (selectedScope && !validSelectedScope()) invalidateSelectedScope()
        Component.onDestruction: {
            requestWatchdog.stop(); mediaWatchdog.stop(); stepTimer.stop(); api2PaceTimer.stop();
            if (pendingRequest) pendingRequest.abort();
            if (pendingPreviewRequest) pendingPreviewRequest.abort();
            if (nativeDownloadRequest) nativeDownloadRequest.abort();
            if (nativeApi2ExportAvailable && exportMode === "api2-text" && jobId && jobToken) {
                const job = {jobId: jobId, jobToken: jobToken};
                cancelApi2NativeJob(job);
                releaseApi2NativeHistory(job);
            }
            api2HistoryContext = null; seedContext = null;
        }

        function getContext(wrapper) {
            try { return wrapper && !wrapper.isNull ? wrapper.get() : null; }
            catch (_) { return null; }
        }
        function validSelectedScope() {
            if (scopeInvalidated || closedByUser || !selectedScope) return false;
            try {
                const scope = selectedScope;
                const model = application.accountModel;
                const properties = getContext(nativeChatProperties);
                const owner = commonDependency;
                const currentChatId = String(QmlUtils.chatIdString(nativeChatId));
                const propertyChatId = String(QmlUtils.chatIdString(properties.chatId));
                const propertyContactId = canonicalId(properties.dialogContactId, "contact");
                if (!/^-?[0-9]{1,20}$/.test(scope.ownId) || !/^-?[0-9]{1,20}$/.test(scope.chatId)
                        || !/^-?[0-9]{1,20}$/.test(scope.contactId)) return false;
                return scope.owner === owner && scope.propertiesContext === nativeChatProperties
                    && scope.accountKey !== undefined && scope.accountKey !== null
                    && scope.accountKey === owner.selfInfo.accountId
                    && scope.accountKey === model.currentAccountId
                    && model.activeAccountDependency.commonDependency === owner
                    && scope.ownId === canonicalId(owner.selfInfo.id, "contact")
                    && scope.chatId === currentChatId && scope.chatId === propertyChatId
                    && scope.chatId === String(QmlUtils.chatIdString(scope.nativeChatId))
                    && scope.contactId === propertyContactId
                    && scope.title === chatTitle && properties.isPreventCopyingEnabled === false;
            } catch (_) { return false; }
        }
        function invalidateSelectedScope() {
            if (scopeInvalidated) return;
            scopeInvalidated = true;
            if (previewBusy) abortPreview();
            if (busy && !cancelRequested) cancelExport();
            try { destinationDialog.close(); } catch (_) {}
            close();
        }
        function canonicalId(value, kind) {
            try {
                const text = String(kind === "message" ? QmlUtils.messageIdString(value) : QmlUtils.contactIdString(value));
                return /^-?[0-9]{1,20}$/.test(text) ? text : "";
            } catch (_) { return ""; }
        }
        function plain(value, limit) {
            return typeof value === "string" ? value.slice(0, limit).replace(/\u0000/g, "") : "";
        }
        function timestamp(value) {
            try { const stamp = QmlUtils.toDateTime(value).getTime(); return Number.isSafeInteger(stamp) ? stamp : null; }
            catch (_) { return null; }
        }
        function utf8Length(value) {
            let bytes = 0;
            for (const ch of value) { const code = ch.codePointAt(0); bytes += code <= 0x7f ? 1 : code <= 0x7ff ? 2 : code <= 0xffff ? 3 : 4; }
            return bytes;
        }
        function localFileURL(value) {
            return typeof value === "string" && value.length < 32768 && /^file:\/\/\/[A-Za-z]:\//.test(value) && !/[\u0000-\u001f]/.test(value);
        }
        function nativeFilePath(value) {
            if (!localFileURL(value)) throw new Error("local_path");
            return decodeURIComponent(value.slice(8)).replace(/\//g, "\\");
        }
        function jobBody(extra) {
            return Object.assign({schema: 1, jobId: jobId, jobToken: jobToken}, extra || {});
        }
        function validApi2Counts(value) {
            if (!value || typeof value !== "object" || Array.isArray(value)
                    || Object.keys(value).sort().join(",") !== "attachments,mediaBytes,messages,ready,unavailable") return false;
            for (const key of ["messages", "attachments", "ready", "unavailable", "mediaBytes"])
                if (!Number.isSafeInteger(value[key]) || value[key] < 0) return false;
            return value.ready + value.unavailable <= value.attachments;
        }
        function validApi2Receipt(value) {
            if (!value || value.schema !== 1 || value.jobId !== jobId || !validApi2Counts(value.counts)
                    || value.downloadsPending !== false || !value.api2
                    || Object.keys(value.api2).sort().join(",") !== "done,mediaProcessed,mediaTotal,phase,windowsFetched"
                    || !Number.isSafeInteger(value.api2.windowsFetched)
                    || value.api2.windowsFetched < api2WindowsFetched || value.api2.windowsFetched > 1000
                    || !Number.isSafeInteger(value.api2.mediaProcessed)
                    || !Number.isSafeInteger(value.api2.mediaTotal)
                    || value.api2.mediaProcessed < api2MediaProcessed
                    || value.api2.mediaTotal < api2MediaTotal || value.api2.mediaTotal > 1000000
                    || value.api2.mediaProcessed > value.api2.mediaTotal
                    || ["history", "media", "done"].indexOf(value.api2.phase) < 0
                    || ["history", "media", "done"].indexOf(value.api2.phase)
                        < ["history", "media", "done"].indexOf(api2Stage)
                    || typeof value.api2.done !== "boolean"
                    || value.api2.done !== (value.api2.phase === "done")
                    || value.counts.ready + value.counts.unavailable
                        + value.api2.mediaTotal - value.api2.mediaProcessed !== value.counts.attachments) return false;
            const allowed = ["api2", "counts", "downloadsPending", "error", "htmlFileURL", "htmlPath", "jobId", "schema", "state"];
            if (Object.keys(value).some(function(key) { return allowed.indexOf(key) < 0; })) return false;
            if (value.state === "exporting") return !value.api2.done
                && (value.api2.windowsFetched > api2WindowsFetched
                    || value.api2.mediaProcessed > api2MediaProcessed || value.api2.phase !== api2Stage)
                && value.htmlPath === undefined && value.htmlFileURL === undefined && value.error === undefined;
            if (!value.api2.done || value.api2.mediaProcessed !== value.api2.mediaTotal) return false;
            if (value.state === "partial") return typeof value.htmlPath === "string" && value.htmlPath.length > 0
                && localFileURL(value.htmlFileURL) && value.error === undefined;
            return (value.state === "failed" || value.state === "cancelled")
                && value.htmlPath === undefined && value.htmlFileURL === undefined;
        }
        function cancelApi2NativeJob(job) { /* Native generator binds detached cancellation. */ }
        function beginApi2NativeHistory(job) { return false; /* Native generator starts the selected-view lease. */ }
        function bindApi2NativeHistory(view, job) { return false; /* Native generator binds the selected view. */ }
        function releaseApi2NativeHistory(job) { /* Native generator releases the selected view. */ }
        function stopApi2Export(text, failed) {
            if (api2StopPending) return;
            const job = jobId && jobToken ? {jobId: jobId, jobToken: jobToken} : null;
            api2StopPending = true; cancelRequested = true;
            requestWatchdog.stop(); stepTimer.stop(); renderPoll.stop(); historyProofTimer.stop(); api2PaceTimer.stop();
            api2Pacing = false; api2PauseRemainingMs = 0;
            const xhr = pendingRequest; pendingRequest = null;
            if (xhr) xhr.abort();
            if (job) { cancelApi2NativeJob(job); releaseApi2NativeHistory(job); }
            api2HistoryContext = null; seedContext = null;
            jobId = ""; jobToken = ""; htmlPath = ""; htmlFileURL = "";
            phase = "cancelling"; hasError = failed === true; statusText = text;
            if (closedByUser) dispose();
        }
        function applyApi2Receipt(value) {
            if (!validApi2Receipt(value)) throw new Error("api2_receipt");
            if (!validSelectedScope()) { invalidateSelectedScope(); return; }
            api2WindowsFetched = value.api2.windowsFetched;
            api2MediaProcessed = value.api2.mediaProcessed;
            api2MediaTotal = value.api2.mediaTotal;
            api2Stage = value.api2.phase;
            messageCount = value.counts.messages; attachmentCount = value.counts.attachments;
            mediaCount = value.counts.ready; omissionCount = value.counts.unavailable;
            if (!value.api2.done) {
                phase = "api2-next";
                statusText = api2Stage === "media"
                    ? "Вложения: обработано " + api2MediaProcessed + " из " + api2MediaTotal
                        + ", скачано " + mediaCount + ", недоступно " + omissionCount + "."
                    : "История: обработано окон " + api2WindowsFetched
                        + ", сообщений " + messageCount + ". Вложения: обработано "
                        + api2MediaProcessed + " из " + api2MediaTotal
                        + ", скачано " + mediaCount + ", недоступно " + omissionCount
                        + ". Полнота пока не подтверждена.";
                queueApi2Window();
                return;
            }
            api2PaceTimer.stop(); api2Pacing = false; api2PauseRemainingMs = 0;
            phase = value.state; api2StopPending = false;
            htmlPath = value.state === "partial" ? value.htmlPath : "";
            htmlFileURL = value.state === "partial" ? value.htmlFileURL : "";
            hasError = value.state === "failed";
            statusText = value.state === "partial"
                ? "Сохранена доступная часть чата. Файлов вложений: " + mediaCount
                    + "; недоступно: " + omissionCount + ". Полнота истории не подтверждена."
                : value.state === "cancelled" ? "Экспорт остановлен." : "Не удалось завершить экспорт.";
            releaseApi2NativeHistory({jobId: jobId, jobToken: jobToken});
            api2HistoryContext = null; seedContext = null;
            jobId = ""; jobToken = "";
            if (closedByUser) dispose();
        }
        function queueApi2Window() {
            if (phase !== "api2-next" || cancelRequested || pendingRequest) return;
            api2PauseUntil = Date.now() + 500;
            api2PauseRemainingMs = 500;
            api2Pacing = true;
            api2PaceTimer.restart();
        }
        function nextApi2Window() {
            if (phase !== "api2-next" || cancelRequested || pendingRequest || api2Pacing) return;
            if (!validSelectedScope()) { invalidateSelectedScope(); return; }
            if (!getContext(api2HistoryContext)) {
                stopApi2Export("Привязка истории выбранного чата потеряна. Остановка запрошена; её завершение может произойти в фоне.", true);
                return;
            }
            request("api2-next", jobBody(), function(value) {
                if (phase === "api2-next" && !cancelRequested) applyApi2Receipt(value);
            });
        }
        function fail(reason, text) {
            if (nativeApi2ExportAvailable && exportMode === "api2-text"
                    && ["api2-start", "api2-seed", "api2-next"].indexOf(phase) >= 0) {
                stopApi2Export(text + " Остановка запрошена; её завершение может произойти в фоне.", true);
                return;
            }
            hasError = true; statusText = text; failureText = text;
            if (phase === "cancelling" || cancelRequested) { phase = "failed"; releaseNative(); return; }
            if (jobId) cancelExport();
            else { phase = "failed"; releaseNative(); }
        }
        function abortPreview() {
            const xhr = pendingPreviewRequest;
            pendingPreviewRequest = null;
            previewWatchdog.stop();
            previewBusy = false;
            if (xhr) xhr.abort();
            previewStatusText = "Показ результата отменён. Начатый запрос может завершиться в фоне.";
        }
        function validPreviewResult(value) {
            if (!value || value.schema !== 1 || value.complete !== false
                    || (value.pages !== 1 && value.pages !== 2)) return false;
            const keys = ["schema", "pages", "firstCount", "secondCount", "boundaryOverlapCount",
                "firstAdvanced", "secondAdvanced", "emptyFollowupWindow", "cursorFieldSeen",
                "hasMoreFieldSeen", "totalFieldSeen", "complete"];
            if (Object.keys(value).sort().join(",") !== keys.sort().join(",")) return false;
            for (const key of ["firstCount", "secondCount", "boundaryOverlapCount"])
                if (!Number.isSafeInteger(value[key]) || value[key] < 0 || value[key] > 3) return false;
            return value.boundaryOverlapCount <= Math.min(value.firstCount, value.secondCount)
                && typeof value.firstAdvanced === "boolean" && typeof value.secondAdvanced === "boolean"
                && typeof value.emptyFollowupWindow === "boolean"
                && typeof value.cursorFieldSeen === "boolean" && typeof value.hasMoreFieldSeen === "boolean"
                && typeof value.totalFieldSeen === "boolean";
        }
        function previewFailureText(responseText) {
            const fallback = "Не удалось проверить историю этого чата.";
            try {
                const failure = JSON.parse(responseText);
                if (!failure || failure.schema !== 1 || typeof failure.error !== "string") return fallback;
                const code = failure.error;
                const nativeCode = /^api2_(?:connect_(?:trust|tls|timeout|cancelled|io)|init_(?:rejected|limit|protocol|timeout|cancelled|io)|credential_(?:invalid|unavailable)|login_(?:request_(?:token|protocol)|frame_(?:rejected|limit|protocol|timeout|cancelled|io)|decompress_(?:limit|protocol)|preflight_(?:limit|protocol)|msgpack_protocol|trailing_protocol|profile_missing|contact_missing|id_invalid|bind_(?:account|protocol))|history_(?:rejected|scope|clock|limit|protocol|timeout|cancelled|io))$/.test(code);
                if (!nativeCode && ["invalid_request", "busy", "unavailable", "internal_error"].indexOf(code) < 0) return fallback;
                const stage = nativeCode ? code.split("_")[1] : "bridge";
                const names = {connect: "соединение", init: "инициализация", credential: "доступ к аккаунту",
                    login: "вход", history: "чтение истории", bridge: "вызов модуля"};
                return fallback + " Этап: " + names[stage] + "; код: " + code + ".";
            } catch (_) { return fallback; }
        }
        function previewHistory() {
            if (!nativeHistoryPreviewAvailable || previewRequested || busy || pendingRequest) return;
            if (!validSelectedScope()) { invalidateSelectedScope(); return; }
            const scope = selectedScope;
            previewRequested = true;
            previewBusy = true;
            previewStatusText = "Проверка чтения истории…";
            const xhr = new XMLHttpRequest(); pendingPreviewRequest = xhr;
            previewWatchdog.restart();
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== 4 || pendingPreviewRequest !== xhr) return;
                pendingPreviewRequest = null; previewWatchdog.stop(); previewBusy = false;
                if (!validSelectedScope()) { invalidateSelectedScope(); return; }
                if (xhr.status !== 200) { previewStatusText = previewFailureText(xhr.responseText); return; }
                try {
                    const value = JSON.parse(xhr.responseText);
                    if (!validPreviewResult(value)) throw new Error("preview_response");
                    previewStatusText = "Проверены страницы: " + value.pages + "; сообщений в них: "
                        + value.firstCount + " + " + value.secondCount + "; повторов на границе: "
                        + value.boundaryOverlapCount + ". Полнота истории ещё не подтверждена.";
                } catch (_) { previewStatusText = "Ответ проверки истории не прошёл проверку."; }
            };
            try {
                xhr.open("POST", "api2.history.preview");
                if (!validSelectedScope()) { abortPreview(); invalidateSelectedScope(); return; }
                xhr.send(JSON.stringify({schema: 1, ownId: scope.ownId, chatId: scope.chatId}));
            } catch (_) {
                try { xhr.abort(); } catch (_) {}
                pendingPreviewRequest = null; previewWatchdog.stop(); previewBusy = false;
                previewStatusText = "Не удалось начать проверку истории.";
            }
        }
        function request(route, body, callback) {
            if (pendingRequest !== null) return false;
            const xhr = new XMLHttpRequest(); pendingRequest = xhr;
            // A native VIDEO_PLAY transfer may make steady progress for four
            // hours. Leave five minutes for resolution, import, and receipt.
            requestWatchdog.interval = route === "media-chunk" ? 10000 : route === "api2-next" ? 14700000 : 15000;
            requestWatchdog.restart();
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE || pendingRequest !== xhr) return;
                pendingRequest = null; requestWatchdog.stop();
                if (xhr.status !== 200) { fail("history_error", "Не удалось сохранить экспорт. Уже полученные данные не считаются полной историей."); return; }
                try {
                    const value = JSON.parse(xhr.responseText);
                    if (!value || value.schema !== 1 || jobId && value.jobId !== jobId) throw new Error("schema");
                    callback(value);
                    if (terminalResult && !pendingRequest) drainTerminal();
                    else if (cancelRequested && !pendingRequest && route !== "cancel" && phase !== "cancelled" && phase !== "failed") cancelExport();
                } catch (_) { fail("history_error", "Ответ задачи экспорта не прошёл проверку."); }
            };
            xhr.open("POST", "http://127.0.0.1:18973/chat-export/" + route);
            xhr.setRequestHeader("Content-Type", "application/json");
            xhr.setRequestHeader("X-Max-Mod-Local-Write", "chat-export-v1");
            xhr.send(JSON.stringify(body));
            return true;
        }
        function dependencyForAccount() {
            const count = accountSources.count;
            if (!Number.isSafeInteger(count) || count < 1 || count > 32) return null;
            let selected = null;
            for (let i = 0; i < count; ++i) {
                const item = accountSources.itemAt(i);
                try {
                    if (item && item.dependency && canonicalId(item.dependency.commonDependency.selfInfo.id, "contact") === ownId) {
                        if (selected) return null;
                        selected = item.dependency;
                    }
                }
                catch (_) {}
            }
            return selected;
        }

        function startApi2Export(directory) {
            if (!nativeApi2ExportAvailable || exportMode !== "api2-text" || busy || !localFileURL(directory)) return;
            if (!validSelectedScope()) { invalidateSelectedScope(); return; }
            jobId = ""; jobToken = ""; htmlPath = ""; htmlFileURL = "";
            api2HistoryContext = null; seedContext = null;
            cancelRequested = false; api2StopPending = false; failureText = "";
            api2PaceTimer.stop(); api2Pacing = false; api2PauseRemainingMs = 0;
            exportStartedAt = Date.now(); elapsedSeconds = 0;
            messageCount = 0; attachmentCount = 0; mediaCount = 0; omissionCount = 0; api2WindowsFetched = 0;
            api2MediaProcessed = 0; api2MediaTotal = 0; api2Stage = "history";
            phase = "api2-start"; hasError = false; statusText = "Подготовка бета-экспорта…";
            request("start", {schema: 1, accountId: ownId,
                chat: {id: chatIdText, title: plain(chatTitle, 256), timezoneOffsetMinutes: -new Date().getTimezoneOffset()},
                directory: directory}, function(value) {
                if (!/^[0-9a-f]{32}$/.test(value.jobId) || !/^[0-9a-f]{64}$/.test(value.jobToken)) throw new Error("api2_start_job");
                jobId = value.jobId; jobToken = value.jobToken;
                if (value.state !== "exporting" || value.downloadsPending !== false
                        || !validApi2Counts(value.counts) || value.counts.messages !== 0
                        || value.counts.attachments !== 0 || value.htmlPath !== undefined
                        || value.htmlFileURL !== undefined) throw new Error("api2_start");
                if (!validSelectedScope()) { stopApi2Export("Выбранный чат изменился. Остановка запрошена; её завершение может произойти в фоне.", true); return; }
                try {
                    accountDependency = dependencyForAccount();
                    if (!accountDependency || accountDependency.commonDependency !== commonDependency
                            || !accountDependency.historyPanelDependency
                            || !accountDependency.historyPanelDependency.openedChatsContext
                            || !accountDependency.historyPanelDependency.historyViewFactory) throw new Error("history_dependency");
                    seedContext = accountDependency.historyPanelDependency.openedChatsContext.getOrCreateChatHistory(nativeChatId);
                    if (!seedContext) throw new Error("history_seed");
                } catch (_) {
                    stopApi2Export("Не удалось привязать историю выбранного чата. Остановка запрошена; её завершение может произойти в фоне.", true);
                    return;
                }
                phase = "api2-seed"; waitStarted = Date.now();
                statusText = "Подготовка истории выбранного чата…";
                stepTimer.start();
            });
        }
        function api2SeedStep() {
            if (phase !== "api2-seed" || cancelRequested) return;
            if (!validSelectedScope()) { invalidateSelectedScope(); return; }
            if (!seedHistory || seedModel.items.count === 0) {
                if (Date.now() - waitStarted >= 30000)
                    stopApi2Export("Не удалось получить отправное сообщение выбранного чата. Остановка запрошена; её завершение может произойти в фоне.", true);
                return;
            }
            const message = getContext(seedModel.items.get(0).model.messageContext);
            if (!message) {
                if (Date.now() - waitStarted >= 30000)
                    stopApi2Export("Не удалось прочитать отправное сообщение выбранного чата. Остановка запрошена; её завершение может произойти в фоне.", true);
                return;
            }
            try {
                if (String(QmlUtils.chatIdString(message.chatId)) !== chatIdText
                        || message.messageTimeId === undefined || message.messageTimeId === null)
                    throw new Error("history_seed_scope");
                const job = {jobId: jobId, jobToken: jobToken};
                if (!beginApi2NativeHistory(job)) throw new Error("history_begin");
                api2HistoryContext = accountDependency.historyPanelDependency.historyViewFactory.createOnMessage(nativeChatId, message.messageTimeId);
                const selectedView = getContext(api2HistoryContext);
                if (!selectedView || !validSelectedScope()) throw new Error("history_view");
                selectedView.objectName = "valmax-export-" + jobId;
                if (!bindApi2NativeHistory(selectedView, job))
                    throw new Error("history_bind");
            } catch (_) {
                stopApi2Export("Не удалось привязать историю выбранного чата. Остановка запрошена; её завершение может произойти в фоне.", true);
                return;
            }
            stepTimer.stop();
            phase = "api2-next"; statusText = "Чтение истории выбранного чата…";
            Qt.callLater(function() { if (exportDialog.phase === "api2-next" && !exportDialog.cancelRequested)
                exportDialog.nextApi2Window(); });
        }
        function startExport(directory) {
            if (busy || !localFileURL(directory)) return;
            if (!validSelectedScope()) { invalidateSelectedScope(); return; }
            if (nativeApi2ExportAvailable && exportMode === "api2-text") { startApi2Export(directory); return; }
            exportStartedAt = Date.now(); elapsedSeconds = 0;
            jobId = ""; jobToken = ""; htmlPath = ""; htmlFileURL = "";
            cancelRequested = false; activeNative = false; terminalResult = null; failureText = "";
            sentIds = ({}); nativeTasks = ({}); mediaQueue = []; pendingMessages = [];
            messageCount = 0; attachmentCount = 0; mediaCount = 0; omissionCount = 0;
            pagesFetched = 0; sequence = 0; historyRevision = 0; noProgress = 0;
            stopReason = ""; direction = "backward"; awaitingHistory = false;
            sawHistoryChange = false; initialHistoryReady = false; backwardEnd = false; forwardEnd = false; historicalEndProven = false;
            accountDependency = dependencyForAccount();
            if (!accountDependency || !accountDependency.historyPanelDependency
                    || accountDependency.commonDependency !== commonDependency || !validSelectedScope()) {
                invalidateSelectedScope(); return;
            }
            if (nativeChatProperties.get().isPreventCopyingEnabled) { fail("history_error", "В этом чате запрещено сохранение содержимого."); return; }
            phase = "seed"; hasError = false; statusText = "Подготовка экспорта…";
            request("start", {schema: 1, accountId: ownId, chat: {id: chatIdText, title: plain(chatTitle, 256), timezoneOffsetMinutes: -new Date().getTimezoneOffset()}, directory: directory}, function(value) {
                if (!/^[0-9a-f]{32}$/.test(value.jobId) || !/^[0-9a-f]{64}$/.test(value.jobToken)) throw new Error("job");
                jobId = value.jobId; jobToken = value.jobToken;
                if (value.limits) for (const key of Object.keys(limits)) {
                    if (!Number.isSafeInteger(value.limits[key]) || value.limits[key] < 1 || value.limits[key] > limits[key]) throw new Error("limits");
                }
                limits = value.limits || limits;
                seedContext = accountDependency.historyPanelDependency.openedChatsContext.getOrCreateChatHistory(nativeChatId);
                waitStarted = Date.now(); changedAt = waitStarted; stepTimer.start();
            });
        }
        function seedStep() {
            if (!seedHistory) return;
            if (seedModel.items.count > 0) {
                const context = seedModel.items.get(0).model.messageContext;
                const message = getContext(context);
                if (!message) return;
                historyContext = accountDependency.historyPanelDependency.historyViewFactory.createOnMessage(nativeChatId, message.messageTimeId);
                historyContext.get().objectName = "valmax-export-" + jobId;
                phase = "history"; waitStarted = Date.now(); changedAt = waitStarted;
                statusText = "Загрузка доступной истории…";
            } else if (Date.now() - waitStarted > 30000) finishExport("unsupported_history");
        }
        function historyUpdated() {
            historyRevision += 1; changedAt = Date.now(); sawHistoryChange = true;
        }
        function messageDTO(context) {
            const message = getContext(context);
            if (!message) throw new Error("message_context");
            const id = canonicalId(message.messageId, "message");
            if (!id) throw new Error("message_id");
            const text = message.isRemoved === true ? "Сообщение удалено" : typeof message.text === "string" ? message.text : "";
            if (text.length > 65536) { stopReason = "message_too_large"; return null; }
            const sender = canonicalId(message.senderId, "contact");
            let name = "";
            try { name = plain(commonDependency.contactProperties.createDisplayNameContext(message.senderId).get().displayText, 256); } catch (_) {}
            let reply = null;
            const replied = message.hasReply ? getContext(message.replyMessageContext) : null;
            if (replied && canonicalId(replied.messageId, "message")) reply = {id: canonicalId(replied.messageId, "message"), authorName: "", text: plain(replied.text, 65536)};
            return {id: id, author: {id: sender, name: name}, timestampMs: timestamp(message.time), text: text, outgoing: message.isOutgoing === true, reply: reply, attachments: []};
        }
        function prepareAttachments(context, dto) {
            activeBubble = accountDependency.historyPanelDependency.bubbleViewContextFactory.create(context, MessageBubbleTypes.Scope.General);
            const bubble = getContext(activeBubble), message = getContext(context);
            if (!bubble || !message || message.isRemoved === true) { activeBubble = null; return; }
            previewContext.previews = bubble.mediaGridItems;
            if (previewModel.items.count > 256) stopReason = "attachment_limit";
            for (let i = 0; i < Math.min(previewModel.items.count, 256); ++i) {
                const media = previewModel.items.get(i).model;
                const type = media.attachType === AttachmentsPreviewGrid.AttachType.Video ? "video" : "image";
                const id = "media-" + i;
                dto.attachments.push({id: id, name: type === "video" ? "video.mp4" : media.attachType === AttachmentsPreviewGrid.AttachType.Gif ? "animation.gif" : "photo.jpg", mime: type === "video" ? "video/mp4" : media.attachType === AttachmentsPreviewGrid.AttachType.Gif ? "image/gif" : "image/jpeg", type: type, status: "pending", reason: null});
                nativeTasks[dto.id + "|" + id] = {kind: type, chatId: message.chatId, messageId: message.messageId, messageTimeId: message.messageTimeId, attachId: media.itemId, url: media.downloadUrl, isGif: media.attachType === AttachmentsPreviewGrid.AttachType.Gif, context: context};
            }
            filesModel.model = bubble.filesContext && !bubble.filesContext.isNull ? bubble.filesContext.get().model : null;
            const fileLimit = Math.min(filesModel.items.count, 256 - dto.attachments.length);
            if (filesModel.items.count > fileLimit) stopReason = "attachment_limit";
            for (let i = 0; i < fileLimit; ++i) {
                const file = filesModel.items.get(i).model;
                const id = "file-" + i;
                if (file.isPending) {
                    dto.attachments.push({id: id, name: plain(file.title, 256), mime: "application/octet-stream", type: "file", status: "unavailable", reason: "not_available"});
                    continue;
                }
                const fileContext = commonDependency.attachments.fileAttachment.contextFactory.create(file.fileId, message.chatId, message.messageId, file.size, file.title);
                dto.attachments.push({id: id, name: plain(file.title, 256), mime: "application/octet-stream", type: "file", status: "pending", reason: null});
                nativeTasks[dto.id + "|" + id] = {kind: "file", fileContext: fileContext, context: context};
            }
            for (const pair of [["voice", bubble.audioMessageContext], ["video", bubble.videoMessageContext]]) {
                const attachment = getContext(pair[1]);
                if (!attachment) continue;
                if (dto.attachments.length >= 256) { stopReason = "attachment_limit"; continue; }
                const id = pair[0] + "-message";
                if (pair[0] === "video") {
                    dto.attachments.push({id: id, name: "video-message.mp4", mime: "video/mp4", type: "video", status: "pending", reason: null});
                    nativeTasks[dto.id + "|" + id] = {kind: "video", chatId: message.chatId, messageTimeId: message.messageTimeId, attachId: attachment.attachId, context: context};
                } else {
                    dto.attachments.push({id: id, name: "voice-message.ogg", mime: "audio/ogg", type: "voice", status: "pending", reason: null});
                    nativeTasks[dto.id + "|" + id] = {kind: "voice", url: String(attachment.url), context: context};
                }
            }
            if (dto.attachments.length === 0 && message.attachmentsType !== 0)
                dto.attachments.push({id: "other", name: "Дополнительное содержимое сообщения", mime: "", type: "unsupported", status: "unavailable", reason: "unsupported"});
            activeBubble = null; filesModel.model = null; previewContext.previews = [];
        }
        function harvestPage() {
            let records = [], bytes = 512, pageAttachments = 0;
            for (let i = 0; i < historyModel.items.count && records.length < limits.maxPageMessages; ++i) {
                const context = historyModel.items.get(i).model.messageContext;
                const nativeMessage = getContext(context);
                const key = nativeMessage ? canonicalId(nativeMessage.messageId, "message") : "";
                if (nativeMessage && (nativeMessage.isPending === true || nativeMessage.isDelayed === true)) {
                    if (key) sentIds["pending:" + key] = true;
                    continue;
                }
                if (!key || sentIds[key]) continue;
                if (messageCount + records.length >= limits.maxMessages) { stopReason = "message_limit"; break; }
                const dto = messageDTO(context);
                if (!dto) break;
                prepareAttachments(context, dto);
                if (attachmentCount + pageAttachments + dto.attachments.length > limits.maxAttachments) { stopReason = "attachment_limit"; break; }
                const size = utf8Length(JSON.stringify(dto)) + 1;
                if (size + 512 > limits.maxPageBytes) { stopReason = "message_too_large"; break; }
                if (records.length > 0 && bytes + size > limits.maxPageBytes - 1024) break;
                records.push(dto); bytes += size; pageAttachments += dto.attachments.length;
            }
            if (records.length === 0) return false;
            pendingMessages = records; phase = "page";
            request("page", jobBody({sequence: sequence, messages: records}), function(value) {
                if (value.sequence !== sequence || !Array.isArray(value.media)) throw new Error("page");
                for (const dto of records) { sentIds[dto.id] = true; messageCount += 1; attachmentCount += dto.attachments.length; for (const a of dto.attachments) if (a.status === "unavailable") omissionCount += 1; }
                sequence += 1; pendingMessages = []; mediaQueue = value.media.slice(); phase = "media";
                statusText = "Сохранено сообщений: " + messageCount + ". Загрузка вложений…";
                nextMedia();
            });
            return true;
        }
        function nextMedia() {
            if (phase !== "media" || pendingRequest || currentMedia) return;
            if (cancelRequested) { cancelExport(); return; }
            if (mediaQueue.length === 0) { nativeTasks = ({}); phase = "history"; return; }
            const media = mediaQueue.shift();
            if (!media || typeof media.messageId !== "string" || typeof media.attachmentId !== "string" || !localFileURL(media.nativePathFileURL)) throw new Error("reservation");
            const task = nativeTasks[media.messageId + "|" + media.attachmentId];
            currentMedia = {reservation: media, task: task};
            if (!task) { mediaFinished("unavailable", "not_available"); return; }
            mediaWatchdog.restart();
            if (task.kind === "voice" || task.kind === "audio") { downloadAudio(task.url); return; }
            request("media-start", jobBody({messageId: media.messageId, attachmentId: media.attachmentId}), function(_) {
                activeNative = true;
                if (cancelRequested) { mediaFinished("cancelled", "cancelled"); return; }
                invokeNative(media, task);
            });
        }
        function invokeNative(media, task) {
            try {
                if (task.kind === "image") downloader.downloadImageWithJSCallback(task.chatId, task.messageId, task.attachId, String(task.url || ""), nativeFilePath(media.nativePathFileURL), function(resultPath) {
                    if (currentMedia && currentMedia.reservation === media) {
                        const saved = typeof resultPath === "string" && resultPath.length > 0 && sameNativePath(resultPath);
                        mediaFinished(saved ? "ready" : "error", saved ? null : "download_failed");
                    }
                }, task.isGif ? MediaDownloaderPhotoType.Gif : MediaDownloaderPhotoType.Photo);
                else if (task.kind === "video") downloader.downloadVideo(task.chatId, task.messageTimeId, task.attachId, nativeFilePath(media.nativePathFileURL));
                else if (task.kind === "file") {
                    const file = getContext(task.fileContext);
                    if (!file) { mediaFinished("unavailable", "not_available"); return; }
                    file.saveAs(Qt.url(media.nativePathFileURL));
                } else mediaFinished("unavailable", "unsupported");
            } catch (_) { mediaFinished("error", "download_failed"); }
        }
        function downloadAudio(url) {
            // Only the URL already supplied to the native audio player is
            // used. It stays inside this QML object; no headers are copied.
            if (typeof url !== "string" || url.length > 8192 || !/^https:\/\/[^\s\/@]+(?::443)?(?:\/|$)/.test(url)) {
                mediaFinished("unavailable", "not_available"); return;
            }
            const xhr = new XMLHttpRequest(); nativeDownloadRequest = xhr;
            let expected = -1;
            function stop(status, reason) {
                if (nativeDownloadRequest !== xhr) return;
                nativeDownloadRequest = null; xhr.abort(); mediaFinished(status, reason);
            }
            xhr.onreadystatechange = function() {
                if (nativeDownloadRequest !== xhr || !currentMedia) return;
                if (xhr.readyState === XMLHttpRequest.HEADERS_RECEIVED) {
                    if (xhr.status !== 200) { stop("error", xhr.status === 403 ? "permission_denied" : xhr.status === 404 ? "not_available" : "download_failed"); return; }
                    const length = String(xhr.getResponseHeader("Content-Length") || "").trim();
                    const encoding = String(xhr.getResponseHeader("Content-Encoding") || "").trim().toLowerCase();
                    if (!/^[1-9][0-9]{0,8}$/.test(length) || Number(length) > limits.maxAudioBytes || encoding && encoding !== "identity") {
                        stop("unavailable", "not_available"); return;
                    }
                    expected = Number(length);
                }
                if (xhr.readyState !== XMLHttpRequest.DONE) return;
                nativeDownloadRequest = null;
                if (xhr.status !== 200 || expected < 1 || !xhr.response || xhr.response.byteLength !== expected || expected > limits.maxAudioBytes) {
                    mediaFinished("error", "download_failed"); return;
                }
                audioBuffer = new Uint8Array(xhr.response); audioOffset = 0; sendAudioChunk();
            };
            xhr.open("GET", url); xhr.responseType = "arraybuffer"; xhr.send();
        }
        function base64Chunk(bytes) {
            const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
            let result = "";
            for (let i = 0; i < bytes.length; i += 3) {
                const has2 = i + 1 < bytes.length, has3 = i + 2 < bytes.length;
                const value = bytes[i] * 65536 + (has2 ? bytes[i + 1] * 256 : 0) + (has3 ? bytes[i + 2] : 0);
                result += alphabet[(value >>> 18) & 63] + alphabet[(value >>> 12) & 63] + (has2 ? alphabet[(value >>> 6) & 63] : "=") + (has3 ? alphabet[value & 63] : "=");
            }
            return result;
        }
        function sendAudioChunk() {
            if (!currentMedia || !audioBuffer || pendingRequest) return;
            if (audioOffset === audioBuffer.length) { audioBuffer = null; mediaFinished("ready", null); return; }
            const next = Math.min(audioBuffer.length, audioOffset + limits.maxChunkBytes);
            const payload = base64Chunk(audioBuffer.subarray(audioOffset, next));
            request("media-chunk", jobBody({messageId: currentMedia.reservation.messageId, attachmentId: currentMedia.reservation.attachmentId, offset: audioOffset, data: payload}), function(value) {
                if (value.offset !== next) throw new Error("chunk_offset");
                audioOffset = next; sendAudioChunk();
            });
        }
        function mediaFinished(status, reason) {
            if (!currentMedia || terminalResult) return;
            if (nativeDownloadRequest) { const xhr = nativeDownloadRequest; nativeDownloadRequest = null; xhr.abort(); }
            audioBuffer = null; audioOffset = 0;
            mediaWatchdog.stop();
            terminalResult = {status: cancelRequested ? "cancelled" : status, reason: cancelRequested ? "cancelled" : reason};
            drainTerminal();
        }
        function drainTerminal() {
            if (!terminalResult || !currentMedia || pendingRequest) return;
            const result = terminalResult, current = currentMedia;
            request("media", jobBody({messageId: current.reservation.messageId, attachmentId: current.reservation.attachmentId, status: result.status, reason: result.reason}), function(_) {
                activeNative = false; terminalResult = null;
                if (result.status === "ready") mediaCount += 1; else omissionCount += 1;
                currentMedia = null;
                if (closedByUser) dispose();
                else if (!cancelRequested) nextMedia();
            });
        }
        function sameNativePath(path) {
            if (!currentMedia || typeof path !== "string") return false;
            try { return (localFileURL(path) ? nativeFilePath(path) : path.replace(/\//g, "\\")).toLowerCase() === nativeFilePath(currentMedia.reservation.nativePathFileURL).toLowerCase(); }
            catch (_) { return false; }
        }
        function historyStep() {
            if (!history || pendingRequest) return;
            if (messageCount >= limits.maxMessages) { finishExport("message_limit"); return; }
            if (attachmentCount >= limits.maxAttachments) { finishExport("attachment_limit"); return; }
            if (pagesFetched >= limits.maxPages) { finishExport("page_limit"); return; }
            if (harvestPage()) { noProgress = 0; return; }
            if (stopReason) { finishExport(stopReason); return; }
            if (Date.now() - waitStarted > 30000) { finishExport("timeout"); return; }
            if (awaitingHistory && !sawHistoryChange) return;
            if (Date.now() - changedAt < 500) return;
            if (awaitingHistory) { awaitingHistory = false; noProgress += 1; }
            initialHistoryReady = initialHistoryReady || historyModel.items.count > 0;
            if (!initialHistoryReady) return;
            const more = direction === "backward" ? moreBackward : moreForward;
            if (more === true) {
                if (noProgress > 3) { finishExport("no_progress"); return; }
                sawHistoryChange = false; awaitingHistory = true; requestRevision = historyRevision;
                pagesFetched += 1; waitStarted = Date.now(); changedAt = waitStarted;
                if (direction === "backward") history.fetchBackward(); else history.fetchForward();
            } else if (more === false) {
                if (direction === "backward") { backwardEnd = true; direction = "forward"; noProgress = 0; }
                else { forwardEnd = true; phase = "history-proof"; proofStarted = Date.now(); pollHistoryProof(); }
            }
        }
        function pollHistoryProof() {
            if (phase !== "history-proof" || pendingRequest) return;
            request("history", jobBody(), function(value) {
                const proof = value.history;
                if (!proof || typeof proof.ready !== "boolean" || typeof proof.backward !== "boolean" || typeof proof.forward !== "boolean") throw new Error("history_proof");
                historicalEndProven = proof.ready && proof.backward && proof.forward;
                if (historicalEndProven) finishExport("end_of_history");
                else if (Date.now() - proofStarted >= 2000) finishExport("unsupported_history");
                else historyProofTimer.restart();
            });
        }
        function finishExport(reason) {
            if (!jobId || pendingRequest || ["finishing", "rendering", "cancelling"].indexOf(phase) >= 0) return;
            stopReason = reason; phase = "finishing"; stepTimer.stop();
            statusText = "Создание HTML…";
            request("finish", jobBody({completion: {historyComplete: reason === "end_of_history" && backwardEnd && forwardEnd && historicalEndProven, stopReason: reason, pagesFetched: pagesFetched}}), function(value) {
                releaseNative(); applyStatus(value); if (phase === "rendering") renderPoll.start();
            });
        }
        function applyStatus(value) {
            const state = value.state;
            if (["exporting", "rendering", "complete", "partial", "failed", "cancelled"].indexOf(state) < 0) throw new Error("state");
            if (state === "rendering" || state === "exporting") { phase = "rendering"; return; }
            phase = state; renderPoll.stop();
            htmlPath = typeof value.htmlPath === "string" ? value.htmlPath : "";
            htmlFileURL = localFileURL(value.htmlFileURL) ? value.htmlFileURL : "";
            hasError = state === "failed";
            statusText = state === "complete" ? "Чат сохранён. Открой HTML вместе с папкой вложений." : state === "partial" ? "Сохранена доступная часть чата. Причины пропусков указаны в HTML." : state === "cancelled" ? activeNative ? "Экспорт остановлен. Начатая загрузка завершится в фоне; временная папка будет очищена после неё." : "Экспорт остановлен." : "Не удалось завершить экспорт.";
            if (failureText) { phase = "failed"; hasError = true; statusText = failureText; }
            if (closedByUser && !activeNative) dispose();
        }
        function cancelExport() {
            if (nativeApi2ExportAvailable && exportMode === "api2-text"
                    && ["api2-start", "api2-seed", "api2-next"].indexOf(phase) >= 0) {
                stopApi2Export("Остановка запрошена. Текущая операция может завершиться в фоне; файл пока недоступен.", false);
                return;
            }
            if ((!busy && !cancelRequested) || phase === "cancelling") return;
            cancelRequested = true;
            requestWatchdog.stop(); mediaWatchdog.stop(); stepTimer.stop(); renderPoll.stop();
            historyProofTimer.stop();
            if (pendingRequest) { requestWatchdog.restart(); statusText = "Остановка экспорта…"; return; }
            releaseNative();
            if (!jobId) { phase = "cancelled"; statusText = "Экспорт остановлен."; return; }
            phase = "cancelling";
            request("cancel", jobBody(), function(value) { applyStatus(value); });
        }
        function releaseNative() {
            mediaWatchdog.stop(); historyContext = null; seedContext = null;
            if (nativeDownloadRequest) { const xhr = nativeDownloadRequest; nativeDownloadRequest = null; xhr.abort(); }
            audioBuffer = null; audioOffset = 0;
            activeBubble = null; filesModel.model = null; previewContext.previews = [];
            nativeTasks = ({}); mediaQueue = [];
            if (!activeNative) { currentMedia = null; terminalResult = null; }
        }
        function dispose() {
            if (activeNative || pendingRequest) return;
            releaseNative();
            exportFinished();
        }

        Connections {
            target: typeof application !== "undefined" && application && application.accountModel
                && application.accountModel.currentAccountId !== undefined ? application.accountModel : null
            ignoreUnknownSignals: true
            function onCurrentAccountIdChanged() { if (exportDialog.scopeArmed) exportDialog.invalidateSelectedScope(); }
            function onActiveAccountDependencyChanged() { if (exportDialog.scopeArmed) exportDialog.invalidateSelectedScope(); }
            function onCurrentIndexChanged() { if (exportDialog.scopeArmed) exportDialog.invalidateSelectedScope(); }
        }
        Repeater {
            id: accountSources
            model: typeof application !== "undefined" && application ? application.accountModel : null
            delegate: Item { required property var dependency; visible: false }
        }
        ExportModels.DelegateModel { id: seedModel; model: exportDialog.seedHistory ? exportDialog.seedHistory.model : null; delegate: QtObject {} }
        ExportModels.DelegateModel { id: historyModel; model: exportDialog.history ? exportDialog.history.model : null; delegate: QtObject {} }
        ExportModels.DelegateModel { id: filesModel; delegate: QtObject {} }
        ExportPreview.PreviewGridContext { id: previewContext; previews: []; panelWidth: 600; extraWidth: 0 }
        ExportModels.DelegateModel { id: previewModel; model: previewContext.previewModel; delegate: QtObject {} }
        Connections {
            target: exportDialog.history ? exportDialog.history.model : null
            ignoreUnknownSignals: true
            function onRowsInserted() { exportDialog.historyUpdated(); }
            function onDataChanged() { exportDialog.historyUpdated(); }
            function onModelReset() { exportDialog.historyUpdated(); }
        }
        Connections {
            target: exportDialog.downloader
            function onVideoDownloadSucceed(filePath) { if (exportDialog.currentMedia && exportDialog.currentMedia.task && exportDialog.currentMedia.task.kind === "video" && exportDialog.sameNativePath(filePath)) exportDialog.mediaFinished("ready", null); }
            // Failure carries no task ID and could belong to another download.
            // The export's own watchdog reports its timeout instead.
        }
        Connections {
            target: exportDialog.currentMedia && exportDialog.currentMedia.task && exportDialog.currentMedia.task.kind === "file" ? exportDialog.getContext(exportDialog.currentMedia.task.fileContext) : null
            ignoreUnknownSignals: true
            function onFileSaveAsSucceed(filePath) { if (exportDialog.sameNativePath(filePath)) exportDialog.mediaFinished("ready", null); }
            function onFileSaveAsFailed() { exportDialog.mediaFinished("error", "download_failed"); }
            function onConfirmRequested() {
                const file = exportDialog.getContext(exportDialog.currentMedia.task.fileContext);
                if (file) file.rejectDownloadUnsafeFile();
                exportDialog.mediaFinished("unavailable", "permission_denied");
            }
        }
        Timer { id: requestWatchdog; interval: 15000; onTriggered: { if (exportDialog.pendingRequest) { const xhr = exportDialog.pendingRequest; exportDialog.pendingRequest = null; xhr.abort(); } exportDialog.fail("timeout", "Локальная задача не ответила вовремя."); } }
        Timer { id: previewWatchdog; interval: 45000; onTriggered: { if (exportDialog.previewBusy) { exportDialog.abortPreview(); exportDialog.previewStatusText = "Показ результата прекращён из-за долгого ожидания. Начатый запрос может завершиться в фоне."; } } }
        Timer { id: mediaWatchdog; interval: 120000; onTriggered: { if (exportDialog.activeNative) { exportDialog.failureText = "Загрузка вложения не завершилась вовремя. Экспорт остановлен; текущая загрузка может продолжиться в фоне."; exportDialog.cancelExport(); } else exportDialog.mediaFinished("error", "timeout"); } }
        Timer { id: stepTimer; interval: 250; repeat: true; onTriggered: { try { if (exportDialog.phase === "seed") exportDialog.seedStep(); else if (exportDialog.phase === "api2-seed") exportDialog.api2SeedStep(); else if (exportDialog.phase === "history") exportDialog.historyStep(); } catch (_) { exportDialog.fail("history_error", "Не удалось прочитать историю этого чата."); } } }
        Timer { id: historyProofTimer; interval: 200; onTriggered: exportDialog.pollHistoryProof() }
        Timer { id: renderPoll; interval: 1000; repeat: true; onTriggered: { if (!exportDialog.pendingRequest) exportDialog.request("status", exportDialog.jobBody(), exportDialog.applyStatus); } }
        Timer { id: exportClock; interval: 1000; repeat: true; running: exportDialog.busy && exportDialog.exportStartedAt > 0; onTriggered: exportDialog.elapsedSeconds = Math.max(0, Math.floor((Date.now() - exportDialog.exportStartedAt) / 1000)) }
        Timer { id: api2PaceClock; interval: 100; repeat: true; running: exportDialog.api2Pacing; onTriggered: exportDialog.api2PauseRemainingMs = Math.max(0, Math.ceil((exportDialog.api2PauseUntil - Date.now()) / 100) * 100) }
        Timer { id: api2PaceTimer; interval: 500; onTriggered: { exportDialog.api2Pacing = false; exportDialog.api2PauseRemainingMs = 0; exportDialog.nextApi2Window(); } }
        ExportDialogs.FolderDialog { id: destinationDialog; title: "Папка для экспорта чата"; onAccepted: exportDialog.startExport(selectedFolder.toString()) }
        dialogContentItem: ColumnLayout {
            spacing: 8
            UiKit.MeText { objectName: "valmaxExportBetaWarning"; Layout.fillWidth: true; text: exportDialog.nativeApi2ExportAvailable && exportDialog.exportMode === "api2-text"
                ? "Бета: архив чата может быть неполным. Фото, видео и голосовые сохраняются, когда доступны (до 4 ГиБ на файл). Отдельный раздел содержит только входящие сообщения, полученные и локально сохранённые после включения функции; прежние и недоступные удаления не восстановятся. Возможны повторы и пропуски."
                : "Бета: архив чата может быть неполным. В режиме «История из клиента» сохраняются показанные клиентом сообщения и доступные вложения. Локально сохранённые удаления в этот архив не входят; возможны пропуски."; wrapMode: Text.WordWrap; color: exportDialog.colors.colorTextAttention; typography: ExportTypography.FontUtil.Styles.Description }
            UiKit.MeText { Layout.fillWidth: true; text: exportDialog.statusText; wrapMode: Text.WordWrap; color: exportDialog.hasError ? exportDialog.colors.colorTextAttention : exportDialog.colors.colorTextPrimary; typography: ExportTypography.FontUtil.Styles.Body }
            UiKit.MeText { objectName: "valmaxExportProgressStage"; Layout.fillWidth: true; visible: exportDialog.busy && exportDialog.phase !== "idle"; text: exportDialog.progressStageText + " · " + exportDialog.elapsedSeconds + " с"; wrapMode: Text.WordWrap; color: exportDialog.colors.colorTextPrimary; typography: ExportTypography.FontUtil.Styles.Description }
            ColumnLayout {
                Layout.fillWidth: true; visible: exportDialog.nativeApi2ExportAvailable
                Button { objectName: "valmaxExportApi2TextMode"; Layout.fillWidth: true; enabled: !exportDialog.busy && !exportDialog.htmlFileURL; text: "История и медиа · Бета" + (exportDialog.exportMode === "api2-text" ? " · выбрано" : ""); onClicked: exportDialog.exportMode = "api2-text" }
                Button { objectName: "valmaxExportClientMode"; Layout.fillWidth: true; enabled: !exportDialog.busy && !exportDialog.htmlFileURL; text: "История из клиента" + (exportDialog.exportMode === "client" ? " · выбрано" : ""); onClicked: exportDialog.exportMode = "client" }
                UiKit.MeText { Layout.fillWidth: true; text: exportDialog.exportMode === "api2-text"
                    ? "История читается через штатный API. Доступные фото, видео и голосовые скачиваются по одному; остальные вложения отмечаются в архиве."
                    : "Сообщения и доступные вложения берутся из истории, показанной клиентом.";
                    wrapMode: Text.WordWrap; color: exportDialog.colors.colorTextSecondary; typography: ExportTypography.FontUtil.Styles.Description }
            }
            Button { Layout.fillWidth: true; visible: exportDialog.nativeHistoryPreviewAvailable; enabled: !exportDialog.busy && !exportDialog.previewRequested && exportDialog.validSelectedScope(); text: "Проверить загрузку истории"; onClicked: exportDialog.previewHistory() }
            UiKit.MeText { Layout.fillWidth: true; visible: exportDialog.previewStatusText.length > 0; text: exportDialog.previewStatusText; wrapMode: Text.WordWrap; color: exportDialog.colors.colorTextSecondary; typography: ExportTypography.FontUtil.Styles.Description }
            UiKit.MeText { Layout.fillWidth: true; text: exportDialog.exportMode === "api2-text"
                ? "Окон: " + exportDialog.api2WindowsFetched + " · сообщений: " + exportDialog.messageCount
                    + " · скачано файлов: " + exportDialog.mediaCount + " · без файлов: " + exportDialog.omissionCount
                    + " · обработано вложений: " + exportDialog.api2MediaProcessed + "/" + exportDialog.api2MediaTotal
                : "Сообщений: " + exportDialog.messageCount + " · вложений: " + exportDialog.mediaCount + " · пропущено: " + exportDialog.omissionCount;
                wrapMode: Text.WordWrap; color: exportDialog.colors.colorTextSecondary; typography: ExportTypography.FontUtil.Styles.Description }
            UiKit.MeText { objectName: "valmaxExportPace"; Layout.fillWidth: true; visible: exportDialog.api2Pacing; text: "Пауза перед следующим запросом: " + (exportDialog.api2PauseRemainingMs / 1000).toFixed(1) + " с"; color: exportDialog.colors.colorTextSecondary; typography: ExportTypography.FontUtil.Styles.Description }
            ProgressBar { objectName: "valmaxExportMediaProgressBar"; Layout.fillWidth: true; visible: exportDialog.busy && !exportDialog.api2StopPending;
                indeterminate: exportDialog.exportMode !== "api2-text" || exportDialog.api2Stage !== "media";
                from: 0; to: Math.max(1, exportDialog.api2MediaTotal); value: exportDialog.api2MediaProcessed }
            UiKit.MeText { Layout.fillWidth: true; text: exportDialog.exportMode === "api2-text"
                ? "HTML и временные файлы — в выбранной папке."
                : "История и файлы — в выбранной папке.";
                wrapMode: Text.WordWrap; color: exportDialog.colors.colorTextSecondary; typography: ExportTypography.FontUtil.Styles.Description }
        }
    }
