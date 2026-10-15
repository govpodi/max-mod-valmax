"""Real Qt/QML synthetic search + draft callbacks, ephemeral loopback fixture only."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    for name in ('TEMP','TMP','TMPDIR','APPDATA','LOCALAPPDATA','USERPROFILE','QML_DISK_CACHE_PATH'):
        path=output/'synthetic-profile'/name.lower()
        path.mkdir(parents=True,exist_ok=True)
        os.environ[name]=str(path)
    os.environ.update(QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QT_QUICK_CONTROLS_STYLE='Basic',QML_DISABLE_DISK_CACHE='1')
    font_dir=output/'fonts';font_dir.mkdir(exist_ok=True)
    for filename in ('arial.ttf','arialbd.ttf'):
        shutil.copyfile(Path('C:/Windows/Fonts')/filename,font_dir/filename)
    os.environ['QT_QPA_FONTDIR']=str(font_dir)
    from PySide6.QtCore import QObject,Property,Slot,QUrl,QTimer,QEventLoop,QMetaObject,Q_ARG,Qt,qInstallMessageHandler,qVersion
    from PySide6.QtGui import QFont,QGuiApplication
    from PySide6.QtQml import QQmlEngine,QQmlComponent,qmlRegisterSingletonInstance
    from PySide6.QtQuick import QQuickWindow,QQuickItem
    class Fonts(QObject):
        @Property('QVariantMap',constant=True)
        def Styles(self):return {'BodyStrong':1,'Description':2}
        @Property('QVariantMap',constant=True)
        def Sizes(self):return {'Default':0}
        @Slot(int,int,result=QFont)
        def getFont(self,style,size):
            font=QFont('Arial');font.setPixelSize(16 if style<2 else 14);return font
    class Design(QObject):
        @Property('QVariantMap',constant=True)
        def SpacingCommon(self):return {'_S':8}
        @Property('QVariantMap',constant=True)
        def BorderRadiusCommon(self):return {'_L':12,'_S':4}
    requests=[];flags={'stall':False};release=threading.Event()
    row={'id':'1'*32,'title':'Синтетический закат','author':'Тестовый автор','license':'CC BY 4.0','source':'https://commons.wikimedia.org/wiki/File:Synthetic.png','bytes':30,'preview':False}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'path':self.path,'body':body,'write':self.headers.get('X-Max-Mod-Local-Write'),'origin':self.headers.get('Origin')})
            if flags['stall']:release.wait(6)
            if self.path.endswith('/search'):
                mode=body['mode'];value={'schema':1,'provider':'Wikimedia Commons','mode':mode,'results':[{**row,'mime':'video/webm' if mode=='vid' else 'image/png'}]}
            else:
                value={'schema':1,'provider':'Wikimedia Commons','mime':'image/png','url':(output/'synthetic.png').as_uri(),'caption':'Источник: Wikimedia Commons\nТестовый автор · CC BY 4.0\n'+row['source']}
            data=json.dumps(value,ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    source=(Path(__file__).with_name('inline_media_composer.qml')).read_text(encoding='utf-8')
    declarations,menu=source.split('// BEGIN INLINE MEDIA MENU',1);menu=menu.split('// END INLINE MEDIA MENU',1)[0]
    declarations=declarations.replace('http://127.0.0.1:18973/',f'http://127.0.0.1:{server.server_port}/')
    imports=output/'imports'/'UiKitModule';imports.mkdir(parents=True,exist_ok=True)
    (imports/'qmldir').write_text('module UiKitModule\nMeLoader 1.0 MeLoader.qml\n',encoding='utf-8')
    (imports/'MeLoader.qml').write_text('import QtQuick\nLoader { property bool activeAndVisible: false; active: activeAndVisible; visible: activeAndVisible }\n',encoding='utf-8')
    qml='''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import UiKitModule as UiKit
import TypographyModule as Typography
ApplicationWindow {
 id: root
 width: 700; height: 560; visible: true
 property bool testDark: false
 property bool textInputVisible: true
 property alias text: chatInput.text
 property var modQuickReplySelected: null
 property bool modQuickReplyAttachmentIntegration: true
 property var attachmentUploadModel: ({attachmentsCount:0})
 property int attachmentCount: 0
 property var attachedMedia: []
 property int sentCount: 0
 property var currentOpenedChatId: "synthetic-chat-1"
 property string modQuickReplyAccountId: "synthetic-account-1"
 signal modQuickReplyAttachmentsRequested(var files,var media)
 onModQuickReplyAttachmentsRequested: (files,media) => { attachmentCount++; attachedMedia=media; attachmentUploadModel={attachmentsCount:media.length}; }
 onCurrentOpenedChatIdChanged: modInlineReset(true)
 onModQuickReplyAccountIdChanged: modInlineReset(true)
 property var chatAppearance: tokens
 QtObject {
  id: tokens
  readonly property color colorTextPrimary: root.testDark ? "#edf0f2" : "#0c0d0e"
  readonly property color colorTextSecondary: root.testDark ? "#a7b0ba" : "#5e6269"
  readonly property color colorBackgroundCard: root.testDark ? "#222e39" : "#ffffff"
  readonly property color colorStatesBackgroundCardHover: root.testDark ? "#34404c" : "#e2e4e8"
 }
 color: testDark ? "#17212a" : "#edeef2"
 function setText(value,formats) { chatInput.text=value; }
 function simulateCompletion() { if (modInlineVisible && modInlineLoader.item) modInlineLoader.item.completeSelected(); }
''' + declarations + '''
 ColumnLayout {
  anchors.fill: parent; anchors.margins: 16
  Item { Layout.fillWidth:true; Layout.fillHeight:true }
''' + menu + '''
  TextArea { id: chatInput; objectName:"inlineMediaDraft"; Layout.fillWidth:true; Layout.preferredHeight:100; font:Typography.FontUtil.getFont(1,0); color:tokens.colorTextPrimary; textFormat:TextEdit.PlainText; wrapMode:TextEdit.Wrap; background:Rectangle {color:tokens.colorBackgroundCard}
   function moveCursorToEnd() { cursorPosition=length; }
  }
 }
 Component.onDestruction: modInlineReset(false)
}
'''
    qmlfile=output/'fixture.qml';qmlfile.write_text(qml,encoding='utf-8')
    app=QGuiApplication([]);warnings=[]
    qInstallMessageHandler(lambda mode,context,message:warnings.append(str(message)))
    fonts=Fonts();design=Design()
    qmlRegisterSingletonInstance(Fonts,'TypographyModule',1,0,'FontUtil',fonts)
    qmlRegisterSingletonInstance(Design,'UiKitModule',1,0,'DesignSystem',design)
    engine=QQmlEngine();engine.addImportPath(str(output/'imports'))
    component=QQmlComponent(engine,QUrl.fromLocalFile(str(qmlfile)))
    if component.isError():raise RuntimeError('\n'.join(x.toString() for x in component.errors()))
    window=component.create()
    if window is None:raise RuntimeError('\n'.join(x.toString() for x in component.errors()))
    def settle(ms=200):
        loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
    def method(name,*values):
        assert QMetaObject.invokeMethod(window,name,Qt.DirectConnection,*[Q_ARG('QVariant',value) for value in values])
        settle()
    def variant(value):return value.toVariant() if hasattr(value,'toVariant') else value
    checks=[];screens=[]
    settle()
    window.setProperty('text','PRIVATE_SYNTHETIC_DRAFT_NOT_A_QUERY');settle()
    window.setProperty('text','@pic синтетический тест');settle()
    assert not requests and window.property('modInlineVisible')
    method('simulateCompletion');assert not requests and window.property('sentCount')==0
    checks.append('ordinary draft, alias typing and completion do not send any HTTP query or message')
    method('modInlineSearch');settle(250)
    assert len(requests)==1 and len(variant(window.property('modInlineRows')))==1
    screenshot=output/'search-light-700.png';assert window.grabWindow().save(str(screenshot));screens.append(str(screenshot))
    method('modInlineSelect',0)
    assert window.property('attachmentCount')==1 and window.property('sentCount')==0
    assert window.property('text').startswith('Источник: Wikimedia Commons') and not window.property('modInlineVisible')
    assert variant(window.property('attachedMedia'))==[(output/'synthetic.png').as_uri()]
    checks.append('explicit result selection replaces only matching draft and stages media + attribution once, never sends')
    window.setProperty('text','@vid синтетический тест');settle();method('modInlineSearch')
    assert requests[-1]['body']['mode']=='vid'
    assert variant(window.property('modInlineRows'))[0]['mime']=='video/webm'
    window.setWidth(390);window.setProperty('testDark',True);settle()
    screenshot=output/'search-dark-390.png';assert window.grabWindow().save(str(screenshot));screens.append(str(screenshot))
    checks.append('both aliases show provider-labelled results; narrow light/dark QML layouts compile and render')
    # Stop a search while switching chat, then allow the old response to finish.
    flags['stall']=True;release.clear()
    window.setProperty('text','@pic stale synthetic');settle();method('modInlineSearch')
    window.setProperty('currentOpenedChatId','synthetic-chat-2');settle()
    release.set();flags['stall']=False;settle(300)
    assert not variant(window.property('modInlineRows')) and not window.property('modInlineBusy')
    # An account switch must also discard a selected download callback.
    window.setProperty('text','@pic select synthetic');settle();method('modInlineSearch')
    flags['stall']=True;release.clear();method('modInlineSelect',0)
    window.setProperty('modQuickReplyAccountId','synthetic-account-2');settle()
    release.set();flags['stall']=False;settle(300)
    assert window.property('attachmentCount')==1
    checks.append('stale search and selected-download responses are discarded after chat/account switch')
    for item in requests:
        assert item['write']=='inline-media-v1' and item['origin'] is None
        assert set(item['body'])==({'mode','query'} if item['path'].endswith('/search') else {'id'})
    checks.append('native QML sends the local-write guard; body never includes chat/account/auth data')
    assert not warnings, warnings
    result={'schema':1,'qtVersion':qVersion(),'checks':checks,'screenshots':screens,'warnings':warnings,'httpRequests':len(requests),'sentMessages':window.property('sentCount'),'stagedAttachments':window.property('attachmentCount'),'liveClientStarted':False,'liveObserverStarted':False,'externalSearchPerformed':False}
    (output/'proof.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    window.close();window.deleteLater();settle(50)
    server.shutdown();server.server_close()


if __name__=='__main__':main()
