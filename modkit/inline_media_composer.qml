// ValMAX explicit media search. Draft parsing is local; only a button sends a query.
property var modInlineRows: []
property var modInlineRequest: null
property int modInlineGeneration: 0
property bool modInlineBusy: false
property string modInlineStatus: ""
property string modInlineDismissedDraft: ""
readonly property string modInlineDraftKey: /^@(pic|vid)(?:\s[^\r\n]{0,160})?$/i.test(chatInput.text) ? chatInput.text : ""
readonly property bool modInlineVisible: textInputVisible && modInlineDraftKey.length > 0 && modInlineDismissedDraft !== modInlineDraftKey
readonly property string modInlineMode: modInlineDraftKey.substring(1, 4).toLowerCase()
readonly property string modInlineQuery: modInlineDraftKey.substring(4).trim()
onModInlineDraftKeyChanged: modInlineReset(false)
function modInlineReset(accountOrChatChanged) {
    ++modInlineGeneration;
    var request = modInlineRequest;
    modInlineRequest = null;
    if (request !== null) { request.onreadystatechange = function() {}; request.abort(); }
    modInlineWatchdog.stop();
    modInlineBusy = false;
    modInlineRows = [];
    modInlineStatus = "";
    if (accountOrChatChanged) modInlineDismissedDraft = "";
}
function modInlineClose() {
    modInlineDismissedDraft = chatInput.text;
    modInlineReset(false);
    chatInput.forceActiveFocus();
}
function modInlineError(code) {
    var messages = { invalid_input: "Введи от 2 до 160 символов после @pic или @vid.",
        provider_unavailable: "Wikimedia Commons недоступна. Попробуй позже.", provider_busy: "Wikimedia Commons просит подождать. Попробуй позже.",
        search_too_fast: "Подожди две секунды перед новым поиском.", result_expired: "Результаты устарели. Нажми «Найти» ещё раз.",
        file_too_large: "Файл слишком большой: максимум 25 МБ.", unsupported_media: "Файл не прошёл проверку формата. Выбери другой.",
        cache_full: "Для поиска медиа заполнена временная папка. Файлы старше суток очищаются при следующем выборе.",
        storage_unavailable: "Не удалось сохранить временный файл рядом с ValMAX.", download_busy: "Дождись загрузки выбранного файла." };
    return messages[code] || "Поиск медиа недоступен. Запусти ValMAX обычным ярлыком.";
}
function modInlineRequestJson(route, payload, success) {
    if (modInlineBusy) return;
    var xhr = new XMLHttpRequest(), generation = modInlineGeneration, originalDraft = chatInput.text;
    modInlineBusy = true;
    modInlineRequest = xhr;
    modInlineWatchdog.restart();
    xhr.onreadystatechange = function() {
        if (xhr.readyState !== XMLHttpRequest.DONE || root.modInlineRequest !== xhr || generation !== root.modInlineGeneration) return;
        root.modInlineRequest = null;
        root.modInlineBusy = false;
        modInlineWatchdog.stop();
        if (chatInput.text !== originalDraft || !root.modInlineVisible) return;
        try {
            var value = JSON.parse(xhr.responseText);
            if (xhr.status !== 200) { root.modInlineStatus = root.modInlineError(value && value.error); return; }
            if (!value || value.schema !== 1 || value.provider !== "Wikimedia Commons") throw new Error("schema");
            success(value);
        } catch (ignored) { root.modInlineStatus = root.modInlineError("unavailable"); }
    };
    xhr.open("POST", "http://127.0.0.1:18973/inline-media/" + route);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.setRequestHeader("X-Max-Mod-Local-Write", "inline-media-v1");
    xhr.send(JSON.stringify(payload));
}
function modInlineSearch() {
    if (!modInlineVisible || modInlineBusy) return;
    if (modInlineQuery.length < 2 || modInlineQuery.length > 160) { modInlineStatus = modInlineError("invalid_input"); return; }
    modInlineRows = [];
    modInlineStatus = "Ищем в Wikimedia Commons…";
    modInlineRequestJson("search", { mode: modInlineMode, query: modInlineQuery }, function(value) {
        if (!Array.isArray(value.results) || value.results.length > 6 || value.mode !== root.modInlineMode) throw new Error("rows");
        for (var i = 0; i < value.results.length; ++i) {
            var row = value.results[i];
            if (!row || !/^[0-9a-f]{32}$/.test(row.id) || typeof row.title !== "string" || typeof row.author !== "string" || typeof row.license !== "string" || typeof row.source !== "string" || row.source.indexOf("https://commons.wikimedia.org/wiki/File:") !== 0 || typeof row.preview !== "boolean" || typeof row.bytes !== "number" || row.bytes <= 0 || row.bytes > 26214400) throw new Error("row");
        }
        root.modInlineRows = value.results;
        root.modInlineStatus = value.results.length ? "Выбери файл: он добавится в черновик с подписью источника." : "Подходящих файлов не найдено. Попробуй другой запрос.";
    });
}
function modInlineSelect(index) {
    if (!modInlineVisible || modInlineBusy || index < 0 || index >= modInlineRows.length) return;
    if (!modQuickReplyAttachmentIntegration) { modInlineStatus = "Добавление медиа недоступно в этом поле сообщения."; return; }
    if (attachmentUploadModel && attachmentUploadModel.attachmentsCount >= 10) { modInlineStatus = "В черновике уже десять вложений. Сначала отправь или убери файлы."; return; }
    modInlineStatus = "Загружаем выбранный файл…";
    modInlineRequestJson("select", { id: modInlineRows[index].id }, function(value) {
        if (typeof value.url !== "string" || !/^file:\/\/\/[A-Za-z]:\//.test(value.url) || typeof value.caption !== "string" || value.caption.length > 3000 || !/^(image\/(jpeg|png|gif|webp)|video\/(webm|mp4))$/.test(value.mime)) throw new Error("file");
        if (attachmentUploadModel && attachmentUploadModel.attachmentsCount >= 10) { root.modInlineStatus = "В черновике уже десять вложений."; return; }
        root.modQuickReplySelected = null;
        root.setText(value.caption, []);
        root.modQuickReplyAttachmentsRequested([], [value.url]);
        chatInput.moveCursorToEnd();
        chatInput.forceActiveFocus();
    });
}
Timer {
    id: modInlineWatchdog
    interval: 30000
    onTriggered: {
        var request = root.modInlineRequest;
        root.modInlineRequest = null;
        root.modInlineBusy = false;
        if (request !== null) { request.onreadystatechange = function() {}; request.abort(); }
        root.modInlineStatus = "Wikimedia Commons не ответила. Попробуй ещё раз.";
    }
}
// BEGIN INLINE MEDIA MENU
UiKit.MeLoader {
    id: modInlineLoader
    objectName: "inlineMediaLoader"
    Layout.fillWidth: true
    // The native write bar is capped at 60% of the chat height. Let this
    // loader and its results shrink within that allocation, above the editor.
    Layout.fillHeight: true
    Layout.minimumHeight: 0
    activeAndVisible: root.modInlineVisible
    sourceComponent: Control {
        id: inlineMediaMenu
        objectName: "inlineMediaMenu"
        // Enter/Tab only consume completion keys. Searches require the explicit button.
        readonly property int count: 0
        function selectPrevious() {}
        function selectNext() {}
        function completeSelected() {}
        padding: UiKit.DesignSystem.SpacingCommon._S
        implicitHeight: Math.min(440, inlineMediaContents.implicitHeight + 2 * padding)
        clip: true
        background: Rectangle { color: root.chatAppearance.colorBackgroundCard; radius: UiKit.DesignSystem.BorderRadiusCommon._L }
        contentItem: ColumnLayout {
            id: inlineMediaContents
            spacing: UiKit.DesignSystem.SpacingCommon._S
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: (root.modInlineMode === "vid" ? "Поиск видео" : "Поиск фото") + " · Wikimedia Commons"; color: root.chatAppearance.colorTextPrimary; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.BodyStrong, Typography.FontUtil.Sizes.Default); textFormat: Text.PlainText; wrapMode: Text.Wrap }
                Button { objectName: "inlineMediaSearch"; focusPolicy: Qt.NoFocus; text: "Найти"; implicitHeight: 36; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default); enabled: !root.modInlineBusy && root.modInlineQuery.length >= 2; onClicked: root.modInlineSearch()
                    contentItem: Label { text: parent.text; font: parent.font; color: parent.enabled ? root.chatAppearance.colorTextPrimary : root.chatAppearance.colorTextSecondary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText }
                    background: Rectangle { color: root.chatAppearance.colorStatesBackgroundCardHover; radius: UiKit.DesignSystem.BorderRadiusCommon._S }
                }
                Button { objectName: "inlineMediaClose"; focusPolicy: Qt.NoFocus; text: "Закрыть"; implicitHeight: 36; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default); onClicked: root.modInlineClose()
                    contentItem: Label { text: parent.text; font: parent.font; color: root.chatAppearance.colorTextPrimary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText }
                    background: Rectangle { color: root.chatAppearance.colorStatesBackgroundCardHover; radius: UiKit.DesignSystem.BorderRadiusCommon._S }
                }
            }
            Label { Layout.fillWidth: true; text: "Внешний поиск только по кнопке «Найти». Файл добавится в черновик; отправка вручную."; color: root.chatAppearance.colorTextSecondary; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default); textFormat: Text.PlainText; wrapMode: Text.Wrap }
            Label { Layout.fillWidth: true; visible: root.modInlineStatus.length > 0; text: root.modInlineStatus; color: root.chatAppearance.colorTextSecondary; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default); textFormat: Text.PlainText; wrapMode: Text.Wrap }
            ListView {
                id: inlineMediaResults
                objectName: "inlineMediaResults"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 0
                Layout.preferredHeight: Math.min(288, contentHeight)
                visible: count > 0
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                model: root.modInlineRows
                delegate: Button {
                    id: mediaRow
                    required property int index
                    required property var modelData
                    objectName: "inlineMediaResult_" + index
                    Accessible.name: modelData.title + ". Добавить в черновик"
                    width: inlineMediaResults.width
                    implicitHeight: Math.max(88, contentItem.implicitHeight + 16)
                    padding: 8
                    focusPolicy: Qt.NoFocus
                    enabled: !root.modInlineBusy
                    onClicked: root.modInlineSelect(index)
                    background: Rectangle { color: mediaRow.hovered ? root.chatAppearance.colorStatesBackgroundCardHover : "transparent"; radius: UiKit.DesignSystem.BorderRadiusCommon._S }
                    contentItem: RowLayout {
                        spacing: UiKit.DesignSystem.SpacingCommon._S
                        Item {
                            Layout.preferredWidth: 96; Layout.preferredHeight: 72
                            Image { id: mediaPreview; objectName: "inlineMediaPreview_" + mediaRow.index; anchors.fill: parent; source: modelData.preview ? "http://127.0.0.1:18973/inline-media/preview?id=" + modelData.id : ""; asynchronous: true; fillMode: Image.PreserveAspectFit; sourceSize: Qt.size(240, 180) }
                            Label { anchors.fill: parent; visible: mediaPreview.status !== Image.Ready; text: root.modInlineMode === "vid" ? "Видео" : "Фото"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: root.chatAppearance.colorTextSecondary; textFormat: Text.PlainText; background: Rectangle { color: root.chatAppearance.colorStatesBackgroundCardHover; radius: UiKit.DesignSystem.BorderRadiusCommon._S } }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label { Layout.fillWidth: true; text: modelData.title; color: root.chatAppearance.colorTextPrimary; textFormat: Text.PlainText; maximumLineCount: 2; elide: Text.ElideRight; wrapMode: Text.Wrap }
                            Label { Layout.fillWidth: true; text: modelData.author + " · " + modelData.license; color: root.chatAppearance.colorTextSecondary; textFormat: Text.PlainText; maximumLineCount: 2; elide: Text.ElideRight; wrapMode: Text.Wrap; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default) }
                            Label { text: (modelData.bytes / 1048576).toFixed(1) + " МБ · добавить в черновик"; color: root.chatAppearance.colorTextSecondary; textFormat: Text.PlainText; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default) }
                        }
                    }
                }
            }
        }
    }
}
// END INLINE MEDIA MENU
