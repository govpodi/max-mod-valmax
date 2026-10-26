// Selected-chat retained text in the native history ListView footer.
// This component never changes the client's history model or its indices.
import QtQuick
import QtQuick.Controls
import UiKitModule as UiKit
import ValMAX.Native 1.0 as Native

Item {
    id: inline
    objectName: "valmaxRetentionInline"
    property string accountId: ""
    property string chatId: ""
    property bool active: false
    property int nativeRowCount: 0
    property var chatAppearance: null
    readonly property bool backendReady: Native.RetentionTransport.lookupAvailable
    readonly property int pageSize: 20
    readonly property int maximumResponseCharacters: 8 * 1024 * 1024
    readonly property int displayedCount: savedRows.count
    property bool completed: false
    property int generation: 0
    property var pendingRequest: null
    property var currentCursor: null
    property var nextCursor: null
    property var previousCursors: []
    property string revision: ""
    property int staleRestarts: 0
    property bool failed: false

    width: parent ? parent.width : 0
    implicitHeight: content.visible ? content.implicitHeight + 10 : 0
    height: implicitHeight
    SystemPalette { id: colors }
    ListModel { id: savedRows }

    function validReply(value, requestedAccount, requestedChat) {
        if (!value || typeof value !== "object" || Array.isArray(value)
                || Object.keys(value).sort().join("|") !== "accountId|chatId|items|nextCursor|revision|schema"
                || value.schema !== 1 || value.accountId !== requestedAccount || value.chatId !== requestedChat
                || typeof value.revision !== "string" || !/^[0-9a-f]{64}$/.test(value.revision)
                || !(value.nextCursor === null || typeof value.nextCursor === "string"
                    && value.nextCursor.length > 0 && value.nextCursor.length <= 256)
                || !Array.isArray(value.items) || value.items.length > pageSize) return false;
        const seen = {};
        for (const row of value.items) {
            if (!row || typeof row !== "object" || Array.isArray(row)
                    || Object.keys(row).sort().join("|") !== "deletedAtMs|messageId|orderKey|receivedAtMs|sentAtMs|text"
                    || !Native.RetentionTransport.validId(row.messageId, true) || seen[row.messageId]
                    || !(row.orderKey === null || Native.RetentionTransport.validId(row.orderKey, true))
                    || !Number.isSafeInteger(row.receivedAtMs) || row.receivedAtMs < 0
                    || !Number.isSafeInteger(row.deletedAtMs) || row.deletedAtMs < 0
                    || !(row.sentAtMs === null || Number.isSafeInteger(row.sentAtMs) && row.sentAtMs >= 0)
                    || typeof row.text !== "string" || row.text.length < 1 || row.text.length > 65536)
                return false;
            seen[row.messageId] = true;
        }
        return true;
    }
    function sameRows(items) {
        if (savedRows.count !== items.length) return false;
        for (let index = 0; index < items.length; index += 1) {
            const old = savedRows.get(index), row = items[index];
            if (old.messageId !== row.messageId || old.text !== row.text
                    || old.receivedAtMs !== row.receivedAtMs
                    || old.deletedAtMs !== row.deletedAtMs) return false;
        }
        return true;
    }
    function abortPending() {
        requestWatchdog.stop();
        const request = pendingRequest;
        pendingRequest = null;
        if (request) {
            request.onreadystatechange = function() {};
            try { request.abort(); } catch (_) {}
        }
    }
    function resetScope() {
        generation += 1;
        abortPending(); savedRows.clear();
        currentCursor = null; nextCursor = null; previousCursors = [];
        revision = ""; staleRestarts = 0; failed = false;
        if (completed && active && backendReady
                && Native.RetentionTransport.validId(accountId, true)
                && Native.RetentionTransport.validId(chatId, false)) requestPage(null, "first");
    }
    function restartAfterStale() {
        if (staleRestarts >= 2) {
            savedRows.clear(); nextCursor = null; failed = true; return;
        }
        staleRestarts += 1; generation += 1;
        abortPending(); savedRows.clear();
        currentCursor = null; nextCursor = null; previousCursors = []; revision = "";
        requestPage(null, "first");
    }
    function requestPage(cursor, direction) {
        if (!completed || !active || !backendReady || pendingRequest
                || !Native.RetentionTransport.validId(accountId, true)
                || !Native.RetentionTransport.validId(chatId, false)) return;
        const requestedAccount = accountId, requestedChat = chatId, requestGeneration = generation;
        const legacyOnly = nativeRowCount > 0;
        const oldCursor = currentCursor, oldPrevious = previousCursors;
        let request;
        try { request = Native.RetentionTransport.createRequest("inline"); request.open("POST"); }
        catch (_) { failed = true; return; }
        pendingRequest = request;
        requestWatchdog.restart();
        request.onreadystatechange = function() {
            if (request.readyState !== 4 || pendingRequest !== request) return;
            pendingRequest = null; requestWatchdog.stop();
            if (!completed || !active || generation !== requestGeneration
                    || accountId !== requestedAccount || chatId !== requestedChat) return;
            try {
                if (request.status === 409) {
                    const error = JSON.parse(request.responseText);
                    if (error && error.error === "stale_cursor") { restartAfterStale(); return; }
                }
                if (request.status !== 200 || typeof request.responseText !== "string"
                        || request.responseText.length > maximumResponseCharacters) throw new Error("response");
                const value = JSON.parse(request.responseText);
                if (!validReply(value, requestedAccount, requestedChat)) throw new Error("schema");
                if (legacyOnly && value.items.some(function(row) { return row.orderKey !== null; }))
                    throw new Error("ordered_row_in_legacy_page");
                const footerRows = value.items;
                if (cursor !== null && revision.length && revision !== value.revision) {
                    restartAfterStale(); return;
                }
                // Polling an unchanged first page must not rebuild bubbles:
                // that would collapse expanded text and move the user's scroll.
                if ((direction === "refresh" || direction === "resume")
                        && currentCursor === cursor
                        && nextCursor === value.nextCursor && sameRows(footerRows)) {
                    revision = value.revision; failed = false; staleRestarts = 0;
                    return;
                }
                if (direction === "next") {
                    previousCursors = oldPrevious.concat([oldCursor]); currentCursor = cursor;
                } else if (direction === "previous") {
                    previousCursors = oldPrevious.slice(0, -1); currentCursor = cursor;
                } else if (direction === "first") {
                    previousCursors = []; currentCursor = null;
                }
                revision = value.revision; nextCursor = value.nextCursor;
                savedRows.clear();
                for (const row of footerRows) savedRows.append({
                    messageId: row.messageId, text: row.text,
                    receivedAtMs: row.receivedAtMs, deletedAtMs: row.deletedAtMs
                });
                failed = false;
                if (cursor === null) staleRestarts = 0;
            } catch (_) {
                savedRows.clear(); nextCursor = null; failed = true;
            }
        };
        try {
            request.send(JSON.stringify({schema: 1, accountId: requestedAccount,
                chatId: requestedChat, cursor: cursor, limit: pageSize, legacyOnly: legacyOnly}));
        } catch (_) { abortPending(); savedRows.clear(); nextCursor = null; failed = true; }
    }
    function nextPage() {
        if (!pendingRequest && nextCursor !== null) requestPage(nextCursor, "next");
    }
    function previousPage() {
        if (!pendingRequest && previousCursors.length > 0)
            requestPage(previousCursors[previousCursors.length - 1], "previous");
    }
    function refresh() {
        if (!completed) return;
        generation += 1; abortPending();
        currentCursor = null; previousCursors = []; staleRestarts = 0;
        requestPage(null, "refresh");
    }
    onAccountIdChanged: if (completed) resetScope()
    onChatIdChanged: if (completed) resetScope()
    onNativeRowCountChanged: if (completed) resetScope()
    onActiveChanged: {
        if (!completed) return;
        if (!active) { generation += 1; abortPending(); }
        else if (backendReady) requestPage(currentCursor,
            currentCursor === null ? "refresh" : "resume");
    }
    onBackendReadyChanged: if (completed) resetScope()
    Component.onCompleted: { completed = true; resetScope(); }
    Component.onDestruction: { completed = false; generation += 1; abortPending(); savedRows.clear(); }

    Timer {
        id: refreshTimer
        interval: 5000; repeat: true
        running: inline.completed && inline.active && inline.backendReady
        onTriggered: if (inline.currentCursor === null && !inline.pendingRequest) inline.requestPage(null, "refresh")
    }
    Timer {
        id: requestWatchdog
        interval: 10000
        onTriggered: {
            inline.abortPending(); savedRows.clear();
            inline.nextCursor = null; inline.failed = true;
        }
    }

    Column {
        id: content
        objectName: "valmaxRetentionInlineContent"
        width: inline.width
        spacing: 8
        // An empty page may still have a cursor when marker-only rows are skipped.
        visible: savedRows.count > 0 || inline.nextCursor !== null
                 || inline.previousCursors.length > 0 || inline.failed

        Text {
            objectName: "valmaxRetentionInlineCaption"
            x: 16; width: Math.max(0, content.width - 32)
            text: "Сохранённые удаления · положение в истории приблизительное"
            color: colors.windowText
            font.pixelSize: 12
            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
            textFormat: Text.PlainText
        }
        Repeater {
            model: savedRows
            delegate: Item {
                id: retainedRow
                objectName: "valmaxRetainedInlineBubbleRow"
                required property string messageId
                required property string text
                required property double receivedAtMs
                required property double deletedAtMs
                property bool expanded: false
                width: content.width
                height: bubble.height

                Rectangle {
                    id: bubble
                    objectName: "valmaxRetainedInlineBubble"
                    x: 16
                    width: Math.max(0, Math.min(480, retainedRow.width - 32))
                    height: bubbleContent.implicitHeight + 16
                    radius: 14
                    color: inline.chatAppearance
                        ? inline.chatAppearance.colorBubblesIncomingBackgroundSurfaceSecondary : colors.base
                    border.color: inline.chatAppearance
                        ? inline.chatAppearance.colorBubblesIncomingStrokeNeutralSecondary : colors.mid
                    Column {
                        id: bubbleContent
                        x: 10; y: 8
                        width: Math.max(0, bubble.width - 20)
                        spacing: 5
                        Row {
                            width: bubbleContent.width
                            height: 20; spacing: 5
                            Rectangle {
                                width: 20; height: 20; radius: 10
                                color: inline.chatAppearance
                                    ? inline.chatAppearance.colorBubblesIncomingBackgroundSurfaceSecondary : colors.base
                                UiKit.SvgIcon {
                                    objectName: "valmaxRetainedInlineTrashIcon"
                                    anchors.centerIn: parent
                                    width: 12; height: 12
                                    enabled: false
                                    imageWidth: 12; imageHeight: 12
                                    source: "qrc:/icon_pack/delete_fill.svg"
                                    fillColor: inline.chatAppearance
                                        ? inline.chatAppearance.colorBubblesIncomingTextBodySecondary : colors.windowText
                                }
                            }
                            Text {
                                objectName: "valmaxRetainedInlineDeletedAt"
                                anchors.verticalCenter: parent.verticalCenter
                                width: Math.max(0, parent.width - 25)
                                text: "Удалено " + new Date(retainedRow.deletedAtMs).toLocaleString()
                                color: inline.chatAppearance
                                    ? inline.chatAppearance.colorBubblesIncomingTextBodySecondary : colors.windowText
                                font.pixelSize: 11
                                elide: Text.ElideRight
                                wrapMode: Text.NoWrap
                                textFormat: Text.PlainText
                            }
                        }
                        Flickable {
                            id: bodyScroll
                            objectName: "valmaxRetainedInlineBodyScroll"
                            width: bubbleContent.width
                            height: Math.min(420, bodyText.implicitHeight)
                            contentWidth: width
                            contentHeight: bodyText.implicitHeight
                            flickableDirection: Flickable.VerticalFlick
                            boundsBehavior: Flickable.StopAtBounds
                            interactive: contentHeight > height
                            clip: true
                            Text {
                                id: bodyText
                                objectName: "valmaxRetainedInlineText"
                                width: bodyScroll.width
                                text: retainedRow.expanded || retainedRow.text.length <= 700
                                    ? retainedRow.text : retainedRow.text.slice(0, 700) + "…"
                                color: inline.chatAppearance
                                    ? inline.chatAppearance.colorBubblesIncomingTextBody : colors.windowText
                                font.pixelSize: 14
                                wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                                textFormat: Text.PlainText
                            }
                        }
                        Button {
                            visible: retainedRow.text.length > 700
                            text: retainedRow.expanded ? "Свернуть" : "Показать полностью"
                            onClicked: retainedRow.expanded = !retainedRow.expanded
                        }
                    }
                }
            }
        }
        Text {
            x: 16; width: Math.max(0, content.width - 32)
            visible: savedRows.count === 0 && !inline.failed
            text: "На этой странице нет доступных записей. Нажми «Ещё»."
            color: colors.windowText
            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
            textFormat: Text.PlainText
        }
        Text {
            x: 16; width: Math.max(0, content.width - 32)
            visible: inline.failed
            text: "Не удалось прочитать локальные удаления. Нажми «Обновить»."
            color: colors.windowText
            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
            textFormat: Text.PlainText
        }
        Row {
            x: 16; spacing: 8
            visible: inline.previousCursors.length > 0 || inline.nextCursor !== null || inline.failed
            Button {
                objectName: "valmaxRetentionInlinePrevious"
                text: "Назад"
                enabled: !inline.pendingRequest && inline.previousCursors.length > 0
                onClicked: inline.previousPage()
            }
            Button {
                objectName: "valmaxRetentionInlineNext"
                text: "Ещё"
                enabled: !inline.pendingRequest && inline.nextCursor !== null
                onClicked: inline.nextPage()
            }
            Button {
                objectName: "valmaxRetentionInlineRefresh"
                text: "Обновить"
                enabled: !inline.pendingRequest
                onClicked: inline.refresh()
            }
        }
    }
}
