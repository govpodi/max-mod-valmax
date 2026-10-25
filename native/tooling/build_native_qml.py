"""Explicit native-runtime QML build mode; legacy generators remain unchanged.

Uses immutable baseline bytes and existing pinned transforms. Writes only a
fresh caller-specified build directory. Does not patch or launch any client.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import pprint
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "modkit"))


def sha(data):
    return hashlib.sha256(data).hexdigest()


SETTINGS_PAGES = {
    "settingsValMAXOptimization": ("optimization", "optimization-settings"),
    "settingsValMAXGhost": ("ghost", "ghost-settings"),
    "settingsValMAXAccounts": ("accounts", "account-settings"),
    "settingsModQuickReplies": ("quick_replies", "quick-replies"),
}

ACCOUNT_READERS = {
    "AccountsStackView.qml": 5000,
    "SettingsAccountSelectorDelegate.qml": 3000,
    "ProfileInfoCell.qml": 3000,
    "AccountButton.qml": 3000,
}


def native_account_reader(source: str, *, interval: int) -> str:
    """Use the existing accounts adapter; preserve ID, schedule and view logic."""
    from account_preferences_ui import reader
    original = reader(interval=interval)
    if source.count(original) != 1:
        raise ValueError("Native account reader anchor changed")
    native = original.replace("new XMLHttpRequest()", 'ValMAXNative.SettingsTransport.createRequest("accounts")')
    native = native.replace("XMLHttpRequest.DONE", "4").replace(
        'xhr.open("GET", "http://127.0.0.1:18973/account-settings");', 'xhr.open("GET");')
    source = source.replace(original, native, 1)
    imported = "import ValMAX.Native 1.0 as ValMAXNative\n"
    if source.count(imported) == 0 and source.count("import QtQuick\n") == 1:
        source = source.replace("import QtQuick\n", "import QtQuick\n" + imported, 1)
    elif source.count(imported) != 1:
        raise ValueError("Native account reader imports changed")
    return source


def native_account_sources(sources: dict[str, bytes]) -> dict[str, bytes]:
    prefix = ":/qt/qml/OneMeUi/"
    required = set(ACCOUNT_READERS) - {"AccountButton.qml"}
    if any(prefix + name not in sources for name in required):
        raise ValueError("Native account reader resource missing")
    result = dict(sources)
    for name, interval in ACCOUNT_READERS.items():
        key = prefix + name
        if key in result:
            result[key] = native_account_reader(result[key].decode("utf8"), interval=interval).encode("utf8")
    # Any new account reader must join this transform rather than silently
    # depending on the removed local service in the native build.
    if any(b"http://127.0.0.1:18973/account-settings" in value for value in result.values()):
        raise ValueError("Legacy account settings transport remained")
    return result


def native_settings_panel(panel: str, family: str, endpoint: str) -> str:
    """Replace only transport in a known page; validation and UI stay intact."""
    write_headers = ('            if (method === "POST") {\n'
                     '                xhr.setRequestHeader("Content-Type", "application/json");\n'
                     '                xhr.setRequestHeader("X-Max-Mod-Local-Write", "quick-replies-v1");\n'
                     '            }') if family == "quick_replies" else (
                     '            if (method === "POST") { xhr.setRequestHeader("Content-Type", "application/json"); xhr.setRequestHeader("X-Max-Mod-Local-Write", "' + endpoint + '-v1"); }')
    replacements = (
        ("new XMLHttpRequest()", 'ValMAXNative.SettingsTransport.createRequest("' + family + '")'),
        ("XMLHttpRequest.DONE", "4"),
        ('xhr.open(method, "http://127.0.0.1:18973/' + endpoint + '");', "xhr.open(method);"),
        (write_headers, ""),
    )
    for old, new in replacements:
        if panel.count(old) != 1:
            raise ValueError("Native settings transport anchor changed: " + family)
        panel = panel.replace(old, new, 1)
    for old, new in (("Помощник ValMAX", "Модуль настроек ValMAX"),
                     ("помощника", "модуля настроек"), ("помощником", "модулем настроек"),
                     ("нужен работающий помощник мода", "нужны подключённые нативные фильтры")):
        panel = panel.replace(old, new)
    return panel


def native_quick_replies_declarations(source: str) -> str:
    """Transport-only patch; stock draft/attachment/manual-send logic is intact."""
    for old, new in (
        ("new XMLHttpRequest()", 'ValMAXNative.SettingsTransport.createRequest("quick_replies")'),
        ("XMLHttpRequest.DONE", "4"),
        ('xhr.open("GET", "http://127.0.0.1:18973/quick-replies");', 'xhr.open("GET");'),
    ):
        if source.count(old) != 1:
            raise ValueError("Native quick-reply composer anchor changed")
        source = source.replace(old, new, 1)
    return source


def native_quick_replies_source(source: str) -> str:
    if source.count("import QtQuick\n") != 1:
        raise ValueError("Native quick-reply imports changed")
    source = source.replace("import QtQuick\n", "import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\n", 1)
    begin = "property var modQuickReplyRows: []\n"
    end = ('    if (modQuickRepliesRequest !== null) { modQuickRepliesRequest.onreadystatechange = function() {}; modQuickRepliesRequest.abort(); }\n'
           '}\n')
    if source.count(begin) != 1 or source.count(end) != 1:
        raise ValueError("Native quick-reply declarations changed")
    start, finish = source.index(begin), source.index(end) + len(end)
    if finish <= start:
        raise ValueError("Native quick-reply declaration order changed")
    return source[:start] + native_quick_replies_declarations(source[start:finish]) + source[finish:]


def native_inline_media_source(source: str) -> str:
    """Pinned composer replacement; native owns network, files and draft invoke."""
    if source.count("import ValMAX.Native 1.0 as ValMAXNative\n") != 1:
        raise ValueError("Native media requires preceding native composer import")
    legacy = (PROJECT / "modkit/inline_media_composer.qml").read_text(encoding="utf8").replace("\r\n", "\n")
    declarations, menu = legacy.split("// BEGIN INLINE MEDIA MENU", 1)
    menu, suffix = menu.split("// END INLINE MEDIA MENU", 1)
    if suffix.strip() or source.count(declarations) != 1 or source.count(menu.strip()) != 1:
        raise ValueError("Native inline media declaration/menu anchor changed")
    native = (PROJECT / "native/qt/InlineMediaComposer.qml").read_text(encoding="utf8").replace("\r\n", "\n")
    source = source.replace(declarations, native + "\n", 1)
    changed_menu = menu.strip()
    replacements = (
        ('source: modelData.preview ? "http://127.0.0.1:18973/inline-media/preview?id=" + modelData.id : ""; asynchronous: true;',
         'source: typeof modelData.previewSource === "string" && modelData.previewSource.indexOf("image://valmaxmedia/") === 0 ? modelData.previewSource : ""; cache: false; asynchronous: true;'),
        ('enabled: !root.modInlineBusy && !(root.modInlineRetryRoute === "search" && root.modInlineRetrySeconds > 0)',
         'enabled: modNativeMedia.available && !root.modInlineBusy && root.modInlineRetrySeconds === 0'),
        ('enabled: !root.modInlineBusy && !(root.modInlineRetryRoute === "select" && root.modInlineRetrySeconds > 0)',
         'enabled: modNativeMedia.available && !root.modInlineBusy && root.modInlineRetrySeconds === 0'),
        ('Файл добавится в черновик; отправка вручную.',
         'Выбранный файл передаётся в черновик; отправка вручную.'),
    )
    for old, new in replacements:
        if changed_menu.count(old) != 1:
            raise ValueError("Native inline media menu element changed")
        changed_menu = changed_menu.replace(old, new, 1)
    source = source.replace(menu.strip(), changed_menu, 1)
    for anchor in (
        'onCurrentOpenedChatIdChanged: { modQuickReplySelected = null; modInlineReset(true); }',
        'onModQuickReplyAccountIdChanged: { modQuickReplySelected = null; modInlineReset(true); }',
        'Component.onDestruction: {\n    modInlineReset(false);',
    ):
        if source.count(anchor) != 1:
            raise ValueError("Native inline media lifecycle anchor changed")
    if "http://127.0.0.1:18973/inline-media" in source or "function modInlineRequestJson" in source:
        raise ValueError("Legacy inline media transport remained")
    return source


def native_support_panel(panel: str) -> str:
    """Keep the actual support page and explicit SaveFile action; replace HTTP."""
    for old, new in (
        ("new XMLHttpRequest()", "ValMAXNative.SupportTransport.createRequest()"),
        ("XMLHttpRequest.DONE", "4"),
        ('xhr.open(method, "http://127.0.0.1:18973/support-diagnostics");', 'xhr.open(method);'),
        ('                xhr.setRequestHeader("Content-Type", "application/json");\n'
         '                xhr.setRequestHeader("X-Max-Mod-Local-Write", "support-diagnostics-v1");\n', ""),
        ('if (!value.report || value.report.schema !== 1)', 'if (!value.report || value.report.schema !== 2)'),
        ("Локальный помощник недоступен. Опиши ошибку без отчёта или перезапусти ValMAX обычным ярлыком.",
         "Нативный модуль недоступен. Опиши ошибку без отчёта или перезапусти ValMAX обычным ярлыком."),
        ("Помощник не ответил вовремя. Проверь выбранную папку перед повторным сохранением.",
         "Модуль не ответил вовремя. Проверь выбранную папку перед повторным сохранением."),
        ("В файл входят только версии программ, известный код состояния запуска, переключатели режима призрака и оптимизации, общие счётчики и коды ошибок наблюдения.",
         "В файл входят доступные версии программ, результат проверки приватности и переключатели режима призрака и оптимизации. Недоступные сведения отмечены null."),
        ("Счётчики меняются в текущем запуске; сохранённые переключатели могут требовать перезапуска.",
         "Сохранённые переключатели могут требовать перезапуска; они не подтверждают работу фильтров."),
    ):
        if panel.count(old) != 1:
            raise ValueError("Native support transport anchor changed")
        panel = panel.replace(old, new, 1)
    return panel


def native_mdata_panel(panel: str) -> str:
    """In-process export; explicit sign-in handoff to the native helper."""
    for old, new in (
        ('        property bool available: false\n',
         '        property bool available: false\n        property bool exportAvailable: false\n'),
        ('                file_exists: "Файл уже существует. Выбери другое имя.",\n',
         '                file_exists: "Файл уже существует. Выбери другое имя.",\n'
         '                outcome_unknown: "Результат не подтверждён. Операция могла завершиться; проверь выбранный файл перед повторной попыткой.",\n'),
        ('if (busy || !available || activeDialog !== null) return;',
         'if (busy || !(action === "export" ? exportAvailable : available) || activeDialog !== null) return;'),
        ('if (busy || !available) return;',
         'if (busy || !(dialog.transferAction === "export" ? exportAvailable : available)) return;'),
        ('                        available = value.available;\n',
         '                        available = value.available;\n                        exportAvailable = value.exportAvailable === true;\n'),
        ('if (!available) { showError("Сервис MData недоступен. Запусти MAX Mod обычным ярлыком.", activeDialog); return; }',
         'if (!available && !exportAvailable) { showError("Модуль MData недоступен. Перезапусти ValMAX обычным ярлыком.", activeDialog); return; }'),
        ('"Создай MData для переноса входа или войди с готовой MData."',
         '"Создай MData для переноса входа или открой отдельное окно входа с MData."'),
        ('(!mdataPage.available || mdataPage.operationState === "waiting_for_exit" || mdataPage.operationState === "working")',
         '((!mdataPage.available && !mdataPage.exportAvailable) || mdataPage.operationState === "waiting_for_exit" || mdataPage.operationState === "working")'),
        ('text: "Создать MData"; enabled: mdataPage.available && !mdataPage.busy;',
         'text: "Создать MData"; enabled: mdataPage.exportAvailable && !mdataPage.busy;'),
        ("new XMLHttpRequest()", "ValMAXNative.MDataTransport.createRequest()"),
        ("XMLHttpRequest.DONE", "4"),
        ('xhr.open(method, "http://127.0.0.1:18973" + (route || "/mdata"));',
         'xhr.open(method, route || "/mdata");'),
        ('            if (method === "POST") { xhr.setRequestHeader("Content-Type", "application/json"); xhr.setRequestHeader("X-Max-Mod-Local-Write", "mdata-v1"); }\n', ""),
    ):
        if panel.count(old) != 1:
            raise ValueError("Native MData transport anchor changed")
        panel = panel.replace(old, new, 1)
    panel = panel.replace('        id: mdataPage\n',
                          '        id: mdataPage\n        ValMAXNative.MDataTransferEntry { id: mdataHandoff }\n', 1)
    start, end = '        function submit(dialog) {\n', '        function chooseImport()'
    if panel.count(start) != 1 or panel.count(end) != 1:
        raise ValueError("Native MData submit anchor changed")
    panel = panel[:panel.index(start)] + '''        function submit(dialog) {
            if (busy || !exportAvailable || dialog.transferAction !== "export") return;
            if (!/^file:\\/\\/\\//.test(dialog.selectedFile) || !/\\.mdata$/i.test(dialog.selectedFile)) { showError("Выбери локальный файл с расширением .mdata.", dialog); return; }
            var password = dialog.secret, bytes;
            try { bytes = encodeURIComponent(password).replace(/%[0-9a-f]{2}|./gi, "x").length; }
            catch (_) { bytes = -1; }
            if (bytes < 12 || bytes > 1024 || password.indexOf("\\u0000") >= 0) { password = ""; showError("Используй пароль от 12 до 1024 байт UTF-8.", dialog); return; }
            if (dialog.secret !== dialog.confirmation) { password = ""; showError("Пароли не совпадают. Введи их заново.", dialog); return; }
            const selected = dialog.exportAccountIds.length > 0;
            var body = JSON.stringify(selected
                ? {schema: 1, file: dialog.selectedFile, password: password, accountIds: dialog.exportAccountIds}
                : {schema: 1, action: "export", file: dialog.selectedFile, password: password, mode: "restore"});
            password = ""; dialog.clearSecrets();
            request("POST", body, "export", dialog, selected ? "/mdata/export-selected" : "/mdata"); body = "";
        }
''' + panel[panel.index(end):]
    old = '        function chooseImport() { if (!busy && available) loadFileDialog.open(); }'
    if panel.count(old) != 1:
        raise ValueError("Native MData import action anchor changed")
    panel = panel.replace(old, '        function chooseImport() { if (!busy) mdataHandoff.open(); }', 1)
    block = '''        Dialogs.FileDialog {
            id: loadFileDialog
            title: "Войти с MData"
            fileMode: Dialogs.FileDialog.OpenFile
            nameFilters: ["MData и файл сессии (*.mdata *.maxsession)"]
            onAccepted: mdataPage.openPasswordDialog("import", selectedFile.toString())
        }
'''
    if panel.count(block) != 1:
        raise ValueError("Native MData old import picker anchor changed")
    panel = panel.replace(block, "", 1)
    panel = panel.replace('onClicked: loadFileDialog.open()', 'onClicked: mdataPage.chooseImport()', 1)
    begin = '                    if (action !== "import" || value.state !== "waiting_for_exit") throw new Error("import ack");\n'
    finish = '                    Qt.callLater(function() { appDelegate.closeMessenger(); });\n'
    if panel.count(begin) != 1 or panel.count(finish) != 1:
        raise ValueError("Native MData old quit anchor changed")
    panel = panel[:panel.index(begin)] + '                    throw new Error("unsupported_action");\n' + panel[panel.index(finish) + len(finish):]
    return panel


def native_mdata_login_source(source: str) -> str:
    """Replace the embedded legacy settings controller on both sign-in screens."""
    from pathlib import Path
    panel = (PROJECT / "modkit/mdata_panel.qml").read_text(encoding="utf8")
    panel = panel.replace("SettingsBaseItem {", "ModUi.SettingsBaseItem {", 1)
    panel = panel.replace("if (pendingRequest === null) root.backClicked();", "return;")
    loader = '''    Loader {
        id: valmaxMDataEntry
        visible: false; width: 0; height: 0
        sourceComponent: settingsModMData
    }
'''
    if source.count(panel) != 1 or source.count(loader) != 1:
        raise ValueError("Native MData login controller anchor changed")
    source = source.replace(panel, "", 1).replace(loader,
        '    ValMAXNative.MDataTransferEntry { id: valmaxMDataEntry }\n', 1)
    for old, new in (
        ("import QtQuick\n", "import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\n"),
        ('enabled: valmaxMDataEntry.item !== null && valmaxMDataEntry.item.available && !valmaxMDataEntry.item.busy',
         'enabled: valmaxMDataEntry.available && !valmaxMDataEntry.busy'),
        ('onClicked: valmaxMDataEntry.item.chooseImport()', 'onClicked: valmaxMDataEntry.open()'),
    ):
        if source.count(old) != 1:
            raise ValueError("Native MData login button anchor changed")
        source = source.replace(old, new, 1)
    if "127.0.0.1:18973" in source or "settingsModMData" in source:
        raise ValueError("Legacy MData login controller remained")
    return source


def native_retention_settings_panel(panel: str) -> str:
    """Port configuration/status without pretending an archive is connected."""
    if panel.count("Component {\n    id: settingsValMAXRetention\n") != 1:
        raise ValueError("Native retention settings identity changed")
    replacements = (
        ('        property var runtime: null\n',
         '        property var runtime: null\n        property var capabilities: null\n'
         '        readonly property bool canSavePreference: loaded && runtimeTrusted && capabilities !== null && (editorEnabled ? capabilities.enable && runtime !== null && runtime.ready && runtime.error === null : capabilities.disable)\n'),
        ('        readonly property bool confirmedActive: runtimeTrusted && runtime !== null && runtime.error === null && runtime.ready && runtime.applied && runtime.enabled && savedEnabled',
         '        readonly property bool confirmedActive: runtimeTrusted && capabilities !== null && capabilities.capture && runtime !== null && runtime.error === null && runtime.ready && runtime.applied && runtime.enabled && savedEnabled'),
        ('new XMLHttpRequest()', 'ValMAXNative.RetentionTransport.createRequest("settings")'),
        ('XMLHttpRequest.DONE', '4'),
        ('xhr.open(method, "http://127.0.0.1:18973/message-retention/" + (method === "POST" ? "settings" : "status"));',
         'xhr.open(method);'),
        ('                if (method === "POST") { xhr.setRequestHeader("Content-Type", "application/json"); xhr.setRequestHeader("X-Max-Mod-Local-Write", "message-retention-v1"); }\n', ''),
        ('savedEnabled = value.enabled; revision = value.revision; runtime = value.runtime; runtimeTrusted = true; loaded = true;',
         'savedEnabled = value.enabled; revision = value.revision; runtime = value.runtime; capabilities = value.capabilities; runtimeTrusted = true; loaded = true;'),
        ('if (!loaded || !runtimeTrusted || pendingRequest !== null || !dirty || needsRefresh) return;',
         'if (!canSavePreference || pendingRequest !== null || !dirty || needsRefresh) return;'),
        ('enabled: retentionPage.loaded && !retentionPage.saving; checked:',
         'enabled: retentionPage.loaded && !retentionPage.saving && retentionPage.capabilities !== null && (retentionPage.capabilities.enable || retentionPage.savedEnabled && retentionPage.capabilities.disable); checked:'),
        ('visible: retentionPage.runtimeTrusted && retentionPage.runtime !== null; text: retentionPage.runtime ?',
         'visible: retentionPage.runtimeTrusted && retentionPage.runtime !== null && retentionPage.runtime.retainedEvents !== null && retentionPage.runtime.retainedMessages !== null; text: retentionPage.runtime ?'),
        ('enabled: retentionPage.loaded && retentionPage.runtimeTrusted && retentionPage.pendingRequest === null && retentionPage.dirty && !retentionPage.needsRefresh;',
         'enabled: retentionPage.canSavePreference && retentionPage.pendingRequest === null && retentionPage.dirty && !retentionPage.needsRefresh;'),
    )
    for old, new in replacements:
        if panel.count(old) != 1:
            raise ValueError("Native retention settings anchor changed")
        panel = panel.replace(old, new, 1)
    start, end = "        function validResponse(value) {\n", "        function abortPending() {\n"
    if panel.count(start) != 1 or panel.count(end) != 1:
        raise ValueError("Native retention status validator anchor changed")
    validator = '''        function validResponse(value) {
            if (!value || typeof value !== "object" || Array.isArray(value)
                    || Object.keys(value).sort().join("|") !== "capabilities|enabled|revision|runtime|schema"
                    || value.schema !== 2 || typeof value.enabled !== "boolean"
                    || typeof value.revision !== "string" || !/^[0-9a-f]{64}$/.test(value.revision)) return false;
            const c = value.capabilities, r = value.runtime;
            if (!c || typeof c !== "object" || Array.isArray(c)
                    || Object.keys(c).sort().join("|") !== "capture|disable|enable|lookup") return false;
            for (const key of ["capture", "disable", "enable", "lookup"]) if (typeof c[key] !== "boolean") return false;
            if (c.enable && !c.capture) return false;
            if (!r || typeof r !== "object" || Array.isArray(r)
                    || !Object.keys(r).every(function(key) { return ["applied", "enabled", "error", "historySyncVerified", "ready", "retainedEvents", "retainedMessages", "queue"].indexOf(key) >= 0; })
                    || ["applied", "enabled", "error", "historySyncVerified", "ready", "retainedEvents", "retainedMessages"].some(function(key) { return !Object.prototype.hasOwnProperty.call(r, key); })
                    || typeof r.ready !== "boolean" || typeof r.applied !== "boolean" || typeof r.enabled !== "boolean"
                    || typeof r.historySyncVerified !== "boolean"
                    || !(r.error === null || typeof r.error === "string" && r.error.length <= 128)
                    || r.enabled && (!value.enabled || !r.applied || !r.ready || r.error !== null)
                    || (r.ready || r.applied || r.enabled) && !c.capture
                    || c.enable && (!r.ready || r.error !== null)
                    || r.queue !== undefined && (!r.queue || typeof r.queue !== "object" || Array.isArray(r.queue)
                        || Object.keys(r.queue).sort().join("|") !== "captured|failures|inFlight|marked|missed|missing|pending|queued")) return false;
            for (const count of [r.retainedEvents, r.retainedMessages]) {
                if (count !== null && (!Number.isSafeInteger(count) || count < 0 || count > 1000000000)) return false;
            }
            if (r.queue !== undefined) for (const count of Object.values(r.queue)) {
                if (!Number.isSafeInteger(count) || count < 0 || count > 1000000000) return false;
            }
            return true;
        }
'''
    panel = panel[:panel.index(start)] + validator + panel[panel.index(end):]
    title = '        readonly property string runtimeTitle:'
    detail = '        readonly property string runtimeDetail:'
    for prefix, new in (
        (title, '        readonly property string runtimeTitle: !runtimeTrusted || runtime === null ? "Состояние сохранения неизвестно" : capabilities !== null && !capabilities.capture ? "Нативное сохранение ещё не подключено" : runtime.error === "retention_not_ready" ? "Ожидание готовности сохранения" : runtime.error !== null ? "Сохранение новых удалений остановлено" : !runtime.ready ? "Ожидание готовности сохранения" : confirmedActive ? "Сохранение новых удалений включено" : savedEnabled ? "Ожидание применения настройки" : "Сохранение выключено"'),
        (detail, '        readonly property string runtimeDetail: !runtimeTrusted || runtime === null ? "Нажми «Обновить», чтобы проверить нативный модуль ValMAX." : capabilities !== null && !capabilities.capture ? "Прежняя настройка прочитана. В этой нативной сборке обработчик сообщений и архив ещё не подключены. Включить сохранение пока нельзя; прежний выбор можно выключить." : runtime.error === "retention_not_ready" ? "Обработчик ждёт привязки владельца аккаунта и готовности локального хранилища. Новые удаления пока не считаются сохранёнными." : runtime.error !== null ? "Обработчик сообщил об ошибке. Новые удаления сейчас не считаются сохранёнными; уже записанные сообщения можно открыть в выбранном чате." : !runtime.ready ? "Обработчик и локальное хранилище ещё не подтвердили готовность. Включение недоступно." : confirmedActive ? "Клиент принимает новые удаления в очередь. В локальном архиве появляются только успешно записанные сообщения; ожидающие записи там не показываются." : savedEnabled ? "Настройка записана, но нативный обработчик ещё не подтвердил её применение. Новые сообщения пока не считаются сохранёнными." : "Сохранение новых удалений сейчас выключено."'),
    ):
        lines = [line for line in panel.splitlines() if line.startswith(prefix)]
        if len(lines) != 1:
            raise ValueError("Native retention status text anchor changed")
        panel = panel.replace(lines[0], new, 1)
    panel = panel.replace("Помощник ValMAX", "Нативный модуль ValMAX").replace("помощником", "нативным модулем").replace("помощника", "нативного модуля")
    panel = panel.replace('text: "Общий для всех аккаунтов ValMAX"',
        'text: "Настройка общая; локальный архив разделён по аккаунтам"')
    counts = '                RetentionText { objectName: "retentionCounts";'
    if panel.count(counts) != 1:
        raise ValueError("Native retention count anchor changed")
    line = next(line for line in panel.splitlines() if line.startswith(counts))
    panel = panel.replace(line, line + '\n'
        '                RetentionText { objectName: "retentionPending"; Layout.fillWidth: true; '
        'visible: retentionPage.runtimeTrusted && retentionPage.runtime !== null && retentionPage.runtime.queue !== undefined && retentionPage.runtime.queue.pending > 0; '
        'text: retentionPage.runtime && retentionPage.runtime.queue ? "В очереди локальной записи на устройстве: " + retentionPage.runtime.queue.pending + ". Эти сообщения ещё не входят в архив." : ""; '
        'typography: Typography.FontUtil.Styles.Description; color: retentionPage.maxwellColorTokens.colorTextAttention }', 1)
    if "127.0.0.1" in panel or "XMLHttpRequest" in panel:
        raise ValueError("Legacy retention settings transport remained")
    return panel


def native_retention_controller(source: str) -> str:
    """No archive lookups/polling while the native capability is unavailable."""
    replacements = (
        ('import QtQuick\n', 'import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\n'),
        ('    id: retentionController\n', '    id: retentionController\n    readonly property bool nativeLookupAvailable: ValMAXNative.RetentionTransport.lookupAvailable\n'),
        ('if (!completed || closing || !active) return;', 'if (!completed || closing || !active || !nativeLookupAvailable) return;'),
        ('if (!completed || closing || !active || !validId(accountId, true)', 'if (!completed || closing || !active || !nativeLookupAvailable || !validId(accountId, true)'),
        ('if (closing || !active || pendingRequest !== null || cycleScope !== scopeGeneration) return;',
         'if (closing || !active || !nativeLookupAvailable || pendingRequest !== null || cycleScope !== scopeGeneration) return;'),
        ('new XMLHttpRequest()', 'ValMAXNative.RetentionTransport.createRequest("lookup")'),
        ('XMLHttpRequest.DONE', '4'),
        ('xhr.open("POST", "http://127.0.0.1:18973/message-retention/lookup");', 'xhr.open("POST");'),
        ('            xhr.setRequestHeader("Content-Type", "application/json");\n', ''),
        ('            xhr.setRequestHeader("X-Max-Mod-Local-Write", "message-retention-v1");\n', ''),
        ('    onActiveChanged: if (completed) handleActiveChanged()\n',
         '    onActiveChanged: if (completed) handleActiveChanged()\n'
         '    onNativeLookupAvailableChanged: if (completed) {\n'
         '        if (!nativeLookupAvailable) { abortPending(); debounceTimer.stop(); unavailable(); }\n'
         '        else schedule();\n'
         '    }\n'),
        ('running: retentionController.completed && retentionController.active && !retentionController.closing',
         'running: retentionController.completed && retentionController.active && !retentionController.closing && retentionController.nativeLookupAvailable'),
    )
    for old, new in replacements:
        if source.count(old) != 1:
            raise ValueError("Native retention controller anchor changed")
        source = source.replace(old, new, 1)
    if "127.0.0.1" in source or "XMLHttpRequest" in source:
        raise ValueError("Legacy retention lookup transport remained")
    return source


RETENTION_ARCHIVE_RESOURCE = ":/valmax/MessageRetentionArchive.qml"
RETENTION_INLINE_RESOURCE = ":/valmax/MessageRetentionInline.qml"
RETENTION_RANGE_RESOURCE = ":/valmax/MessageRetentionRange.qml"
RETENTION_BUBBLE_RESOURCE = ":/valmax/MessageRetentionBubble.qml"
RETENTION_HISTORY_INPUT_SHA256 = "aabfd3c0029eb5f3de6ea67789c2954c2ca19ffa71ced2ad76d5b52d590c2ea7"


def native_retention_history(source: str) -> str:
    """Add selected-chat retained text to the stock history scroll.

    The stock model, delegate indices and compiled bubble resources stay
    untouched. This native-only addition is pinned to its exact input bytes.
    """
    if hashlib.sha256(source.encode("utf8")).hexdigest() != RETENTION_HISTORY_INPUT_SHA256:
        raise ValueError("Native retention history input changed")
    if source.count('id: valmaxRetentionLoader') != 1 or 'valmaxRetentionArchiveButton' in source:
        raise ValueError("Native retention controller anchor changed")
    anchor = "    contentItem: ColumnLayout {\n"
    if source.count(anchor) != 1:
        raise ValueError("Native retention content anchor changed")
    addition = '''    // Open the local archive only inside the selected chat. The viewer
    // receives account/chat IDs from the existing bound client model.
    Button {
        id: valmaxRetentionArchiveButton
        objectName: "valmaxRetentionArchiveButton"
        anchors.top: root.top
        anchors.right: root.right
        anchors.margins: 12
        z: 99
        text: "Сохранённые удаления"
        visible: root.isActiveChat && root.isHistoryPanelActive &&
                 valmaxRetentionLoader.item !== null && valmaxRetentionLoader.item.nativeLookupAvailable
        onClicked: valmaxRetentionArchiveLoader.active = true
    }
    Loader {
        id: valmaxRetentionArchiveLoader
        objectName: "valmaxRetentionArchiveLoader"
        anchors.fill: parent
        active: false
        source: "qrc:/valmax/MessageRetentionArchive.qml"
        z: 100
        onLoaded: {
            item.accountId = Qt.binding(function() {
                return valmaxRetentionLoader.item ? valmaxRetentionLoader.item.accountId : "";
            });
            item.chatId = Qt.binding(function() {
                return valmaxRetentionLoader.item ? valmaxRetentionLoader.item.chatId : "";
            });
            item.closeRequested.connect(function() { valmaxRetentionArchiveLoader.active = false; });
            item.openArchive();
        }
    }
    Connections {
        target: root
        function onIsActiveChatChanged() {
            if (!root.isActiveChat) valmaxRetentionArchiveLoader.active = false;
        }
        function onIsHistoryPanelActiveChanged() {
            if (!root.isHistoryPanelActive) valmaxRetentionArchiveLoader.active = false;
        }
    }

'''
    source = source.replace(anchor, addition + anchor, 1)
    footer_anchor = '''            footer: Item {
                height: bottomLoader.visible ? bottomLoader.height : root.chatBottomPadding
            }
'''
    if source.count(footer_anchor) != 1 or 'valmaxRetentionInlineLoader' in source:
        raise ValueError("Native retention history footer anchor changed")
    footer = '''            // Retained text stays in this ListView's own scroll and leaves its
            // model/delegate indices untouched. The original bottom spacer remains.
            footer: Item {
                objectName: "valmaxRetentionInlineFooter"
                width: listView.width
                height: valmaxRetentionInlineLoader.height +
                        (bottomLoader.visible ? bottomLoader.height : root.chatBottomPadding)
                Loader {
                    id: valmaxRetentionInlineLoader
                    objectName: "valmaxRetentionInlineLoader"
                    width: parent.width
                    height: item ? item.implicitHeight : 0
                    active: valmaxRetentionLoader.item !== null &&
                            valmaxRetentionLoader.item.nativeLookupAvailable
                    source: "qrc:/valmax/MessageRetentionInline.qml"
                    onLoaded: {
                        item.accountId = Qt.binding(function() {
                            return valmaxRetentionLoader.item ? valmaxRetentionLoader.item.accountId : "";
                        });
                        item.chatId = Qt.binding(function() {
                            return valmaxRetentionLoader.item ? valmaxRetentionLoader.item.chatId : "";
                        });
                        item.active = Qt.binding(function() {
                            return valmaxRetentionLoader.item ? valmaxRetentionLoader.item.active : false;
                        });
                        item.chatAppearance = Qt.binding(function() { return root.chatAppearance; });
                        item.nativeRowCount = Qt.binding(function() { return listView.count; });
                    }
                }
            }
'''
    return native_retention_inline_delegate(source.replace(footer_anchor, footer, 1))


def native_retention_inline_delegate(source: str) -> str:
    """Attach local bubbles as siblings of stock delegates without model rows."""
    if 'import "qrc:/valmax" as ValMAX' not in source:
        raise ValueError("Native retention range import changed")
    if source.count("property real valmaxLargestDelegate: 500") != 1:
        raise ValueError("Native retention cache height contract changed")
    view_anchor = "            id: listView\n"
    if source.count(view_anchor) != 1 or "valmaxRetentionRange" in source:
        raise ValueError("Native retention list anchor changed")
    view_addition = '''            ValMAX.MessageRetentionRange {
                id: valmaxRetentionRange
                view: listView
                accountId: valmaxRetentionLoader.item ? valmaxRetentionLoader.item.accountId : ""
                chatId: valmaxRetentionLoader.item ? valmaxRetentionLoader.item.chatId : ""
                active: valmaxRetentionLoader.item !== null &&
                        valmaxRetentionLoader.item.active &&
                        valmaxRetentionLoader.item.nativeLookupAvailable
                hasMoreBackward: root.hasMoreBackward
                hasMoreForward: root.hasMoreForward
            }
            property bool valmaxGhostLayoutQueued: false
            function valmaxScheduleGhostLayout() {
                if (valmaxGhostLayoutQueued) return;
                valmaxGhostLayoutQueued = true;
                Qt.callLater(function() {
                    valmaxGhostLayoutQueued = false;
                    listView.forceLayout();
                });
            }
'''
    source = source.replace(view_anchor, view_anchor + view_addition, 1)
    start_anchor = "            delegate: HistoryMessageDelegate {\n"
    end_anchor = "\n            }\n\n            currentIndex: -1"
    if source.count(start_anchor) != 1 or source.count(end_anchor) != 1:
        raise ValueError("Native retention delegate boundary changed")
    start = source.index(start_anchor)
    end = source.index(end_anchor, start) + len("\n            }")
    inner = source[start:end]
    height_handler = '''                onHeightChanged: {
                    if (height > listView.valmaxLargestDelegate) {
                        listView.valmaxLargestDelegate = height;
                    }
                    if (listView.currentIndex != -1 && listView.currentIndex < index)
                    {
                        Qt.callLater(root.ensureCurrentIndexVisible);
                    }
                }
'''
    replacements = (
        ("            delegate: HistoryMessageDelegate {\n", "            HistoryMessageDelegate {\n"),
        ("                id: messageDelegate\n", '''                id: messageDelegate
                messageContext: valmaxHistoryRow.messageContext
                messageChainedFlag: valmaxHistoryRow.messageChainedFlag
                selectionContext: valmaxHistoryRow.selectionContext
                y: valmaxGhostBefore.implicitHeight
                height: implicitHeight
'''),
        ("                required property int index\n",
         "                readonly property int index: valmaxHistoryRow.index\n"),
        ("                property int offset: y - listView.contentY\n",
         "                property int offset: valmaxHistoryRow.y + y - listView.contentY\n"),
        ("const absoluteY = y - listView.originY;",
         "const absoluteY = valmaxHistoryRow.y + y - listView.originY;"),
        ("                delegateY: y\n", "                delegateY: valmaxHistoryRow.y + y\n"),
        ("                listView: ListView.view\n",
         "                listView: valmaxHistoryRow.valmaxOwnerView\n"),
        (height_handler, ""),
    )
    for old, new in replacements:
        if inner.count(old) != 1:
            raise ValueError("Native retention delegate contract changed")
        inner = inner.replace(old, new, 1)
    inner = "\n".join("    " + line for line in inner.splitlines())
    wrapper = '''            delegate: Item {
                id: valmaxHistoryRow
                objectName: "valmaxRetentionHistoryRow"
                required property int index
                required property var messageContext
                required property int messageChainedFlag
                required property var selectionContext
                readonly property var valmaxOwnerView: listView
                property alias isHighlighted: messageDelegate.isHighlighted
                width: listView.width
                height: valmaxGhostBefore.implicitHeight + messageDelegate.height + valmaxGhostAfter.implicitHeight
                onHeightChanged: {
                    if (height > listView.valmaxLargestDelegate)
                        listView.valmaxLargestDelegate = height;
                    if (listView.currentIndex !== -1 && listView.currentIndex < index)
                        Qt.callLater(root.ensureCurrentIndexVisible);
                    listView.valmaxScheduleGhostLayout();
                }
'''
    return source[:start] + wrapper + inner + native_retention_ghost_columns() + source[end:]


def native_retention_ghost_columns() -> str:
    """QML sibling views; stock delegate pointer handlers cannot own a ghost."""
    return '''
                Column {
                    id: valmaxGhostBefore
                    objectName: "valmaxRetentionGhostBefore"
                    width: valmaxHistoryRow.width
                    spacing: 4
                    Button {
                        objectName: "valmaxRetentionRangePreviousBefore"
                        visible: valmaxRetentionRange.pagingBefore && valmaxRetentionRange.hasPreviousWindow &&
                                 valmaxRetentionRange.pagingAnchorIndex === valmaxHistoryRow.index
                        text: "Предыдущая часть сохранённых сообщений"
                        onClicked: valmaxRetentionRange.previousWindow()
                        onVisibleChanged: valmaxGhostBefore.forceLayout()
                    }
                    Repeater {
                        model: valmaxRetentionRange.rowsBeforeIndex[String(valmaxHistoryRow.index)] || []
                        delegate: ValMAX.MessageRetentionBubble {
                            required property var modelData
                            width: valmaxGhostBefore.width
                            messageId: modelData.messageId
                            messageText: modelData.text
                            deletedAtMs: modelData.deletedAtMs
                            messageTimeMs: modelData.messageTimeMs
                            beforeStart: true
                            dayUnknown: modelData.dayUnknown
                            tieUnknown: modelData.tieUnknown
                            chatAppearance: root.chatAppearance
                        }
                        onItemAdded: valmaxGhostBefore.forceLayout()
                        onItemRemoved: valmaxGhostBefore.forceLayout()
                    }
                    Button {
                        objectName: "valmaxRetentionRangeMoreBefore"
                        visible: valmaxRetentionRange.pagingBefore && valmaxRetentionRange.hasMore &&
                                 valmaxRetentionRange.pagingAnchorIndex === valmaxHistoryRow.index
                        text: "Показать ещё сохранённые сообщения"
                        onClicked: valmaxRetentionRange.loadMore()
                        onVisibleChanged: valmaxGhostBefore.forceLayout()
                    }
                    Button {
                        objectName: "valmaxRetentionRangeNextBefore"
                        visible: valmaxRetentionRange.pagingBefore && valmaxRetentionRange.hasNextWindow &&
                                 valmaxRetentionRange.pagingAnchorIndex === valmaxHistoryRow.index
                        text: "Следующая часть сохранённых сообщений"
                        onClicked: valmaxRetentionRange.nextWindow()
                        onVisibleChanged: valmaxGhostBefore.forceLayout()
                    }
                    Button {
                        objectName: "valmaxRetentionRangeArchiveBefore"
                        visible: valmaxRetentionRange.pagingBefore && valmaxRetentionRange.hasNextWindow &&
                                 valmaxRetentionRange.pagingAnchorIndex === valmaxHistoryRow.index
                        text: "Открыть архив сохранённых сообщений"
                        onClicked: valmaxRetentionArchiveLoader.active = true
                        onVisibleChanged: valmaxGhostBefore.forceLayout()
                    }
                    Component.onCompleted: forceLayout()
                }
                // Native pointer handlers remain confined to messageDelegate.
                Column {
                    id: valmaxGhostAfter
                    objectName: "valmaxRetentionGhostAfter"
                    y: messageDelegate.y + messageDelegate.height
                    width: valmaxHistoryRow.width
                    spacing: 4
                    Button {
                        objectName: "valmaxRetentionRangePreviousAfter"
                        visible: !valmaxRetentionRange.pagingBefore && valmaxRetentionRange.hasPreviousWindow &&
                                 valmaxRetentionRange.pagingAnchorIndex === valmaxHistoryRow.index
                        text: "Предыдущая часть сохранённых сообщений"
                        onClicked: valmaxRetentionRange.previousWindow()
                        onVisibleChanged: valmaxGhostAfter.forceLayout()
                    }
                    Repeater {
                        model: valmaxRetentionRange.rowsByIndex[String(valmaxHistoryRow.index)] || []
                        delegate: ValMAX.MessageRetentionBubble {
                            required property var modelData
                            width: valmaxGhostAfter.width
                            messageId: modelData.messageId
                            messageText: modelData.text
                            deletedAtMs: modelData.deletedAtMs
                            messageTimeMs: modelData.messageTimeMs
                            crossDay: modelData.crossDay
                            dayUnknown: modelData.dayUnknown
                            afterEnd: modelData.afterEnd
                            tieUnknown: modelData.tieUnknown
                            chatAppearance: root.chatAppearance
                        }
                        onItemAdded: valmaxGhostAfter.forceLayout()
                        onItemRemoved: valmaxGhostAfter.forceLayout()
                    }
                    Button {
                        objectName: "valmaxRetentionRangeMoreAfter"
                        visible: !valmaxRetentionRange.pagingBefore && valmaxRetentionRange.hasMore &&
                                 valmaxRetentionRange.pagingAnchorIndex === valmaxHistoryRow.index
                        text: "Показать ещё сохранённые сообщения"
                        onClicked: valmaxRetentionRange.loadMore()
                        onVisibleChanged: valmaxGhostAfter.forceLayout()
                    }
                    Button {
                        objectName: "valmaxRetentionRangeNextAfter"
                        visible: !valmaxRetentionRange.pagingBefore && valmaxRetentionRange.hasNextWindow &&
                                 valmaxRetentionRange.pagingAnchorIndex === valmaxHistoryRow.index
                        text: "Следующая часть сохранённых сообщений"
                        onClicked: valmaxRetentionRange.nextWindow()
                        onVisibleChanged: valmaxGhostAfter.forceLayout()
                    }
                    Button {
                        objectName: "valmaxRetentionRangeArchiveAfter"
                        visible: !valmaxRetentionRange.pagingBefore && valmaxRetentionRange.hasNextWindow &&
                                 valmaxRetentionRange.pagingAnchorIndex === valmaxHistoryRow.index
                        text: "Открыть архив сохранённых сообщений"
                        onClicked: valmaxRetentionArchiveLoader.active = true
                        onVisibleChanged: valmaxGhostAfter.forceLayout()
                    }
                    Button {
                        objectName: "valmaxRetentionRangeRetry"
                        visible: valmaxRetentionRange.failed && valmaxRetentionRange.currentBounds &&
                                 valmaxRetentionRange.currentBounds.start === valmaxHistoryRow.index
                        text: "Повторить чтение сохранённых сообщений"
                        onClicked: valmaxRetentionRange.scanVisible(true)
                        onVisibleChanged: valmaxGhostAfter.forceLayout()
                    }
                    Component.onCompleted: forceLayout()
                }
            }'''


def native_settings_source(source: str) -> str:
    if not source.startswith("import QtQuick\n"):
        raise ValueError("Native settings imports changed")
    source = source.replace("import QtQuick\n", "import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\n", 1)
    for component, (family, endpoint) in SETTINGS_PAGES.items():
        start_anchor = "Component {\n    id: " + component + "\n"
        if source.count(start_anchor) != 1:
            raise ValueError("Native settings component changed: " + component)
        start = source.index(start_anchor)
        end = source.index("\n}\n", start) + 2
        source = source[:start] + native_settings_panel(source[start:end], family, endpoint) + source[end:]
    privacy_anchor = "    Component {\n        id: settingsModPrivacy\n"
    if source.count(privacy_anchor) != 1:
        raise ValueError("Native privacy component changed")
    start = source.index(privacy_anchor)
    end = source.index("\n    }\n", start) + 6
    panel = (PROJECT / "native/qt/PrivacyPanel.qml").read_text(encoding="utf8").replace("\r\n", "\n")
    source = source[:start] + "\n".join("    " + line for line in panel.rstrip().splitlines()) + source[end:]
    support_anchor = "Component {\n    id: settingsValMAXSupport\n"
    if source.count(support_anchor) != 1:
        raise ValueError("Native support component changed")
    start = source.index(support_anchor)
    end = source.index("\n}\n", start) + 2
    source = source[:start] + native_support_panel(source[start:end]) + source[end:]
    mdata_anchor = "Component {\n    id: settingsModMData\n"
    if source.count(mdata_anchor) != 1:
        raise ValueError("Native MData component changed")
    start = source.index(mdata_anchor)
    end = source.index("\n}\n", start) + 2
    source = source[:start] + native_mdata_panel(source[start:end]) + source[end:]
    retention_anchor = "Component {\n    id: settingsValMAXRetention\n"
    if source.count(retention_anchor) != 1:
        raise ValueError("Native retention settings component changed")
    start = source.index(retention_anchor)
    end = source.index("\n}\n", start) + 2
    source = source[:start] + native_retention_settings_panel(source[start:end]) + source[end:]
    business_anchor = "Component {\n    id: settingsValMAXBusiness\n"
    if source.count(business_anchor) != 1:
        raise ValueError("Native business component changed")
    start = source.index(business_anchor)
    end = source.index("\n}\n", start) + 2
    panel = (PROJECT / "native/qt/BusinessPanel.qml").read_text(encoding="utf8").replace("\r\n", "\n")
    panel = panel[panel.index("Component {\n"):].rstrip()
    source = source[:start] + panel + source[end:]
    return source


def native_chat_lock_controller(source: str) -> str:
    """Keep the native MAX PIN UI and its grants; replace only HTTP transport."""
    for old, new in (
        ("import QtQuick\n", "import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\n"),
        ('    property string endpointBase: "http://127.0.0.1:18973"\n', ""),
        ("new XMLHttpRequest()", "ValMAXNative.ChatLockTransport.createRequest()"),
        ('xhr.open(method, endpointBase + (method === "GET" ? "/chat-locks" : "/chat-locks/" + action));', "xhr.open(method, action);"),
        ('        if (method === "POST") {\n'
         '            xhr.setRequestHeader("Content-Type", "application/json");\n'
         '            xhr.setRequestHeader("X-Max-Mod-Local-Write", "chat-locks-v1");\n'
         '        }', ""),
        ("XMLHttpRequest.DONE", "4"),
    ):
        if source.count(old) != 1:
            raise ValueError("Native PIN transport anchor changed")
        source = source.replace(old, new, 1)
    return source


def native_export_panel(source: str) -> str:
    """Preserve selected-chat model access; move commands to the native service."""
    if source.count('xhr.open("POST", "api2.history.preview");') != 1:
        raise ValueError("Native history preview anchor changed")
    start, end = "        function request(route, body, callback) {\n", "        function dependencyForAccount() {\n"
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError("Native export request anchor changed")
    begin, finish = source.index(start), source.index(end)
    block = source[begin:finish]
    for old, new in (
        ("new XMLHttpRequest()", "ValMAXNative.ExportTransport.createRequest()"),
        ("XMLHttpRequest.DONE", "4"),
        ('xhr.open("POST", "http://127.0.0.1:18973/chat-export/" + route);', 'xhr.open("POST", route);'),
        ('            xhr.setRequestHeader("Content-Type", "application/json");\n'
         '            xhr.setRequestHeader("X-Max-Mod-Local-Write", "chat-export-v1");\n', ""),
        ('if (xhr.status !== 200) { fail("history_error", "Не удалось сохранить экспорт. Уже полученные данные не считаются полной историей."); return; }',
         'if (xhr.status !== 200) {\n'
         '                    let code = "io_error"; try { code = JSON.parse(xhr.responseText).error; } catch (_) {}\n'
         '                    const text = code === "unsupported_operation" ? "Нативное сохранение этого вложения ещё не подключено. Экспорт не завершён."\n'
         '                        : code === "insufficient_space" ? "Недостаточно места для сохранения чата."\n'
         '                        : code === "busy" ? "Другая задача экспорта ещё выполняется. Дождись её завершения."\n'
         '                        : "Не удалось сохранить экспорт. Уже полученные данные не считаются полной историей.";\n'
         '                    fail("history_error", text); return;\n'
         '                }'),
    ):
        if block.count(old) != 1:
            raise ValueError("Native export transport anchor changed")
        block = block.replace(old, new, 1)
    source = source[:begin] + block + source[finish:]
    for old, new in (
        ("import QtQuick\n", "import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\n"),
        ("        property bool nativeHistoryPreviewAvailable: false\n",
         "        property bool nativeHistoryPreviewAvailable: true\n"),
        ("        property bool nativeApi2ExportAvailable: false\n",
         "        property bool nativeApi2ExportAvailable: true\n"),
        ('        property string exportMode: "client"\n',
         '        property string exportMode: "api2-text"\n'),
        ("        function cancelApi2NativeJob(job) { /* Native generator binds detached cancellation. */ }\n",
         "        function cancelApi2NativeJob(job) { ValMAXNative.ExportTransport.cancelDetached(job); }\n"),
        ("        function beginApi2NativeHistory(job) { return false; /* Native generator starts the selected-view lease. */ }\n",
         "        function beginApi2NativeHistory(job) { return typeof ValMAXNative.Bridge.beginExportHistory === \"function\"\n"
         "                && ValMAXNative.Bridge.beginExportHistory(job.jobId, job.jobToken) === true; }\n"),
        ("        function bindApi2NativeHistory(view, job) { return false; /* Native generator binds the selected view. */ }\n",
         "        function bindApi2NativeHistory(view, job) { return typeof ValMAXNative.Bridge.bindExportHistory === \"function\"\n"
         "                && ValMAXNative.Bridge.bindExportHistory(view, job.jobId, job.jobToken) === true; }\n"),
        ("        function releaseApi2NativeHistory(job) { /* Native generator releases the selected view. */ }\n",
         "        function releaseApi2NativeHistory(job) { if (typeof ValMAXNative.Bridge.releaseExportHistory === \"function\")\n"
         "                ValMAXNative.Bridge.releaseExportHistory(job.jobId, job.jobToken); }\n"),
        ("const xhr = new XMLHttpRequest(); pendingPreviewRequest = xhr;",
         "const xhr = ValMAXNative.ExportTransport.createRequest(); pendingPreviewRequest = xhr;"),
        ('                historyContext = accountDependency.historyPanelDependency.historyViewFactory.createOnMessage(nativeChatId, message.messageTimeId);\n'
         '                historyContext.get().objectName = "valmax-export-" + jobId;',
         '                if (typeof ValMAXNative.Bridge.beginExportHistory === "function") ValMAXNative.Bridge.beginExportHistory(jobId, jobToken);\n'
         '                historyContext = accountDependency.historyPanelDependency.historyViewFactory.createOnMessage(nativeChatId, message.messageTimeId);\n'
         '                historyContext.get().objectName = "valmax-export-" + jobId;\n'
         '                if (typeof ValMAXNative.Bridge.bindExportHistory === "function") ValMAXNative.Bridge.bindExportHistory(historyContext.get(), jobId, jobToken);'),
        ('                releaseNative(); applyStatus(value); if (phase === "rendering") renderPoll.start();',
         '                applyStatus(value); if (phase === "rendering") renderPoll.start();'),
        ('        function releaseNative() {\n'
         '            mediaWatchdog.stop(); historyContext = null; seedContext = null;',
         '        function releaseNative(keepHistory) {\n'
         '            mediaWatchdog.stop();\n'
         '            if (!keepHistory) {\n'
         '                if (jobId && jobToken && typeof ValMAXNative.Bridge.releaseExportHistory === "function") ValMAXNative.Bridge.releaseExportHistory(jobId, jobToken);\n'
         '                historyContext = null; seedContext = null;\n'
         '            }'),
        ('            phase = state; renderPoll.stop();',
         '            phase = state; renderPoll.stop(); releaseNative();'),
        ('if (!media || typeof media.messageId !== "string" || typeof media.attachmentId !== "string" || !localFileURL(media.nativePathFileURL)) throw new Error("reservation");',
         'if (!media || typeof media.messageId !== "string" || typeof media.attachmentId !== "string"\n'
         '                    || typeof media.source !== "string" || !/^media\\/[0-9a-f]{32}\\.[a-z0-9]+$/.test(media.source)) throw new Error("reservation");'),
        ('request("media-start", jobBody({messageId: media.messageId, attachmentId: media.attachmentId}), function(_) {\n'
         '                activeNative = true;\n'
         '                if (cancelRequested) { mediaFinished("cancelled", "cancelled"); return; }',
         'request("media-start", jobBody({messageId: media.messageId, attachmentId: media.attachmentId}), function(receipt) {\n'
         '                if (!receipt || !localFileURL(receipt.nativePathFileURL)) throw new Error("reservation");\n'
         '                media.nativePathFileURL = receipt.nativePathFileURL;\n'
         '                if (cancelRequested) { mediaFinished("cancelled", "cancelled"); return; }\n'
         '                activeNative = true;'),
        ('            if (pendingRequest) pendingRequest.abort();\n',
         '            if (pendingRequest) pendingRequest.abort();\n'
         '            ValMAXNative.ExportMediaOwner.cancelForJob(jobId);\n'
         '            if (jobId && jobToken) ValMAXNative.ExportTransport.cancelDetached({jobId: jobId, jobToken: jobToken});\n'),
        ('            if (task.kind === "voice" || task.kind === "audio") { downloadAudio(task.url); return; }\n', ''),
        ('            cancelRequested = true;\n', '            cancelRequested = true;\n            ValMAXNative.ExportMediaOwner.cancelForJob(jobId);\n'),
        ('{kind: "video", chatId: message.chatId, messageTimeId: message.messageTimeId, attachId: attachment.attachId, context: context}',
         '{kind: "video", chatId: message.chatId, messageTimeId: message.messageTimeId, attachId: attachment.attachId, url: String(attachment.url), context: context}'),
    ):
        if source.count(old) != 1:
            raise ValueError("Native export panel anchor changed")
        source = source.replace(old, new, 1)
    if "http://127.0.0.1:18973/chat-export" in source:
        raise ValueError("Legacy export transport remained")
    start, end = "        function invokeNative(media, task) {\n", "        function downloadAudio(url) {\n"
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError("Native export SDK ownership anchor changed")
    begin, finish = source.index(start), source.index(end)
    source = source[:begin] + '''        function invokeNative(media, task) {
            const admitted = ValMAXNative.ExportMediaOwner.start({jobId:jobId,jobToken:jobToken}, media, task, downloader,
                function(status, reason, value) {
                    activeNative = false;
                    if (!currentMedia || currentMedia.reservation !== media) return;
                    currentMedia = null; terminalResult = null;
                    if (value && ["cancelled", "failed"].indexOf(value.state) >= 0) { applyStatus(value); return; }
                    if (status === "ready") mediaCount += 1; else omissionCount += 1;
                    if (closedByUser) dispose();
                    else if (cancelRequested && !pendingRequest) cancelExport();
                    else if (!cancelRequested) nextMedia();
                });
            if (!admitted) { activeNative = false; mediaFinished("error", "download_failed"); }
        }
''' + source[finish:]
    start, end = "        function downloadAudio(url) {\n", "        function mediaFinished(status, reason) {\n"
    if source.count(start)!=1 or source.count(end)!=1:
        raise ValueError("Native export buffered audio anchor changed")
    source = source[:source.index(start)] + source[source.index(end):]
    start = '        Connections {\n            target: exportDialog.downloader\n'
    end = '        Timer { id: requestWatchdog;'
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError("Native export SDK signal ownership anchor changed")
    source = source[:source.index(start)] + source[source.index(end):]
    start, end = "        function harvestPage() {\n", "        function nextMedia() {\n"
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError("Native export model cursor anchor changed")
    collector = (PROJECT / "native/qt/export_harvest.js").read_text(encoding="utf8").replace("\r\n", "\n")
    source = source[:source.index(start)] + collector.rstrip() + "\n" + source[source.index(end):]
    old = '        ExportModels.DelegateModel { id: historyModel; model: exportDialog.history ? exportDialog.history.model : null; delegate: QtObject {} }'
    new = '        ValMAXNative.HistoryModelCursor { id: historyCursor; model: exportDialog.history ? exportDialog.history.model : null; roleName: "messageContext" }'
    if source.count(old) != 1 or source.count("historyModel.items.count") != 1:
        raise ValueError("Native export model binding anchor changed")
    source = source.replace(old, new, 1).replace("historyModel.items.count", "historyCursor.rowCount", 1)
    old = ('            if (Date.now() - waitStarted > 30000) { finishExport("timeout"); return; }\n'
           '            if (awaitingHistory && !sawHistoryChange) return;')
    new = ('            if (Date.now() - waitStarted > 30000) { finishExport("timeout"); return; }\n'
           '            // A local wrapper may still be initializing. Do not page\n'
           '            // away from it or enter EOF proof; the timeout above bounds waiting.\n'
           '            if (historyCursor.pendingCount > 0) return;\n'
           '            if (awaitingHistory && !sawHistoryChange) return;')
    if source.count(old) != 1:
        raise ValueError("Native export pending model guard anchor changed")
    source = source.replace(old, new, 1)
    old = "Timer { id: stepTimer; interval: 250;"
    if source.count(old) != 1:
        raise ValueError("Native export step timer anchor changed")
    source = source.replace(old, "Timer { id: stepTimer; interval: 16;", 1)
    if "historyModel" in source:
        raise ValueError("Legacy export DelegateModel walk remained")
    return source


def prepare(binary: bytes, output: Path, *, native_runtime: bool, rcc: Path):
    import icon_resources
    import ui_prepare_features
    from ui_multi_resource_patch import make_resource_spec, read_source, PRESERVED_COMPILED_RESOURCES
    from message_retention_ui import CONTROLLER_RESOURCE, controller_source
    from chat_lock_ui import CONTROLLER_RESOURCE as LOCK_RESOURCE, controller_source as lock_source, transform_export_panel
    from async_list_rows_ui import ROW_RESOURCE, row_source
    from list_work_queue_ui import QUEUE_RESOURCE, queue_source
    from history_wheel_ui import WHEEL_RESOURCE, wheel_source
    from optimization_gates import transform_extra_resources, IMPORT, flag
    from optimization_ui import transform_export_panel as beta_export_panel
    if type(native_runtime) is not bool:
        raise ValueError("native_runtime must be explicit bool")
    output.mkdir(parents=True, exist_ok=False)
    resources = transform_extra_resources({
        ":/valmax/ChatExportPanel.qml": beta_export_panel(transform_export_panel((PROJECT / "modkit/chat_export_panel.qml").read_text(encoding="utf8"))).encode(),
        CONTROLLER_RESOURCE: controller_source().encode(), LOCK_RESOURCE: lock_source().encode(),
        ROW_RESOURCE: row_source().encode(), QUEUE_RESOURCE: queue_source().encode(), WHEEL_RESOURCE: wheel_source().encode(),
    })
    if native_runtime:
        key = ":/valmax/ChatExportPanel.qml"
        resources[key] = native_export_panel(resources[key].decode()).encode()
        resources[CONTROLLER_RESOURCE] = native_retention_controller(resources[CONTROLLER_RESOURCE].decode()).encode()
        resources[RETENTION_ARCHIVE_RESOURCE] = (PROJECT / "native/qt/MessageRetentionArchive.qml").read_bytes()
        resources[RETENTION_INLINE_RESOURCE] = (PROJECT / "native/qt/MessageRetentionInline.qml").read_bytes()
        resources[RETENTION_RANGE_RESOURCE] = (PROJECT / "native/qt/MessageRetentionRange.qml").read_bytes()
        resources[RETENTION_BUBBLE_RESOURCE] = (PROJECT / "native/qt/MessageRetentionBubble.qml").read_bytes()
        resources[LOCK_RESOURCE] = native_chat_lock_controller(resources[LOCK_RESOURCE].decode()).encode()
        source = (PROJECT / "native/qt/ValMAXListWorkQueue.qml").read_text(encoding="utf8").replace("\r\n", "\n")
        anchor = "readonly property bool viewActive: pageActive &&"
        if source.count(anchor) != 1:
            raise ValueError("Native queue optimization gate anchor changed")
        source = IMPORT + source.replace(anchor, "readonly property bool viewActive: " + flag("listScheduling") + " && pageActive &&", 1)
        resources[QUEUE_RESOURCE] = source.encode()
    bundle, icons = icon_resources.prepare(binary, output / "icons", rcc=rcc, extra_resources=resources)
    sources, generated = ui_prepare_features.prepare(binary, icon_bundle=bundle)
    if native_runtime:
        name = ":/qt/qml/OneMeUi/MeWindow.qml"
        source = sources[name].decode()
        for old, new in (
            ("import QtQuick\n", "import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\n"),
            ("    id: root\n", "    id: root\n    readonly property var valmaxNativeBridge: ValMAXNative.Bridge\n"),
        ):
            if source.count(old) != 1:
                raise ValueError("Native bridge window anchor changed")
            source = source.replace(old, new, 1)
        from startup_interaction_ui import transform as startup_interaction_transform
        source = startup_interaction_transform(source)
        sources[name] = source.encode()
        settings_name = ":/qt/qml/OneMeUi/LeadingSettingsBar.qml"
        sources[settings_name] = native_settings_source(sources[settings_name].decode()).encode()
        history_name = ":/qt/qml/OneMeUi/HistoryListView.qml"
        sources[history_name] = native_retention_history(sources[history_name].decode()).encode()
        for name in ("PhoneNumberPage.qml", "QrCodePage.qml"):
            key = ":/qt/qml/OneMeAuthorization/" + name
            sources[key] = native_mdata_login_source(sources[key].decode()).encode()
        composer_name = ":/qt/qml/OneMeUi/BaseSendPanel.qml"
        sources[composer_name] = native_inline_media_source(native_quick_replies_source(sources[composer_name].decode())).encode()
        sources = native_account_sources(sources)
        from chat_row_identity import RESOURCE as CHAT_LIST_RESOURCE, apply as chat_row_identity
        sources[CHAT_LIST_RESOURCE] = chat_row_identity(sources[CHAT_LIST_RESOURCE].decode()).encode()
        from recording_indicator_ui import RESOURCE_PATHS as RECORDING_RESOURCES, transform as recording_transform
        for key in RECORDING_RESOURCES:
            if key in sources:
                raise ValueError("Native recording indicator resource was already selected")
            sources[key] = recording_transform(key, read_source(binary, key))
        spec, evidence = make_resource_spec(binary, sources, icon_bundle=bundle)
        evidence["preservedCompiledResources"] = list(PRESERVED_COMPILED_RESOURCES)
    else:
        spec, evidence = generated["spec"], generated["evidence"]
    for name, payload in sources.items():
        (output / name.rsplit("/", 1)[1]).write_bytes(payload)
    (output / "ui_native.py").write_text(
        '"""Generated isolated native runtime resources; do not edit bytes."""\nPATCH_FILES = '
        + pprint.pformat([spec], width=110, sort_dicts=False) + "\n", encoding="utf8")
    proof = {"schema": 1, "nativeRuntime": native_runtime, "legacyGeneratorsModified": False,
             "generatedArtifacts": {"ui_native.py": sha((output / "ui_native.py").read_bytes()),
                                    "icons/icon-pack-plus-valmax.rcc": sha(bundle)},
             "baselineSha256": sha(binary), "sourceHashes": {name: sha(value) for name, value in sources.items()},
             "extraResourceHashes": {name: sha(value) for name, value in resources.items()},
             "requiredNativeFiles": ["qml/ValMAX/Native/valmaxnativeplugin.dll", "qml/ValMAX/Native/qmldir", "qml/ValMAX/Native/SettingsTransport.qml", "qml/ValMAX/Native/RetentionTransport.qml", "qml/ValMAX/Native/ChatLockTransport.qml", "qml/ValMAX/Native/SupportTransport.qml", "qml/ValMAX/Native/MDataTransport.qml", "qml/ValMAX/Native/MDataTransferEntry.qml", "qml/ValMAX/Native/ExportTransport.qml", "qml/ValMAX/Native/ExportMediaOwner.qml", "valmax_core.dll"] if native_runtime else [],
             "compiledPreserved": list(PRESERVED_COMPILED_RESOURCES), "icons": icons, "resourceEvidence": evidence,
             "scope": "Native queue, direct bridge, bounded main-window interaction gate after protection host ACKs, optimization/ghost/accounts/quick-reply settings, account labels/colors/work-hours readers, privacy verifier, explicit support reports, quick-reply composer, native media composer/preview/draft dispatch, bundled business WebView error handling and PIN controller transport; PIN creation requires trusted native notification hooks; background startup protection remains unproven"}
    (output / "native-qml-proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf8")
    return sources, spec, bundle, proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rcc", required=True, type=Path)
    parser.add_argument("--native-runtime", action="store_true")
    args = parser.parse_args()
    _, _, _, proof = prepare(args.baseline.read_bytes(), args.output, native_runtime=args.native_runtime, rcc=args.rcc)
    print(json.dumps({key: proof[key] for key in ("schema", "nativeRuntime", "legacyGeneratorsModified", "requiredNativeFiles")}))


if __name__ == "__main__":
    main()
