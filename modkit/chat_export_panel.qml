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
        signal exportFinished()
        property bool closedByUser: false
        property bool cancelRequested: false
        property bool activeNative: false
        property var terminalResult: null
        property string failureText: ""
        property var accountDependency: null
        property var seedContext: null
        property var historyContext: null
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
        readonly property bool busy: ["seed", "history", "history-proof", "page", "media", "finishing", "rendering", "cancelling"].indexOf(phase) >= 0
        readonly property string ownId: String(QmlUtils.contactIdString(commonDependency.selfInfo.id))
        readonly property string chatIdText: String(QmlUtils.chatIdString(nativeChatId))
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
        cancelText: busy ? "Остановить" : "Закрыть"
        confirmButtonEnabled: !busy
        closePolicy: busy ? Popup.NoAutoClose : Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onConfirmClicked: {
            if (htmlFileURL) Qt.openUrlExternally(htmlFileURL);
            else destinationDialog.open();
        }
        onCancelClicked: { if (busy) cancelExport(); else close(); }
        onCloseClicked: { if (busy) cancelExport(); else close(); }
        onClosed: {
            closedByUser = true;
            if (busy && !cancelRequested) cancelExport();
            if (!activeNative && !busy) dispose();
        }
        Component.onDestruction: {
            requestWatchdog.stop(); mediaWatchdog.stop(); stepTimer.stop();
            if (pendingRequest) pendingRequest.abort();
            if (nativeDownloadRequest) nativeDownloadRequest.abort();
        }

        function getContext(wrapper) {
            try { return wrapper && !wrapper.isNull ? wrapper.get() : null; }
            catch (_) { return null; }
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
        function fail(reason, text) {
            hasError = true; statusText = text; failureText = text;
            if (phase === "cancelling" || cancelRequested) { phase = "failed"; releaseNative(); return; }
            if (jobId) cancelExport();
            else { phase = "failed"; releaseNative(); }
        }
        function request(route, body, callback) {
            if (pendingRequest !== null) return false;
            const xhr = new XMLHttpRequest(); pendingRequest = xhr;
            requestWatchdog.interval = route === "media-chunk" ? 10000 : 15000;
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
            for (let i = 0; i < Math.min(accountSources.count, 3); ++i) {
                const item = accountSources.itemAt(i);
                try { if (item && item.dependency && canonicalId(item.dependency.commonDependency.selfInfo.id, "contact") === ownId) return item.dependency; }
                catch (_) {}
            }
            return null;
        }
        function startExport(directory) {
            if (busy || !localFileURL(directory)) return;
            jobId = ""; jobToken = ""; htmlPath = ""; htmlFileURL = "";
            cancelRequested = false; activeNative = false; terminalResult = null; failureText = "";
            sentIds = ({}); nativeTasks = ({}); mediaQueue = []; pendingMessages = [];
            messageCount = 0; attachmentCount = 0; mediaCount = 0; omissionCount = 0;
            pagesFetched = 0; sequence = 0; historyRevision = 0; noProgress = 0;
            stopReason = ""; direction = "backward"; awaitingHistory = false;
            sawHistoryChange = false; initialHistoryReady = false; backwardEnd = false; forwardEnd = false; historicalEndProven = false;
            accountDependency = dependencyForAccount();
            if (!accountDependency || !accountDependency.historyPanelDependency) { fail("unsupported_history", "История этого аккаунта сейчас недоступна."); return; }
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
            target: exportDialog.history
            ignoreUnknownSignals: true
            function onHistoryStateChanged() { exportDialog.historyUpdated(); }
            function onMessagesCountChanged() { exportDialog.historyUpdated(); }
        }
        Connections {
            target: exportDialog.history ? exportDialog.history.model : null
            ignoreUnknownSignals: true
            function onRowsInserted() { exportDialog.historyUpdated(); }
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
        Timer { id: mediaWatchdog; interval: 120000; onTriggered: { if (exportDialog.activeNative) { exportDialog.failureText = "Загрузка вложения не завершилась вовремя. Экспорт остановлен; текущая загрузка может продолжиться в фоне."; exportDialog.cancelExport(); } else exportDialog.mediaFinished("error", "timeout"); } }
        Timer { id: stepTimer; interval: 250; repeat: true; onTriggered: { try { if (exportDialog.phase === "seed") exportDialog.seedStep(); else if (exportDialog.phase === "history") exportDialog.historyStep(); } catch (_) { exportDialog.fail("history_error", "Не удалось прочитать историю этого чата."); } } }
        Timer { id: historyProofTimer; interval: 200; onTriggered: exportDialog.pollHistoryProof() }
        Timer { id: renderPoll; interval: 1000; repeat: true; onTriggered: { if (!exportDialog.pendingRequest) exportDialog.request("status", exportDialog.jobBody(), exportDialog.applyStatus); } }
        ExportDialogs.FolderDialog { id: destinationDialog; title: "Папка для экспорта чата"; onAccepted: exportDialog.startExport(selectedFolder.toString()) }
        dialogContentItem: ColumnLayout {
            spacing: UiKit.DesignSystem.SpacingCommon._M
            UiKit.MeText { Layout.fillWidth: true; text: exportDialog.statusText; wrapMode: Text.WordWrap; color: exportDialog.hasError ? exportDialog.colors.colorTextAttention : exportDialog.colors.colorTextPrimary; typography: ExportTypography.FontUtil.Styles.Body }
            UiKit.MeText { Layout.fillWidth: true; text: "Сообщений: " + exportDialog.messageCount + " · вложений: " + exportDialog.mediaCount + " · пропущено: " + exportDialog.omissionCount; wrapMode: Text.WordWrap; color: exportDialog.colors.colorTextSecondary; typography: ExportTypography.FontUtil.Styles.Description }
            ProgressBar { Layout.fillWidth: true; visible: exportDialog.busy; indeterminate: true }
            UiKit.MeText { Layout.fillWidth: true; text: "История и файлы сохраняются в выбранную папку. Другие аккаунты и их переписка не затрагиваются."; wrapMode: Text.WordWrap; color: exportDialog.colors.colorTextSecondary; typography: ExportTypography.FontUtil.Styles.Description }
        }
    }
