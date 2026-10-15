"""Native MAX QML UI fixtures; only unavailable C++ registrations are simulated.

Visual fixtures reuse byte-identical baseline UiKit QML. Native-registry
compilation remains a separate anonymous MAX check; no real profile is opened.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

BASE = Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml")
OUTPUT = Path("D:/ValMAX/analysis/chat-lock-v2/native-ui")
CONTROLLER = Path(__file__).with_name("chat_lock_controller.qml")
_CPP_FIXTURES = []


def register_cpp_fixtures(design_values, style_names):
    from PySide6.QtCore import QObject, Property, Slot, Qt
    from PySide6.QtGui import QFont
    from PySide6.QtQml import qmlRegisterSingletonInstance
    design_type = type("NativeDesignFixture",(QObject,),{
        name:Property("QVariantMap",lambda self,value=value:value,constant=True)
        for name,value in design_values.items()})
    styles={name:index for index,name in enumerate(style_names)}
    class FontFixture(QObject):
        @Property("QVariantMap",constant=True)
        def Styles(self):return styles
        @Property("QVariantMap",constant=True)
        def Sizes(self):return {"Default":0}
        def size(self,style):return 20 if style==styles["Header"] else 14 if style in (styles["Description"],styles.get("Detail")) else 16
        @Slot(int,result=QFont)
        @Slot(int,int,result=QFont)
        def getFont(self,style,size=0):
            value=QFont("Arial");value.setPixelSize(self.size(style))
            value.setBold(style in [styles.get(key) for key in ("Header","BodyStrong","ActionLarge","ActionMedium")])
            return value
        @Slot(int,result=QFont)
        @Slot(int,int,result=QFont)
        def getMonospaceFont(self,style,size=0):return self.getFont(style,size)
        @Slot(int,result=int)
        @Slot(int,int,result=int)
        def getLineHeight(self,style,size=0):return self.size(style)+4
    class ButtonsFixture(QObject):
        @Slot(int,result=int)
        def getTypography(self,size):return styles["ActionLarge"]
        @Slot(int,result=int)
        def getBorderRadius(self,size):return 16
        @Slot(int,bool,result=int)
        def getIconSize(self,size,has_text):return 24
        @Slot(int,bool,result=int)
        def getHorizontalPadding(self,size,has_text):return 16
    property_type=type("NativeTextPropertyFixture",(QObject,),{
        "EchoMode":Property("QVariantMap",lambda self:{"Default":0,"Password":2},constant=True),
        "AlignLeft":Property(int,lambda self:int(Qt.AlignmentFlag.AlignLeft),constant=True),
        "AlignHCenter":Property(int,lambda self:int(Qt.AlignmentFlag.AlignHCenter),constant=True),
        "AlignRight":Property(int,lambda self:int(Qt.AlignmentFlag.AlignRight),constant=True),
        "ElideRight":Property(int,lambda self:int(Qt.TextElideMode.ElideRight.value),constant=True)})
    for obj,module,name in ((design_type(),"UiKitModule","DesignSystem"),
                            (FontFixture(),"TypographyModule","FontUtil"),
                            (property_type(),"MeCustomText","Property"),
                            (ButtonsFixture(),"QmlSingletonesModule","ButtonsKitHelper")):
        qmlRegisterSingletonInstance(type(obj),module,1,0,name,obj)
        _CPP_FIXTURES.append(obj)


def prepare_native_fixture(directory):
    """Keep actual UiKit composition, typography hooks and native token names.

    Qt's Python runtime does not register MAX's C++ singletons/text engine.
    Only those missing registrations are replaced here, never in production.
    """
    directory = Path(directory)
    ui = directory / "UiKitModule"
    ui.mkdir(parents=True, exist_ok=True)
    available = {path.stem:path for path in (BASE / "UiKitModule").glob("*.qml")}
    if not available:
        raise RuntimeError("Pinned baseline UiKit sources unavailable")
    pending = ["BaseModalDialog","MeInputBackground","MeLineEdit","MeText","MeHintLine",
               "MeCellAction","MePrimaryButton","MeSecondaryButton","MeGhostButton","SvgIcon"]
    selected = set()
    while pending:
        name = pending.pop()
        if name in selected or name not in available:continue
        selected.add(name)
        pending.extend(re.findall(r"(?:UiKit\.)?\b([A-Z]\w+)\s*\{",available[name].read_text("utf8")))
    source_files = [available[name] for name in sorted(selected)]
    pins = {}
    for path in source_files:
        data = path.read_bytes()
        (ui / path.name).write_bytes(data)
        pins[path.name] = hashlib.sha256(data).hexdigest()
    declarations = ["module UiKitModule"]
    declarations.extend(f"{path.stem} 1.0 {path.name}" for path in source_files)
    declarations += ["singleton Utils 1.0 Utils.qml"]
    (ui / "qmldir").write_text("\n".join(declarations) + "\n", "utf8")
    corpus = "\n".join(path.read_text("utf8") for path in source_files)
    groups = {}
    for group, member in re.findall(r"DesignSystem\.(\w+)\.(\w+)", corpus):
        groups.setdefault(group, set()).add(member)
    values = {name: {member: index for index, member in enumerate(sorted(members))}
              for name, members in groups.items()}
    values.update({"SpacingCommon": {"_Zero":0,"_2XS":2,"_XS":4,"_Shalf":6,"_S":8,
                                     "_Mhalf":10,"_M":12,"_L":16,"_XLhalf":20,"_XL":24,"_2XL":32},
                   "SizeBase": {"px"+str(n):n for n in range(129)},
                   "BorderRadiusCommon": {"_Zero":0,"_XS":4,"_S":8,"_Mhalf":10,"_M":12,"_L":16,"_XL":20,"_2XL":24},
                   "Size":{"Small":0,"Medium":1,"Large":2},
                   "Buttons2Size":{"Buttons2xSmall":28,"Buttons2Small":36,"Buttons2Medium":44,"Buttons2Large":52},
                   "Mode":{"Primary":0,"Secondary":1,"Tertiary":2,"Link":3,"InlineKeyboard":4},
                   "Appearance":{"Neutral":0,"Themed":1,"Negative":2,"ContrastStatic":3},
                   "HintType":{"Hint":0,"DynamicHint":1,"Error":2,"Success":3}})
    (ui / "Utils.qml").write_text("pragma Singleton\nimport QtQml\nQtObject { readonly property bool insertPeriodOnDoubleSpace:false }\n", "utf8")

    custom = directory / "MeCustomText"
    custom.mkdir(exist_ok=True)
    (custom / "qmldir").write_text("module MeCustomText\nText 1.0 Text.qml\nTextInput 1.0 TextInput.qml\n", "utf8")
    (custom / "Text.qml").write_text('''import QtQuick
Text {
 property bool useLinkFormats:false
 property bool ignoreLineBreaks:false
 property font monospaceFont
 property font headingFont
 property font quoteFont
 property real emojiRightMargin:0
 property color selectionColor:"transparent"
 property var photoPreviewsModel:[]
 property var avatarsModel:[]
 property var resourceImagesModel:[]
 property var imageUrlModel:[]
 property var stickersModel:[]
 property var animojisModel:[]
 property var localVideoPreviewModel:[]
 property var verificationMarksModel:[]
 property var quoteRectModel:[]
 wrapMode: Text.WordWrap
 textFormat: Text.PlainText
}
''', "utf8")
    (custom / "TextInput.qml").write_text('''import QtQuick
import QtQuick.Controls
TextField {
 property bool ignoreLineBreaks:true
 property int maximumLineCount:1
 property bool insertPeriodOnDoubleSpaceEnabled:false
 property real cursorWidth:1
 readonly property bool mousePressed:false
 readonly property bool hasSelection:selectionStart!==selectionEnd
 property color cursorColor:"black"
 background:null
 padding:0
 function clearSelection() { deselect(); }
}
''', "utf8")
    typography = directory / "TypographyModule"
    typography.mkdir(exist_ok=True)
    (typography / "qmldir").write_text("module TypographyModule\n", "utf8")
    styles = sorted(set(re.findall(r"FontUtil\.Styles\.(\w+)", corpus)) | {"Body","Description","Header","BodyStrong"})
    register_cpp_fixtures(values,styles)
    appearance = directory / "ClientAppearanceModule"
    appearance.mkdir(exist_ok=True)
    (appearance / "qmldir").write_text("module ClientAppearanceModule\nsingleton AppearanceTokens 1.0 AppearanceTokens.qml\n", "utf8")
    token_names = set(re.findall(r"readonly property color (\w+):", (BASE / "ClientAppearanceModule/AppearanceTokensObject.qml").read_text("utf8")))
    colors = {name: "#717780" for name in token_names}
    colors.update(colorBackgroundPrimary="#ffffff",colorBackgroundSecondary="#edeef2",colorBackgroundCard="#ffffff",
                  colorBackgroundTertiary="#f0f1f4",colorBackgroundOverlaySecondary="#66000000",
                  colorTextPrimary="#111318",colorTextSecondary="#737982",colorTextTertiary="#8a9099",
                  colorTextPrimaryInverseStatic="#ffffff",colorTextThemed="#633bff",colorTextNegative="#d64150",
                  colorButtonPrimary="#633bff",colorButtonSecondary="#e8e8ed",colorButtonGhost="#00000000",
                  colorStrokeTransparent="#00000000",colorStatesButtonPrimaryHover="#704aff",
                  colorStatesButtonPrimaryPressed="#5230dd",colorStatesButtonPrimaryDisabled="#bbb6d5",
                  colorStatesButtonSecondaryHover="#ddddE5",colorStatesButtonSecondaryPressed="#d2d2dc",
                  colorStatesButtonGhostHover="#10000000",colorStatesButtonGhostPressed="#18000000",
                  colorStatesButtonGhostDisabled="#00000000",colorStatesBackgroundCardDisabled="#e8e8ed",
                  colorStatesTextPrimaryDisabled="#b0b2b9",colorStatesTextPrimaryInverseStaticDisabled="#ffffff",
                  colorIconThemed="#633bff",colorIconPrimary="#737982",colorIconNegative="#d64150")
    lines = [f"property color {name}: {json.dumps(value)}" for name,value in sorted(colors.items())]
    (appearance / "AppearanceTokens.qml").write_text("pragma Singleton\nimport QtQuick\nQtObject {\n" + "\n".join(lines) + "\n}\n", "utf8")
    components = directory / "ru/oneme/components"
    components.mkdir(parents=True, exist_ok=True)
    (components / "qmldir").write_text("module ru.oneme.components\nFixtureType 1.0 FixtureType.qml\n", "utf8")
    (components / "FixtureType.qml").write_text("import QtQml\nQtObject {}\n", "utf8")
    for name in ("QmlSingletonesModule",):
        stub=directory / name;stub.mkdir(exist_ok=True)
        (stub/"qmldir").write_text("module "+name+"\nFixtureType 1.0 FixtureType.qml\n","utf8")
        (stub/"FixtureType.qml").write_text("import QtQml\nQtObject {}\n","utf8")
    lottie=directory/"LottieModule";lottie.mkdir(exist_ok=True)
    (lottie/"qmldir").write_text("module LottieModule\nPlayer 1.0 Player.qml\n","utf8")
    (lottie/"Player.qml").write_text('''import QtQuick
Item { property var processor; property url lottieResource; property bool playing:false; property bool infiniteLoop:false; property color fillColor; property color strokeColor }
''',"utf8")
    (directory / "baseline-ui-source-pins.json").write_text(json.dumps(pins,indent=2)+"\n", "utf8")
    return directory


class ChatLockNativeUiTests(unittest.TestCase):
    def test_source_uses_sdk_dialog_password_actions_and_native_tokens(self):
        source = CONTROLLER.read_text("utf8")
        self.assertIn("UiKit.BaseModalDialog {", source)
        self.assertIn("dialogContentItem: ColumnLayout", source)
        self.assertIn("echoMode: MeCustomText.Property.EchoMode.Password", source)
        self.assertIn("UiKit.MeInputBackground", source)
        self.assertIn("UiKit.MeLineEdit", source)
        self.assertIn("UiKit.MeCellAction", source)
        self.assertNotRegex(source, r"(?m)^\s*(?:Dialog|Button|TextField|Label)\s*\{")
        self.assertNotIn("passwordMaskDelay",source)
        self.assertIn("confirmButtonLoading: root.busy",source)
        self.assertIn("useNegativeConfirmMode: root.dialogMode === \"remove\"",source)

    def test_actual_baseline_qml_light_dark_narrow_and_native_password_bridge(self):
        OUTPUT.mkdir(parents=True,exist_ok=True)
        result=subprocess.run([sys.executable,"-B","-X","utf8",__file__,"--render"],
                              capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr[-6000:])
        proof=json.loads(result.stdout)
        self.assertEqual(proof["warnings"],[])
        self.assertTrue(proof["nativePasswordMasked"])
        self.assertTrue(proof["buffersCleared"])
        self.assertTrue(proof["digitFilter"])
        self.assertTrue(proof["fitsNarrow"])
        self.assertEqual(len(proof["screenshots"]),4)


def render():
    import threading
    import time
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    OUTPUT.mkdir(parents=True,exist_ok=True)
    fonts=OUTPUT/"fonts";fonts.mkdir(exist_ok=True)
    for name in ("arial.ttf","arialbd.ttf"):
        shutil.copyfile(Path("C:/Windows/Fonts")/name,fonts/name)
    os.environ.update(TEMP=str(OUTPUT),TMP=str(OUTPUT),TMPDIR=str(OUTPUT),QT_QPA_PLATFORM="offscreen",
                      QT_QUICK_BACKEND="software",QT_QUICK_CONTROLS_STYLE="Basic",QML_DISABLE_DISK_CACHE="1",QT_QPA_FONTDIR=str(fonts))
    from PySide6.QtCore import QObject,QUrl,qInstallMessageHandler,qVersion
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent,QQmlEngine,QQmlExpression
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    app=QGuiApplication([]);warnings=[]
    previous=qInstallMessageHandler(lambda _kind,_context,text:warnings.append(text))
    modules=prepare_native_fixture(OUTPUT/"qml")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length",0)))
            body=b'{"schema":1,"ready":true,"protected":[]}'
            self.send_response(200);self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    engine=QQmlEngine();engine.addImportPath(str(modules))
    source='''import QtQuick
import QtQuick.Controls
import UiKitModule as UiKit
import TypographyModule as Typography
import ClientAppearanceModule as Appearance
ApplicationWindow {
 id:host; width:760;height:660;visible:true
 color: Appearance.AppearanceTokens.colorBackgroundSecondary
 readonly property var controller:loader.item
 Loader {id:loader;active:false}
 Component.onCompleted:{loader.setSource(CONTROLLER,{endpointBase:ENDPOINT,hostItem:host.Overlay.overlay,colorTokens:Appearance.AppearanceTokens});loader.active=true;}
 function setDark(){
  const c=Appearance.AppearanceTokens;
  c.colorBackgroundPrimary="#25232c";c.colorBackgroundSecondary="#17151d";c.colorBackgroundCard="#25232c";
  c.colorBackgroundTertiary="#37343e";c.colorTextPrimary="#f3f1f7";c.colorTextSecondary="#aaa6b3";
  c.colorTextTertiary="#a09baa";c.colorButtonSecondary="#49454f";c.colorStatesButtonSecondaryHover="#514c59";
  c.colorStatesButtonGhostHover="#20ffffff";c.colorStatesButtonGhostPressed="#30ffffff";
 }
}'''.replace("CONTROLLER",json.dumps(QUrl.fromLocalFile(str(CONTROLLER)).toString())).replace("ENDPOINT",json.dumps("http://127.0.0.1:"+str(server.server_port)))
    component=QQmlComponent(engine);component.setData(source.encode(),QUrl.fromLocalFile(str(OUTPUT/"Host.qml")))
    if component.isError():raise AssertionError(" | ".join(e.toString() for e in component.errors()))
    host=component.create()
    if host is None:raise AssertionError("Host creation: "+" | ".join(warnings))
    assert isinstance(host,QQuickWindow)
    QTest.qWait(150)
    controller=host.property("controller")
    if controller is None:raise AssertionError("Controller creation: "+" | ".join(warnings))
    deadline=time.monotonic()+4
    while not controller.property("ready") and time.monotonic()<deadline:
        app.processEvents();time.sleep(.01)
    def evaluate(code,obj=host):
        call=QQmlExpression(QQmlEngine.contextForObject(obj),obj,code);value=call.evaluate()
        if call.hasError():raise AssertionError(call.error().toString())
        return value[0] if isinstance(value,tuple) else value
    assert controller.property("ready") is True
    assert evaluate('controller.setCurrentScope("42","7");controller.openSettings("42","7","Личный чат")') is True
    QTest.qWait(100)
    dialog=controller.findChild(QObject,"valmaxChatPinDialog")
    assert dialog.property("opened") is True, (dialog.property("visible"),dialog.property("width"),dialog.property("height"))
    if warnings:raise AssertionError(" | ".join(warnings))
    pin=controller.findChild(QObject,"valmaxChatPinSecret")
    confirmation=controller.findChild(QObject,"valmaxChatPinConfirmation")
    assert evaluate("pin.editor.echoMode === MeCustomText.Property.EchoMode.Password",controller) is True
    pin.setProperty("text","318642");QTest.qWait(30)
    assert evaluate("pin.editor.text === pin.text",controller) is True
    evaluate('pin.editor.text="3a1-8642"',controller);QTest.qWait(30)
    assert pin.property("text")=="318642"
    evaluate("clearSecrets()",controller)
    assert pin.property("text")==confirmation.property("text")==""
    screenshots=[]
    def snapshot(label):
        assert dialog.property("opened") is True
        QTest.qWait(100);path=OUTPUT/(label+".png");assert host.grabWindow().save(str(path));screenshots.append(str(path))
    snapshot("pin-set-light")
    evaluate('setDark()');snapshot("pin-set-dark")
    host.setWidth(360);host.setHeight(660);QTest.qWait(100)
    dialog=controller.findChild(QObject,"valmaxChatPinDialog")
    assert dialog.property("width")<=328
    assert dialog.property("height")<=628
    snapshot("pin-set-narrow")
    evaluate('dialogMode="manage";dialogTitle="Чат защищён PIN"',controller)
    snapshot("pin-manage-dark")
    proof={"syntheticCppRegistrations":True,"actualBaselineUiKitSources":True,"nativeClientStarted":False,
           "qtVersion":qVersion(),"nativePasswordMasked":True,"buffersCleared":True,"digitFilter":True,
           "fitsNarrow":True,"warnings":warnings,"screenshots":screenshots}
    (OUTPUT/"ui-proof.json").write_text(json.dumps(proof,ensure_ascii=False,indent=2)+"\n","utf8")
    host.close();host.deleteLater();app.processEvents();qInstallMessageHandler(previous)
    server.shutdown();server.server_close();thread.join(3)
    print(json.dumps(proof,ensure_ascii=False))


if __name__=="__main__":
    if "--render" in sys.argv:render()
    else:unittest.main()
