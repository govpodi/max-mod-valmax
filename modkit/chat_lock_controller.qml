import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import UiKitModule as UiKit
import ClientAppearanceModule as ClientAppearance
import TypographyModule as Typography
import MeCustomText as MeCustomText

Item {
    id: root
    objectName: "valmaxChatLockController"
    property var hostItem: null
    property var colorTokens: ({})
    property string endpointBase: "http://127.0.0.1:18973"
    property bool ready: false
    property bool busy: false
    property var catalog: ({})
    property var denied: ({})
    property var grantUntil: ({})
    property double clockMs: Date.now()
    property int revision: 0
    property int scopeGeneration: 0
    property string currentAccountId: ""
    property string currentChatId: ""
    readonly property bool hasLocks: Object.keys(catalog).length > 0
    property bool catalogPending: false
    property bool lockAllPending: false
    property string dialogAccountId: ""
    property string dialogChatId: ""
    property string dialogTitle: ""
    property string dialogMode: "unlock"
    property string errorText: ""
    property var catalogRequest: null
    property var mutationRequest: null

    function tone(name, fallback) { return colorTokens && colorTokens[name] ? colorTokens[name] : fallback; }
    function accountKey(value) {
        const s = typeof value === "string" ? value : "";
        return /^[1-9][0-9]{0,18}$/.test(s) && (s.length < 19 || s <= "9223372036854775807") ? s : "";
    }
    function chatKey(value) {
        const s = typeof value === "string" ? value : "", n = s.startsWith("-") ? s.slice(1) : s;
        return /^(?:0|-?[1-9][0-9]{0,18})$/.test(s) && (n.length < 19 || n <= (s.startsWith("-") ? "9223372036854775808" : "9223372036854775807")) ? s : "";
    }
    function key(account, chat) { const a = accountKey(account), c = chatKey(chat); return a && c ? a + ":" + c : ""; }
    function isProtected(account, chat) { const r = revision, k = key(account, chat); return k !== "" && catalog[k] !== undefined; }
    function isLocked(account, chat) {
        const r = revision, k = key(account, chat);
        return !ready || !k || (catalog[k] !== undefined &&
            (catalog[k] === true || denied[k] === true || !(grantUntil[k] > clockMs)));
    }
    function maskScope(account, chat) {
        const r = revision;
        if (!ready || !accountKey(account)) return true;
        return chatKey(chat) !== "" && isLocked(account, chat);
    }
    function deny(account, chat) {
        const k = key(account, chat); if (!k) return;
        const next = Object.assign({}, denied); next[k] = true;
        for (const old of Object.keys(next)) {
            if (Object.keys(next).length <= 512) break;
            if (old !== k && catalog[old] === undefined) delete next[old];
        }
        denied = next; revision += 1;
    }
    function clearSecrets() { pin.clear(); confirmation.clear(); }
    function setCurrentScope(account, chat) {
        const a = accountKey(account), c = chatKey(chat);
        if (a === currentAccountId && c === currentChatId) return;
        const oldA = currentAccountId, oldC = currentChatId;
        deny(oldA, oldC);
        scopeGeneration += 1; clearSecrets(); pinDialog.close();
        currentAccountId = a; currentChatId = c;
        if (key(oldA, oldC) && isProtected(oldA, oldC)) request("POST", "lock", {schema:1,accountId:oldA,chatId:oldC}, function(ok) { if (!ok) root.ready = false; });
    }
    function releaseAccount(account) {
        if (accountKey(account) === currentAccountId) setCurrentScope("", "");
    }
    function errorFor(code) {
        const texts = {invalid_pin:"Введите от 6 до 12 цифр; при создании оба PIN должны совпадать.",
            wrong_pin:"Неверный PIN.",rate_limited:"Слишком много попыток. Подождите и попробуйте снова.",
            already_protected:"Этот чат уже защищён.",not_protected:"PIN для этого чата уже отключён.",
            limit_reached:"Достигнут предел защищённых чатов.",store_busy:"Хранилище занято. Попробуйте ещё раз.",
            protector_unavailable:"Защищённое хранилище Windows сейчас недоступно.",
            notification_unavailable:"Не удалось включить приватные уведомления. PIN не установлен. Повторите попытку."};
        return texts[code] || "Не удалось проверить PIN. Чат остаётся закрытым.";
    }
    function applyCatalog(value) {
        if (!value || value.schema !== 1 || value.ready !== true || !Array.isArray(value.protected) || value.protected.length > 256) return false;
        const next = {};
        for (const item of value.protected) {
            if (!item || Object.keys(item).sort().join(",") !== "accountId,chatId,locked" || typeof item.locked !== "boolean") return false;
            const k = key(item.accountId, item.chatId);
            if (!k || next[k] !== undefined) return false;
            next[k] = item.locked;
        }
        const oldKeys = Object.keys(catalog), newKeys = Object.keys(next);
        const changed = oldKeys.length !== newKeys.length || newKeys.some(k => catalog[k] !== next[k]);
        if (changed) {
            catalog = next;
            revision += 1;
        }
        ready = true;
        return true;
    }
    function applyStatus(value, account, chat, allowGrant, expiry) {
        if (!value || value.schema !== 1 || value.ready !== true || value.accountId !== account || value.chatId !== chat ||
                typeof value.protected !== "boolean" || typeof value.locked !== "boolean" || (!value.protected && value.locked)) return false;
        const k = key(account, chat); if (!k) return false;
        const next = Object.assign({}, catalog), local = Object.assign({}, denied), grants = Object.assign({}, grantUntil);
        if (value.protected) {
            next[k] = value.locked;
            if (allowGrant && !value.locked) { delete local[k]; grants[k] = expiry; }
            else if (!allowGrant) local[k] = true;
        } else { delete next[k]; delete local[k]; delete grants[k]; }
        catalog = next; denied = local; grantUntil = grants; clockMs=Date.now(); revision += 1; return true;
    }
    function request(method, action, payload, completed) {
        let finished = false;
        let deadline = null;
        function complete(ok, value) {
            if (finished) return;
            finished = true;
            if (deadline) { deadline.stop(); deadline.destroy(); deadline=null; }
            completed(ok, value);
        }
        const xhr = new XMLHttpRequest();
        xhr.open(method, endpointBase + (method === "GET" ? "/chat-locks" : "/chat-locks/" + action));
        if (method === "POST") {
            xhr.setRequestHeader("Content-Type", "application/json");
            xhr.setRequestHeader("X-Max-Mod-Local-Write", "chat-locks-v1");
        }
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE || finished) return;
            let value = null;
            try { if (xhr.responseText.length <= 262144) value = JSON.parse(xhr.responseText); } catch (_) {}
            complete(xhr.status === 200 && value !== null, value);
        };
        deadline = requestDeadlineComponent.createObject(root, {expire:function(){complete(false,null);xhr.abort();}});
        if (!deadline) { complete(false,null); return xhr; }
        deadline.start();
        try { let raw = payload ? JSON.stringify(payload) : ""; xhr.send(raw); raw = ""; payload = null; }
        catch (_) { complete(false, null); }
        return xhr;
    }
    function refreshCatalog() {
        if (catalogPending || busy) return;
        catalogPending = true;
        catalogRequest = request("GET", "", null, function(ok, value) {
            catalogDeadline.stop(); root.catalogRequest = null; root.catalogPending = false;
            if (!ok || !root.applyCatalog(value)) root.ready = false;
        });
        if (catalogPending) catalogDeadline.restart();
    }
    function lockAll() {
        scopeGeneration += 1; clearSecrets(); pinDialog.close();
        const next = Object.assign({}, denied);
        for (const k of Object.keys(catalog)) next[k] = true;
        denied = next; grantUntil = ({}); revision += 1;
        if (lockAllPending) return;
        lockAllPending = true;
        request("POST", "lock-all", {schema:1}, function(ok, value) {
            root.lockAllPending = false;
            if (!ok || !root.applyCatalog(value)) root.ready = false;
        });
    }
    function openSettings(account, chat, title) {
        if (!ready || busy || !key(account, chat)) return false;
        dialogAccountId = account; dialogChatId = chat;
        dialogTitle = isProtected(account, chat) && isLocked(account, chat) ? "Чат защищён PIN" :
            (typeof title === "string" ? title.slice(0, 128) : "");
        dialogMode = isProtected(account, chat) ? "manage" : "set";
        clearSecrets(); errorText = ""; pinDialog.open(); return true;
    }
    function openUnlock(account, chat) {
        if (!ready || busy || !key(account, chat) || !isProtected(account, chat)) return false;
        dialogAccountId = account; dialogChatId = chat; dialogTitle = ""; dialogMode = "unlock";
        clearSecrets(); errorText = ""; pinDialog.open(); return true;
    }
    function lockSelected() {
        const a = dialogAccountId, c = dialogChatId;
        deny(a, c); scopeGeneration += 1; pinDialog.close(); clearSecrets();
        request("POST", "lock", {schema:1,accountId:a,chatId:c}, function(ok) { if (!ok) root.ready = false; });
    }
    function submitPin() {
        if (busy || !ready || !key(dialogAccountId, dialogChatId) || ["set","unlock","remove"].indexOf(dialogMode) < 0) return;
        if (!/^[0-9]{6,12}$/.test(pin.text) || (dialogMode === "set" && pin.text !== confirmation.text)) { errorText = errorFor("invalid_pin"); clearSecrets(); return; }
        const a = dialogAccountId, c = dialogChatId, action = dialogMode, generation = scopeGeneration, expiry = Date.now() + 120000;
        const payload = {schema:1,accountId:a,chatId:c,pin:pin.text};
        if (action === "set") payload.confirmPin = confirmation.text;
        clearSecrets(); busy = true; errorText = "";
        mutationRequest = request("POST", action, payload, function(ok, value) {
            mutationDeadline.stop(); root.mutationRequest = null; root.busy = false;
            if (generation !== root.scopeGeneration) {
                root.deny(a,c);
                request("POST","lock",{schema:1,accountId:a,chatId:c},function(ok) {if (!ok) root.ready=false;});
                root.refreshCatalog(); return;
            }
            if (!ok) {
                root.deny(a,c); root.errorText = root.errorFor(value && value.error);
                if (!value || !value.error) root.ready = false;
                return;
            }
            const sameScope = a === root.currentAccountId && c === root.currentChatId;
            if (!root.applyStatus(value, a, c, sameScope, expiry)) { root.ready = false; root.errorText = root.errorFor(""); return; }
            if (value.protected && !sameScope) request("POST","lock",{schema:1,accountId:a,chatId:c},function(ok) {if (!ok) root.ready=false;});
            pinDialog.close(); root.clearSecrets();
        });
        payload.pin = ""; if (payload.confirmPin !== undefined) payload.confirmPin = "";
        if (busy) mutationDeadline.restart();
    }

    Component.onCompleted: lockAll()
    Component {
        id: requestDeadlineComponent
        Timer { property var expire; interval:8000; repeat:false; onTriggered:if (expire) expire() }
    }
    Timer { interval: 2000; repeat: true; running: root.hasLocks; onTriggered: root.refreshCatalog() }
    Timer { interval: 250; repeat: true; running: root.hasLocks && Object.keys(root.grantUntil).length > 0; onTriggered: root.clockMs=Date.now() }
    Timer { id: catalogDeadline; interval: 8000; onTriggered: { root.ready=false; if (root.catalogRequest) root.catalogRequest.abort(); root.catalogPending=false; } }
    Timer { id: mutationDeadline; interval: 10000; onTriggered: { root.ready=false; root.scopeGeneration+=1; root.clearSecrets(); if (root.mutationRequest) root.mutationRequest.abort(); root.busy=false; } }

    // Keep the PIN buffers outside the dialog's lazily created Component.
    // The native inputs mirror these RAM-only buffers; existing lifecycle
    // functions clear them on every close, attempt and scope change.
    QtObject {
        id: pin
        objectName: "valmaxChatPinSecret"
        property string text: ""
        property var editor: null
        function clear() { text = ""; if (editor) editor.clear(); }
        function forceActiveFocus() { if (editor) editor.forceActiveFocus(); }
    }
    QtObject {
        id: confirmation
        objectName: "valmaxChatPinConfirmation"
        property string text: ""
        property var editor: null
        function clear() { text = ""; if (editor) editor.clear(); }
        function forceActiveFocus() { if (editor) editor.forceActiveFocus(); }
    }
    readonly property var pinAppearance: colorTokens && colorTokens.colorTextPrimary ? colorTokens : ClientAppearance.AppearanceTokens

    component PinField: UiKit.MeInputBackground {
        id: field
        required property var buffer
        property string placeholderText
        signal accepted()
        mode: UiKit.DesignSystem.Mode.Secondary
        maxwellColorTokens: root.pinAppearance
        implicitHeight: UiKit.DesignSystem.SizeBase.px52
        contentItem: UiKit.MeLineEdit {
            id: nativeInput
            objectName: field.buffer.objectName + "Input"
            focus: true
            maxwellColorTokens: field.maxwellColorTokens
            placeholderText: field.placeholderText
            maximumLength: 12
            echoMode: MeCustomText.Property.EchoMode.Password
            onAccepted: field.accepted()
            onTextChanged: {
                const digits = text.replace(/[^0-9]/g, "").slice(0, 12);
                if (digits !== text) text = digits;
                if (field.buffer.text !== digits) field.buffer.text = digits;
            }
            Binding { target: nativeInput; property: "text"; value: field.buffer.text }
            Component.onCompleted: field.buffer.editor = nativeInput
            Component.onDestruction: if (field.buffer.editor === nativeInput) field.buffer.editor = null
        }
    }

    UiKit.BaseModalDialog {
        id: pinDialog
        objectName: "valmaxChatPinDialog"
        parent: root.hostItem
        chatAppearance: root.pinAppearance
        minimumWidth: Math.min(420, parent ? parent.width - 2 * margins : 420)
        margins: UiKit.DesignSystem.SpacingCommon._L
        buttonsHorizontal: width >= 380
        closePolicy: Popup.CloseOnEscape
        title: root.dialogMode === "set" ? "PIN-код чата" : root.dialogMode === "manage" ? "Защита чата" : root.dialogMode === "remove" ? "Убрать PIN" : "Введите PIN"
        subtitle: root.dialogTitle
        confirmText: root.dialogMode === "manage" ? "" : root.dialogMode === "set" ? "Установить PIN" : root.dialogMode === "remove" ? "Убрать PIN" : "Открыть чат"
        cancelText: root.dialogMode === "manage" ? "Готово" : "Отмена"
        confirmButtonEnabled: !root.busy && root.ready
        confirmButtonLoading: root.busy
        useNegativeConfirmMode: root.dialogMode === "remove"
        onConfirmClicked: root.submitPin()
        onCancelClicked: close()
        onCloseClicked: close()
        onClosed: {
            if (root.busy) { root.scopeGeneration += 1; root.deny(root.dialogAccountId,root.dialogChatId); }
            root.clearSecrets(); root.errorText = "";
        }
        onOpened: if (root.dialogMode !== "manage") pin.forceActiveFocus()
        dialogContentItem: ColumnLayout {
            spacing: UiKit.DesignSystem.SpacingCommon._L
            UiKit.MeText {
                Layout.fillWidth: true
                visible: root.dialogMode !== "manage"
                chatAppearance: root.pinAppearance
                typography: Typography.FontUtil.Styles.Body
                text: root.dialogMode === "set" ? "Придумайте PIN от 6 до 12 цифр." : root.dialogMode === "remove" ? "Введите PIN, чтобы отключить защиту этого чата." : "Введите PIN, чтобы открыть переписку."
                useLinkFormats: false
            }
            PinField {
                Layout.fillWidth: true
                visible: root.dialogMode !== "manage"
                enabled: !root.busy
                buffer: pin
                placeholderText: "PIN-код"
                onAccepted: {
                    if (root.dialogMode === "set") confirmation.forceActiveFocus();
                    else root.submitPin();
                }
            }
            PinField {
                Layout.fillWidth: true
                visible: root.dialogMode === "set"
                enabled: !root.busy
                buffer: confirmation
                placeholderText: "Повторите PIN"
                onAccepted: root.submitPin()
            }
            UiKit.MeHintLine {
                Layout.fillWidth: true
                visible: root.errorText !== ""
                maxwellColorTokens: root.pinAppearance
                chatAppearance: root.pinAppearance
                hintType: UiKit.DesignSystem.HintType.Error
                text: root.errorText
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: root.dialogMode === "manage"
                spacing: UiKit.DesignSystem.SpacingCommon._XS
                UiKit.MeCellAction {
                    Layout.fillWidth: true
                    maxwellColorTokens: root.pinAppearance
                    compact: true
                    captionText: "Открыть чат"
                    trailingRightShevronVisible: false
                    appearance: UiKit.DesignSystem.Appearance.Themed
                    backgroundRadius: UiKit.DesignSystem.BorderRadiusCommon._L
                    onClicked: { root.dialogMode = "unlock"; pin.forceActiveFocus(); }
                }
                UiKit.MeCellAction {
                    Layout.fillWidth: true
                    maxwellColorTokens: root.pinAppearance
                    compact: true
                    captionText: "Закрыть чат"
                    trailingRightShevronVisible: false
                    enabled: !root.isLocked(root.dialogAccountId, root.dialogChatId)
                    backgroundRadius: UiKit.DesignSystem.BorderRadiusCommon._L
                    onClicked: root.lockSelected()
                }
                UiKit.MeCellAction {
                    Layout.fillWidth: true
                    maxwellColorTokens: root.pinAppearance
                    compact: true
                    captionText: "Убрать PIN"
                    trailingRightShevronVisible: false
                    appearance: UiKit.DesignSystem.Appearance.Negative
                    backgroundRadius: UiKit.DesignSystem.BorderRadiusCommon._L
                    onClicked: { root.dialogMode = "remove"; pin.forceActiveFocus(); }
                }
            }
            UiKit.MeText {
                Layout.fillWidth: true
                chatAppearance: root.pinAppearance
                typography: Typography.FontUtil.Styles.Description
                color: root.pinAppearance.colorTextSecondary
                useLinkFormats: false
                text: root.dialogMode === "set" ? "Только на этом компьютере. PIN не шифрует переписку." : "PIN закрывает чат в ValMAX. Уведомления приходят без имени, фото и текста."
            }
        }
    }
}
