// Local, selected-chat view for text captured before a deletion was observed.
// The caller binds accountId and chatId from the current client model. No
// token, remote endpoint or global account catalogue is available here.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ValMAX.Native 1.0 as Native

Item {
    id: archive
    objectName: "valmaxRetentionArchive"
    signal closeRequested()
    property string accountId: ""
    property string chatId: ""
    property bool opened: false
    property int generation: 0
    property var pendingRequest: null
    property var markerRequest: null
    property var captureRequest: null
    property var currentCursor: null
    property var nextCursor: null
    property var previousCursors: []
    property string revision: ""
    property int staleRestarts: 0
    property string statusText: ""
    property bool failed: false
    property int markerOnlyCount: -1
    property string markerStatus: ""
    property bool markerFailed: false
    property string captureStatus: ""
    property bool captureFailed: false
    readonly property int pageSize: 20
    readonly property int maximumResponseCharacters: 8 * 1024 * 1024
    readonly property int displayedCount: savedRows.count
    focus: true

    SystemPalette { id: colors }
    ListModel { id: savedRows }

    function validId(value, positiveOnly) {
        return Native.RetentionTransport.validId(value, positiveOnly);
    }
    function abortPending() {
        const request = pendingRequest;
        pendingRequest = null;
        if (request) {
            request.onreadystatechange = function() {};
            try { request.abort(); } catch (_) {}
        }
    }
    function abortMarker() {
        const request = markerRequest;
        markerRequest = null;
        markerDelay.stop(); markerWatchdog.stop();
        if (request) {
            request.onreadystatechange = function() {};
            try { request.abort(); } catch (_) {}
        }
    }
    function clearMarkerSummary() {
        abortMarker();
        markerOnlyCount = -1; markerStatus = ""; markerFailed = false;
    }
    function abortCaptureStatus() {
        const request = captureRequest;
        captureRequest = null;
        captureDelay.stop(); captureWatchdog.stop();
        if (request) {
            request.onreadystatechange = function() {};
            try { request.abort(); } catch (_) {}
        }
    }
    function validCaptureReply(value) {
        if (!value || typeof value !== "object" || Array.isArray(value)
                || value.schema !== 2 || typeof value.enabled !== "boolean"
                || !value.capabilities || typeof value.capabilities.capture !== "boolean"
                || !value.runtime || typeof value.runtime !== "object" || Array.isArray(value.runtime)) return false;
        const runtime = value.runtime;
        if (typeof runtime.ready !== "boolean" || typeof runtime.applied !== "boolean"
                || typeof runtime.enabled !== "boolean"
                || !(runtime.error === null || typeof runtime.error === "string" && runtime.error.length <= 128)
                || runtime.enabled && (!runtime.ready || !runtime.applied || runtime.error !== null)) return false;
        if (runtime.queue !== undefined) {
            if (!runtime.queue || typeof runtime.queue !== "object" || Array.isArray(runtime.queue)
                    || !Number.isSafeInteger(runtime.queue.pending)
                    || runtime.queue.pending < 0 || runtime.queue.pending > 1000000000) return false;
        }
        return true;
    }
    function requestCaptureStatus() {
        if (!opened || captureRequest) return;
        if (!Native.RetentionTransport.lookupAvailable) {
            captureFailed = true; captureStatus = "Не удалось проверить сохранение новых удалений.";
            return;
        }
        const requestGeneration = generation;
        let xhr;
        try { xhr = Native.RetentionTransport.createRequest("settings"); xhr.open("GET"); }
        catch (_) { captureFailed = true; captureStatus = "Не удалось проверить сохранение новых удалений."; return; }
        captureRequest = xhr;
        captureWatchdog.restart();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== 4 || captureRequest !== xhr) return;
            captureRequest = null; captureWatchdog.stop();
            if (!opened || generation !== requestGeneration) return;
            try {
                if (xhr.status !== 200 || typeof xhr.responseText !== "string"
                        || xhr.responseText.length > 16384) throw new Error("response");
                const value = JSON.parse(xhr.responseText);
                if (!validCaptureReply(value)) throw new Error("schema");
                const runtime = value.runtime;
                if (!value.capabilities.capture) {
                    captureStatus = "Новый захват ещё недоступен в этой сборке. Ранее записанное можно просматривать.";
                } else if (runtime.error === "retention_not_ready") {
                    captureStatus = "Обработчик ожидает готовности аккаунта и хранилища. В списке только ранее сохранённое.";
                } else if (runtime.error !== null) {
                    captureStatus = "Сохранение новых удалений остановлено. Уже записанные сообщения доступны ниже.";
                } else if (runtime.ready && runtime.enabled) {
                    captureStatus = runtime.queue && runtime.queue.pending > 0
                        ? "На устройстве ожидают локальной записи: " + runtime.queue.pending + ". Они ещё не входят в архив."
                        : "Новые удаления принимаются. В списке только подтверждённые локальные записи.";
                } else if (runtime.ready && value.enabled) {
                    captureStatus = "Настройка записана, но захват ещё не подтверждён. В списке только ранее сохранённое.";
                } else if (runtime.ready) {
                    captureStatus = "Сохранение новых удалений выключено. Ранее записанное доступно ниже.";
                } else {
                    captureStatus = "Сохранение новых удалений пока не готово. В списке только ранее записанное.";
                }
                captureFailed = runtime.error !== null && runtime.error !== "retention_not_ready"
                    && value.capabilities.capture;
            } catch (_) {
                captureFailed = true; captureStatus = "Не удалось проверить сохранение новых удалений.";
            }
        };
        try { xhr.send(""); }
        catch (_) {
            abortCaptureStatus(); captureFailed = true;
            captureStatus = "Не удалось проверить сохранение новых удалений.";
        }
    }
    function closeArchive() {
        if (!opened) return;
        opened = false;
        generation += 1;
        abortPending();
        clearMarkerSummary();
        abortCaptureStatus(); captureStatus = ""; captureFailed = false;
        savedRows.clear();
        previousCursors = []; currentCursor = null; nextCursor = null; revision = "";
        staleRestarts = 0;
        statusText = "";
        closeRequested();
    }
    function openArchive() {
        if (!validId(accountId, true) || !validId(chatId, false)
                || !Native.RetentionTransport.lookupAvailable) return;
        opened = true;
        generation += 1;
        previousCursors = []; currentCursor = null; nextCursor = null; revision = "";
        staleRestarts = 0;
        savedRows.clear(); failed = false;
        clearMarkerSummary();
        abortCaptureStatus(); captureStatus = "Проверка сохранения новых удалений…"; captureFailed = false;
        forceActiveFocus();
        requestPage(null);
        captureDelay.restart();
    }
    function validReply(value, requestedAccount, requestedChat) {
        if (!value || typeof value !== "object" || Array.isArray(value)
                || Object.keys(value).sort().join("|") !== "accountId|chatId|items|nextCursor|revision|schema"
                || value.schema !== 1 || value.accountId !== requestedAccount || value.chatId !== requestedChat
                || typeof value.revision !== "string" || !/^[0-9a-f]{64}$/.test(value.revision)
                || !(value.nextCursor === null || typeof value.nextCursor === "string" && value.nextCursor.length > 0 && value.nextCursor.length <= 256)
                || !Array.isArray(value.items) || value.items.length > pageSize) return false;
        for (const item of value.items) {
            if (!item || typeof item !== "object" || Array.isArray(item)
                    || Object.keys(item).sort().join("|") !== "deletedAtMs|receivedAtMs|text"
                    || !Number.isSafeInteger(item.receivedAtMs) || item.receivedAtMs < 0
                    || !Number.isSafeInteger(item.deletedAtMs) || item.deletedAtMs < 0
                    || typeof item.text !== "string" || item.text.length < 1 || item.text.length > 65536)
                return false;
        }
        return true;
    }
    function validMarkerReply(value, requestedAccount, requestedChat, requestedRevision) {
        return value && typeof value === "object" && !Array.isArray(value)
            && Object.keys(value).sort().join("|") === "accountId|chatId|markerOnlyCount|revision|schema"
            && value.schema === 1 && value.accountId === requestedAccount
            && value.chatId === requestedChat && value.revision === requestedRevision
            && Number.isSafeInteger(value.markerOnlyCount)
            && value.markerOnlyCount >= 0 && value.markerOnlyCount <= 65536;
    }
    function requestMarkerSummary() {
        if (!opened || markerRequest || revision.length !== 64 || !Native.RetentionTransport.lookupAvailable)
            return;
        if (pendingRequest) { markerDelay.restart(); return; }
        const requestedAccount = accountId, requestedChat = chatId;
        const requestedRevision = revision, requestGeneration = generation;
        let xhr;
        try { xhr = Native.RetentionTransport.createRequest("marker"); xhr.open("POST"); }
        catch (_) { markerFailed = true; markerStatus = "Старые метки сейчас недоступны."; return; }
        markerRequest = xhr;
        markerStatus = "Проверка старых меток…"; markerFailed = false;
        markerWatchdog.restart();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== 4 || markerRequest !== xhr) return;
            markerRequest = null; markerWatchdog.stop();
            if (!opened || generation !== requestGeneration || accountId !== requestedAccount
                    || chatId !== requestedChat || revision !== requestedRevision) return;
            try {
                if (xhr.status !== 200 || typeof xhr.responseText !== "string"
                        || xhr.responseText.length > 4096) throw new Error("response");
                const value = JSON.parse(xhr.responseText);
                if (!validMarkerReply(value, requestedAccount, requestedChat, requestedRevision))
                    throw new Error("schema");
                markerOnlyCount = value.markerOnlyCount;
                markerStatus = ""; markerFailed = false;
            } catch (_) {
                markerOnlyCount = -1; markerFailed = true;
                markerStatus = xhr.status === 409 ? "Архив изменился. Нажми «Обновить», чтобы проверить старые метки." :
                    "Не удалось проверить старые метки. Нажми «Обновить».";
            }
        };
        try {
            xhr.send(JSON.stringify({schema: 1, accountId: requestedAccount,
                chatId: requestedChat, revision: requestedRevision}));
        } catch (_) {
            abortMarker(); markerFailed = true;
            markerStatus = "Старые метки сейчас недоступны.";
        }
    }
    function requestPage(cursor) {
        if (!opened || pendingRequest || !Native.RetentionTransport.lookupAvailable
                || !validId(accountId, true) || !validId(chatId, false)) return;
        const requestedAccount = accountId, requestedChat = chatId, requestGeneration = generation;
        let xhr;
        try { xhr = Native.RetentionTransport.createRequest("list"); xhr.open("POST"); }
        catch (_) { failed = true; statusText = "Локальный архив сейчас недоступен."; return; }
        pendingRequest = xhr;
        failed = false; statusText = "Загрузка сохранённых удалений…";
        archiveWatchdog.restart();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== 4 || pendingRequest !== xhr) return;
            pendingRequest = null;
            archiveWatchdog.stop();
            if (!opened || generation !== requestGeneration || accountId !== requestedAccount || chatId !== requestedChat) return;
            try {
                if (xhr.status === 409 && typeof xhr.responseText === "string") {
                    const failure = JSON.parse(xhr.responseText);
                    if (failure && failure.error === "stale_cursor") {
                        if (cursor === null || staleRestarts >= 2) throw new Error("stale_cursor");
                        staleRestarts += 1;
                        generation += 1;
                        previousCursors = []; currentCursor = null; nextCursor = null; revision = "";
                        clearMarkerSummary();
                        savedRows.clear(); requestPage(null); return;
                    }
                }
                if (xhr.status !== 200 || typeof xhr.responseText !== "string"
                        || xhr.responseText.length > maximumResponseCharacters) throw new Error("response");
                const value = JSON.parse(xhr.responseText);
                if (!validReply(value, requestedAccount, requestedChat)) throw new Error("schema");
                if (revision.length && revision !== value.revision) {
                    if (cursor === null || staleRestarts >= 2) throw new Error("stale_revision");
                    staleRestarts += 1;
                    // A deletion arrived while paging. Restart from a fresh
                    // snapshot rather than mixing two revisions or accounts.
                    generation += 1;
                    previousCursors = []; currentCursor = null; nextCursor = null; revision = "";
                    clearMarkerSummary();
                    savedRows.clear(); requestPage(null); return;
                }
                revision = value.revision;
                if (cursor === null) staleRestarts = 0;
                nextCursor = value.nextCursor;
                savedRows.clear();
                for (const row of value.items) savedRows.append(row);
                statusText = value.items.length ? "Только локально сохранённые сообщения этого чата." :
                    value.nextCursor !== null ? "На этой странице нет доступных записей. Нажми «Дальше»." :
                    "В этом чате пока нет сохранённых удалений.";
                failed = false;
                if (cursor === null && markerOnlyCount < 0 && !markerFailed) markerDelay.restart();
            } catch (_) {
                savedRows.clear(); nextCursor = null; failed = true;
                statusText = "Не удалось прочитать локальный архив. Нажми «Обновить».";
            }
        };
        try {
            xhr.send(JSON.stringify({schema: 1, accountId: requestedAccount,
                chatId: requestedChat, cursor: cursor, limit: pageSize}));
        } catch (_) {
            archiveWatchdog.stop(); abortPending(); failed = true;
            statusText = "Локальный архив сейчас недоступен.";
        }
    }
    function nextPage() {
        if (!opened || pendingRequest || nextCursor === null) return;
        previousCursors = previousCursors.concat([currentCursor]);
        currentCursor = nextCursor; requestPage(currentCursor);
    }
    function previousPage() {
        if (!opened || pendingRequest || previousCursors.length === 0) return;
        currentCursor = previousCursors[previousCursors.length - 1];
        previousCursors = previousCursors.slice(0, -1);
        requestPage(currentCursor);
    }
    function refresh() {
        if (!opened) return;
        generation += 1; abortPending(); archiveWatchdog.stop();
        clearMarkerSummary();
        previousCursors = []; currentCursor = null; nextCursor = null; revision = "";
        staleRestarts = 0;
        savedRows.clear(); requestPage(null);
        captureDelay.restart();
    }
    onAccountIdChanged: if (opened) closeArchive()
    onChatIdChanged: if (opened) closeArchive()
    Component.onDestruction: { generation += 1; abortPending(); abortMarker(); abortCaptureStatus(); archiveWatchdog.stop(); savedRows.clear(); }
    Keys.onEscapePressed: closeArchive()

    Timer {
        id: archiveWatchdog
        interval: 10000
        onTriggered: {
            archive.abortPending(); archive.failed = true;
            archive.statusText = "Локальный архив не ответил. Нажми «Обновить».";
        }
    }
    Timer { id: markerDelay; interval: 250; onTriggered: archive.requestMarkerSummary() }
    Timer {
        id: markerWatchdog; interval: 30000
        onTriggered: {
            archive.abortMarker(); archive.markerOnlyCount = -1;
            archive.markerFailed = true;
            archive.markerStatus = "Проверка старых меток не ответила. Нажми «Обновить».";
        }
    }
    Timer { id: captureDelay; interval: 500; onTriggered: archive.requestCaptureStatus() }
    Timer { id: capturePoll; interval: 10000; repeat: true; running: archive.opened; onTriggered: archive.requestCaptureStatus() }
    Timer {
        id: captureWatchdog; interval: 7000
        onTriggered: {
            archive.abortCaptureStatus(); archive.captureFailed = true;
            archive.captureStatus = "Состояние сохранения не ответило. Ранее записанное остаётся доступным.";
        }
    }
    Rectangle {
        anchors.fill: parent
        color: "#99000000"
        MouseArea { anchors.fill: parent; onClicked: archive.closeArchive() }
    }
    Pane {
        id: card
        anchors.centerIn: parent
        width: Math.min(540, Math.max(280, archive.width - 24))
        height: Math.min(610, Math.max(260, archive.height - 24))
        focus: true
        ColumnLayout {
            anchors.fill: parent
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: "Сохранённые удаления"; font.bold: true; elide: Text.ElideRight }
                Button { objectName: "valmaxRetentionArchiveClose"; text: "Закрыть"; onClicked: archive.closeArchive() }
            }
            Label {
                Layout.fillWidth: true
                text: "Здесь только сообщения, полученные этим аккаунтом до удаления. То, что удалено раньше включения функции, восстановить нельзя."
                wrapMode: Text.WordWrap
            }
            Label {
                objectName: "valmaxRetentionCaptureStatus"
                Layout.fillWidth: true; text: archive.captureStatus; wrapMode: Text.WordWrap
                color: archive.captureFailed ? "#c62828" : colors.windowText
            }
            Label { Layout.fillWidth: true; text: archive.statusText; wrapMode: Text.WordWrap; color: archive.failed ? "#c62828" : colors.windowText }
            Label {
                objectName: "valmaxRetentionMarkerSummary"
                Layout.fillWidth: true
                visible: archive.markerStatus.length > 0 || archive.markerOnlyCount >= 0
                text: archive.markerStatus.length > 0 ? archive.markerStatus :
                    "Меток без парной сохранённой записи текста: " + archive.markerOnlyCount + ". Текст для них в локальной копии недоступен."
                wrapMode: Text.WordWrap
                color: archive.markerFailed ? "#c62828" : colors.windowText
            }
            ListView {
                id: rows
                objectName: "valmaxRetentionArchiveRows"
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; spacing: 8; model: savedRows
                delegate: Rectangle {
                    required property string text
                    required property double receivedAtMs
                    required property double deletedAtMs
                    width: rows.width; height: contentColumn.implicitHeight + 16
                    radius: 8; color: colors.base
                    Column {
                        id: contentColumn
                        x: 8; y: 8; width: parent.width - 16; spacing: 5
                        Text { width: parent.width; text: "Удалено " + new Date(deletedAtMs).toLocaleString(); color: colors.windowText; font.bold: true; textFormat: Text.PlainText }
                        Text { width: parent.width; text: parent.parent.text; color: colors.windowText; wrapMode: Text.WrapAtWordBoundaryOrAnywhere; textFormat: Text.PlainText }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Button { objectName: "valmaxRetentionArchivePrevious"; text: "Назад"; enabled: !archive.pendingRequest && archive.previousCursors.length > 0; onClicked: archive.previousPage() }
                Button { objectName: "valmaxRetentionArchiveNext"; text: "Дальше"; enabled: !archive.pendingRequest && archive.nextCursor !== null; onClicked: archive.nextPage() }
                Item { Layout.fillWidth: true }
                Button { objectName: "valmaxRetentionArchiveRefresh"; text: "Обновить"; enabled: !archive.pendingRequest; onClicked: archive.refresh() }
            }
        }
    }
}
