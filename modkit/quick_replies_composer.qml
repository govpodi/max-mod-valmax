// Inline declarations in native BaseSendPanel. Reads templates only, never drafts.
property var modQuickReplyRows: []
property string modQuickReplyRevision: ""
property string modQuickRepliesDismissedQuery: ""
property var modQuickRepliesRequest: null
property string modQuickRepliesStatus: "Загрузка локальных ответов…"
readonly property string modQuickReplyAccountId: commonDependency && commonDependency.selfInfo ? QmlUtils.contactIdString(commonDependency.selfInfo.id) : ""
property var modQuickReplySelected: null
property int modQuickReplyPartIndex: 0
property bool modQuickReplyFilesAdded: false
property bool modQuickReplyNativeFilesObserved: false
property bool modQuickReplyAttachmentIntegration: false
property bool modQuickReplyPartCleared: false
property string modQuickReplyDraftStatus: ""
signal modQuickReplyAttachmentsRequested(var files, var media)
readonly property var modQuickReplyDraftParts: modQuickReplySelected ? [modQuickReplySelected].concat(modQuickReplySelected.parts || []) : []
readonly property var modQuickReplyCurrentPart: modQuickReplyDraftParts.length > modQuickReplyPartIndex ? modQuickReplyDraftParts[modQuickReplyPartIndex] : null
readonly property bool modQuickReplyDraftEmpty: chatInput.text.length === 0 && (!attachmentUploadModel || attachmentUploadModel.attachmentsCount === 0)
readonly property int modQuickReplyAttachmentCount: attachmentUploadModel ? attachmentUploadModel.attachmentsCount : 0
onModQuickReplyAttachmentCountChanged: { if (modQuickReplyFilesAdded && modQuickReplyAttachmentCount > 0) modQuickReplyNativeFilesObserved = true; }
onCurrentOpenedChatIdChanged: modQuickReplySelected = null
onModQuickReplyAccountIdChanged: modQuickReplySelected = null
readonly property bool modQuickRepliesQueryEligible: textInputVisible && chatInput.activeFocus && !chatInput.mentionStarted && /^\/(?:[a-zа-яё][a-zа-яё0-9_]{0,31})?$/.test(chatInput.text.toLowerCase()) && modQuickRepliesDismissedQuery !== chatInput.text
readonly property bool modQuickRepliesVisible: modQuickRepliesQueryEligible && (modFilteredQuickReplies.length > 0 || chatInput.text === "/" && !(!!botCommandListContext && chatInput.botCommandListStarted))
readonly property var modFilteredQuickReplies: {
    var prefix = chatInput.text.substring(1).toLowerCase();
    var result = [];
    var specific = Object.create(null);
    for (var i = 0; i < modQuickReplyRows.length; ++i) if (modQuickReplyRows[i].accountId && modQuickReplyRows[i].accountId === modQuickReplyAccountId) specific[modQuickReplyRows[i].command] = true;
    for (var j = 0; j < modQuickReplyRows.length; ++j) {
        var row = modQuickReplyRows[j], account = row.accountId || "";
        if (account && account !== modQuickReplyAccountId || !account && specific[row.command]) continue;
        if (row.command.indexOf(prefix) === 0) result.push(row);
    }
    return result;
}
function modQuickReplyFileName(url) { try { return decodeURIComponent(String(url).split("/").pop()); } catch (ignored) { return "Файл"; } }
function modQuickReplySummary(row) {
    var parts = [row].concat(row.parts || []), files = 0;
    for (var i = 0; i < parts.length; ++i) files += (parts[i].attachments || []).length;
    return parts.length + " сообщ. " + (files ? "· файлов: " + files : "") + (row.accountId ? " · этот аккаунт" : "");
}
function modApplyQuickReplyPart(index) {
    if (index < 0 || index >= modQuickReplyDraftParts.length) return;
    if (index !== 0 && (!modQuickReplyDraftEmpty || !modQuickReplyPartCleared && modQuickReplyFilesAdded && !modQuickReplyNativeFilesObserved)) { modQuickReplyDraftStatus = "Сначала отправь или очисти текущий черновик."; return; }
    modQuickReplyPartIndex = index;
    modQuickReplyFilesAdded = false;
    modQuickReplyNativeFilesObserved = false;
    modQuickReplyPartCleared = false;
    modQuickReplyDraftStatus = "";
    var part = modQuickReplyDraftParts[index];
    modQuickRepliesDismissedQuery = part.text;
    root.setText(part.text, []);
    chatInput.moveCursorToEnd();
    chatInput.forceActiveFocus();
    modAddQuickReplyFiles();
}
// The native sender emits cleared after sending/resetting its draft. Text edits
// and asynchronous upload-model changes are deliberately not completion signals.
function modQuickReplyNativeCleared() {
    if (!modQuickReplySelected) return;
    modQuickReplyPartCleared = true;
    modQuickReplyDraftStatus = "";
    if (modQuickReplyPartIndex + 1 >= modQuickReplyDraftParts.length) modQuickReplySelected = null;
}
function modAddQuickReplyFiles() {
    var part = modQuickReplyCurrentPart;
    if (!part || modQuickReplyPartCleared || modQuickReplyFilesAdded || !(part.attachments || []).length) return;
    if (!modQuickReplyAttachmentIntegration) { modQuickReplyDraftStatus = "Добавление файлов недоступно в этом поле сообщения."; return; }
    var files = [], media = [];
    for (var i = 0; i < part.attachments.length; ++i) {
        var file = part.attachments[i];
        if (!file || typeof file.url !== "string" || !/^file:\/\/[\/][A-Za-z]:\//.test(file.url) || (file.kind !== "file" && file.kind !== "media")) { modQuickReplyDraftStatus = "Некорректный путь файла. Проверь заготовку в настройках."; return; }
        (file.kind === "media" ? media : files).push(file.url);
    }
    if (files.length + media.length > 10 || attachmentUploadModel && attachmentUploadModel.attachmentsCount + files.length + media.length > 10) { modQuickReplyDraftStatus = "В черновике слишком много файлов. Сначала отправь или убери вложения."; return; }
    modQuickReplyFilesAdded = true;
    modQuickReplyAttachmentsRequested(files, media);
    chatInput.forceActiveFocus();
}
function modLoadQuickReplies() {
    if (modQuickRepliesRequest !== null) return;
    var xhr = new XMLHttpRequest();
    modQuickRepliesRequest = xhr;
    modQuickRepliesWatchdog.restart();
    xhr.onreadystatechange = function() {
        if (xhr.readyState !== XMLHttpRequest.DONE || root.modQuickRepliesRequest !== xhr) return;
        root.modQuickRepliesRequest = null;
        modQuickRepliesWatchdog.stop();
        try {
            if (xhr.status !== 200) throw new Error("unavailable");
            var value = JSON.parse(xhr.responseText);
            if (!value || value.schema !== 1 || !/^[0-9a-f]{64}$/.test(value.revision) || !Array.isArray(value.replies) || value.replies.length > 100) throw new Error("schema");
            for (var i = 0; i < value.replies.length; ++i) {
                var row = value.replies[i];
                if (!row || !/^[a-zа-яё][a-zа-яё0-9_]{0,31}$/.test(row.command) || typeof row.title !== "string" || typeof row.text !== "string" || row.text.length === 0 && !(row.attachments || []).length || row.accountId && !/^[1-9][0-9]{0,18}$/.test(row.accountId) || row.parts && (!Array.isArray(row.parts) || row.parts.length > 9)) throw new Error("row");
            }
            if (value.revision !== root.modQuickReplyRevision) { root.modQuickReplyRows = value.replies; root.modQuickReplyRevision = value.revision; }
            root.modQuickRepliesStatus = value.replies.length ? "Нет подходящих ответов. Шаблоны можно изменить в настройках." : "Нет шаблонов. Создай ответ в Настройки → Быстрые ответы.";
        } catch (ignored) { root.modQuickRepliesStatus = "Хранилище недоступно. Запусти MAX Mod обычным ярлыком."; }
    };
    xhr.open("GET", "http://127.0.0.1:18973/quick-replies");
    xhr.send();
}
Timer {
    id: modQuickRepliesWatchdog
    interval: 7000
    onTriggered: {
        var xhr = root.modQuickRepliesRequest;
        root.modQuickRepliesRequest = null;
        if (xhr !== null) { xhr.onreadystatechange = function() {}; xhr.abort(); }
        root.modQuickRepliesStatus = "Хранилище не ответило. Попробуй ещё раз через несколько секунд.";
    }
}
Component.onCompleted: modLoadQuickReplies()
Timer { interval: 2000; repeat: true; triggeredOnStart: true; running: root.modQuickRepliesQueryEligible; onTriggered: root.modLoadQuickReplies() }
Component.onDestruction: {
    if (modQuickRepliesRequest !== null) { modQuickRepliesRequest.onreadystatechange = function() {}; modQuickRepliesRequest.abort(); }
}
// BEGIN MENU LOADER — inserted before mentionsListLoader in native ColumnLayout.
Control {
    objectName: "quickReplyDraftPreview"
    Layout.fillWidth: true
    visible: root.modQuickReplySelected !== null
    padding: UiKit.DesignSystem.SpacingCommon._S
    background: Rectangle { color: root.chatAppearance.colorBackgroundCard; radius: UiKit.DesignSystem.BorderRadiusCommon._L }
    contentItem: ColumnLayout {
        spacing: UiKit.DesignSystem.SpacingCommon._2XS
        Label { Layout.fillWidth: true; text: root.modQuickReplySelected ? "/" + root.modQuickReplySelected.command + " · заготовка " + (root.modQuickReplyPartIndex + 1) + " из " + root.modQuickReplyDraftParts.length + " · отправка вручную" : ""; color: root.chatAppearance.colorTextSecondary; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default); textFormat: Text.PlainText; wrapMode: Text.Wrap }
        Label { Layout.fillWidth: true; visible: root.modQuickReplyCurrentPart !== null && (root.modQuickReplyCurrentPart.attachments || []).length > 0; text: root.modQuickReplyCurrentPart ? (root.modQuickReplyCurrentPart.attachments || []).map(function(file) { return root.modQuickReplyFileName(file.url); }).join(", ") : ""; color: root.chatAppearance.colorTextSecondary; textFormat: Text.PlainText; maximumLineCount: 2; elide: Text.ElideRight }
        Label { Layout.fillWidth: true; visible: root.modQuickReplyDraftStatus.length > 0; text: root.modQuickReplyDraftStatus; color: root.chatAppearance.colorTextSecondary; textFormat: Text.PlainText; wrapMode: Text.Wrap; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default) }
        Flow {
            Layout.fillWidth: true; spacing: UiKit.DesignSystem.SpacingCommon._S
            Button { font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default); implicitHeight: 36; contentItem: Label { text: parent.text; font: parent.font; color: parent.enabled ? root.chatAppearance.colorTextPrimary : root.chatAppearance.colorTextSecondary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText }
                background: Rectangle { color: root.chatAppearance.colorStatesBackgroundCardHover; radius: UiKit.DesignSystem.BorderRadiusCommon._S }
                objectName: "quickReplyNextPart"; text: "Следующая заготовка"; visible: root.modQuickReplyPartIndex + 1 < root.modQuickReplyDraftParts.length; enabled: root.modQuickReplyDraftEmpty && (root.modQuickReplyPartCleared || !root.modQuickReplyFilesAdded || root.modQuickReplyNativeFilesObserved); onClicked: root.modApplyQuickReplyPart(root.modQuickReplyPartIndex + 1) }
            Button { font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default); implicitHeight: 36; contentItem: Label { text: parent.text; font: parent.font; color: parent.enabled ? root.chatAppearance.colorTextPrimary : root.chatAppearance.colorTextSecondary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText }
                background: Rectangle { color: root.chatAppearance.colorStatesBackgroundCardHover; radius: UiKit.DesignSystem.BorderRadiusCommon._S }
                objectName: "quickReplyCloseDraftPreview"; text: "Закрыть подсказку"; onClicked: root.modQuickReplySelected = null }
        }
    }
}
UiKit.MeLoader {
    id: modQuickRepliesLoader
    objectName: "quickRepliesMenuLoader"
    Layout.fillWidth: true
    Layout.topMargin: -root.topPadding
    Layout.leftMargin: -root.leftPadding
    Layout.rightMargin: -root.rightPadding
    activeAndVisible: root.modQuickRepliesVisible
    sourceComponent: Control {
        id: quickReplyMenu
        objectName: "quickRepliesMenu"
        readonly property int count: replyList.count
        implicitHeight: Math.min(256, Math.max(96, menuHeader.implicitHeight + (count ? replyList.contentHeight : emptyMenuText.implicitHeight) + 2 * padding + UiKit.DesignSystem.SpacingCommon._S))
        padding: UiKit.DesignSystem.SpacingCommon._S
        function selectPrevious() { if (count > 0) { replyList.currentIndex = replyList.currentIndex <= 0 ? count - 1 : replyList.currentIndex - 1; replyList.positionViewAtIndex(replyList.currentIndex, ListView.Contain); } }
        function selectNext() { if (count > 0) { replyList.currentIndex = (replyList.currentIndex + 1) % count; replyList.positionViewAtIndex(replyList.currentIndex, ListView.Contain); } }
        function completeSelected() { completeIndex(replyList.currentIndex); }
        function completeIndex(index) {
            if (!root.modQuickRepliesVisible || index < 0 || index >= root.modFilteredQuickReplies.length) return;
            var reply = root.modFilteredQuickReplies[index];
            root.modQuickReplySelected = reply;
            root.modApplyQuickReplyPart(0);
        }
        background: Rectangle { color: root.chatAppearance.colorBackgroundCard; topLeftRadius: UiKit.DesignSystem.BorderRadiusCommon._L; topRightRadius: UiKit.DesignSystem.BorderRadiusCommon._L }
        contentItem: ColumnLayout {
            spacing: UiKit.DesignSystem.SpacingCommon._2XS
            Label {
                id: menuHeader
                Layout.fillWidth: true
                text: "Быстрые ответы · Enter или Tab — подставить · Esc — закрыть"
                font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default)
                color: root.chatAppearance.colorTextSecondary
                textFormat: Text.PlainText; wrapMode: Text.Wrap
            }
            ListView {
                id: replyList
                objectName: "quickRepliesList"
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; boundsBehavior: Flickable.StopAtBounds
                model: root.modFilteredQuickReplies
                currentIndex: 0
                onCountChanged: currentIndex = count > 0 ? 0 : -1
                delegate: Button {
                    id: replyDelegate
                    required property int index
                    required property var modelData
                    width: replyList.width
                    focusPolicy: Qt.NoFocus
                    padding: UiKit.DesignSystem.SpacingCommon._S
                    implicitHeight: contentItem.implicitHeight + 2 * padding
                    onClicked: quickReplyMenu.completeIndex(index)
                    background: Rectangle { radius: UiKit.DesignSystem.BorderRadiusCommon._S; color: replyDelegate.ListView.isCurrentItem ? root.chatAppearance.colorStatesBackgroundCardHover : "transparent" }
                    contentItem: ColumnLayout {
                        spacing: UiKit.DesignSystem.SpacingCommon._2XS
                        Label { Layout.fillWidth: true; text: "/" + modelData.command + " · " + modelData.title; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.BodyStrong, Typography.FontUtil.Sizes.Default); color: root.chatAppearance.colorTextPrimary; textFormat: Text.PlainText; wrapMode: Text.Wrap }
                        Label { Layout.fillWidth: true; text: modelData.text; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default); color: root.chatAppearance.colorTextSecondary; textFormat: Text.PlainText; maximumLineCount: 1; elide: Text.ElideRight }
                        Label { Layout.fillWidth: true; text: root.modQuickReplySummary(modelData); font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default); color: root.chatAppearance.colorTextSecondary; textFormat: Text.PlainText }
                    }
                }
            }
            Label { id: emptyMenuText; visible: replyList.count === 0; Layout.fillWidth: true; text: root.modQuickRepliesStatus; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default); color: root.chatAppearance.colorTextSecondary; textFormat: Text.PlainText; wrapMode: Text.Wrap }
        }
    }
}
// END MENU LOADER
