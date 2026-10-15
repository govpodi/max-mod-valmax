"""Synthetic Qt QA for session transfer UI; no MAX/profile/credential access."""
from __future__ import annotations
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import threading


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("LOCALAPPDATA","APPDATA","USERPROFILE","QML_DISK_CACHE_PATH"):
        path=args.output/"synthetic-profile"/name.lower(); path.mkdir(parents=True,exist_ok=True); os.environ[name]=str(path.resolve())
    os.environ.update(QT_QPA_PLATFORM="offscreen",QT_QUICK_BACKEND="software",QT_QUICK_CONTROLS_STYLE="Basic",QML_DISABLE_DISK_CACHE="1")
    fonts=args.output/"fonts";fonts.mkdir(exist_ok=True)
    for name in ("arial.ttf","arialbd.ttf"):shutil.copyfile(Path(r"C:\Windows\Fonts")/name,fonts/name)
    os.environ["QT_QPA_FONTDIR"]=str(fonts.resolve())
    from PySide6.QtCore import QObject,Property,Slot,QEventLoop,QMetaObject,QTimer,QUrl,Qt,QtMsgType,qInstallMessageHandler,qVersion
    from PySide6.QtGui import QFont,QGuiApplication
    from PySide6.QtQml import QQmlEngine,QQmlComponent,qmlRegisterSingletonInstance
    from PySide6.QtQuick import QQuickItem
    class FontUtil(QObject):
        @Property("QVariantMap",constant=True)
        def Styles(self):return {"Body":0,"BodyStrong":1,"Description":2}
        @Property("QVariantMap",constant=True)
        def Sizes(self):return {"Default":0}
        @Slot(int,int,result=QFont)
        def getFont(self,style,_size):
            font=QFont("Arial");font.setPixelSize(16 if style<2 else 14);font.setWeight(QFont.DemiBold if style==1 else QFont.Normal);return font
        @Slot(int,int,result=float)
        def getLineHeight(self,style,_size):return 22.0 if style<2 else 20.0
    class DesignSystem(QObject):
        @Property("QVariantMap",constant=True)
        def SpacingCommon(self):return {"_M":12,"_2XL":24}
        @Property("QVariantMap",constant=True)
        def BorderRadiusCommon(self):return {"_L":12}
        @Property("QVariantMap",constant=True)
        def SizeBase(self):return {"px6":6,"px48":48}
    traffic=[]
    state={"schema":1,"available":True,"version":"26.35.1","state":"idle","error":None}
    flags={"post_error":None,"get_stall":False,"post_hold":False}
    release_get,release_post=threading.Event(),threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            traffic.append({"method":"GET","path":self.path})
            if flags["get_stall"]:release_get.wait(12)
            self.reply(200,dict(state))
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))))
            traffic.append({"method":"POST","path":self.path,"keys":sorted(body),"action":body.get("action"),"file":body.get("file"),"password_present":isinstance(body.get("password"),str),"type":self.headers.get("Content-Type"),"write":self.headers.get("X-Max-Mod-Local-Write"),"origin":self.headers.get("Origin")})
            if flags["post_hold"]:release_post.wait(3)
            if flags["post_error"]:self.reply(409,{"schema":1,"state":"error","error":flags["post_error"]});return
            result={"schema":1,"state":"done" if body["action"]=="export" else "waiting_for_exit","action":body["action"]}
            if body["action"]=="export":result["credentials"]=2
            else:state["state"]="waiting_for_exit"
            self.reply(200,result)
        def reply(self,status,value):
            blob=json.dumps(value).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(blob)));self.end_headers()
            try:self.wfile.write(blob)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def log_message(self,*_args):pass
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    panel_path=Path(__file__).with_name("session_transfer_panel.qml")
    panel=panel_path.read_text(encoding="utf-8").replace("http://127.0.0.1:18973/session-transfer",f"http://127.0.0.1:{server.server_port}/session-transfer")
    panel=panel.replace("echoMode: TextInput.Password", "echoMode: TextInput.Password\n            readonly property bool qaPasswordMasked: echoMode === TextInput.Password")
    source='''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs as Dialogs
import UiKitModule as UiKit
import TypographyModule as Typography
ApplicationWindow {
 id: root
 width:650;height:1180;visible:true
 property bool testDark:false
 property var testedPage:null
 property string businessUrl:""
 property var commonDependency:({clickDispatcher:{onUrlClicked:function(url){root.businessUrl=url;}}})
 signal backClicked()
 color:tokens.colorBackgroundSurface
 QtObject {
  id:tokens
  readonly property color colorTextPrimary:root.testDark?"#edf0f2":"#0c0d0e"
  readonly property color colorTextSecondary:root.testDark?"#a7b0ba":"#5e6269"
  readonly property color colorTextTertiary:root.testDark?"#a7b0ba":"#7a7f85"
  readonly property color colorTextThemed:root.testDark?"#b98cff":"#8a42ff"
  readonly property color colorTextAttention:root.testDark?"#f2c773":"#9c6813"
  readonly property color colorBackgroundSurface:root.testDark?"#17212a":"#edeef2"
  readonly property color colorBackgroundPrimary:root.testDark?"#1e2933":"white"
  readonly property color colorBackgroundCard:root.testDark?"#222e39":"white"
  readonly property color colorDividerPrimary:root.testDark?"#34404c":"#e2e4e8"
 }
 component SettingsBaseItem:Control {
  id:base
  property string title
  property var maxwellColorTokens:tokens
  property alias contentLoaderItem:loader.sourceComponent
  signal backClicked()
  background:Rectangle{color:tokens.colorBackgroundSurface}
  contentItem:ColumnLayout {
   spacing:0
   Label{Layout.fillWidth:true;Layout.preferredHeight:64;text:base.title;font:Typography.FontUtil.getFont(1,0);color:tokens.colorTextPrimary;horizontalAlignment:Text.AlignHCenter;verticalAlignment:Text.AlignVCenter;background:Rectangle{color:tokens.colorBackgroundPrimary}}
   Loader{id:loader;Layout.fillWidth:true;Layout.fillHeight:true;Layout.leftMargin:24;Layout.rightMargin:24;Layout.maximumWidth:640;Layout.alignment:Qt.AlignHCenter|Qt.AlignTop}
  }
 }
'''+panel+'''
 Component.onCompleted:testedPage=settingsModSessionTransfer.createObject(root.contentItem)
 Binding{target:root.testedPage;property:"width";value:root.width;when:root.testedPage!==null}
 Binding{target:root.testedPage;property:"height";value:root.height;when:root.testedPage!==null}
}
'''
    qml_file=args.output/"SyntheticSessionTransfer.qml";qml_file.write_text(source,encoding="utf-8")
    warnings=[]
    def message(kind,_context,text):
        if kind in (QtMsgType.QtWarningMsg,QtMsgType.QtCriticalMsg,QtMsgType.QtFatalMsg):warnings.append(text)
    previous=qInstallMessageHandler(message)
    app=QGuiApplication(sys.argv[:1]);font_util,design_system=FontUtil(),DesignSystem()
    qmlRegisterSingletonInstance(FontUtil,"TypographyModule",1,0,"FontUtil",font_util)
    qmlRegisterSingletonInstance(DesignSystem,"UiKitModule",1,0,"DesignSystem",design_system)
    from panel_scroll_qa import register_native_scroll
    register_native_scroll(args.output)
    engine=QQmlEngine();component=QQmlComponent(engine,QUrl.fromLocalFile(str(qml_file.resolve())))
    if component.isError():raise RuntimeError("QML compile: "+"\n".join(e.toString() for e in component.errors()))
    window=component.create()
    if window is None:raise RuntimeError("QML create: "+"\n".join(e.toString() for e in component.errors()))
    def settle(ms=180):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def named(name):
        pending=[window.contentItem()]
        while pending:
            item=pending.pop()
            if item.objectName()==name:return item
            pending.extend(item.childItems())
        raise AssertionError(name)
    def click(name):assert QMetaObject.invokeMethod(named(name),"clicked",Qt.DirectConnection);settle()
    def posts():return [r for r in traffic if r["method"]=="POST"]
    def password(name,value):named(name).setProperty("text",value)
    def cleared():return all(named(n).property("text")=="" for n in ("sessionExportPassword","sessionExportConfirmation","sessionImportPassword"))
    def screen(name):
        path=args.output/(name+".png");assert window.grabWindow().save(str(path));screenshots.append(str(path.resolve()))
    checks,screenshots=[],[]
    settle(500);page=window.property("testedPage")
    assert page.property("available") and len(posts())==0
    assert all(named(n).property("qaPasswordMasked") for n in ("sessionExportPassword","sessionExportConfirmation","sessionImportPassword"))
    screen("transfer-light-650")
    page.setProperty("exportFile","file:///D:/synthetic-account.maxsession")
    assert len(posts())==0
    password("sessionExportPassword","synthetic-pass-12");password("sessionExportConfirmation","different-pass-12");click("exportSession")
    assert not posts() and cleared() and "не совпадают" in page.property("statusText")
    password("sessionExportPassword","short");password("sessionExportConfirmation","short");click("exportSession")
    assert not posts() and cleared() and "короткий" in page.property("statusText")
    password("sessionExportPassword","x"*1025);password("sessionExportConfirmation","x"*1025);click("exportSession")
    assert not posts() and cleared() and "длинный" in page.property("statusText")
    checks.append("native FileDialog types compile without opening them; selecting file does not post; masked passwords and limits clear failed input")
    password("sessionExportPassword","synthetic-pass-12");password("sessionExportConfirmation","synthetic-pass-12")
    flags["post_hold"]=True;click("exportSession")
    assert cleared() and page.property("pendingRequest") is not None and not named("sessionExportForm").isEnabled()
    assert not named("sessionImportForm").isEnabled()
    assert any(child.property("interval")==30000 for child in page.children())
    release_post.set();flags["post_hold"]=False;settle(250)
    assert len(posts())==1 and "копия создана" in page.property("statusText")
    checks.append("explicit export only; passwords clear at dispatch, pending forms disabled, successful response shown without credential values")
    flags["post_error"]="file_exists"
    password("sessionExportPassword","synthetic-pass-12");password("sessionExportConfirmation","synthetic-pass-12");click("exportSession")
    assert cleared() and "уже существует" in page.property("statusText") and page.property("operationState")=="error"
    flags["post_error"]="destination_not_empty"
    page.setProperty("importFile","file:///D:/synthetic-account.maxsession")
    password("sessionImportPassword","synthetic-pass-12");click("importSession")
    assert cleared() and "пустой профиль" in page.property("statusText")
    checks.append("exclusive-create and destination-not-empty errors use fixed Russian text; no overwrite or merge claim")
    flags["post_error"]=None
    password("sessionImportPassword","synthetic-pass-12");click("importSession")
    assert page.property("operationState")=="waiting_for_exit" and not named("sessionImportForm").isEnabled()
    assert "Выход" in page.property("statusText") and "20 секунд" in page.property("statusText")
    count=len(posts());settle(2300);assert len(posts())==count
    checks.append("import waits for own client exit; controls lock, polling uses GET only and never repeats POST")
    state.update(state="error",error="client_still_running")
    click("refreshSessionTransfer")
    assert page.property("available") and page.property("operationState")=="error"
    assert not page.property("busy") and named("sessionImportForm").isEnabled()
    assert "Выход" in page.property("statusText") and cleared()
    page.setProperty("available",False)
    click("refreshSessionTransfer")
    assert page.property("available") and named("sessionExportForm").isEnabled()
    assert len(posts())==count
    checks.append("GET available:true/error updates availability and leaves waiting state; fresh error page and failed worker permit explicit retry")
    click("openBusinessCabinet");assert window.property("businessUrl")=="https://business.max.ru/self/"
    assert all(r["path"]=="/session-transfer" for r in traffic)
    assert all(r["keys"]==["action","file","password","schema"] and r["password_present"] and r["type"].split(";",1)[0]=="application/json" and r["write"]=="session-transfer-v1" and r["origin"] is None for r in posts())
    checks.append("exact local POST contract/write marker/no Origin; business dispatch uses only canonical public URL")
    state.update(state="idle",error=None);click("refreshSessionTransfer")
    window.setWidth(390);window.setProperty("testDark",True);settle();screen("transfer-dark-390")
    password("sessionImportPassword","synthetic-pass-12")
    page.setProperty("visible",False);settle(20);assert cleared();page.setProperty("visible",True)
    flags["get_stall"]=True;click("refreshSessionTransfer");settle(7200)
    assert page.property("pendingRequest") is None and cleared() and "не ответил вовремя" in page.property("statusText")
    message_after_timeout=page.property("statusText")
    release_get.set();settle();assert page.property("statusText")==message_after_timeout
    flags["get_stall"]=False;click("refreshSessionTransfer");assert not page.property("hasError")
    checks.append("hide clears passwords; actual seven-second GET watchdog rejects late callback; explicit refresh recovers")
    server.shutdown();server.server_close();window.close();settle(30);qInstallMessageHandler(previous)
    result={"qt_version":qVersion(),"checks":checks,"warnings":warnings,"screenshots":screenshots,"post_count":len(posts()),"max_started":False,"real_profiles_or_credentials_accessed":False,"archive_files_read_or_written":False,"file_dialogs_opened":False,"source_sha256":hashlib.sha256(panel_path.read_bytes()).hexdigest(),"limitations":["FontUtil, DesignSystem and SettingsBaseItem are synthetic; real FileDialog type is compiled but never opened.","Fake endpoint never reads credentials, creates archives, or starts import workers. Backend integration is validated separately.","Password clearing removes QML field contents; managed string memory is not promised to be securely zeroed."]}
    (args.output/"session-transfer-qa.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(result,ensure_ascii=False,indent=2))
    if warnings:raise RuntimeError("Qt warnings: "+"\n".join(warnings))


if __name__=="__main__":main()
