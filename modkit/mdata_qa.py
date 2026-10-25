"""Offscreen MData UI QA; synthetic HTTP and fake closeMessenger only."""
from __future__ import annotations
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from time import monotonic


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    for name in ("LOCALAPPDATA","APPDATA","USERPROFILE","QML_DISK_CACHE_PATH"):
        path=args.output/"synthetic-profile"/name.lower();path.mkdir(parents=True,exist_ok=True);os.environ[name]=str(path.resolve())
    os.environ.update(QT_QPA_PLATFORM="offscreen",QT_QUICK_BACKEND="software",QT_QUICK_CONTROLS_STYLE="Basic",QML_DISABLE_DISK_CACHE="1")
    fonts=args.output/"fonts";fonts.mkdir(exist_ok=True)
    for name in ("arial.ttf","arialbd.ttf"):shutil.copyfile(Path(r"C:\Windows\Fonts")/name,fonts/name)
    os.environ["QT_QPA_FONTDIR"]=str(fonts.resolve())
    from PySide6.QtCore import QObject,Property,Signal,Slot,QEventLoop,QMetaObject,QTimer,QUrl,Qt,QtMsgType,Q_ARG,qInstallMessageHandler,qVersion
    from PySide6.QtGui import QFont,QGuiApplication
    from PySide6.QtQml import QQmlEngine,QQmlComponent,qmlRegisterSingletonInstance,qmlRegisterType
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
        def SpacingCommon(self):return {"_M":12,"_L":16,"_2XL":24}
        @Property("QVariantMap",constant=True)
        def BorderRadiusCommon(self):return {"_S":4,"_L":12}
        @Property("QVariantMap",constant=True)
        def SizeBase(self):return {"px6":6,"px12":12,"px20":20,"px48":48}
    class FakeDelegate(QObject):
        def __init__(self):super().__init__();self.close_calls=0
        @Slot()
        def closeMessenger(self):self.close_calls+=1
    class FakeApplication(QObject):
        accountModelChanged=Signal()
        def __init__(self):super().__init__();self.rows=[("42","Личный аккаунт"),("7","Работа"),("9","Третий")]
        @Property("QVariantList",notify=accountModelChanged)
        def accountModel(self):return [{"accountId":rid,"dependency":{"commonDependency":{"selfInfo":{"id":rid,"name":name}}}} for rid,name in self.rows]
        def removeAccount(self,rid):self.rows=[row for row in self.rows if row[0]!=rid];self.accountModelChanged.emit()
    class FakeQmlUtils(QObject):
        @Slot(str,result=str)
        def contactIdString(self,value):return value
    state={"schema":1,"available":True,"state":"idle","action":None,"error":None,"profiles":3,"backupAvailable":True}
    flags={"ack":"valid","preview":"valid","post_stall":False,"get_stall":False}
    release_post,release_get=threading.Event(),threading.Event()
    traffic=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            traffic.append({"method":"GET","path":self.path})
            if flags["get_stall"]:release_get.wait(12)
            self.reply(200,dict(state))
        def do_POST(self):
            value=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))))
            traffic.append({"method":"POST","path":self.path,"keys":sorted(value),"action":value.get("action"),"mode":value.get("mode"),"accountIds":value.get("accountIds"),"fingerprint_matches":value.get("fingerprint")=="a"*64,"file_extension":str(value.get("file","")).rsplit(".",1)[-1].lower(),"password_present":isinstance(value.get("password"),str),"write":self.headers.get("X-Max-Mod-Local-Write"),"type":self.headers.get("Content-Type"),"origin":self.headers.get("Origin")})
            if flags["post_stall"]:release_post.wait(40)
            if self.path=="/mdata/inspect":
                preview={"schema":1,"kind":"login","fingerprint":"a"*64,"profiles":2,"accounts":[{"accountId":"42","displayName":"<b>Личный аккаунт</b>","shortName":"ЛА"},{"accountId":"7","displayName":"Работа","shortName":"Р"}]}
                if flags["preview"]=="error":self.reply(400,{"schema":1,"error":"authentication_failed"});return
                if flags["preview"]=="legacy":self.reply(400,{"schema":1,"error":"unsupported_version"});return
                if flags["preview"]=="bad_schema":preview["schema"]=9
                elif flags["preview"]=="bad_count":preview["profiles"]=3
                elif flags["preview"]=="bad_id":preview["accounts"][0]["accountId"]="9223372036854775808"
                elif flags["preview"]=="duplicate":preview["accounts"][1]["accountId"]="42"
                elif flags["preview"]=="long_name":preview["accounts"][0]["displayName"]="x"*257
                elif flags["preview"]=="controls":preview["accounts"][0]["displayName"]="x\u202ey"
                elif flags["preview"]=="bad_fingerprint":preview["fingerprint"]="not-a-digest"
                elif flags["preview"]=="unicode_name":preview["accounts"][0]["displayName"]="🙂"*256;preview["accounts"][0]["shortName"]="😀"*32
                self.reply(200,preview);return
            action="export" if self.path=="/mdata/export-selected" else "import" if self.path=="/mdata/import-confirmed" else value["action"]
            result={"schema":1,"state":"waiting_for_exit","accepted":True,"action":action}
            if action=="export":result.update(state="done",result="exported",profiles=len(value["accountIds"]) if self.path=="/mdata/export-selected" else len(fake_application.rows))
            if flags["ack"]=="not_accepted":result["accepted"]=False
            elif flags["ack"]=="wrong_action":result["action"]="different"
            elif flags["ack"]=="wrong_state":result["state"]="waiting_for_exit" if action=="export" else "done"
            elif flags["ack"]=="wrong_result":result["result"]="added"
            elif flags["ack"]=="missing_profiles":result.pop("profiles",None)
            elif flags["ack"]=="string_profiles":result["profiles"]="2"
            elif flags["ack"]=="zero_profiles":result["profiles"]=0
            elif flags["ack"]=="excess_profiles":result["profiles"]=4
            elif flags["ack"]=="fractional_profiles":result["profiles"]=1.5
            elif flags["ack"]=="error":self.reply(409,{"schema":1,"state":"error","error":"file_exists"});return
            elif flags["ack"]=="bad_schema":result["schema"]=9
            self.reply(200,result)
        def reply(self,status,value):
            data=json.dumps(value).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(data)));self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def log_message(self,*_args):pass
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    panel_path=Path(__file__).with_name("mdata_panel.qml");panel_original=panel_path.read_text(encoding="utf-8")
    panel=panel_original.replace("http://127.0.0.1:18973",f"http://127.0.0.1:{server.server_port}")
    assert "http://127.0.0.1:18973" not in panel, "Synthetic QA must never reach the real helper"
    panel=panel.replace("echoMode: TextInput.Password","echoMode: TextInput.Password\n                        readonly property bool qaMasked: echoMode === TextInput.Password")
    panel=panel.replace("textFormat: Text.PlainText; wrapMode: Text.Wrap", "textFormat: Text.PlainText; wrapMode: Text.Wrap\n            readonly property bool qaPlainText: textFormat === Text.PlainText")
    popup='''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import TypographyModule as Typography
Popup {
 id:root
 property int minimumWidth:400
 property var chatAppearance
 property string title
 property string subtitle
 property string confirmText
 property string cancelText
 property bool confirmButtonEnabled:true
 property bool confirmButtonLoading:false
 property Component dialogContentItem
 signal confirmClicked()
 signal cancelClicked()
 signal closeClicked()
 width:minimumWidth;implicitHeight:body.implicitHeight+2*padding;height:implicitHeight
 padding:24;margins:16;modal:true;focus:true;anchors.centerIn:parent
 background:Rectangle{color:root.chatAppearance.colorBackgroundPrimary;radius:16}
 contentItem:ColumnLayout {
  id:body;spacing:16
  Label{Layout.fillWidth:true;text:root.title;font:Typography.FontUtil.getFont(1,0);color:root.chatAppearance.colorTextPrimary;textFormat:Text.PlainText;wrapMode:Text.Wrap}
  Label{Layout.fillWidth:true;text:root.subtitle;font:Typography.FontUtil.getFont(2,0);color:root.chatAppearance.colorTextSecondary;textFormat:Text.PlainText;wrapMode:Text.Wrap}
  Loader{Layout.fillWidth:true;sourceComponent:root.dialogContentItem}
  RowLayout {
   Layout.fillWidth:true;spacing:12
   Button{Layout.fillWidth:true;enabled:root.confirmButtonEnabled;text:root.confirmText;onClicked:root.confirmClicked()}
   Button{Layout.fillWidth:true;visible:root.cancelText.length>0;text:root.cancelText;onClicked:root.cancelClicked()}
  }
 }
}
'''
    popup_file=args.output/"SyntheticBaseModalDialog.qml";popup_file.write_text(popup,encoding="utf-8")
    source='''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs as Dialogs
import UiKitModule as UiKit
import TypographyModule as Typography
ApplicationWindow {
 id:root
 width:650;height:720;visible:true
 property bool testDark:false
 property var testedPage:null
 property var selfInfo:({id:"42",name:"Личный аккаунт"})
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
 Component.onCompleted:testedPage=settingsModMData.createObject(root.contentItem)
 Binding{target:root.testedPage;property:"width";value:root.width;when:root.testedPage!==null}
 Binding{target:root.testedPage;property:"height";value:root.height;when:root.testedPage!==null}
}
'''
    qml_file=args.output/"SyntheticMData.qml";qml_file.write_text(source,encoding="utf-8")
    warnings=[]
    def message(kind,_context,text):
        if kind in (QtMsgType.QtWarningMsg,QtMsgType.QtCriticalMsg,QtMsgType.QtFatalMsg):warnings.append(text)
    previous=qInstallMessageHandler(message)
    app=QGuiApplication(sys.argv[:1]);font_util,design_system,delegate=FontUtil(),DesignSystem(),FakeDelegate()
    fake_application,fake_utils=FakeApplication(),FakeQmlUtils()
    qmlRegisterSingletonInstance(FontUtil,"TypographyModule",1,0,"FontUtil",font_util)
    qmlRegisterSingletonInstance(DesignSystem,"UiKitModule",1,0,"DesignSystem",design_system)
    qmlRegisterType(QUrl.fromLocalFile(str(popup_file.resolve())),"UiKitModule",1,0,"BaseModalDialog")
    from panel_scroll_qa import register_native_scroll
    register_native_scroll(args.output)
    engine=QQmlEngine();engine.rootContext().setContextProperty("appDelegate",delegate)
    engine.rootContext().setContextProperty("application",fake_application);engine.rootContext().setContextProperty("QmlUtils",fake_utils)
    component=QQmlComponent(engine,QUrl.fromLocalFile(str(qml_file.resolve())))
    if component.isError():raise RuntimeError("QML compile: "+"\n".join(e.toString() for e in component.errors()))
    window=component.create()
    if window is None:raise RuntimeError("QML create: "+"\n".join(e.toString() for e in component.errors()))
    def settle(ms=180):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def until(predicate,ms=3500):
        deadline=monotonic()+ms/1000
        while not predicate() and monotonic()<deadline:settle(50)
        assert predicate(),"UI state did not arrive before deadline"
    def named(name):
        pending=[window.contentItem()]
        while pending:
            item=pending.pop()
            if item.objectName()==name:return item
            pending.extend(item.childItems())
        raise AssertionError(name)
    def visual_items():
        pending=[window.contentItem()];items=[]
        while pending:
            item=pending.pop();items.append(item);pending.extend(item.childItems())
        return items
    def posts():return [r for r in traffic if r["method"]=="POST"]
    def gets():return [r for r in traffic if r["method"]=="GET"]
    def value_of(value):return value.toVariant() if hasattr(value,"toVariant") else value
    def open_dialog(action="export",file="file:///D:/Synthetic-MData.mdata"):
        assert QMetaObject.invokeMethod(page,"openPasswordDialog",Qt.DirectConnection,Q_ARG("QVariant",action),Q_ARG("QVariant",file));settle();return page.property("activeDialog")
    def confirm(dialog):assert QMetaObject.invokeMethod(dialog,"confirmClicked",Qt.DirectConnection);settle()
    def prepare_import(dialog):
        secret(dialog);count=delegate.close_calls;confirm(dialog)
        assert dialog.property("previewReady") and dialog.property("secret")=="" and dialog.property("previewSecret")=="synthetic-password-12"
        assert dialog.property("previewFingerprint")=="a"*64
        assert delegate.close_calls==count
    def cancel(dialog):assert QMetaObject.invokeMethod(dialog,"cancelClicked",Qt.DirectConnection);settle()
    def secret(dialog,value="synthetic-password-12"):
        dialog.setProperty("secret",value);dialog.setProperty("confirmation",value)
    def refresh():assert QMetaObject.invokeMethod(page,"refreshStatus",Qt.DirectConnection);settle()
    def reset():state.update(available=True,state="idle",action=None,error=None,result=None);refresh()
    def screen(name):
        path=args.output/(name+".png");assert window.grabWindow().save(str(path));screenshots.append(str(path.resolve()))
    checks,screenshots=[],[]
    settle(500);page=window.property("testedPage")
    assert page.property("available") and not posts() and delegate.close_calls==0
    pending=[page];file_filters={}
    while pending:
        item=pending.pop();pending.extend(item.children())
        if item.property("title") in ("Создать MData","Войти с MData") and item.property("nameFilters") is not None:
            file_filters[item.property("title")]=item.property("nameFilters")
    assert len(file_filters)==2 and "*.mdata" in " ".join(file_filters["Создать MData"])
    assert "*.maxsession" not in " ".join(file_filters["Создать MData"])
    assert all(extension in " ".join(file_filters["Войти с MData"]) for extension in ("*.mdata","*.maxsession"))
    assert named("createMData").property("text")=="Создать MData" and named("loadMData").property("text")=="Войти с MData"
    assert named("mdataDescription").property("text")=="MData переносит только вход в аккаунты. Переписка, файлы и кэш в неё не входят."
    screen("mdata-page-light-650")
    dialog=open_dialog();assert named("mdataPassword").property("qaMasked") and named("mdataPasswordConfirmation").property("qaMasked")
    assert dialog.property("subtitle")=="Только данные входа MAX Mod, включая его токены из Windows. Переписка, медиа и кэш не копируются. MAX Mod остаётся открыт."
    screen("mdata-create-light-650")
    secret(dialog,"short");confirm(dialog);assert not posts() and dialog.property("secret")==""
    secret(dialog);dialog.setProperty("confirmation","different");confirm(dialog);assert not posts() and dialog.property("secret")==""
    secret(dialog,"x"*1025);confirm(dialog);assert not posts() and dialog.property("secret")==""
    secret(dialog);cancel(dialog);assert page.property("activeDialog") is None and delegate.close_calls==0
    checks.append("two-action login-only page explains omitted history/files/cache and own Windows login tokens; real FileDialog types compile but never open; password limits and cancel clear secrets without POST or quit")
    dialog=open_dialog()
    for ack in ("not_accepted","wrong_action","wrong_state","bad_schema","error","wrong_result","missing_profiles","string_profiles","zero_profiles","excess_profiles","fractional_profiles"):
        flags["ack"]=ack;secret(dialog);confirm(dialog)
        assert delegate.close_calls==0 and page.property("activeDialog") is not None and page.property("pendingRequest") is None and dialog.property("secret")=="",ack
    checks.append("export HTTP errors, accepted:false, action/state/schema/result mismatch and invalid profile counts cannot invoke closeMessenger or close the dialog")
    flags["ack"]="valid";secret(dialog);confirm(dialog)
    assert delegate.close_calls==0 and page.property("activeDialog") is None
    assert page.property("operationState")=="done" and page.property("statusText")=="MData создана." and not page.property("busy")
    assert posts()[-1]["action"]=="export" and posts()[-1]["mode"]=="restore"
    export_close_calls=delegate.close_calls
    checks.append("synchronous done/exported ACK closes only the password dialog, shows MData создана. and invokes closeMessenger zero times; export retains the compatible request mode field")
    reset();assert QMetaObject.invokeMethod(named("mdataExportCurrent"),"clicked",Qt.DirectConnection);settle()
    assert page.property("exportScope")=="current"
    dialog=open_dialog();secret(dialog);confirm(dialog)
    assert posts()[-1]["path"]=="/mdata/export-selected" and posts()[-1]["accountIds"]==["42"] and delegate.close_calls==0
    assert QMetaObject.invokeMethod(named("mdataExportAccount_7"),"clicked",Qt.DirectConnection);settle()
    dialog=open_dialog();assert dialog is not None,(page.property("exportScope"),value_of(page.property("selectedExportAccounts")),page.property("statusText"),warnings)
    assert dialog.property("exportAccountName")=="Работа";secret(dialog);confirm(dialog)
    assert posts()[-1]["path"]=="/mdata/export-selected" and posts()[-1]["accountIds"]==["7"] and delegate.close_calls==0
    assert QMetaObject.invokeMethod(named("mdataExportAccount_42"),"clicked",Qt.DirectConnection);settle()
    assert page.property("exportScope")=="subset"
    assert named("mdataExportAccount_7").property("text").endswith(" · выбран")
    assert named("mdataExportAccount_42").property("text").endswith(" · выбран")
    dialog=open_dialog();assert value_of(dialog.property("exportAccountIds"))==["7","42"]
    assert any(item.property("text")=="Выбрано аккаунтов: 2\nID: 7, 42" and item.property("qaPlainText") is True for item in visual_items())
    screen("mdata-subset-light-650")
    secret(dialog);confirm(dialog)
    assert posts()[-1]["path"]=="/mdata/export-selected" and posts()[-1]["accountIds"]==["7","42"] and delegate.close_calls==0
    assert QMetaObject.invokeMethod(named("mdataExportAccount_7"),"clicked",Qt.DirectConnection);settle()
    assert page.property("exportScope")=="one"
    assert QMetaObject.invokeMethod(named("mdataExportAccount_42"),"clicked",Qt.DirectConnection);settle()
    dialog=open_dialog();assert dialog.property("exportAccountName")=="Личный аккаунт"
    secret(dialog);confirm(dialog)
    assert posts()[-1]["path"]=="/mdata/export-selected" and posts()[-1]["accountIds"]==["42"]
    assert QMetaObject.invokeMethod(named("mdataExportAccount_7"),"clicked",Qt.DirectConnection);settle()
    assert QMetaObject.invokeMethod(named("mdataExportAccount_9"),"clicked",Qt.DirectConnection);settle()
    assert page.property("exportScope")=="subset"
    assert QMetaObject.invokeMethod(page,"toggleExportAccount",Qt.DirectConnection,Q_ARG("QVariant","999"),Q_ARG("QVariant","Fourth"));settle()
    dialog=open_dialog();assert value_of(dialog.property("exportAccountIds"))==["42","7","9"]
    cancel(dialog)
    fake_application.removeAccount("9");settle()
    assert "9" not in value_of(page.property("modelExportAccountIds"))
    post_count=len(posts());assert open_dialog() is None and len(posts())==post_count and delegate.close_calls==0
    assert QMetaObject.invokeMethod(named("mdataExportAll"),"clicked",Qt.DirectConnection);settle()
    dialog=open_dialog();secret(dialog);confirm(dialog)
    assert posts()[-1]["path"]=="/mdata" and posts()[-1]["accountIds"] is None and delegate.close_calls==0
    checks.append("real native-model synthetic delegates select current/one/subset/all export scope; two of three use exact selected-only IDs, a removed account never falls back to all, and legacy all retains its route")
    reset();dialog=open_dialog("import")
    assert dialog.property("restoreMode")=="add" and not named("mdataPasswordConfirmation").isVisible()
    assert dialog.property("subtitle")=="Сначала проверим MData и покажем аккаунты. Вход выполняется только после отдельного подтверждения."
    assert not any(item.objectName().startswith("mdataMode_") for item in visual_items())
    window.setWidth(390);window.setProperty("testDark",True);settle();screen("mdata-load-dark-390")
    for preview in ("bad_schema","bad_count","bad_id","duplicate","long_name","controls","bad_fingerprint","error"):
        flags["preview"]=preview;secret(dialog);count=len(posts());confirm(dialog)
        assert len(posts())==count+1 and posts()[-1]["path"]=="/mdata/inspect" and not dialog.property("previewReady") and dialog.property("previewSecret")=="" and delegate.close_calls==0,preview
    flags["preview"]="valid";prepare_import(dialog)
    assert len(dialog.property("previewAccounts").toVariant())==2
    assert dialog.property("confirmText")=="Войти с MData" and not named("mdataPassword").isVisible()
    assert any(item.property("text")=="<b>Личный аккаунт</b>" and item.property("qaPlainText") is True for item in visual_items())
    screen("mdata-preview-dark-390")
    count=len(posts());cancel(dialog);assert len(posts())==count and delegate.close_calls==0
    dialog=open_dialog("import");flags["preview"]="unicode_name";prepare_import(dialog)
    assert len(dialog.property("previewAccounts").toVariant()[0]["displayName"])==256
    screen("mdata-preview-unicode-dark-390");cancel(dialog);flags["preview"]="valid"
    dialog=open_dialog("import")
    checks.append("authenticated compact preview validates schema/count/canonical IDs/duplicates/bounded names/control characters; errors clear retained password; valid names render plain text without photos; cancel after preview never imports or quits")
    for ack in ("not_accepted","wrong_action","wrong_state","bad_schema","error"):
        flags["ack"]=ack;prepare_import(dialog);confirm(dialog)
        assert delegate.close_calls==0 and page.property("activeDialog") is not None and page.property("pendingRequest") is None and dialog.property("secret")=="" and dialog.property("previewSecret")=="",ack
    flags["ack"]="valid";prepare_import(dialog);confirm(dialog)
    assert delegate.close_calls==1 and page.property("activeDialog") is None and posts()[-1]["path"]=="/mdata/import-confirmed" and posts()[-1]["fingerprint_matches"]
    import_ack_close_calls=delegate.close_calls-export_close_calls
    checks.append("confirmed compact import carries the authenticated ciphertext fingerprint; no automatic import or native quit before separate confirmation and matching import ACK")
    state.update(state="error",error="backup_failed");refresh()
    assert delegate.close_calls==1 and page.property("available") and page.property("operationState")=="error" and not page.property("busy")
    for action in ("export","inspect","import"):
        reset();dialog=open_dialog("import" if action=="inspect" else action)
        if action=="import":prepare_import(dialog)
        else:secret(dialog)
        release_post.clear();flags["post_stall"]=True
        confirm(dialog)
        assert page.property("pendingRequest") is not None and dialog.property("secret")=="" and delegate.close_calls==1
        assert dialog.property("cancelText")=="" and dialog.property("confirmButtonEnabled") is False
        assert any(child.property("interval")==30000 for child in page.children())
        count=len(posts());settle(30700)
        assert page.property("pendingRequest") is None and delegate.close_calls==1 and len(posts())==count and dialog.property("previewSecret")=="",action
        release_post.set();settle(250);assert delegate.close_calls==1 and dialog.property("secret")=="",action
        flags["post_stall"]=False;cancel(dialog)
    checks.append("actual 30-second POST watchdog for export/preview/confirmed import, pending lock and discarded late ACK; all password properties cleared, no automatic retry or quit")
    page.setProperty("visible",False)
    flags["get_stall"]=True;refresh();settle(7200)
    assert page.property("pendingRequest") is None and delegate.close_calls==1
    message_after_timeout=page.property("statusText");release_get.set();settle();assert page.property("statusText")==message_after_timeout
    flags["get_stall"]=False;reset();assert not page.property("hasError")
    page.setProperty("visible",True)
    dialog=open_dialog("export","file:///D:/Synthetic-MData.maxsession");secret(dialog)
    count=len(posts());confirm(dialog)
    assert len(posts())==count and delegate.close_calls==1 and dialog.property("secret")==""
    cancel(dialog)
    dialog=open_dialog("import","file:///D:/Synthetic-MData.maxsession");secret(dialog);confirm(dialog)
    assert delegate.close_calls==1 and len(posts())==count and dialog.property("previewReady") and dialog.property("legacyPreview") and dialog.property("confirmText")=="Добавить без предпросмотра"
    confirm(dialog)
    assert delegate.close_calls==2 and len(posts())==count+1 and posts()[-1]["mode"]=="add" and posts()[-1]["file_extension"]=="maxsession"
    reset()
    dialog=open_dialog("import");flags["preview"]="legacy";secret(dialog);count=len(posts());confirm(dialog)
    assert len(posts())==count+1 and posts()[-1]["path"]=="/mdata/inspect" and delegate.close_calls==2 and dialog.property("previewReady") and dialog.property("legacyPreview")
    confirm(dialog);assert delegate.close_calls==3 and posts()[-1]["path"]=="/mdata" and posts()[-1]["mode"]=="add"
    flags["preview"]="valid";reset()
    checks.append("export accepts only mdata; legacy maxsession and recognized full v1 archives show explicit unsupported-preview notice and require a separate manual confirmation; old add-only import remains available")
    count=len(posts());close_count=delegate.close_calls
    result_messages={"exported":"MData создана.","restored":"Данные из MData восстановлены.","added":"Аккаунты из MData добавлены.","already_present":"Аккаунты из MData уже добавлены."}
    for result,expected in result_messages.items():
        state.update(state="done",error=None,result=result);refresh()
        assert page.property("statusText")==expected and not page.property("hasError") and not page.property("busy"),result
    error_messages={"account_limit":"Можно добавить не больше трёх аккаунтов. Освободи место для нового аккаунта.","profile_collision":"Этот аккаунт уже есть в MAX Mod. Текущие данные сохранены.","recovery_required":"Сначала восстанови резервную копию через ярлык восстановления MData.","legacy_add_only":"Файл .maxsession поддерживает только добавление аккаунтов.","unsupported_credential":"MData содержит неподдерживаемый тип данных входа Windows.","source_changed":"Данные MAX Mod изменились во время создания копии. Повтори операцию."}
    for code,expected in error_messages.items():
        state.update(state="error",error=code,result=None);refresh()
        assert page.property("statusText")==expected and page.property("hasError") and page.property("available") and not page.property("busy"),code
    assert len(posts())==count and delegate.close_calls==close_count
    checks.append("GET result codes have distinct Russian completion text; collision, recovery, legacy, credential and source errors are fixed messages without POST or quit")
    page.setProperty("visible",False);state.update(available=False,state="idle",error=None,result=None);refresh()
    assert not page.property("available") and page.property("hasError")
    get_count=len(gets());settle(2200);assert len(gets())==get_count
    state.update(available=True);page.setProperty("visible",True)
    until(lambda:page.property("available") and len(gets())>get_count)
    assert not page.property("hasError") and not page.property("busy")
    state.update(state="waiting_for_exit");refresh()
    state.update(state="working");until(lambda:page.property("operationState")=="working")
    state.update(state="done",result="already_present");until(lambda:page.property("operationState")=="done")
    assert page.property("statusText")==result_messages["already_present"] and not page.property("busy")
    get_count=len(gets());settle(2200);assert len(gets())==get_count
    state.update(state="working",result=None);refresh()
    release_get.clear();flags["get_stall"]=True;refresh()
    assert page.property("pendingRequest") is not None
    get_count=len(gets());settle(2200)
    assert len(gets())==get_count and page.property("pendingRequest") is not None
    state.update(state="done",result="restored");release_get.set();flags["get_stall"]=False
    until(lambda:page.property("pendingRequest") is None and page.property("operationState")=="done")
    assert len(posts())==count and delegate.close_calls==close_count
    checks.append("real two-second recovery polling reads only GET while visible and unavailable/waiting/working; hidden and completed pages stop polling; pending requests are never duplicated or retried as POST")
    assert all(r["path"] in ("/mdata","/mdata/inspect","/mdata/export-selected","/mdata/import-confirmed") for r in traffic)
    expected_keys={"/mdata":["action","file","mode","password","schema"],"/mdata/inspect":["file","password","schema"],"/mdata/export-selected":["accountIds","file","password","schema"],"/mdata/import-confirmed":["file","fingerprint","password","schema"]}
    assert all(r["keys"]==expected_keys[r["path"]] and r["password_present"] and r["write"]=="mdata-v1" and r["origin"] is None and r["type"].split(";",1)[0]=="application/json" for r in posts())
    checks.append("actual seven-second GET watchdog/error recovery never quit; exact endpoint, payload keys, write marker and no Origin")
    server.shutdown();server.server_close();window.close();settle(30);qInstallMessageHandler(previous)
    result={"qt_version":qVersion(),"checks":checks,"warnings":warnings,"screenshots":screenshots,"post_count":len(posts()),"export_closeMessenger_calls":export_close_calls,"matching_import_ack_closeMessenger_calls":import_ack_close_calls,"fake_closeMessenger_calls":delegate.close_calls,"max_started_or_stopped":False,"real_profiles_or_credentials_accessed":False,"archive_files_read_or_written":False,"file_dialogs_opened":False,"source_sha256":hashlib.sha256(panel_path.read_bytes()).hexdigest(),"limitations":["Native modal public API and SettingsBaseItem/typography are synthetic; actual custom native rendering needs separate integration QA.","Only fake HTTP and fake appDelegate are used; no MAX process, storage or archive operation occurs.","Fields and temporary dialog properties are cleared; managed password string memory is not claimed to be securely zeroed."]}
    (args.output/"mdata-qa.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(result,ensure_ascii=False,indent=2))
    if warnings:raise RuntimeError("Qt warnings: "+"\n".join(warnings))


if __name__=="__main__":main()
