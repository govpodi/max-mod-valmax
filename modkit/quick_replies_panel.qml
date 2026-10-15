// Inline Component inserted into LeadingSettingsBar; no new resource URL.
Component {
    id: settingsModQuickReplies
    SettingsBaseItem {
        id: quickReplySettings
        property var selfComponent: settingsModQuickReplies
        title: "Быстрые ответы"
        onBackClicked: root.backClicked()
        property var replies: []
        property string revision: ""
        property bool loaded: false
        property bool editing: false
        property string editingId: ""
        property string editorCommand: ""
        property string editorTitle: ""
        property string editorText: ""
        property string editorAccountId: ""
        property var editorAttachments: []
        property var editorParts: []
        property int filesPartIndex: -1
        property string filesKind: "file"
        readonly property string currentAccountId: QmlUtils.contactIdString(root.commonDependency.selfInfo.id)
        readonly property var visibleReplies: replies.filter(function(row) { return !row.accountId || row.accountId === currentAccountId; })
        signal editorOpened()
        property bool deleteConfirmation: false
        property var pendingRequest: null
        property string statusText: "Загрузка локальных шаблонов…"
        property bool hasError: false
        readonly property real gap: UiKit.DesignSystem.SpacingCommon._M
        readonly property real sectionGap: UiKit.DesignSystem.SpacingCommon._2XL
        readonly property real radius: UiKit.DesignSystem.BorderRadiusCommon._L
        function newId() {
            var result = "";
            for (var i = 0; i < 32; ++i) result += Math.floor(Math.random() * 16).toString(16);
            return result;
        }
        function showEditor(reply) {
            editingId = reply ? reply.id : "";
            editorCommand = reply ? reply.command : "";
            editorTitle = reply ? reply.title : "";
            editorText = reply ? reply.text : "";
            editorAccountId = reply && reply.accountId ? reply.accountId : "";
            editorAttachments = reply && reply.attachments ? JSON.parse(JSON.stringify(reply.attachments)) : [];
            editorParts = reply && reply.parts ? JSON.parse(JSON.stringify(reply.parts)) : [];
            deleteConfirmation = false;
            editing = true;
            editorOpened();
        }
        function error(message) { hasError = true; statusText = message; }
        function fileName(url) { try { return decodeURIComponent(String(url).split("/").pop()); } catch (ignored) { return "Файл"; } }
        function replySummary(row) {
            var parts = [row].concat(row.parts || []), files = 0;
            for (var i = 0; i < parts.length; ++i) files += (parts[i].attachments || []).length;
            return (row.accountId ? "Для текущего аккаунта" : "Для всех аккаунтов") + " · сообщений: " + parts.length + (files ? " · файлов: " + files : "");
        }
        function addPart() {
            if (editorParts.length >= 9) { error("В ответе может быть до 10 сообщений."); return; }
            var next = editorParts.slice(); next.push({text: ""}); editorParts = next;
        }
        function changePart(index, text) {
            // Keep the delegate and its cursor alive while typing. Structural
            // edits replace the array; text edits update the existing object.
            editorParts[index].text = text;
        }
        function removePart(index) { var next = editorParts.slice(); next.splice(index, 1); editorParts = next; }
        function chooseFiles(partIndex, kind) {
            filesPartIndex = partIndex; filesKind = kind; replyFiles.open();
        }
        function addFiles(urls) {
            var current = filesPartIndex < 0 ? editorAttachments.slice() : (editorParts[filesPartIndex].attachments || []).slice();
            for (var i = 0; i < urls.length; ++i) {
                var url = String(urls[i]), duplicate = false;
                if (!/^file:\/\/[\/][A-Za-z]:\//.test(url)) { error("Выбери файлы на локальном диске компьютера."); return; }
                for (var j = 0; j < current.length; ++j) if (current[j].url.toLowerCase() === url.toLowerCase()) duplicate = true;
                if (!duplicate) current.push({url: url, kind: filesKind});
            }
            if (current.length > 10) { error("К одному сообщению можно добавить до 10 файлов."); return; }
            if (filesPartIndex < 0) editorAttachments = current;
            else { var next = JSON.parse(JSON.stringify(editorParts)); next[filesPartIndex].attachments = current; editorParts = next; }
        }
        function removeFile(partIndex, index) {
            if (partIndex < 0) { var files = editorAttachments.slice(); files.splice(index, 1); editorAttachments = files; }
            else { var next = JSON.parse(JSON.stringify(editorParts)); next[partIndex].attachments.splice(index, 1); if (!next[partIndex].attachments.length) delete next[partIndex].attachments; editorParts = next; }
        }
        function validPart(part) {
            return part && typeof part.text === "string" && Array.from(part.text).length <= 5000 && (part.text.trim().length > 0 || Array.isArray(part.attachments) && part.attachments.length > 0 && part.attachments.length <= 10);
        }
        function validResponse(value) {
            if (!value || value.schema !== 1 || !/^[0-9a-f]{64}$/.test(value.revision) || !Array.isArray(value.replies) || value.replies.length > 100) return false;
            for (var i = 0; i < value.replies.length; ++i) {
                var row = value.replies[i];
                if (!row || !/^[0-9a-f]{32}$/.test(row.id) || !/^[a-zа-яё][a-zа-яё0-9_]{0,31}$/.test(row.command) || typeof row.title !== "string" || typeof row.text !== "string") return false;
            }
            return true;
        }
        function request(method, body) {
            if (pendingRequest !== null) return;
            var xhr = new XMLHttpRequest();
            pendingRequest = xhr;
            hasError = false;
            statusText = method === "POST" ? "Сохранение…" : "Загрузка локальных шаблонов…";
            requestWatchdog.restart();
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE || pendingRequest !== xhr) return;
                pendingRequest = null;
                requestWatchdog.stop();
                if (xhr.status === 409) { error("Список изменился. Обнови его и повтори сохранение. Текст формы сохранён."); return; }
                if (xhr.status === 400) { error("Шаблон не прошёл проверку. Проверь команду, длину текста и повторяющиеся команды."); return; }
                if (xhr.status !== 200) { error("Локальное хранилище недоступно. Запусти MAX Mod обычным ярлыком и повтори."); return; }
                try {
                    var value = JSON.parse(xhr.responseText);
                    if (!validResponse(value)) throw new Error("schema");
                    replies = value.replies;
                    revision = value.revision;
                    loaded = true;
                    statusText = method === "POST" ? "Изменения сохранены на этом компьютере." : "Шаблоны хранятся локально в MAX Mod.";
                    if (method === "POST") { editing = false; deleteConfirmation = false; }
                } catch (ignored) { error("Хранилище вернуло некорректный ответ. Изменения формы сохранены."); }
            };
            xhr.open(method, "http://127.0.0.1:18973/quick-replies");
            if (method === "POST") {
                xhr.setRequestHeader("Content-Type", "application/json");
                xhr.setRequestHeader("X-Max-Mod-Local-Write", "quick-replies-v1");
            }
            xhr.send(body || "");
        }
        function saveReply(remove) {
            if (!loaded || pendingRequest !== null) return;
            var command = editorCommand.trim().toLowerCase();
            var title = editorTitle.trim();
            var text = editorText;
            if (!remove && !/^[a-zа-яё][a-zа-яё0-9_]{0,31}$/.test(command)) { error("Команда: от 1 до 32 букв, цифр или _. Первый символ — буква; знак / добавляется автоматически."); return; }
            if (!remove && (title.length === 0 || Array.from(title).length > 80)) { error("Название должно содержать от 1 до 80 символов."); return; }
            var primary = {text: text}; if (editorAttachments.length) primary.attachments = editorAttachments;
            if (!remove && (!validPart(primary) || editorParts.some(function(part) { return !validPart(part); }))) { error("В каждом сообщении нужен текст или файл. Текст — до 5000 символов, файлов — до 10."); return; }
            var next = [];
            for (var i = 0; i < replies.length; ++i) {
                if (replies[i].id === editingId) continue;
                if (!remove && replies[i].command === command && (replies[i].accountId || "") === editorAccountId) { error("Такая команда уже существует для выбранного аккаунта. Выбери другое имя."); return; }
                next.push(replies[i]);
            }
            if (!remove) {
                var row = {id: editingId || newId(), command: command, title: title, text: text};
                if (editorAccountId) row.accountId = editorAccountId;
                if (editorAttachments.length) row.attachments = editorAttachments;
                if (editorParts.length) row.parts = editorParts;
                next.push(row);
            }
            if (next.length > 100) { error("Можно сохранить не более 100 шаблонов."); return; }
            var body = JSON.stringify({schema: 1, revision: revision, replies: next});
            if (encodeURIComponent(body).replace(/%[0-9a-f]{2}|./gi, "x").length > 262144) { error("Общий размер шаблонов превышает 256 КБ. Сократи текст."); return; }
            request("POST", body);
        }
        Component.onCompleted: request("GET", "")
        Component.onDestruction: {
            if (pendingRequest !== null) { pendingRequest.onreadystatechange = function() {}; pendingRequest.abort(); }
        }
        Timer {
            id: requestWatchdog
            interval: 7000
            onTriggered: {
                var xhr = quickReplySettings.pendingRequest;
                quickReplySettings.pendingRequest = null;
                if (xhr !== null) { xhr.onreadystatechange = function() {}; xhr.abort(); }
                quickReplySettings.error("Хранилище не ответило за 7 секунд. Изменения формы сохранены; можно повторить.");
            }
        }
        QuickReplyDialogs.FileDialog {
            id: replyFiles
            title: quickReplySettings.filesKind === "media" ? "Фото и видео для ответа" : "Файлы для ответа"
            fileMode: QuickReplyDialogs.FileDialog.OpenFiles
            nameFilters: quickReplySettings.filesKind === "media" ? ["Фото и видео (*.jpg *.jpeg *.png *.webp *.gif *.mp4 *.mov *.mkv *.webm)", "Все файлы (*)"] : ["Все файлы (*)"]
            onAccepted: quickReplySettings.addFiles(selectedFiles)
        }
        component ReplyText: Label {
            property int typography: Typography.FontUtil.Styles.Body
            font: Typography.FontUtil.getFont(typography, Typography.FontUtil.Sizes.Default)
            lineHeight: Typography.FontUtil.getLineHeight(typography, Typography.FontUtil.Sizes.Default)
            lineHeightMode: Text.FixedHeight
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: quickReplySettings.maxwellColorTokens.colorTextPrimary
        }
        component ReplyButton: Button {
            font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
            implicitHeight: Math.max(UiKit.DesignSystem.SizeBase.px48, contentItem.implicitHeight + 2 * quickReplySettings.gap)
            horizontalPadding: quickReplySettings.gap
            contentItem: ReplyText { text: parent.text; color: parent.enabled ? quickReplySettings.maxwellColorTokens.colorTextThemed : quickReplySettings.maxwellColorTokens.colorTextTertiary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: quickReplySettings.radius; color: quickReplySettings.maxwellColorTokens.colorBackgroundCard }
        }
        contentLoaderItem: UiKit.MeFlickable {
            id: settingsScroll
            objectName: "quickRepliesSettingsScroll"
            contentWidth: width; contentHeight: settingsScrollContent.implicitHeight + 2 * quickReplySettings.sectionGap
            flickableDirection: Flickable.VerticalFlick
            ScrollBar.vertical: UiKit.MeScrollBar {}
            Connections {
                target: quickReplySettings
                function onEditorOpened() { Qt.callLater(function() { commandInput.forceActiveFocus(); var viewport = settingsScroll; var position = commandInput.mapToItem(viewport.contentItem, 0, 0); viewport.contentY = Math.max(0, Math.min(viewport.contentHeight - viewport.height, position.y - quickReplySettings.gap)); }); }
            }
            ColumnLayout {
                id: settingsScrollContent
                y: quickReplySettings.sectionGap
                width: settingsScroll.width; spacing: quickReplySettings.sectionGap
                ReplyText { Layout.fillWidth: true; text: "Сохрани ответ, затем введи / в чате. Текст и файлы сразу появятся в черновике; каждое сообщение отправляешь вручную."; color: quickReplySettings.maxwellColorTokens.colorTextSecondary }
                ReplyText { Layout.fillWidth: true; text: "Настраиваешь аккаунт: " + quickReplySettings.currentAccountId; typography: Typography.FontUtil.Styles.Description; color: quickReplySettings.maxwellColorTokens.colorTextSecondary }
                ReplyText { objectName: "quickRepliesStatus"; Layout.fillWidth: true; text: quickReplySettings.statusText; typography: Typography.FontUtil.Styles.Description; color: quickReplySettings.hasError ? quickReplySettings.maxwellColorTokens.colorTextAttention : quickReplySettings.maxwellColorTokens.colorTextSecondary }
                RowLayout {
                    Layout.fillWidth: true; spacing: quickReplySettings.gap
                    ReplyButton { objectName: "newQuickReply"; visible: !quickReplySettings.editing; Layout.fillWidth: true; text: "Новый ответ"; enabled: quickReplySettings.loaded && quickReplySettings.pendingRequest === null && quickReplySettings.replies.length < 100; onClicked: quickReplySettings.showEditor(null) }
                    ReplyButton { objectName: "reloadQuickReplies"; Layout.fillWidth: true; text: "Обновить"; enabled: quickReplySettings.pendingRequest === null; onClicked: quickReplySettings.request("GET", "") }
                }
                ReplyText { Layout.fillWidth: true; visible: !quickReplySettings.editing && quickReplySettings.loaded && quickReplySettings.replies.length === 0; text: "Шаблонов пока нет. Например, создай /away с ответом о том, что сейчас недоступен."; color: quickReplySettings.maxwellColorTokens.colorTextSecondary }
                ColumnLayout {
                    visible: !quickReplySettings.editing
                    Layout.fillWidth: true; spacing: UiKit.DesignSystem.SpacingCommon._S
                    Repeater {
                        model: quickReplySettings.visibleReplies
                        delegate: ReplyButton {
                            required property var modelData
                            Layout.fillWidth: true
                            objectName: "editReply_" + modelData.command
                            enabled: quickReplySettings.pendingRequest === null
                            onClicked: quickReplySettings.showEditor(modelData)
                            contentItem: ColumnLayout {
                                spacing: UiKit.DesignSystem.SpacingCommon._2XS
                                ReplyText { Layout.fillWidth: true; text: modelData.title; typography: Typography.FontUtil.Styles.BodyStrong }
                                ReplyText { Layout.fillWidth: true; text: "/" + modelData.command; color: quickReplySettings.maxwellColorTokens.colorTextThemed; typography: Typography.FontUtil.Styles.Description }
                                ReplyText { Layout.fillWidth: true; text: modelData.text; color: quickReplySettings.maxwellColorTokens.colorTextSecondary; typography: Typography.FontUtil.Styles.Description; maximumLineCount: 2; elide: Text.ElideRight }
                                ReplyText { Layout.fillWidth: true; text: quickReplySettings.replySummary(modelData); color: quickReplySettings.maxwellColorTokens.colorTextTertiary; typography: Typography.FontUtil.Styles.Description }
                            }
                        }
                    }
                }
                ColumnLayout {
                    objectName: "quickReplyEditor"
                    visible: quickReplySettings.editing
                    enabled: quickReplySettings.pendingRequest === null
                    Layout.fillWidth: true; spacing: quickReplySettings.gap
                    ReplyText { Layout.fillWidth: true; text: quickReplySettings.editingId ? "Редактирование ответа" : "Новый ответ"; typography: Typography.FontUtil.Styles.BodyStrong }
                    CheckBox { objectName: "quickReplyCurrentAccount"; text: "Только для текущего аккаунта"; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default); palette.windowText: quickReplySettings.maxwellColorTokens.colorTextPrimary; palette.text: quickReplySettings.maxwellColorTokens.colorTextPrimary; checked: quickReplySettings.editorAccountId.length > 0; onToggled: quickReplySettings.editorAccountId = checked ? quickReplySettings.currentAccountId : "" }
                    ReplyText { text: "Команда без /"; typography: Typography.FontUtil.Styles.Description; color: quickReplySettings.maxwellColorTokens.colorTextSecondary }
                    TextField {
                        id: commandInput
                        objectName: "quickReplyCommand"
                        text: quickReplySettings.editorCommand
                        onTextChanged: quickReplySettings.editorCommand = text
                        Layout.fillWidth: true; placeholderText: "away или вне_офиса"; selectByMouse: true
                        font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
                        color: quickReplySettings.maxwellColorTokens.colorTextPrimary
                        placeholderTextColor: quickReplySettings.maxwellColorTokens.colorTextTertiary
                        padding: quickReplySettings.gap
                        background: Rectangle { color: quickReplySettings.maxwellColorTokens.colorBackgroundCard; radius: quickReplySettings.radius; border.color: quickReplySettings.maxwellColorTokens.colorDividerPrimary }
                    }
                    ReplyText { text: "Название"; typography: Typography.FontUtil.Styles.Description; color: quickReplySettings.maxwellColorTokens.colorTextSecondary }
                    TextField {
                        id: titleInput
                        objectName: "quickReplyTitle"
                        text: quickReplySettings.editorTitle
                        onTextChanged: quickReplySettings.editorTitle = text
                        Layout.fillWidth: true; placeholderText: "Сейчас недоступен"; selectByMouse: true
                        font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
                        color: quickReplySettings.maxwellColorTokens.colorTextPrimary
                        placeholderTextColor: quickReplySettings.maxwellColorTokens.colorTextTertiary
                        padding: quickReplySettings.gap
                        background: Rectangle { color: quickReplySettings.maxwellColorTokens.colorBackgroundCard; radius: quickReplySettings.radius; border.color: quickReplySettings.maxwellColorTokens.colorDividerPrimary }
                    }
                    ReplyText { text: "Текст ответа"; typography: Typography.FontUtil.Styles.Description; color: quickReplySettings.maxwellColorTokens.colorTextSecondary }
                    ScrollView {
                        id: replyTextScroll
                        Layout.fillWidth: true; Layout.preferredHeight: 200
                        padding: 0; clip: true; contentWidth: availableWidth
                        background: Rectangle { color: quickReplySettings.maxwellColorTokens.colorBackgroundCard; radius: quickReplySettings.radius; border.color: quickReplySettings.maxwellColorTokens.colorDividerPrimary }
                        TextArea {
                        id: replyInput
                        objectName: "quickReplyText"
                        text: quickReplySettings.editorText
                        onTextChanged: quickReplySettings.editorText = text
                        width: replyTextScroll.availableWidth
                        placeholderText: "Напиши текст, который будет подставляться в сообщение."; selectByMouse: true
                        textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap
                        font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
                        color: quickReplySettings.maxwellColorTokens.colorTextPrimary
                        placeholderTextColor: quickReplySettings.maxwellColorTokens.colorTextTertiary
                        padding: quickReplySettings.gap
                        background: Item {}
                        }
                    }
                    Flow {
                        Layout.fillWidth: true; spacing: quickReplySettings.gap
                        ReplyButton { objectName: "quickReplyAddFiles"; text: "Добавить файлы"; onClicked: quickReplySettings.chooseFiles(-1, "file") }
                        ReplyButton { text: "Добавить фото / видео"; onClicked: quickReplySettings.chooseFiles(-1, "media") }
                    }
                    Repeater {
                        model: quickReplySettings.editorAttachments
                        delegate: RowLayout {
                            required property int index
                            required property var modelData
                            Layout.fillWidth: true
                            ReplyText { Layout.fillWidth: true; text: quickReplySettings.fileName(modelData.url) + (modelData.kind === "media" ? " · фото / видео" : " · файл"); typography: Typography.FontUtil.Styles.Description }
                            ReplyButton { text: "Убрать"; onClicked: quickReplySettings.removeFile(-1, index) }
                        }
                    }
                    Repeater {
                        model: quickReplySettings.editorParts
                        delegate: ColumnLayout {
                            id: partEditor
                            required property int index
                            required property var modelData
                            Layout.fillWidth: true; spacing: quickReplySettings.gap
                            ReplyText { text: "Сообщение " + (partEditor.index + 2); typography: Typography.FontUtil.Styles.BodyStrong }
                            TextArea { objectName: "quickReplyPart_" + partEditor.index; Layout.fillWidth: true; implicitHeight: 120; text: partEditor.modelData.text; onTextChanged: { if (text !== partEditor.modelData.text) quickReplySettings.changePart(partEditor.index, text); } placeholderText: "Следующая заготовка — отправляется вручную"; font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default); padding: quickReplySettings.gap; placeholderTextColor: quickReplySettings.maxwellColorTokens.colorTextTertiary; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; selectByMouse: true; color: quickReplySettings.maxwellColorTokens.colorTextPrimary; background: Rectangle { color: quickReplySettings.maxwellColorTokens.colorBackgroundCard; radius: quickReplySettings.radius } }
                            Flow { Layout.fillWidth: true; spacing: quickReplySettings.gap
                                ReplyButton { text: "Добавить файлы"; onClicked: quickReplySettings.chooseFiles(partEditor.index, "file") }
                                ReplyButton { text: "Добавить фото / видео"; onClicked: quickReplySettings.chooseFiles(partEditor.index, "media") }
                                ReplyButton { text: "Убрать сообщение"; onClicked: quickReplySettings.removePart(partEditor.index) }
                            }
                            Repeater { model: partEditor.modelData.attachments || []; delegate: RowLayout {
                                required property int index
                                required property var modelData
                                Layout.fillWidth: true
                                ReplyText { Layout.fillWidth: true; text: quickReplySettings.fileName(modelData.url); typography: Typography.FontUtil.Styles.Description }
                                ReplyButton { text: "Убрать"; onClicked: quickReplySettings.removeFile(partEditor.index, index) }
                            } }
                        }
                    }
                    ReplyButton { objectName: "quickReplyAddPart"; text: "Добавить следующее сообщение"; enabled: quickReplySettings.editorParts.length < 9; onClicked: quickReplySettings.addPart() }
                    ReplyText { Layout.fillWidth: true; text: "Файлы остаются на своём месте. Если удалить или перенести файл, добавь его в заготовку заново."; typography: Typography.FontUtil.Styles.Description; color: quickReplySettings.maxwellColorTokens.colorTextSecondary }
                    ReplyText { visible: quickReplySettings.deleteConfirmation; Layout.fillWidth: true; text: "Удалить /" + commandInput.text + " с этого компьютера?"; color: quickReplySettings.maxwellColorTokens.colorTextAttention }
                    Flow {
                        Layout.fillWidth: true; spacing: quickReplySettings.gap
                        ReplyButton { objectName: "saveQuickReply"; text: "Сохранить"; enabled: quickReplySettings.pendingRequest === null; onClicked: quickReplySettings.saveReply(false) }
                        ReplyButton { objectName: "cancelQuickReply"; text: "Отмена"; enabled: quickReplySettings.pendingRequest === null; onClicked: { quickReplySettings.editing = false; quickReplySettings.deleteConfirmation = false; } }
                        ReplyButton { objectName: "deleteQuickReply"; visible: quickReplySettings.editingId.length > 0; text: quickReplySettings.deleteConfirmation ? "Подтвердить удаление" : "Удалить"; enabled: quickReplySettings.pendingRequest === null; onClicked: { if (quickReplySettings.deleteConfirmation) quickReplySettings.saveReply(true); else quickReplySettings.deleteConfirmation = true; } }
                    }
                }
                ReplyText { Layout.fillWidth: true; text: "До 100 ответов, до 10 сообщений в ответе. Для каждого сообщения — до 5000 символов и 10 файлов. Общие заготовки доступны всем аккаунтам; личные — только выбранному. Они хранятся на этом компьютере."; typography: Typography.FontUtil.Styles.Description; color: quickReplySettings.maxwellColorTokens.colorTextSecondary }
            }
        }
    }
}
