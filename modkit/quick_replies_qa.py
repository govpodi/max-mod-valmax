"""Offscreen synthetic quick-replies QA. Never starts or attaches MAX."""
from __future__ import annotations
import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from quick_replies_ui import snippets, transform_sources


def brace_body(source: str, marker: str) -> str:
    start = source.index(marker) + len(marker)
    depth = 1
    for i in range(start, len(source)):
        if source[i] == "{": depth += 1
        if source[i] == "}": depth -= 1
        if depth == 0: return source[start:i]
    raise ValueError("Unclosed fixture anchor " + marker)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--native", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "QML_DISK_CACHE_PATH"):
        path = args.output / "synthetic-profile" / name.lower()
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path.resolve())
    os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_QUICK_CONTROLS_STYLE="Basic", QML_DISABLE_DISK_CACHE="1")
    font_dir = args.output / "fonts"
    font_dir.mkdir(exist_ok=True)
    for font_name in ("arial.ttf", "arialbd.ttf"):
        shutil.copyfile(Path(r"C:\Windows\Fonts") / font_name, font_dir / font_name)
    os.environ["QT_QPA_FONTDIR"] = str(font_dir.resolve())
    from PySide6.QtCore import QObject, Property, Slot, QEventLoop, QMetaObject, QTimer, QUrl, Qt, QtMsgType, Q_ARG, Q_RETURN_ARG, qInstallMessageHandler, qVersion
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtQml import QQmlEngine, QQmlComponent, qmlRegisterSingletonInstance, qmlRegisterType
    from PySide6.QtQuick import QQuickItem
    class FontUtil(QObject):
        @Property("QVariantMap", constant=True)
        def Styles(self): return {"Body": 0, "BodyStrong": 1, "Description": 2, "DescriptionStrong": 3, "LabelStrong": 5}
        @Property("QVariantMap", constant=True)
        def Sizes(self): return {"Default": 0}
        @Slot(int, int, result=QFont)
        def getFont(self, style, _size):
            font = QFont("Arial"); font.setPixelSize(16 if style < 2 else 14)
            font.setWeight(QFont.Weight.DemiBold if style % 2 else QFont.Weight.Normal)
            return font
        @Slot(int, int, result=float)
        def getLineHeight(self, style, _size): return 22.0 if style < 2 else 20.0
    class DesignSystem(QObject):
        @Property("QVariantMap", constant=True)
        def SpacingCommon(self): return {"_Zero": 0, "_2XS": 4, "_S": 8, "_M": 12, "_2XL": 24}
        @Property("QVariantMap", constant=True)
        def BorderRadiusCommon(self): return {"_S": 4, "_L": 12}
        @Property("QVariantMap", constant=True)
        def SizeBase(self): return {"px6": 6, "px32": 32, "px48": 48, "px64": 64}
    class QmlUtils(QObject):
        @Slot("QVariant", result=str)
        def contactIdString(self, value): return str(value)
    rows = [{"id": "1" * 32, "command": "away", "title": "Сейчас недоступен", "text": "Сейчас не могу ответить. Напишу, как освобожусь."},
            {"id": "2" * 32, "command": "busy", "title": "На встрече", "text": "Я на встрече. Вернусь к сообщению позже."},
            {"id": "3" * 32, "command": "вне_офиса", "title": "Вне офиса", "text": "<b>Это обычный текст</b>\nОтвечу завтра."}]
    requests, flags = [], {"conflict": False, "stall": False, "stall_post": False}
    release = threading.Event()
    release_post = threading.Event()
    def response():
        revision = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return {"schema": 1, "revision": revision, "replies": copy.deepcopy(rows)}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append({"method": "GET", "path": self.path, "body": ""})
            if flags["stall"]: release.wait(12)
            self.reply(200, response())
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
            requests.append({"method": "POST", "path": self.path, "body": body, "type": self.headers.get("Content-Type"), "write": self.headers.get("X-Max-Mod-Local-Write"), "origin": self.headers.get("Origin")})
            if flags["stall_post"]: release_post.wait(3)
            if flags["conflict"]: self.reply(409, {}); return
            incoming = json.loads(body)
            if incoming["revision"] != response()["revision"]: self.reply(409, {}); return
            rows[:] = incoming["replies"]
            self.reply(200, response())
        def reply(self, status, value):
            blob = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(blob))); self.end_headers()
            try: self.wfile.write(blob)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError): pass
        def log_message(self, *_args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings, declarations, menu = snippets()
    declarations = declarations.replace("property bool modQuickReplyAttachmentIntegration: false",
                                        "property bool modQuickReplyAttachmentIntegration: true")
    native = {name: (args.native / name).read_text(encoding="utf-8") for name in ("LeadingSettingsBar.qml", "BaseSendPanel.qml", "MessageTextInput.qml")}
    native["BaseMessageSendPanel.qml"] = (args.native / "BaseMessageSendPanel.qml").read_text(encoding="utf-8")
    patched = transform_sources(native)
    attachments_body = brace_body(patched["BaseMessageSendPanel.qml"], "    onModQuickReplyAttachmentsRequested: (files, media) => {")
    native_cleared_body = brace_body(patched["BaseMessageSendPanel.qml"], "        onCleared: {")
    editor = patched["MessageTextInput.qml"]
    key_body = brace_body(editor, "        onKeysPressedHandler: event => {")
    up_body = brace_body(editor, "        onKeysUpPressedHandler: event => {")
    down_body = brace_body(editor, "        onKeysDownPressedHandler: event => {")
    close_body = brace_body(editor, "    function onCloseKeysPressed() {")
    active_body = brace_body(patched["BaseSendPanel.qml"], "    readonly property var activeCompletionList: {")
    source = '''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import UiKitModule as UiKit
import TypographyModule as Typography
import QmlSingletonesModule
import QtQuick.Dialogs as QuickReplyDialogs
ApplicationWindow {
 id: root
 width: 650; height: 1100; visible: true
 property bool testDark: false
 property bool testComposer: false
 property var page: null
 property alias composer: composerFixture
 property var commonDependency: ({selfInfo: {id: "101"}})
 signal backClicked()
 color: tokens.colorBackgroundSurface
 QtObject {
  id: tokens
  readonly property color colorTextPrimary: root.testDark ? "#edf0f2" : "#0c0d0e"
  readonly property color colorTextSecondary: root.testDark ? "#a7b0ba" : "#5e6269"
  readonly property color colorTextTertiary: root.testDark ? "#a7b0ba" : "#7a7f85"
  readonly property color colorTextThemed: root.testDark ? "#b98cff" : "#8a42ff"
  readonly property color colorTextAttention: root.testDark ? "#f2c773" : "#9c6813"
  readonly property color colorBackgroundSurface: root.testDark ? "#17212a" : "#edeef2"
  readonly property color colorBackgroundPrimary: root.testDark ? "#1e2933" : "#ffffff"
  readonly property color colorBackgroundCard: root.testDark ? "#222e39" : "#ffffff"
  readonly property color colorDividerPrimary: root.testDark ? "#34404c" : "#e2e4e8"
  readonly property color colorStatesBackgroundCardHover: root.testDark ? "#34404c" : "#e2e4e8"
 }
 QtObject { id: application; property var shortcuts: ({messageSendSetting: {checkSendSequence: function(key, modifiers) { return key === Qt.Key_Return || key === Qt.Key_Enter; }}, navigation: {checkCloseSequence: function(key, modifiers) { return key === Qt.Key_Escape; }}}) }
 component SettingsBaseItem: Control {
  id: base
  property string title
  property var maxwellColorTokens: tokens
  property alias contentLoaderItem: settingsLoader.sourceComponent
  signal backClicked()
  background: Rectangle { color: tokens.colorBackgroundSurface }
  contentItem: ColumnLayout {
   spacing: 0
   Label { Layout.fillWidth: true; Layout.preferredHeight: 64; text: base.title; font: Typography.FontUtil.getFont(1,0); color: tokens.colorTextPrimary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; background: Rectangle { color: tokens.colorBackgroundPrimary } }
   Loader { id: settingsLoader; Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.maximumWidth: 640; Layout.alignment: Qt.AlignHCenter | Qt.AlignTop }
  }
 }
''' + settings + '''
 component SyntheticInput: FocusScope {
  id: root
  property alias text: textInput.text
  property var formatIntervals: []
  property bool mentionStarted: false
  property bool botCommandListStarted: false
  property bool quickRepliesStarted: false
  property bool completionListIsEmpty: true
  readonly property bool completionListStarted: mentionStarted || botCommandListStarted || quickRepliesStarted
  signal quickRepliesCancelPressed()
  signal completionEnterPressed()
  signal completionUpPressed()
  signal completionDownPressed()
  signal sendPressed()
  signal closeKeysPressed()
  signal upPressed()
  function moveCursorToEnd() { textInput.cursorPosition = textInput.length; }
  function onCloseKeysPressed() {''' + close_body + '''}
  function processKey(event) {''' + key_body + '''}
  function processUp(event) {''' + up_body + '''}
  function processDown(event) {''' + down_body + '''}
  function simulateKey(key) { var event = {key: key, modifiers: Qt.NoModifier, accepted: false}; if (key === Qt.Key_Up) processUp(event); else if (key === Qt.Key_Down) processDown(event); else processKey(event); return event.accepted; }
  TextArea { id: textInput; anchors.fill: parent; focus: true; font: Typography.FontUtil.getFont(0,0); color: tokens.colorTextPrimary; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; background: Rectangle { color: tokens.colorBackgroundCard }
   function cancelMention() { root.mentionStarted = false; }
  }
 }
 component ComposerHarness: Control {
  id: root
  property bool textInputVisible: true
  property var chatAppearance: tokens
  property var botCommandListContext: null
  property var input: chatInput
  property alias text: chatInput.text
  property int sentCount: 0
  property int insertCount: 0
  property int closeCount: 0
  property bool forceNativeBotActive: false
 property var commonDependency: ({selfInfo: {id: "101"}})
 property var currentOpenedChatId: "chat1"
 property var attachmentUploadModel: ({attachmentsCount: 0})
 property int attachmentActionCount: 0
 property var attachedFiles: []
 property var attachedMedia: []
  property bool delayAttachments: false
  property int pendingAttachmentCount: 0
  property bool nativeClearOnSend: false
  readonly property QtObject viewContext: QtObject {
    signal cleared()
    readonly property var uploadView: ({
      uploadFiles: function(files) { root.attachmentActionCount++; root.attachedFiles = files; root.stageAttachments(files.length); },
      uploadPhotoOrVideo: function(media) { root.attachmentActionCount++; root.attachedMedia = media; root.stageAttachments(media.length); }
    })
    onCleared: {''' + native_cleared_body + '''}
  }
  function stageAttachments(count) {
    if (delayAttachments) pendingAttachmentCount += count;
    else attachmentUploadModel = {attachmentsCount: attachmentUploadModel.attachmentsCount + count};
  }
  function completeUploads() {
    attachmentUploadModel = {attachmentsCount: attachmentUploadModel.attachmentsCount + pendingAttachmentCount};
    pendingAttachmentCount = 0;
  }
  function clearText() { chatInput.text = ""; chatInput.formatIntervals = []; }
  function nativeReset() { attachmentUploadModel = {attachmentsCount: 0}; viewContext.cleared(); }
  function manualSend() { sentCount++; if (nativeClearOnSend) nativeReset(); }
  function setText(text, formats) { chatInput.text = text; chatInput.formatIntervals = formats; insertCount++; }
''' + declarations + '''
  onModQuickReplyAttachmentsRequested: (files, media) => {''' + attachments_body + '''}
  QtObject { id: mentionsListLoader; property bool active: false; property var item: null }
  QtObject { id: botCommandListLoader; property bool active: root.forceNativeBotActive || (!!root.botCommandListContext && chatInput.botCommandListStarted && !root.modQuickRepliesVisible); property var item: fakeNativeBot }
  QtObject { id: fakeNativeBot; property int count: 1; function selectPrevious() {} function selectNext() {} function completeSelected() { root.sentCount++; } }
  readonly property var activeCompletionList: {''' + active_body + '''}
  contentItem: ColumnLayout {
   spacing: 0
''' + menu + '''
   SyntheticInput {
    id: chatInput
    objectName: "syntheticComposerInput"
    Layout.fillWidth: true; Layout.preferredHeight: 110
    quickRepliesStarted: root.modQuickRepliesVisible
    completionListIsEmpty: !root.activeCompletionList || root.activeCompletionList.count === 0
    onQuickRepliesCancelPressed: root.modQuickRepliesDismissedQuery = chatInput.text
    onCompletionUpPressed: if (root.activeCompletionList) root.activeCompletionList.selectPrevious()
    onCompletionDownPressed: if (root.activeCompletionList) root.activeCompletionList.selectNext()
    onCompletionEnterPressed: if (root.activeCompletionList) root.activeCompletionList.completeSelected()
    onSendPressed: root.manualSend()
    onCloseKeysPressed: root.closeCount++
   }
  }
 }
 ComposerHarness { id: composerFixture; objectName: "syntheticComposer"; visible: root.testComposer; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: implicitHeight }
 Component.onCompleted: page = settingsModQuickReplies.createObject(root.contentItem)
 Binding { target: root.page; property: "width"; value: root.width; when: root.page !== null }
 Binding { target: root.page; property: "height"; value: root.height; when: root.page !== null }
 Binding { target: root.page; property: "visible"; value: !root.testComposer; when: root.page !== null }
}
'''
    source = source.replace("http://127.0.0.1:18973/quick-replies", f"http://127.0.0.1:{server.server_port}/quick-replies")
    qml_file = args.output / "SyntheticQuickReplies.qml"
    qml_file.write_text(source, encoding="utf-8")
    loader_file = args.output / "SyntheticMeLoader.qml"
    loader_file.write_text('import QtQuick\nLoader { property bool activeAndVisible: false; active: activeAndVisible; visible: activeAndVisible }\n', encoding="utf-8")
    warnings = []
    def message(kind, _context, text):
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg): warnings.append(text)
    previous = qInstallMessageHandler(message)
    app = QGuiApplication(sys.argv[:1])
    font_util, design_system, qml_utils = FontUtil(), DesignSystem(), QmlUtils()
    qmlRegisterSingletonInstance(FontUtil, "TypographyModule", 1, 0, "FontUtil", font_util)
    qmlRegisterSingletonInstance(DesignSystem, "UiKitModule", 1, 0, "DesignSystem", design_system)
    qmlRegisterSingletonInstance(QmlUtils, "QmlSingletonesModule", 1, 0, "QmlUtils", qml_utils)
    qmlRegisterType(QUrl.fromLocalFile(str(loader_file.resolve())), "UiKitModule", 1, 0, "MeLoader")
    from panel_scroll_qa import register_native_scroll
    register_native_scroll(args.output)
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_file.resolve())))
    if component.isError(): raise RuntimeError("QML compile: " + "\n".join(e.toString() for e in component.errors()))
    window = component.create()
    if window is None: raise RuntimeError("QML creation: " + "\n".join(e.toString() for e in component.errors()))
    def settle(ms=180):
        loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()
    def named(name):
        pending = [window.contentItem()]
        while pending:
            item = pending.pop()
            if item.objectName() == name: return item
            pending.extend(item.childItems())
        raise AssertionError("Missing " + name)
    def click(name):
        assert QMetaObject.invokeMethod(named(name), "clicked", Qt.DirectConnection); settle()
    def screen(name):
        path = args.output / (name + ".png"); assert window.grabWindow().save(str(path)); screenshots.append(str(path.resolve()))
    def posts(): return [r for r in requests if r["method"] == "POST"]
    def set_form(command, title, text):
        for name, value in (("quickReplyCommand",command),("quickReplyTitle",title),("quickReplyText",text)): named(name).setProperty("text",value)
    def variant(value): return value.toVariant() if hasattr(value, "toVariant") else value
    def method(obj, name, *values):
        arguments = [Q_ARG("QVariant", value) for value in values]
        assert QMetaObject.invokeMethod(obj, name, Qt.DirectConnection, *arguments)
        settle()
    checks, screenshots = [], []
    settle(600)
    page, composer = window.property("page"), named("syntheticComposer")
    assert page.property("loaded") is True
    screen("settings-light-650")
    click("newQuickReply")
    set_form("away2", "После отпуска", "Спасибо за сообщение. Отвечу после отпуска.")
    assert len(posts()) == 0
    screen("editor-light-650")
    click("saveQuickReply")
    assert len(posts()) == 1 and len(rows) == 4 and not page.property("editing"), json.dumps({"status":page.property("statusText"),"posts":posts(),"warnings":warnings},ensure_ascii=False)
    click("editReply_away2")
    set_form("away2", "После отпуска", "Обновлённый шаблон.")
    click("saveQuickReply")
    assert next(r["text"] for r in rows if r["command"] == "away2") == "Обновлённый шаблон."
    checks.append("explicit create/edit save only; revision and full replacement roundtrip")
    click("newQuickReply")
    before = len(posts())
    for command,title,text in (("1bad","Название","Текст"),("away","Название","Текст"),("ok","x"*81,"Текст"),("ok","Название","x"*5001),("ok","Название","  ")):
        set_form(command,title,text); click("saveQuickReply"); assert len(posts()) == before
    checks.append("frontend rejects leading digits, duplicate command, long title/body and empty text without HTTP writes")
    set_form("отпуск", "Отпуск", "Вернусь завтра.")
    flags["conflict"] = True
    click("saveQuickReply")
    assert page.property("editing") is True and named("quickReplyText").property("text") == "Вернусь завтра."
    assert "Список изменился" in page.property("statusText")
    flags["conflict"] = False
    click("reloadQuickReplies"); click("saveQuickReply")
    assert any(r["command"] == "отпуск" for r in rows)
    click("editReply_away2"); before = len(posts()); click("deleteQuickReply")
    assert len(posts()) == before and page.property("deleteConfirmation")
    click("deleteQuickReply")
    assert not any(r["command"] == "away2" for r in rows)
    checks.append("409 preserves editor; explicit refresh/retry; deletion needs confirmation")
    click("newQuickReply"); set_form("locked", "Проверка сохранения", "Текст сохранения.")
    flags["stall_post"] = True; click("saveQuickReply")
    assert page.property("pendingRequest") is not None
    assert not named("quickReplyEditor").isEnabled() and not named("quickReplyText").isEnabled()
    release_post.set(); flags["stall_post"] = False; settle(250)
    assert not page.property("editing")
    checks.append("pending POST disables editor, preventing edits from being lost when save completes")
    click("newQuickReply"); set_form("bundle", "Документы для клиента", "Первое сообщение")
    page.setProperty("editorAccountId", "101")
    method(page, "addFiles", ["file:///D:/QA/report%20final.pdf"])
    click("quickReplyAddPart")
    named("quickReplyPart_0").setProperty("text", "Второе сообщение")
    settle()
    assert variant(page.property("editorParts"))[0]["text"] == "Второе сообщение"
    named("quickReplyPart_0").setProperty("text", "Второе сообщение. Исправлено.")
    settle()
    assert variant(page.property("editorParts"))[0]["text"] == "Второе сообщение. Исправлено."
    click("quickReplyAddPart")
    page.setProperty("filesPartIndex", 1); page.setProperty("filesKind", "media")
    method(page, "addFiles", ["file:///D:/QA/photo.jpg"])
    screen("extended-editor-dark-650")
    click("saveQuickReply")
    bundle = next(row for row in rows if row["command"] == "bundle")
    assert bundle["accountId"] == "101" and len(bundle["parts"]) == 2
    assert bundle["attachments"] == [{"url": "file:///D:/QA/report%20final.pdf", "kind": "file"}]
    assert bundle["parts"][1]["attachments"] == [{"url": "file:///D:/QA/photo.jpg", "kind": "media"}]
    checks.append("editor saves account scope, picked local file references and multiple independently editable message parts")
    window.setWidth(390); window.setProperty("testDark",True); settle(); screen("settings-dark-390")
    window.setProperty("testComposer",True); window.setWidth(650); window.setHeight(560); settle()
    input_item = composer.property("input")
    input_item.forceActiveFocus(); input_item.setProperty("text","/"); settle(400)
    assert composer.property("modQuickRepliesVisible") is True
    screen("slash-dark-650")
    def key(value):
        accepted = QMetaObject.invokeMethod(input_item,"simulateKey",Qt.DirectConnection,Q_RETURN_ARG("QVariant"),Q_ARG("QVariant",int(value)))
        settle(80); return accepted
    # Simulate an asynchronous local Loader while a stale bot menu still exists.
    composer.setProperty("forceNativeBotActive",True)
    named("quickRepliesMenuLoader").setProperty("active",False); settle(50)
    assert key(Qt.Key_Return)
    assert composer.property("sentCount") == 0 and input_item.property("text") == "/"
    named("quickRepliesMenuLoader").setProperty("active",True)
    composer.setProperty("forceNativeBotActive",False); settle()
    assert key(Qt.Key_Down); assert key(Qt.Key_Up)
    assert key(Qt.Key_Return)
    assert composer.property("sentCount") == 0 and composer.property("insertCount") == 1
    assert input_item.property("text") == rows[0]["text"]
    assert composer.property("modQuickRepliesVisible") is False
    input_item.setProperty("text","/busy"); settle(); assert key(Qt.Key_Tab)
    assert composer.property("sentCount") == 0 and input_item.property("text") == rows[1]["text"], json.dumps({"sent":composer.property("sentCount"),"text":input_item.property("text"),"visible":composer.property("modQuickRepliesVisible"),"warnings":warnings},ensure_ascii=False)
    checks.append("native patched key handlers: up/down navigate; Enter/Tab substitute only and consume event; no sends")
    input_item.setProperty("text","/вне_офиса"); settle(); screen("slash-dark-650-plain-text")
    assert key(Qt.Key_Escape)
    assert input_item.property("text") == "/вне_офиса" and not composer.property("modQuickRepliesVisible") and composer.property("closeCount") == 0
    input_item.setProperty("text","/ВНЕ"); settle(); assert composer.property("modQuickRepliesVisible")
    window.setWidth(390); window.setProperty("testDark",False); settle(); screen("slash-light-390")
    checks.append("Esc closes only local menu, leaves draft; case-insensitive Cyrillic prefix; PlainText HTML preview")
    composer.setProperty("botCommandListContext",{}); input_item.setProperty("botCommandListStarted",True)
    input_item.setProperty("text","/start"); settle()
    assert not composer.property("modQuickRepliesVisible")
    assert key(Qt.Key_Return) and composer.property("sentCount") == 1
    composer.setProperty("sentCount",0)
    input_item.setProperty("text","/busy"); settle(); assert composer.property("modQuickRepliesVisible")
    input_item.setProperty("text","обычный DRAFT_ONLY_DO_NOT_TRANSMIT"); settle()
    assert not composer.property("modQuickRepliesVisible")
    composer.setProperty("botCommandListContext",None); input_item.setProperty("botCommandListStarted",False)
    composer.setProperty("modQuickReplyRows",[])
    input_item.setProperty("text","/"); settle(80)
    assert key(Qt.Key_Return) and composer.property("sentCount") == 0 and input_item.property("text") == "/"
    input_item.setProperty("text","Обычный текст"); settle()
    assert key(Qt.Key_Return) and composer.property("sentCount") == 1
    checks.append("pending/empty local menu cannot fall through to bot sending; unmatched /start and explicit ordinary Enter preserve native send path")
    composer.setProperty("sentCount", 0)
    composer.setProperty("modQuickReplyRevision", "")
    input_item.setProperty("text", "/bundle"); settle(400)
    assert key(Qt.Key_Return)
    assert input_item.property("text") == "Первое сообщение"
    assert composer.property("sentCount") == 0 and composer.property("attachmentActionCount") == 1
    assert variant(composer.property("attachedFiles")) == ["file:///D:/QA/report%20final.pdf"]
    assert composer.property("modQuickReplyNativeFilesObserved") is True
    assert 'objectName: "quickReplyDraftAddFiles"' not in menu
    screen("extended-draft-light-390")
    method(composer, "modAddQuickReplyFiles")
    assert composer.property("attachmentActionCount") == 1
    click("quickReplyNextPart")
    assert composer.property("modQuickReplyPartIndex") == 0 and input_item.property("text") == "Первое сообщение"
    composer.setProperty("nativeClearOnSend", True)
    assert key(Qt.Key_Return) and composer.property("sentCount") == 1
    assert composer.property("modQuickReplySelected") is not None and composer.property("modQuickReplyPartCleared") is True
    assert input_item.property("text") == "" and composer.property("modQuickReplyAttachmentCount") == 0
    click("quickReplyNextPart")
    assert composer.property("modQuickReplyPartIndex") == 1
    assert input_item.property("text") == "Второе сообщение. Исправлено." and composer.property("sentCount") == 1
    assert key(Qt.Key_Return) and composer.property("sentCount") == 2
    assert composer.property("modQuickReplySelected") is not None
    click("quickReplyNextPart")
    assert composer.property("modQuickReplyPartIndex") == 2 and input_item.property("text") == ""
    assert composer.property("attachmentActionCount") == 2
    assert variant(composer.property("attachedMedia")) == ["file:///D:/QA/photo.jpg"]
    assert composer.property("sentCount") == 2
    assert key(Qt.Key_Return) and composer.property("sentCount") == 3
    assert composer.property("modQuickReplySelected") is None
    assert not named("quickReplyDraftPreview").isVisible()
    method(composer, "nativeReset")
    assert composer.property("modQuickReplySelected") is None and composer.property("attachmentActionCount") == 2
    checks.append("multipart selection immediately stages native files/media exactly once; every send is manual; native cleared preserves remaining parts and closes the final preview")
    rows.append({"id": "c" * 32, "command": "photo", "title": "Фото с подписью", "text": "Подпись к фото",
                 "attachments": [{"url": "file:///D:/QA/photo.jpg", "kind": "media"}]})
    composer.setProperty("delayAttachments", True)
    composer.setProperty("nativeClearOnSend", False)
    input_item.setProperty("text", "/photo"); settle(400); assert key(Qt.Key_Return)
    assert composer.property("attachmentActionCount") == 3 and composer.property("pendingAttachmentCount") == 1
    assert composer.property("modQuickReplyAttachmentCount") == 0 and composer.property("modQuickReplyNativeFilesObserved") is False
    assert composer.property("modQuickReplySelected") is not None
    input_item.setProperty("text", ""); settle()
    assert composer.property("modQuickReplySelected") is not None and named("quickReplyDraftPreview").isVisible()
    method(composer, "completeUploads")
    assert composer.property("modQuickReplyNativeFilesObserved") is True and composer.property("modQuickReplySelected") is not None
    input_item.setProperty("text", "Подпись отредактирована вручную"); settle()
    assert key(Qt.Key_Return) and composer.property("sentCount") == 4
    assert composer.property("modQuickReplySelected") is not None  # Send attempt without native reset is not success.
    composer.setProperty("nativeClearOnSend", True)
    assert key(Qt.Key_Return) and composer.property("sentCount") == 5
    assert composer.property("modQuickReplySelected") is None and not named("quickReplyDraftPreview").isVisible()
    checks.append("single photo/caption draft survives asynchronous upload, caption edits/deletion and a send attempt without native cleared; final native reset hides it automatically")
    # No model entry is fabricated for a pending/rejected native upload.
    input_item.setProperty("text", "/photo"); settle(); assert key(Qt.Key_Return)
    assert composer.property("pendingAttachmentCount") == 1 and composer.property("modQuickReplyNativeFilesObserved") is False
    assert composer.property("modQuickReplySelected") is not None
    click("quickReplyCloseDraftPreview")
    assert composer.property("modQuickReplySelected") is None
    composer.setProperty("pendingAttachmentCount", 0)
    composer.setProperty("delayAttachments", False)
    method(composer, "nativeReset")
    composer.setProperty("attachmentUploadModel", {"attachmentsCount": 10})
    input_item.setProperty("text", "/photo"); settle(); assert key(Qt.Key_Return)
    assert composer.property("attachmentActionCount") == 4 and not composer.property("modQuickReplyFilesAdded")
    assert "Сначала отправь" in composer.property("modQuickReplyDraftStatus")
    method(composer, "nativeReset")
    assert composer.property("modQuickReplySelected") is None
    checks.append("upload submission never fabricates success; pending/missing uploads remain visible; manual close and native reset work; attachment limit rejects without upload")
    input_item.setProperty("text", "/bundle"); settle(); assert key(Qt.Key_Return)
    assert composer.property("modQuickReplySelected") is not None
    composer.setProperty("currentOpenedChatId", "chat2"); settle()
    assert composer.property("modQuickReplySelected") is None
    method(composer, "nativeReset")
    checks.append("chat change clears quick-reply preview without carrying a sequence to the other chat")
    rows.append({"id": "d" * 32, "command": "asyncparts", "title": "Фото и следующее сообщение", "text": "",
                 "attachments": [{"url": "file:///D:/QA/photo.jpg", "kind": "media"}],
                 "parts": [{"text": "Следующее сообщение вручную"}]})
    composer.setProperty("delayAttachments", True)
    input_item.setProperty("text", "/asyncparts"); settle(400); assert key(Qt.Key_Return)
    assert composer.property("pendingAttachmentCount") == 1 and composer.property("modQuickReplyDraftEmpty") is True
    assert not named("quickReplyNextPart").isEnabled()
    click("quickReplyNextPart")
    assert composer.property("modQuickReplyPartIndex") == 0  # No premature overwrite while upload is pending.
    method(composer, "completeUploads")
    assert not named("quickReplyNextPart").isEnabled()
    assert key(Qt.Key_Return) and composer.property("modQuickReplySelected") is not None
    assert named("quickReplyNextPart").isEnabled()
    click("quickReplyNextPart")
    assert composer.property("modQuickReplyPartIndex") == 1 and input_item.property("text") == "Следующее сообщение вручную"
    assert key(Qt.Key_Return) and composer.property("modQuickReplySelected") is None
    composer.setProperty("delayAttachments", False)
    checks.append("media-only multipart draft cannot advance during asynchronous upload or while attached; native clear unlocks next part without dropping it; final manual Send closes preview")
    rows.extend([{"id": "a" * 32, "command": "busy", "title": "Аккаунт 101", "text": "Личный ответ 101", "accountId": "101"},
                 {"id": "b" * 32, "command": "busy", "title": "Аккаунт 202", "text": "Личный ответ 202", "accountId": "202"}])
    input_item.setProperty("text", "/busy"); settle(400)
    selected = variant(composer.property("modFilteredQuickReplies"))
    assert len(selected) == 1 and selected[0]["accountId"] == "101", selected
    assert key(Qt.Key_Return) and input_item.property("text") == "Личный ответ 101"
    composer.setProperty("commonDependency", {"selfInfo": {"id": "202"}}); settle()
    input_item.setProperty("text", "/busy"); settle(200)
    selected = variant(composer.property("modFilteredQuickReplies"))
    assert len(selected) == 1 and selected[0]["accountId"] == "202", selected
    assert key(Qt.Key_Return) and input_item.property("text") == "Личный ответ 202"
    composer.setProperty("commonDependency", {"selfInfo": {"id": "303"}}); settle()
    input_item.setProperty("text", "/busy"); settle(200)
    selected = variant(composer.property("modFilteredQuickReplies"))
    assert len(selected) == 1 and not selected[0].get("accountId"), selected
    assert key(Qt.Key_Return) and input_item.property("text") == "Я на встрече. Вернусь к сообщению позже."
    composer.setProperty("commonDependency", {"selfInfo": {"id": "101"}})
    checks.append("account-specific commands override common commands only in their account; another account cannot select them; common legacy answers remain available")
    assert all(r["path"] == "/quick-replies" and "DRAFT_ONLY_DO_NOT_TRANSMIT" not in r["body"] for r in requests)
    assert all(r["type"].split(";",1)[0] == "application/json" and r["write"] == "quick-replies-v1" and r["origin"] is None for r in posts()), json.dumps([{k:v for k,v in r.items() if k!="body"} for r in posts()])
    checks.append("HTTP contains fixed endpoint and saved templates only; write marker, JSON content type and no Origin")
    window.setProperty("testComposer",False); window.setWidth(650); window.setHeight(1100); settle()
    flags["stall"] = True; click("reloadQuickReplies"); settle(7200)
    assert page.property("pendingRequest") is None and "7 секунд" in page.property("statusText")
    timed_out_message = page.property("statusText")
    release.set(); settle(); assert page.property("statusText") == timed_out_message
    flags["stall"] = False; click("reloadQuickReplies"); assert not page.property("hasError")
    checks.append("real seven-second timeout clears pending; late response rejected and explicit reload recovers")
    server.shutdown(); server.server_close(); window.close(); settle(30)
    qInstallMessageHandler(previous)
    result = {"qt_version":qVersion(),"checks":checks,"warnings":warnings,"screenshots":screenshots,"http_request_count":len(requests),"post_count":len(posts()),"max_started":False,"real_profile_accessed":False,"sources":{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in ("quick_replies_panel.qml","quick_replies_composer.qml","quick_replies_ui.py")},"limitations":["QML native header/Loader, FontUtil and MeLoader use synthetic fixtures, without MAX DLLs.","Keyboard, native upload dispatch and sender-cleared handlers are taken from transformed native sources; custom C++ event delivery and final resource integration need guarded integration QA.","No files are opened/uploaded and no real messages are sent. Simulated missing/pending uploads establish preview state only, not server delivery or native file-error handling."]}
    (args.output/"quick-replies-qa.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if warnings: raise RuntimeError("Qt warnings: "+"\n".join(warnings))


if __name__ == "__main__": main()
