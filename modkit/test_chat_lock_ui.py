"""Pinned transforms and isolated Qt/HTTP tests; no live MAX/PIN/user store."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import chat_lock_ui as target

BASE = Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml/OneMeUi")
SCRATCH = Path("D:/ValMAX/analysis/chat-lock-v1")


class ChatLockUiTests(unittest.TestCase):
    def sources(self):
        if not BASE.is_dir():
            self.skipTest("Immutable native QML fixtures unavailable")
        return {url.rsplit("/", 1)[1]: (BASE / url.rsplit("/", 1)[1]).read_text("utf8") for url in target.RESOURCES}

    def test_real_native_anchors_and_existing_export_order(self):
        import chat_export_ui
        original = self.sources()
        original["MeWindow.qml"] = chat_export_ui.transform_window(original["MeWindow.qml"])
        original["ContactMoreMenu.qml"] = chat_export_ui.transform(original["ContactMoreMenu.qml"])
        transformed = target.transform_sources(original)
        self.assertEqual(transformed["ContactMoreMenu.qml"].count("import QtQuick.Window\n"), 1)
        self.assertIn("Скачать чат", transformed["ContactMoreMenu.qml"])
        self.assertIn("PIN-код чата", transformed["ContactMoreMenu.qml"])
        self.assertIn("openedChatsContext.clear()", transformed["MessengerView.qml"])
        self.assertIn("Binding.RestoreNone", transformed["ChatDelegate.qml"])
        self.assertIn("Binding.RestoreNone", transformed["SearchDelegate.qml"])
        self.assertLess(transformed["MessengerView.qml"].index("Откройте чат и введите PIN"), transformed["MessengerView.qml"].index("chatPreviewDialog.createObject"))
        for name in ("ChatDelegate.qml", "SearchDelegate.qml"):
            native_content = original[name][original[name].index("    contentItem: RowLayout {"):]
            self.assertTrue(transformed[name].endswith(native_content))
        with self.assertRaises(ValueError):
            target.transform_sources(transformed)
        with self.assertRaises(ValueError):
            target.transform_sources({**original, "MessengerView.qml": original["MessengerView.qml"].replace("    contentItem: SplitView {", "    contentItem: Item {")})

    def test_two_added_rcc_nodes_have_exact_baseline_flags_and_unique_cache(self):
        import ui_multi_resource_patch as resources
        baseline = Path("D:/CodexLabs/max-mod-26.35.1/runtime/baseline/max.exe")
        if not baseline.is_file():
            self.skipTest("Immutable baseline unavailable")
        data = baseline.read_bytes()
        for name in ("ChatDelegate.qml", "SearchDelegate.qml"):
            url = ":/qt/qml/OneMeUi/" + name
            decoded = resources.read_source(data, url).decode("utf8")
            self.assertEqual(decoded.replace("\r\n", "\n"), (BASE / name).read_text("utf8").replace("\r\n", "\n"))
            node, cache, *_ = resources.RESOURCES[url]
            key = url[1:].encode("utf-16-le")
            self.assertEqual(data[cache:cache + len(key)], key)
            self.assertEqual(data.count(key), 1)
            self.assertEqual(int.from_bytes(data[node + 4:node + 6], "big"), 1)

    def test_export_guard_covers_pending_destination_and_actual_start(self):
        import chat_export_ui
        transformed = target.transform_export_panel(chat_export_ui.factory_source())
        self.assertIn("destinationDialog.close()", transformed)
        self.assertIn("if (busy) cancelExport()", transformed)
        self.assertEqual(transformed.count("Component.onCompleted:"), 1)
        self.assertEqual(transformed.count("onOpened:"), 1)
        self.assertIn("scopeArmed = true\n            valmaxExportLockInitialized = true", transformed)
        self.assertIn("if (!valmaxExportAllowed) exportDialog.close()\n            else if (selectedScope && !validSelectedScope()) invalidateSelectedScope()", transformed)
        self.assertLess(transformed.index("if (!valmaxExportAllowed)", transformed.index("function startExport(directory)")),
                        transformed.index('request("start"', transformed.index("function startExport(directory)")))
        with self.assertRaises(ValueError):
            target.transform_export_panel(transformed)

    def test_real_qt_catalog_scope_expiry_masking_and_no_pin_echo(self):
        SCRATCH.mkdir(exist_ok=True)
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", __file__, "--qml-smoke"],
                                capture_output=True, text=True, timeout=65)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        proof = json.loads(result.stdout)
        self.assertTrue(proof["noInitialFlash"])
        self.assertTrue(proof["scopeLargeIds"])
        self.assertTrue(proof["nativeAncestorStatePreserved"])
        self.assertTrue(proof["staleUnlockRejected"])
        self.assertTrue(proof["leaveExpiryAndNetworkFailClosed"])
        self.assertTrue(proof["pinFieldsClearedAndMasked"])
        self.assertEqual(proof["warnings"], [])

    def test_actual_me_stack_view_hidden_creation_does_not_capture_false(self):
        result = subprocess.run([sys.executable,"-B","-X","utf8",__file__,"--stack-lifecycle"],
                                capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr[-6000:])
        proof=json.loads(result.stdout)
        self.assertTrue(proof["oldInheritedFalseReproduced"])
        self.assertTrue(proof["newContentReturns"])
        self.assertTrue(proof["ancestorVisibilityAndEnabledPreserved"])
        self.assertTrue(proof["protectedNeverVisible"])
        self.assertTrue(proof["contextSwapAndSettingsReturn"])
        self.assertEqual(proof["warnings"],[])


def qml_smoke():
    import threading
    import time
    import tempfile
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from chat_lock_service import ChatLockService, ChatLockError
    from test_chat_lock_service import FakeProtector

    SCRATCH.mkdir(exist_ok=True)
    fonts = SCRATCH / "fonts"
    fonts.mkdir(exist_ok=True)
    for name in ("arial.ttf", "arialbd.ttf"):
        shutil.copyfile(Path("C:/Windows/Fonts") / name, fonts / name)
    os.environ.update(TEMP=str(SCRATCH), TMP=str(SCRATCH), TMPDIR=str(SCRATCH),
                      QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_QUICK_CONTROLS_STYLE="Basic", QML_DISABLE_DISK_CACHE="1", QT_QPA_FONTDIR=str(fonts))
    from PySide6.QtCore import QObject, Property, Slot, QUrl, qInstallMessageHandler
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    from test_chat_lock_native_ui import prepare_native_fixture

    app = QGuiApplication([])
    native_fixture = prepare_native_fixture(SCRATCH / "native-sdk-qml")
    warnings = []
    qInstallMessageHandler(lambda _kind, _context, message: warnings.append(message))
    with tempfile.TemporaryDirectory(prefix="synthetic-", dir=SCRATCH) as temporary:
        state = {"now": 1000.0, "fail": False, "delayUnlock": False, "postHeadersOk": True, "gets":0}
        service = ChatLockService(Path(temporary) / "store", protector=FakeProtector(),
                                  clock=lambda: state["now"], monotonic=lambda: state["now"])
        account, chat = "9007199254740993", "-9007199254740995"
        synthetic_pin = "681439"
        service.execute("set", {"schema":1,"accountId":account,"chatId":chat,"pin":synthetic_pin,"confirmPin":synthetic_pin})
        service.execute("lock-all", {"schema":1})

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, value):
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                state["gets"] += 1
                if state["fail"]:
                    self.reply(503, {"schema":1,"error":"store_busy"})
                else:
                    self.reply(200, service.catalog())

            def do_POST(self):
                if self.headers.get("X-Max-Mod-Local-Write") != "chat-locks-v1":
                    state["postHeadersOk"] = False
                action = self.path.removeprefix("/chat-locks/")
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if action == "unlock" and state["delayUnlock"]:
                    time.sleep(0.3)
                try:
                    self.reply(200, service.execute(action, value))
                except ChatLockError as error:
                    self.reply(403, {"schema":1,"error":error.code})
                finally:
                    value.clear()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        endpoint = "http://127.0.0.1:" + str(server.server_port)
        controller_url = QUrl.fromLocalFile(str(Path(target.__file__).with_name("chat_lock_controller.qml"))).toString()
        gate = target.GATE.replace("ClientAppearance.AppearanceTokens", "window.colorTokens")
        # MAX's native SVG image provider is covered by anonymous native QA.
        gate = gate.replace('source: "qrc:/icon_pack/privacy.svg"', 'source: ""')
        source = '''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import UiKitModule as UiKit
import TypographyModule as Typography
import ClientAppearanceModule as ClientAppearance
ApplicationWindow {
 id: window
 visible:true; width:500; height:500
 property var colorTokens: ClientAppearance.AppearanceTokens
 readonly property var valmaxChatLockController: controllerLoader.item
 Loader { id:controllerLoader; active:false }
 Component.onCompleted: {controllerLoader.setSource(CONTROLLER_URL,{endpointBase:ENDPOINT,hostItem:window.Overlay.overlay,colorTokens:window.colorTokens});controllerLoader.active=true;}
 Control {
  id:root; objectName:"syntheticChatGate";anchors.fill:parent
  property string selectedAccount:ACCOUNT
  property string selectedChat:CHAT
  property bool hasOpenedChat:true
  property bool isActiveAccount:true
  property bool nativeVisible:true
  property bool nativeEnabled:true
  property var commonDependency: ({selfInfo:{id:root.selectedAccount}})
  property var chatPropertiesContext: ({get:function(){return{chatId:root.selectedChat};}})
  property var accountDependency: ({historyPanelDependency:{openedChatsContext:{clear:function(){root.hasOpenedChat=false;}}}})
  property var snackbarManager: ({showError:function(_value){}})
  onIsActiveAccountChanged:valmaxSynchronizeChatLock()
  Component.onCompleted:valmaxSynchronizeChatLock()
''' + target.SCOPE + '''
  contentItem:Item {
   Rectangle { objectName:"syntheticLeftChatList";width:100;height:parent.height;color:"#ededf2" }
   Control { id:valmaxProtectedHistory; x:100; width:Math.max(0,parent.width-100);height:parent.height;visible:root.nativeVisible;enabled:root.nativeEnabled
''' + gate + '''
    contentItem:Rectangle { objectName:"syntheticPrivateContent";color:"red";Text {text:"SYNTHETIC PRIVATE PREVIEW"}}
   }
  }
 }
}
'''
        for key, value in (("CONTROLLER_URL", controller_url), ("ENDPOINT", endpoint), ("ACCOUNT", account), ("CHAT", chat)):
            source = source.replace(key, json.dumps(value))
        class Utilities(QObject):
            @Property(int,constant=True)
            def maxMsaaSamples(self):return 1
            @Slot(str, result=str)
            def contactIdString(self, value): return value
            @Slot(str, result=str)
            def chatIdString(self, value): return value
        utilities = Utilities()
        engine = QQmlEngine()
        engine.addImportPath(str(native_fixture))
        engine.rootContext().setContextProperty("QmlUtils", utilities)
        component = QQmlComponent(engine)
        component.setData(source.encode(), QUrl.fromLocalFile(str(SCRATCH / "Synthetic.qml")))
        window = component.create()
        if window is None:
            raise AssertionError([error.toString() for error in component.errors()])
        root = window.findChild(QQuickItem, "syntheticChatGate")
        private = window.findChild(QQuickItem, "syntheticPrivateContent")
        left_list = window.findChild(QQuickItem, "syntheticLeftChatList")
        controller = window.property("valmaxChatLockController")
        assert controller is not None
        assert controller.property("ready") is False
        assert private.isVisible() is False
        assert left_list.isVisible() is True
        engine.rootContext().setContextProperty("syntheticController", controller)
        engine.rootContext().setContextProperty("syntheticGate", root)

        def evaluate(code):
            expression = QQmlExpression(QQmlEngine.contextForObject(controller), controller, code)
            value = expression.evaluate()
            if expression.hasError(): raise AssertionError(expression.error().toString())
            return value[0] if isinstance(value, tuple) else value

        def until(predicate, seconds=4):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.processEvents()
                if predicate(): return
                QTest.qWait(10)
            raise AssertionError("Synthetic Qt state did not settle")

        until(lambda: controller.property("ready"))
        assert evaluate("isLocked("+json.dumps(account)+","+json.dumps(chat)+")") is True
        assert evaluate("isLocked('9007199254740992',"+json.dumps(chat)+")") is False
        assert private.isVisible() is False
        pin = controller.findChild(QObject, "valmaxChatPinSecret")
        confirmation = controller.findChild(QObject, "valmaxChatPinConfirmation")
        assert evaluate("pin.editor.echoMode === MeCustomText.Property.EchoMode.Password && confirmation.editor.echoMode === MeCustomText.Property.EchoMode.Password") is True

        def unlock(value=synthetic_pin):
            assert evaluate("openUnlock("+json.dumps(account)+","+json.dumps(chat)+")") is True
            pin.setProperty("text", value)
            evaluate("submitPin()")
            assert pin.property("text") == "" and confirmation.property("text") == ""
            until(lambda: not controller.property("busy"))

        unlock("000000")
        assert controller.property("errorText") == "Неверный PIN."
        assert private.isVisible() is False
        root.setProperty("nativeVisible", False)
        unlock()
        assert private.isVisible() is False
        assert evaluate("isLocked("+json.dumps(account)+","+json.dumps(chat)+")") is False
        root.setProperty("nativeVisible", True)
        until(lambda: private.isVisible())
        assert private.isEnabled() is True
        root.setProperty("nativeEnabled", False)
        evaluate("lockAll()")
        assert private.isVisible() is False
        until(lambda: controller.property("ready"))
        root.setProperty("nativeEnabled", True)
        unlock()
        until(lambda: private.isVisible())
        evaluate("clockMs=Date.now()+120001")
        assert private.isVisible() is False
        unlock()
        until(lambda: private.isVisible())
        assert private.isEnabled() is True

        state["delayUnlock"] = True
        evaluate("lockAll()")
        assert evaluate("openUnlock("+json.dumps(account)+","+json.dumps(chat)+")") is True
        pin.setProperty("text", synthetic_pin)
        evaluate("submitPin()")
        evaluate("lockAll()")
        assert private.isVisible() is False
        until(lambda: not controller.property("busy"))
        assert private.isVisible() is False
        state["delayUnlock"] = False
        unlock()
        until(lambda: private.isVisible())
        state["now"] += 121
        evaluate("refreshCatalog()")
        until(lambda: not private.isVisible())
        unlock()
        until(lambda: private.isVisible())
        root.setProperty("selectedChat", "-88")
        assert evaluate("isLocked("+json.dumps(account)+","+json.dumps(chat)+")") is True
        root.setProperty("selectedChat", chat)
        assert private.isVisible() is False
        state["fail"] = True
        evaluate("refreshCatalog()")
        until(lambda: not controller.property("ready"))
        assert private.isVisible() is False
        state["fail"] = False
        evaluate("refreshCatalog()")
        until(lambda: controller.property("ready"))
        assert private.isVisible() is False
        assert evaluate("openSettings("+json.dumps(account)+","+json.dumps(chat)+",'Синтетический чат')") is True
        evaluate("dialogMode='remove'")
        pin.setProperty("text", synthetic_pin)
        evaluate("submitPin()")
        until(lambda: not controller.property("busy"))
        assert controller.property("hasLocks") is False
        gets_before = state["gets"]
        QTest.qWait(2200)
        assert state["gets"] == gets_before
        assert evaluate("openSettings("+json.dumps(account)+","+json.dumps(chat)+",'Синтетический чат')") is True
        pin.setProperty("text", synthetic_pin)
        confirmation.setProperty("text", synthetic_pin)
        evaluate("submitPin()")
        assert pin.property("text") == "" and confirmation.property("text") == ""
        until(lambda: not controller.property("busy"))
        assert controller.property("hasLocks") is True
        evaluate("lockAll()")
        assert private.isVisible() is False
        assert state["postHeadersOk"]
        for item in controller.findChildren(QObject):
            if item.property("text") is not None:
                assert synthetic_pin not in str(item.property("text"))
        QTest.qWait(40)
        snapshot = window.grabWindow()
        assert not snapshot.isNull()
        snapshot.save(str(SCRATCH / "synthetic-pin-gate.png"))
        window.close()
        window.deleteLater(); app.processEvents()
        del component, engine
        server.shutdown(); server.server_close(); worker.join(timeout=2)
        assert warnings == [], warnings
    print(json.dumps({"noInitialFlash":True,"scopeLargeIds":True,"nativeAncestorStatePreserved":True,
                      "staleUnlockRejected":True,"leaveExpiryAndNetworkFailClosed":True,
                      "pinFieldsClearedAndMasked":True,"warnings":warnings}))


def stack_lifecycle_smoke():
    """Actual bundled MeStackView, baseline content shape, no MAX or HTTP."""
    import hashlib
    output=Path("D:/ValMAX/analysis/chat-lock-v3/stack-lifecycle")
    output.mkdir(parents=True,exist_ok=True)
    fonts=SCRATCH/"fonts"
    fonts.mkdir(parents=True,exist_ok=True)
    for name in ("arial.ttf","arialbd.ttf"):
        if not (fonts/name).is_file():shutil.copyfile(Path("C:/Windows/Fonts")/name,fonts/name)
    os.environ.update(TEMP=str(output),TMP=str(output),TMPDIR=str(output),QT_QPA_PLATFORM="offscreen",
                      QT_QUICK_BACKEND="software",QT_QUICK_CONTROLS_STYLE="Basic",QML_DISABLE_DISK_CACHE="1",QT_QPA_FONTDIR=str(fonts))
    from PySide6.QtCore import QUrl,qInstallMessageHandler
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent,QQmlEngine,QQmlExpression
    from PySide6.QtQuick import QQuickItem,QQuickWindow
    from PySide6.QtTest import QTest
    app=QGuiApplication([]);warnings=[]
    previous=qInstallMessageHandler(lambda _kind,_context,text:warnings.append(text))
    me_stack=(BASE/"MeStackView.qml").read_bytes()
    assert b"contentItem" not in me_stack
    (output/"MeStackView.qml").write_bytes(me_stack)
    # HistoryPanel's contentItem has no native visible/enabled expression.
    panel=(BASE/"HistoryPanel.qml").read_text("utf8")
    native_block=panel.split("    contentItem: MeStackView {",1)[1].split("    }",1)[0]
    assert "visible:" not in native_block and "enabled:" not in native_block
    current="\n".join(line for line in target.GATE.splitlines() if "Binding {" in line)
    assert current.count("Binding.RestoreNone")==2 and "when:" not in current
    old='''Binding { target: valmaxProtectedHistory.contentItem; property: "visible"; value: false; when: root.valmaxChatBlocked; restoreMode: Binding.RestoreBindingOrValue }
Binding { target: valmaxProtectedHistory.contentItem; property: "enabled"; value: false; when: root.valmaxChatBlocked; restoreMode: Binding.RestoreBindingOrValue }'''
    def page(name,bindings):
        return '''Component {
 id:NAMEPage
 Control {
  id:valmaxProtectedHistory;objectName:"NAMEPage";
  contentItem:MeStackView {objectName:"NAMEContent";initialItem:chat}
  BINDINGS
  Rectangle {objectName:"NAMEGate";anchors.fill:parent;color:"#111111";z:1000;visible:root.valmaxChatBlocked}
  Component {id:chat;Control {objectName:"NAMEChat";contentItem:ColumnLayout {
   Rectangle {Layout.fillWidth:true;Layout.fillHeight:true;color:"#ff2222"}
   TextInput {objectName:"NAMEInput";Layout.fillWidth:true;focus:true;onTextChanged:root.privateEdits+=1}
  }}}
 }
}'''.replace("NAME",name).replace("BINDINGS",bindings)
    source='''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ApplicationWindow {
 id:root;visible:true;width:720;height:500
 property bool ownerVisible:false
 property bool ownerEnabled:false
 property bool ready:false
 property string selectedChat:"0"
 readonly property bool protectedChat:selectedChat==="88"
 property int privateEdits:0
 readonly property bool valmaxChatBlocked:!ready||protectedChat
 Rectangle {objectName:"leftList";width:100;height:parent.height;color:"#ededf2"}
 Item {id:owner;x:100;width:parent.width-100;height:parent.height;visible:root.ownerVisible;enabled:root.ownerEnabled
  StackView {id:oldStack;objectName:"oldOuter";width:parent.width/2;height:parent.height;initialItem:oldPage}
  StackView {id:newStack;objectName:"newOuter";x:parent.width/2;width:parent.width/2;height:parent.height;initialItem:newPage}
 }
 Component {id:settings;Rectangle {objectName:"settingsPage";color:"#bbbbbb"}}
 function openSettings(){newStack.pushItem(settings,{},StackView.Immediate);}
 function closeSettings(){newStack.popCurrentItem(StackView.Immediate);}
''' + page("old",old) + page("new",current) + "}\n"
    engine=QQmlEngine();component=QQmlComponent(engine)
    component.setData(source.encode(),QUrl.fromLocalFile(str(output/"Lifecycle.qml")))
    host=component.create()
    if host is None:raise AssertionError([e.toString() for e in component.errors()])
    assert isinstance(host,QQuickWindow)
    QTest.qWait(40)
    old_content=host.findChild(QQuickItem,"oldContent")
    new_content=host.findChild(QQuickItem,"newContent")
    new_page=host.findChild(QQuickItem,"newPage")
    left=host.findChild(QQuickItem,"leftList")
    assert old_content is not None and new_content is not None
    def evaluate(code):
        call=QQmlExpression(QQmlEngine.contextForObject(host),host,code);value=call.evaluate()
        if call.hasError():raise AssertionError(call.error().toString())
        return value[0] if isinstance(value,tuple) else value
    assert not old_content.isVisible() and not new_content.isVisible()
    # A ready callback can precede native account-page activation.
    host.setProperty("ready",True);QTest.qWait(10)
    assert not new_content.isVisible() and not new_content.isEnabled()
    host.setProperty("ownerVisible",True);host.setProperty("ownerEnabled",True);QTest.qWait(30)
    assert not old_content.isVisible(),"Old conditional restore did not reproduce inherited false"
    assert new_content.isVisible() and new_content.isEnabled()
    assert left.isVisible()
    host.setProperty("selectedChat","88")
    assert not new_content.isVisible() and not new_content.isEnabled()
    assert host.findChild(QQuickItem,"newGate").isVisible()
    host.setProperty("selectedChat","0")
    assert new_content.isVisible() and new_content.isEnabled()
    host.setProperty("ownerEnabled",False)
    assert not new_content.isEnabled()
    host.setProperty("selectedChat","88");host.setProperty("selectedChat","0")
    assert not new_content.isEnabled()
    host.setProperty("ownerEnabled",True)
    assert new_content.isEnabled()
    host.setProperty("ownerVisible",False)
    host.setProperty("selectedChat","88");host.setProperty("selectedChat","0")
    assert not new_content.isVisible()
    host.setProperty("ownerVisible",True)
    assert new_content.isVisible()
    evaluate("openSettings()")
    assert not new_page.isVisible() and not new_content.isVisible()
    assert host.findChild(QQuickItem,"settingsPage").isVisible()
    assert left.isVisible()
    evaluate("closeSettings()")
    assert new_page.isVisible() and new_content.isVisible()
    host.setProperty("ready",False)
    assert not new_content.isVisible() and not new_content.isEnabled()
    host.setProperty("selectedChat","0");host.setProperty("ready",True)
    assert new_content.isVisible() and new_content.isEnabled()
    screenshot=output/"old-hidden-new-restored.png"
    assert host.grabWindow().save(str(screenshot))
    proof={"actualBundledMeStackViewSha256":hashlib.sha256(me_stack).hexdigest(),
           "historyPanelDependenciesSimulated":True,"oldInheritedFalseReproduced":True,
           "newContentReturns":True,"ancestorVisibilityAndEnabledPreserved":True,
           "protectedNeverVisible":True,"contextSwapAndSettingsReturn":True,"warnings":warnings}
    (output/"proof.json").write_text(json.dumps(proof,indent=2)+"\n","utf8")
    host.close();host.deleteLater();app.processEvents();qInstallMessageHandler(previous)
    assert not warnings,warnings
    print(json.dumps(proof))


if __name__ == "__main__":
    if sys.argv[1:] == ["--qml-smoke"]:
        qml_smoke()
    elif sys.argv[1:] == ["--stack-lifecycle"]:
        stack_lifecycle_smoke()
    else:
        unittest.main()
