"""Offscreen support-page QA with owned HTTP fixtures and native Qt dialogs.

No MAX process, real profile, credentials, external browser or report export.
The real FileDialog type is compiled; its accepted/rejected signals are driven
without showing a system dialog. Backend file creation is tested separately.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name in ('TEMP', 'TMP', 'TMPDIR', 'LOCALAPPDATA', 'APPDATA',
                 'USERPROFILE', 'QML_DISK_CACHE_PATH'):
        directory = output / 'synthetic-profile' / name.lower()
        directory.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(directory)
    os.environ.update(QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
        QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1')
    fonts = output / 'fonts'
    fonts.mkdir(exist_ok=True)
    for name in ('arial.ttf', 'arialbd.ttf'):
        shutil.copyfile(Path('C:/Windows/Fonts') / name, fonts / name)
    os.environ['QT_QPA_FONTDIR'] = str(fonts)

    from PySide6.QtCore import (QObject, Property, Slot, QEventLoop, QMetaObject,
        QPointF, QTimer, QUrl, Qt, QtMsgType, qInstallMessageHandler, qVersion)
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtQml import QQmlEngine, QQmlComponent, qmlRegisterSingletonInstance
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest

    class FontUtil(QObject):
        @Property('QVariantMap', constant=True)
        def Styles(self): return {'Body': 0, 'BodyStrong': 1, 'Description': 2}
        @Property('QVariantMap', constant=True)
        def Sizes(self): return {'Default': 0}
        @Slot(int, int, result=QFont)
        def getFont(self, style, _size):
            font = QFont('Arial')
            font.setPixelSize(16 if style < 2 else 14)
            font.setWeight(QFont.DemiBold if style == 1 else QFont.Normal)
            return font
        @Slot(int, int, result=float)
        def getLineHeight(self, style, _size): return 22.0 if style < 2 else 20.0

    class DesignSystem(QObject):
        @Property('QVariantMap', constant=True)
        def SpacingCommon(self): return {'_M': 12, '_2XL': 24}
        @Property('QVariantMap', constant=True)
        def BorderRadiusCommon(self): return {'_L': 12}
        @Property('QVariantMap', constant=True)
        def SizeBase(self): return {'px6': 6, 'px48': 48}

    from support_diagnostics import build_report
    report = build_report(versions={'valmax': '1.0', 'max': '26.35.1',
        'windows': '10.0', 'python': '3.13'},
        ghost={'settings': {'enabled': True, 'hideRead': True}},
        optimization={'settings': {'enabled': False}})
    traffic = []
    flags = {'post_error': None, 'get_error': False, 'hold_post': False}
    release_post = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            traffic.append({'method': 'GET', 'path': self.path})
            self.reply(503 if flags['get_error'] else 200,
                {'schema': 1, 'error': 'io_error'} if flags['get_error']
                else {'schema': 1, 'report': report})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            traffic.append({'method': 'POST', 'path': self.path, 'body': body,
                'write': self.headers.get('X-Max-Mod-Local-Write'),
                'origin': self.headers.get('Origin'),
                'contentType': self.headers.get('Content-Type')})
            if flags['hold_post']: release_post.wait(4)
            error = flags['post_error']
            self.reply(409 if error == 'file_exists' else 503 if error else 200,
                {'schema': 1, 'error': error} if error
                else {'schema': 1, 'saved': True})
        def reply(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
        def log_message(self, *_args): pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    panel_path = Path(__file__).with_name('support_panel.qml')
    panel = panel_path.read_text(encoding='utf8').replace(
        'http://127.0.0.1:18973/support-diagnostics',
        f'http://127.0.0.1:{server.server_port}/support-diagnostics')
    panel = panel.replace('id: saveSupportDialog',
        'id: saveSupportDialog\n            objectName: "qaSaveSupportDialog"')
    source = '''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs as Dialogs
import UiKitModule as UiKit
import TypographyModule as Typography
ApplicationWindow {
 id: root
 width:650; height:1180; visible:true
 property bool testDark:false
 property var testedPage:null
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
 }
 component SettingsBaseItem:Control {
  id:base
  property string title
  property var maxwellColorTokens:tokens
  property alias contentLoaderItem:loader.sourceComponent
  signal backClicked()
  contentItem:ColumnLayout {
   spacing:0
   Label{Layout.fillWidth:true;Layout.preferredHeight:64;text:base.title;font:Typography.FontUtil.getFont(1,0);color:tokens.colorTextPrimary;horizontalAlignment:Text.AlignHCenter;verticalAlignment:Text.AlignVCenter;background:Rectangle{color:tokens.colorBackgroundPrimary}}
   Loader{id:loader;Layout.fillWidth:true;Layout.fillHeight:true;Layout.leftMargin:24;Layout.rightMargin:24;Layout.maximumWidth:640;Layout.alignment:Qt.AlignHCenter|Qt.AlignTop}
  }
 }
''' + panel + '''
 Component.onCompleted:testedPage=settingsValMAXSupport.createObject(root.contentItem)
 Binding{target:root.testedPage;property:"width";value:root.width;when:root.testedPage!==null}
 Binding{target:root.testedPage;property:"height";value:root.height;when:root.testedPage!==null}
}
'''
    qml_file = output / 'SyntheticSupport.qml'
    qml_file.write_text(source, encoding='utf8')
    warnings = []
    def message(kind, _context, text):
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            warnings.append(text)
    previous = qInstallMessageHandler(message)
    app = QGuiApplication([])
    font_util, design_system = FontUtil(), DesignSystem()
    qmlRegisterSingletonInstance(FontUtil, 'TypographyModule', 1, 0, 'FontUtil', font_util)
    qmlRegisterSingletonInstance(DesignSystem, 'UiKitModule', 1, 0, 'DesignSystem', design_system)
    from panel_scroll_qa import register_native_scroll
    scroll_evidence = register_native_scroll(output)
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_file)))
    if component.isError(): raise RuntimeError('\n'.join(error.toString() for error in component.errors()))
    window = component.create()
    if window is None: raise RuntimeError('\n'.join(error.toString() for error in component.errors()))

    def settle(ms=200):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
    def named(name):
        item = window.findChild(QQuickItem, name)
        assert item is not None, name
        return item
    def posts(): return [item for item in traffic if item['method'] == 'POST']
    def click(name):
        item = named(name)
        view = named('supportDiagnosticsScroll')
        top = item.mapToScene(QPointF()).y() - view.mapToScene(QPointF()).y() + view.property('contentY')
        target = max(0, min(top - (view.height() - item.height()) / 2,
            max(0, view.property('contentHeight') - view.height())))
        view.setProperty('contentY', target)
        settle(50)
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
        local = view.mapFromScene(QPointF(point))
        assert item.isEnabled() and 0 <= local.y() <= view.height(), (name, local)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        settle()
    def accept_file(name):
        dialog.setProperty('selectedFile', QUrl.fromLocalFile(str(output / name)))
        assert QMetaObject.invokeMethod(dialog, 'accepted', Qt.DirectConnection)
        settle()
    screenshots, checks = [], []
    def screenshot(name):
        path = output / (name + '.png')
        assert window.grabWindow().save(str(path))
        screenshots.append(str(path))

    settle(500)
    page = window.property('testedPage')
    dialog = window.findChild(QObject, 'qaSaveSupportDialog')
    assert dialog is not None and not dialog.property('visible')
    assert page.property('report') is not None and len(traffic) == 1 and not posts()
    screenshot('support-light-650')
    click('previewSupportDiagnostics')
    assert page.property('showReport') and named('supportDiagnosticsPreview').isVisible()
    assert '"schema": 1' in named('supportDiagnosticsPreview').property('text')
    click('previewSupportDiagnostics')
    assert not page.property('showReport') and not posts()
    checks.append('opening and preview toggles issue only local GET; actual preview-button mouse clicks show/hide synthetic report')
    dialog.setProperty('selectedFile', QUrl.fromLocalFile(str(output/'cancelled.json')))
    settle(50)
    assert not posts()
    assert QMetaObject.invokeMethod(dialog, 'rejected', Qt.DirectConnection)
    settle()
    assert not posts() and not (output/'cancelled.json').exists()
    checks.append('real Qt FileDialog type compiles; selectedFile change and rejected signal never POST or write a report')
    flags['post_error'] = 'file_exists'
    accept_file('existing.json')
    assert len(posts()) == 1 and page.property('hasError') and 'уже существует' in page.property('statusText')
    flags['post_error'] = 'io_error'
    accept_file('io-error.json')
    assert len(posts()) == 2 and page.property('hasError') and 'Не удалось' in page.property('statusText')
    flags['post_error'] = None
    flags['hold_post'] = True
    accept_file('synthetic-report.json')
    assert len(posts()) == 3 and not named('saveSupportDiagnostics').isEnabled()
    assert not named('refreshSupportDiagnostics').isEnabled()
    assert QMetaObject.invokeMethod(dialog, 'accepted', Qt.DirectConnection)
    settle(50)
    assert len(posts()) == 3
    release_post.set(); flags['hold_post'] = False; settle(250)
    assert not page.property('hasError') and 'Отчёт сохранён' in page.property('statusText')
    assert named('saveSupportDiagnostics').isEnabled()
    checks.append('accepted signal sends one guarded POST; existing-file/io errors show fixed Russian text; pending duplicate acceptance is ignored; success recovers controls')
    flags['get_error'] = True
    click('refreshSupportDiagnostics')
    assert page.property('hasError') and 'Не удалось' in page.property('statusText')
    flags['get_error'] = False
    click('refreshSupportDiagnostics')
    assert not page.property('hasError') and len(posts()) == 3
    window.setWidth(390); window.setHeight(840); window.setProperty('testDark', True); settle()
    named('supportDiagnosticsScroll').setProperty('contentY', 0); settle(50)
    screenshot('support-dark-390-top')
    click('previewSupportDiagnostics')
    screenshot('support-dark-390-preview')
    assert page.property('showReport') and named('supportDiagnosticsScroll').property('contentHeight') > named('supportDiagnosticsScroll').height()
    click('previewSupportDiagnostics')
    checks.append('GET failure and explicit refresh recover without POST; pinned native scroll types render and scroll narrow dark/light layouts')
    assert all(item['path'] == '/support-diagnostics' for item in traffic)
    assert all(set(item['body']) == {'schema', 'file'} and item['body']['schema'] == 1
        and item['write'] == 'support-diagnostics-v1' and item['origin'] is None
        and item['contentType'].split(';', 1)[0] == 'application/json' for item in posts())
    assert not warnings, warnings
    result = {'schema': 1, 'qtVersion': qVersion(), 'checks': checks,
        'screenshots': screenshots, 'warnings': warnings, 'httpRequests': len(traffic),
        'postCount': len(posts()), 'nativeScroll': scroll_evidence,
        'sourceSha256': hashlib.sha256(panel_path.read_bytes()).hexdigest(),
        'liveClientStarted': False, 'realProfilesOrCredentialsAccessed': False,
        'externalBrowserOpened': False, 'reportFilesWritten': False,
        'limitations': ['FontUtil, DesignSystem and SettingsBaseItem use synthetic values.',
            'Real FileDialog accepted/rejected signals are invoked without opening a system dialog.',
            'HTTP fixture returns save responses without file creation; backend is tested separately.']}
    (output/'support-qa.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps(result, ensure_ascii=False))
    window.close(); window.deleteLater(); settle(50)
    server.shutdown(); server.server_close(); qInstallMessageHandler(previous)


if __name__ == '__main__': main()
