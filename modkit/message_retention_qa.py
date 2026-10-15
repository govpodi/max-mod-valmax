"""Offscreen instance badge, original native footer and controller lifetime; no MAX."""
from __future__ import annotations

import argparse
import faulthandler
import hashlib
import json
import os
from pathlib import Path
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from message_retention_ui import HOST, DELEGATE_BINDING, BADGE, controller_source
import message_retention_ui as retention_module

NATIVE = Path('D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml')


def main():
    retention_pin = hashlib.sha256(Path(retention_module.__file__).read_bytes()).hexdigest()
    faulthandler.dump_traceback_later(25, exit=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.drive.upper() != 'D:': raise ValueError('Synthetic outputs belong on D')
    output.mkdir(parents=True, exist_ok=False)
    for name in ('APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'TEMP', 'TMP', 'QML_DISK_CACHE_PATH'):
        path = output / 'profile' / name.lower(); path.mkdir(parents=True)
        os.environ[name] = str(path)
    fonts = output / 'fonts'; fonts.mkdir()
    for name in ('arial.ttf', 'arialbd.ttf'): shutil.copyfile(Path('C:/Windows/Fonts') / name, fonts / name)
    os.environ.update(QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1', QT_QPA_FONTDIR=str(fonts))
    from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Signal, Slot, Qt, QDateTime, QUrl, QSize, QMetaObject, qInstallMessageHandler, qVersion
    from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor, QFont
    from PySide6.QtQuick import QQuickImageProvider, QQuickWindow
    from PySide6.QtQml import QQmlEngine, QQmlComponent, QQmlExpression, qmlRegisterSingletonInstance, qmlRegisterType
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtTest import QTest
    app = QGuiApplication([])
    warnings = []
    previous = qInstallMessageHandler(lambda _kind, _context, text: warnings.append(text))

    class Utils(QObject):
        @Slot('QVariant', result=str)
        def contactIdString(self, value): return str(value)
        @Slot('QVariant', result=str)
        def chatIdString(self, value): return str(value)
        @Slot('QVariant', result=str)
        def messageIdString(self, value): return str(value)
        @Slot('QVariant', result=QDateTime)
        def toDateTime(self, value): return QDateTime.fromMSecsSinceEpoch(int(value))
    class Design(QObject):
        @Property('QVariantMap', constant=True)
        def SizeBase(self): return {'px6': 6, 'px12': 12, 'px16': 16, 'px20': 20, 'px48': 48}
        @Property('QVariantMap', constant=True)
        def SpacingCommon(self): return {'_Shalf': 4, '_Zero': 0, '_S': 8, '_M': 12, '_L': 24, '_2XL': 24}
        @Property('QVariantMap', constant=True)
        def BorderRadiusCommon(self): return {'_Mhalf': 6, '_L': 12}
    class MessageTime(QObject):
        @Property('QVariantMap', constant=True)
        def DeliveryStatus(self): return {'Undefined': 0, 'Pending': 1, 'Failed': 2}
    class FontUtil(QObject):
        @Property('QVariantMap', constant=True)
        def Styles(self): return {'Body': 0, 'BodyStrong': 1, 'Description': 2, 'BubbleTag': 3}
        @Property('QVariantMap', constant=True)
        def Sizes(self): return {'Default': 0}
        @Slot(int, int, result=int)
        def getLineHeight(self, style, _size): return 22 if style < 2 else 20 if style == 2 else 14
        @Slot(int, int, result=QFont)
        def getFont(self, style, _size):
            font = QFont('Arial'); font.setPixelSize(16 if style < 2 else 14)
            font.setWeight(QFont.Weight.DemiBold if style == 1 else QFont.Weight.Normal)
            return font
    class Delivery(QObject):
        def __init__(self, parent=None):
            super().__init__(parent); self._message = None; self._time = None; self._default = 0
        messageContext = Property('QVariant', lambda s: s._message, lambda s, v: setattr(s, '_message', v))
        chatViewTime = Property('QVariant', lambda s: s._time, lambda s, v: setattr(s, '_time', v))
        defaultDeliveryStatus = Property(int, lambda s: s._default, lambda s, v: setattr(s, '_default', v))
        deliveryStatus = Property(int, lambda s: 1, constant=True)
    singleton_objects = [Utils(), Design(), MessageTime(), FontUtil()]
    for obj, module, name in zip(singleton_objects, ('QmlSingletonesModule', 'UiKitModule', 'UiKitModule', 'TypographyModule'), ('QmlUtils', 'DesignSystem', 'MessageTime', 'FontUtil')):
        qmlRegisterSingletonInstance(type(obj), module, 1, 0, name, obj)
    qmlRegisterType(Delivery, 'ru.oneme.components', 1, 0, 'MessageDeliveryStatusViewContext')
    from panel_scroll_qa import register_native_scroll
    register_native_scroll(output)

    qml = output / 'qml'
    ui = qml / 'UiKitModule'; ui.mkdir(parents=True)
    (ui / 'qmldir').write_text('module UiKitModule\nMeLoader 1.0 MeLoader.qml\nMeText 1.0 MeText.qml\nSvgIcon 1.0 SvgIcon.qml\nDebugBorder 1.0 DebugBorder.qml\nMeDeliveryTicks 1.0 MeDeliveryTicks.qml\nMeSwitcher 1.0 MeSwitcher.qml\n', encoding='utf8')
    (ui / 'MeSwitcher.qml').write_text('import QtQuick\nimport QtQuick.Controls\nSwitch {}\n', encoding='utf8')
    (ui / 'MeLoader.qml').write_text('''import QtQuick
Loader { property bool activeAndVisible: false; active: activeAndVisible; visible: activeAndVisible }
''', encoding='utf8')
    (ui / 'MeText.qml').write_text('''import QtQuick
Item {
 id: labelRoot
 property int typography: 0; property int typographySize: 0
 property real topPadding: 0; property real rightPadding: 0
 property alias text: label.text; property alias color: label.color
 implicitWidth: label.implicitWidth + rightPadding; implicitHeight: 14 + topPadding
 Text { id: label; y: labelRoot.topPadding; font.family: "Arial"; font.pixelSize: 12 }
}
''', encoding='utf8')
    (ui / 'DebugBorder.qml').write_text('import QtQuick\nItem { property color borderColor; property color bodyColor }\n', encoding='utf8')
    (ui / 'MeDeliveryTicks.qml').write_text('''import QtQuick
Item {
 property int deliveryStatus: 0
 property color readColor; property color deliveredColor; property color pendingColor; property color errorColor
 implicitWidth: 12; implicitHeight: 12
 Text { text: "✓"; color: parent.pendingColor; font.pixelSize: 12 }
}
''', encoding='utf8')
    shutil.copyfile(NATIVE / 'UiKitModule/SvgIcon.qml', ui / 'SvgIcon.qml')
    for module in ('ClientAppearanceModule', 'LottieModule'):
        folder = qml / module; folder.mkdir()
        (folder / 'qmldir').write_text('module ' + module + '\nDummy 1.0 Dummy.qml\n', encoding='utf8')
        (folder / 'Dummy.qml').write_text('import QtQuick\nQtObject {}\n', encoding='utf8')
    for name in ('ChatMessageTimeCapsule.qml', 'ChannelMessageTimeCapsule.qml'):
        shutil.copyfile(NATIVE / 'OneMeUi' / name, output / name)
    shutil.copyfile(NATIVE / 'OneMeUi/MessageTimeCapsule.qml', output / 'MessageTimeCapsule.qml')
    (output / 'MessageRetentionController.qml').write_text(controller_source(), encoding='utf8')

    icon_file = Path('D:/ValMAX/analysis/valmax-icons-rcc-qa-03/inputs/icon_pack/delete_fill.svg')
    if not icon_file.is_file(): raise AssertionError('Immutable native delete icon fixture unavailable')
    icon_data = {name: (icon_file.parent / name).read_bytes()
                 for name in ('delete_fill.svg', 'pin_fill.svg', 'eye_fill_mini.svg')}
    class Icons(QQuickImageProvider):
        def __init__(self): super().__init__(QQuickImageProvider.Image)
        def requestImage(self, requested, size, desired):
            color, suffix = requested.split('/', 1)
            if suffix not in ('qrc:/icon_pack/delete_fill.svg', 'qrc:/icon_pack/pin_fill.svg', 'qrc:/icon_pack/eye_fill_mini.svg'):
                raise AssertionError('Unexpected synthetic SVG source')
            width = max(1, min(48, desired.width() if desired.width() > 0 else 12))
            height = max(1, min(48, desired.height() if desired.height() > 0 else 12))
            image = QImage(width, height, QImage.Format_ARGB32_Premultiplied); image.fill(Qt.transparent)
            painter = QPainter(image); QSvgRenderer(icon_data[suffix.rsplit('/', 1)[1]]).render(painter)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn); painter.fillRect(image.rect(), QColor(color)); painter.end()
            size.setWidth(width); size.setHeight(height)
            return image

    class Message(QObject):
        chatIdChanged = Signal()
        def __init__(self, message_id, parent=None):
            super().__init__(parent); self._id = message_id; self._chat_id = '-9007199254740999'
        def set_chat(self, value): self._chat_id = value; self.chatIdChanged.emit()
        messageId = Property(str, lambda s: s._id, constant=True)
        chatId = Property(str, lambda s: s._chat_id, notify=chatIdChanged)
        time = Property('QVariant', lambda s: 1700000000123, constant=True)
        isEdited = Property(bool, lambda s: True, constant=True)
        shouldHideEditing = Property(bool, lambda s: False, constant=True)
        isDelayed = Property(bool, lambda s: False, constant=True)
    class Wrapper(QObject):
        def __init__(self, value, parent=None): super().__init__(parent); self.value = value
        @Slot(result=QObject)
        def get(self): return self.value
    class History(QAbstractListModel):
        def __init__(self):
            super().__init__(); self.messages = [Message('9007199254740993', self), Message('9007199254740993', self), Message('9007199254740995', self)]
            self.rows = [Wrapper(message, self) for message in self.messages]
        def roleNames(self): return {0x100: b'messageContext'}
        def rowCount(self, _parent=QModelIndex()): return len(self.rows)
        def data(self, index, role): return self.rows[index.row()] if index.isValid() and role == 0x100 else None
        def remove_first(self):
            self.beginRemoveRows(QModelIndex(), 0, 0); self.rows.pop(0); self.endRemoveRows()
    history = History()
    engine = QQmlEngine(); engine.addImportPath(str(qml)); engine.addImageProvider('svg_icon', Icons())
    engine.rootContext().setContextProperty('syntheticHistory', history)
    engine.rootContext().setContextProperty('syntheticMessage', history.rows[0])
    host = HOST.replace('qrc:/valmax/MessageRetentionController.qml', (output / 'MessageRetentionController.qml').as_uri())
    fixture = '''import QtQuick
import QtQuick.Window
import QtQuick.Controls
import UiKitModule as UiKit
import QmlSingletonesModule
Window {
 id: syntheticWindow
 width: 650; height: 390; visible: true
 color: "#edf0f4"
 Item {
  id: root; anchors.fill: parent
  property var window: syntheticWindow
  property bool isActiveChat: false
  property bool isHistoryPanelActive: true
  property var commonDependency: ({selfInfo: {id: "9007199254740997"}})
  property string chatIdText: "-9007199254740999"
  property var chatPropertiesContext: ({get: function() { return {chatId: root.chatIdText}; }})
  property alias controller: valmaxRetentionLoader.item
  function switchAccount() { commonDependency = {selfInfo: {id: "9007199254740001"}}; }
  function switchChat() { chatIdText = "-9007199254740003"; }
  function badgeAt(index) { return syntheticList.itemAtIndex(index).valmaxRetainedDeleted; }
  function badgeLoadedAt(index) { const loader = syntheticList.itemAtIndex(index).markerLoader; return loader.active && loader.item !== null; }
  function rowGeometryAt(index) { const row = syntheticList.itemAtIndex(index); return JSON.stringify([row.width,row.height,row.implicitHeight,row.contentItem.height,row.timeWidth]); }
''' + host + '''
  component NativeDelegate: Control {
   required property var messageContext
   property real timeWidth: 42
   property var viewContext: ({get: function() { return {isVisuallyOutgoing: false}; }})
   property var chatAppearance: appearance
   contentItem: Item { implicitHeight: 35 }
   width: 100; height: 35
  }
  ListView {
   id: syntheticList
   anchors.fill: parent; model: syntheticHistory
   delegate: NativeDelegate {
    id: messageDelegate
''' + DELEGATE_BINDING + BADGE + '''
    property alias markerLoader: valmaxRetainedBadgeLoader
    Component.onCompleted: { valmaxRetentionInitialized = true; valmaxSyncRetentionRegistration(); }
   }
  }
  QtObject {
   id: appearance
   property color colorBubblesOutgoingTextTime: "#228055"
   property color colorBubblesIncomingTextTime: "#687889"
   property color colorTextPrimaryInverseStatic: "white"
   property color colorBubblesOutgoingIconReadStatusCapsule: "white"
   property color colorBubblesOutgoingIconReadStatus: "#228055"
   property color colorBubblesOutgoingIconAlert: "#c43b3b"
   property color colorCapsuleBackground: "#596979"
   property color colorCapsuleOutside: "#687889"
   property color colorBubblesIncomingBackgroundSurfaceSecondary: "#eeeeef"
   property color colorBubblesOutgoingBackgroundSurfaceSecondary: "#c8f1da"
   property color colorBubblesIncomingTextBodySecondary: "#61616a"
   property color colorBubblesOutgoingTextBodySecondary: "#276e49"
  }
  Rectangle {
   x: 20; y: 90; width: parent.width - 40; height: 150; radius: 12; color: "white"
   Text { x: 16; y: 18; text: "Синтетическое сообщение"; font.family: "Arial"; font.pixelSize: 16 }
   MessageTimeCapsule {
    id: capsule; objectName: "syntheticCapsule"
    anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 12
    messageContext: syntheticMessage; chatViewTime: 0; chatAppearance: appearance
    viewsCounter: 0; isChannel: false; isInside: true; isOutgoing: false; isPinned: true
    showBackground: false
   }
  }
  function setRetainedMarker(deleted) {
   controller.applyReply({schema:1,accountId:controller.accountId,chatId:controller.chatId,deletedIds:deleted ? ["9007199254740993"] : [],enabled:true,ready:true,revision:"a".repeat(64)},["9007199254740993"]);
  }
  function showOutgoing() { capsule.isOutgoing = true; }
  function showChannel() { capsule.isChannel = true; capsule.viewsCounter = 12; }
  function showOutside() { capsule.isInside = false; capsule.showBackground = true; }
 }
}
'''
    fixture_path = output / 'SyntheticRetention.qml'; fixture_path.write_text(fixture, encoding='utf8')
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(fixture_path)))
    window = component.create()
    if window is None: raise AssertionError('\n'.join(error.toString() for error in component.errors()))
    QTest.qWait(100)
    root = window.contentItem().childItems()[0]
    def evaluate(expression):
        query = QQmlExpression(engine.contextForObject(root), root, expression)
        value, _undefined = query.evaluate()
        if query.hasError(): raise AssertionError(query.error().toString())
        return value
    controller = root.property('controller')
    if controller is None: raise AssertionError('Synthetic controller did not load')
    if controller.property('registrationCount') != 2: raise AssertionError('Delegate IDs were not refcounted')
    registrations = evaluate('controller.registrations')
    if hasattr(registrations, 'toVariant'): registrations = registrations.toVariant()
    if registrations['9007199254740993'] != 2: raise AssertionError('Duplicate delegate count lost: ' + repr(registrations))
    if controller.property('pendingRequest') is not None: raise AssertionError('Invisible synthetic controller tried HTTP')
    if evaluate('badgeLoadedAt(0) || badgeLoadedAt(1)'): raise AssertionError('Ordinary rows eagerly loaded deletion badges')
    normal_row_geometry = evaluate('rowGeometryAt(0)')
    evaluate('controller.applyReply({schema:1,accountId:controller.accountId,chatId:controller.chatId,deletedIds:["9007199254740993"],enabled:false,ready:true,revision:"a".repeat(64)},["9007199254740993"])')
    if not evaluate('badgeAt(0) && badgeAt(1)'): raise AssertionError('Persisted markers hidden while capture disabled')
    if not evaluate('badgeLoadedAt(0) && badgeLoadedAt(1)'): raise AssertionError('Confirmed markers did not load instance badges')
    if evaluate('rowGeometryAt(0)') != normal_row_geometry: raise AssertionError('Instance badge changed row geometry')
    history.messages[0].set_chat('-44'); QTest.qWait(30)
    if evaluate('badgeAt(0)') or not evaluate('badgeAt(1)'): raise AssertionError('Foreign chat with equal message ID leaked marker')
    history.messages[0].set_chat(''); QTest.qWait(30)
    if evaluate('badgeAt(0)'): raise AssertionError('Unknown chat acquired marker')
    history.messages[0].set_chat('-9007199254740999'); QTest.qWait(30)
    if not evaluate('badgeAt(0) && badgeAt(1)'): raise AssertionError('Matching chat did not re-register')
    history.remove_first(); QTest.qWait(50)
    registrations = evaluate('controller.registrations')
    if hasattr(registrations, 'toVariant'): registrations = registrations.toVariant()
    if registrations['9007199254740993'] != 1: raise AssertionError('Destroyed delegate not unregistered')
    evaluate('controller.applyReply({schema:1,accountId:controller.accountId,chatId:controller.chatId,deletedIds:["9007199254740993"],enabled:true,ready:true,revision:"a".repeat(64)},["9007199254740993"])')
    if not evaluate('controller.contains("9007199254740993")'): raise AssertionError('Confirmed marker not visible')
    evaluate('switchAccount()'); QTest.qWait(50)
    if evaluate('controller.contains("9007199254740993")'): raise AssertionError('Account marker leaked')
    if controller.property('registrationCount') != 2: raise AssertionError('Account change did not re-register delegates')
    evaluate('switchChat()'); QTest.qWait(50)
    if controller.property('registrationCount') != 0: raise AssertionError('Old-chat contexts registered in new chat')
    for message in history.messages: message.set_chat('-9007199254740003')
    QTest.qWait(50)
    if controller.property('registrationCount') != 2: raise AssertionError('Matching new-chat contexts did not re-register')
    capsule = window.findChild(QObject, 'syntheticCapsule')
    evaluate('setRetainedMarker(false)'); QTest.qWait(40); plain_width = capsule.property('implicitWidth')
    plain_row_geometry = evaluate('rowGeometryAt(0)')
    evaluate('setRetainedMarker(true)'); QTest.qWait(80); retained_width = capsule.property('implicitWidth')
    if abs(retained_width - plain_width) > .01: raise AssertionError('Instance marker changed native footer width')
    if evaluate('rowGeometryAt(0)') != plain_row_geometry: raise AssertionError('Instance marker changed row geometry')
    badge = evaluate('syntheticList.itemAtIndex(0).markerLoader.item')
    if hasattr(badge, 'toQObject'): badge = badge.toQObject()
    if badge is None or not evaluate('badgeLoadedAt(0)'): raise AssertionError('Lazy instance deletion badge missing')
    icon = next((child for child in badge.childItems() if child.metaObject().indexOfProperty('imageWidth') >= 0), None)
    if icon is None or abs(icon.property('width') - 12) > .1 or abs(icon.property('height') - 12) > .1: raise AssertionError('Native badge icon is not 12px')
    if icon.property('enabled'): raise AssertionError('Decorative native icon can consume input')
    screenshots = []
    footer_geometry = []
    for width, mode in ((390, 'incoming'), (650, 'outgoing'), (650, 'channel'), (390, 'outside')):
        window.setWidth(width)
        if mode == 'outgoing': evaluate('showOutgoing()')
        if mode == 'channel': evaluate('showChannel()')
        if mode == 'outside': evaluate('showOutside()')
        evaluate('setRetainedMarker(false)'); QTest.qWait(20); mode_plain = capsule.property('implicitWidth')
        evaluate('setRetainedMarker(true)'); QTest.qWait(20); mode_marked = capsule.property('implicitWidth')
        if abs(mode_plain - mode_marked) > .01: raise AssertionError('Native footer geometry changed for ' + mode)
        footer_geometry.append({'mode': mode, 'plainWidth': mode_plain, 'retainedWidth': mode_marked})
        QTest.qWait(80)
        path = output / (str(width) + '-' + mode + '.png')
        if not window.grabWindow().save(str(path)): raise AssertionError('Synthetic render failed')
        screenshots.append(str(path))
    window.close(); window.deleteLater(); QTest.qWait(10)

    state = {'enabled': False, 'revision': 'c' * 64, 'ack': True, 'ready': True, 'conflict': False}
    requests = []
    def status():
        return {'schema': 1, 'revision': state['revision'], 'enabled': state['enabled'], 'runtime': {
            'ready': state['ready'], 'applied': state['ready'] and state['ack'],
            'enabled': state['enabled'] if state['ack'] else not state['enabled'],
            'retainedEvents': 4, 'retainedMessages': 7, 'error': None, 'historySyncVerified': False}}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/message-retention/status': self.reply(404, {}); return
            requests.append({'method': 'GET', 'path': self.path})
            self.reply(200, status())
        def do_POST(self):
            if self.path != '/message-retention/settings': self.reply(404, {}); return
            value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'method': 'POST', 'path': self.path, 'body': value, 'marker': self.headers.get('X-Max-Mod-Local-Write')})
            if set(value) != {'schema', 'revision', 'enabled'} or value['schema'] != 1 or type(value['enabled']) is not bool or self.headers.get('X-Max-Mod-Local-Write') != 'message-retention-v1': self.reply(400, {}); return
            if state['conflict'] or value['revision'] != state['revision']:
                state['revision'] = 'e' * 64; self.reply(409, {'schema': 1, 'error': 'revision_conflict'}); return
            state['enabled'] = value['enabled']; state['revision'] = ('d' if state['enabled'] else 'f') * 64; state['ack'] = False
            self.reply(200, status())
        def reply(self, code, value):
            data = json.dumps(value).encode('utf8')
            self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError): pass
        def log_message(self, *_args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings_fixture = '''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import UiKitModule as UiKit
import TypographyModule as Typography
ApplicationWindow {
 id: root; width: 390; height: 900; visible: true
 property bool dark: false
 property alias page: settingsLoader.item
 signal backClicked()
 QtObject {
  id: tokens
  readonly property color colorTextPrimary: root.dark ? "#edf0f2" : "#0c0d0e"
  readonly property color colorTextSecondary: root.dark ? "#a7b0ba" : "#5e6269"
  readonly property color colorTextTertiary: root.dark ? "#a7b0ba" : "#7a7f85"
  readonly property color colorTextThemed: root.dark ? "#b98cff" : "#8a42ff"
  readonly property color colorTextAttention: root.dark ? "#f2c773" : "#9c6813"
  readonly property color colorTextPositive: root.dark ? "#55c6a0" : "#168663"
  readonly property color colorBackgroundSurface: root.dark ? "#17212a" : "#edeef2"
  readonly property color colorBackgroundCard: root.dark ? "#222e39" : "#ffffff"
 }
 component SettingsBaseItem: Control {
  id: base
  property string title
  property var maxwellColorTokens: tokens
  property alias contentLoaderItem: contentLoader.sourceComponent
  signal backClicked()
  background: Rectangle { color: tokens.colorBackgroundSurface }
  contentItem: ColumnLayout {
   spacing: 0
   Label { Layout.fillWidth: true; Layout.preferredHeight: 64; text: base.title; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: tokens.colorTextPrimary; font.pixelSize: 18 }
   Loader { id: contentLoader; Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: 24; Layout.rightMargin: 24 }
  }
 }
 __PANEL__
 Loader { id: settingsLoader; anchors.fill: parent; sourceComponent: settingsValMAXRetention }
}
'''
    panel = Path(__file__).with_name('message_retention_settings_panel.qml').read_text('utf8')
    settings_fixture = settings_fixture.replace('__PANEL__', panel).replace('http://127.0.0.1:18973/message-retention/', 'http://127.0.0.1:' + str(server.server_port) + '/message-retention/')
    settings_path = output / 'SyntheticRetentionSettings.qml'; settings_path.write_text(settings_fixture, encoding='utf8')
    settings_component = QQmlComponent(engine)
    settings_component.setData(settings_fixture.encode('utf8'), QUrl.fromLocalFile(str(settings_path)))
    settings_window = settings_component.create()
    if settings_window is None: raise AssertionError('\n'.join(error.toString() for error in settings_component.errors()))
    page = settings_window.property('page')
    def pump(predicate):
        for _ in range(300):
            QTest.qWait(10)
            if predicate(): return
        raise AssertionError('Synthetic retention settings timed out')
    def page_eval(expression):
        query = QQmlExpression(engine.contextForObject(page), page, expression)
        value, _undefined = query.evaluate()
        if query.hasError(): raise AssertionError(query.error().toString())
        return value
    def refresh(quiet=False):
        pump(lambda: page.property('pendingRequest') is None)
        page_eval('request("GET", "", ' + ('true' if quiet else 'false') + ')')
        pump(lambda: page.property('pendingRequest') is None)
    try:
        pump(lambda: page.property('loaded'))
        page.findChild(QObject, 'retentionSettingsPoll').setProperty('running', False)
        if page.property('savedEnabled') or page.property('editorEnabled') or any(x['method'] == 'POST' for x in requests): raise AssertionError('Retention default is not off/read-only GET')
        QTest.qWait(80); path = output / '390-settings-off-light.png'; settings_window.grabWindow().save(str(path)); screenshots.append(str(path))
        switch = page.findChild(QObject, 'retentionEnabled')
        switch.setProperty('checked', True); QMetaObject.invokeMethod(switch, 'toggled', Qt.DirectConnection)
        page_eval('save()'); pump(lambda: page.property('pendingRequest') is None)
        if not page.property('savedEnabled') or page.property('confirmedActive') or page.property('runtimeTitle') != 'Ожидание применения': raise AssertionError('Save is falsely treated as native ACK')
        state['ack'] = True; refresh(True)
        if not page.property('confirmedActive'): raise AssertionError('Native ACK not reflected')
        if not page.findChild(QObject, 'retentionExperimental').property('visible'): raise AssertionError('Unverified history-sync warning hidden')
        settings_window.setWidth(650); settings_window.setProperty('dark', True); QTest.qWait(80)
        path = output / '650-settings-active-dark.png'; settings_window.grabWindow().save(str(path)); screenshots.append(str(path))
        switch.setProperty('checked', False); QMetaObject.invokeMethod(switch, 'toggled', Qt.DirectConnection)
        state['conflict'] = True; page_eval('save()'); pump(lambda: page.property('pendingRequest') is None)
        if not page.property('needsRefresh') or page.property('editorEnabled') or not page.property('dirty'): raise AssertionError('CAS conflict lost draft')
        refresh(True)
        if not page.property('needsRefresh') or page.property('editorEnabled'): raise AssertionError('Quiet GET bypassed conflict confirmation')
        state['conflict'] = False; refresh(False); page_eval('save()'); pump(lambda: page.property('pendingRequest') is None)
        if page.property('savedEnabled'): raise AssertionError('Capture disable not saved')
        state['ready'] = False; state['ack'] = False; refresh(True)
        if page.property('confirmedActive') or page.property('runtimeTitle') != 'Фильтр не подключён': raise AssertionError('Unavailable native runtime shown as active')
    finally:
        settings_window.close(); settings_window.deleteLater(); QTest.qWait(10)
        server.shutdown(); server.server_close()

    result = {'schema': 1, 'qtVersion': qVersion(), 'delegateRefcounts': True, 'scopeReset': True,
        'retentionModuleSha256': retention_pin,
        'qaSourceSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'footerWidthUnchanged': True, 'plainWidth': plain_width, 'retainedWidth': retained_width,
        'footerByteEqualBaseline': (output / 'MessageTimeCapsule.qml').read_bytes() == (NATIVE / 'OneMeUi/MessageTimeCapsule.qml').read_bytes(),
        'nativeFooterSha256': hashlib.sha256((output / 'MessageTimeCapsule.qml').read_bytes()).hexdigest(),
        'instanceBadgeLazy': True, 'instanceBadgeGeometryUnchanged': True, 'footerGeometry': footer_geometry,
        'icon12px': True, 'screenshots': screenshots, 'warnings': warnings,
        'messageChatMismatchRejected': True, 'unknownMessageChatRejected': True,
        'persistedMarkerWhileCaptureOff': True,
        'settingsDefaultOff': True, 'settingsSaveCAS': True, 'savedVersusNativeACK': True,
        'settingsConflictPreservesDraft': True, 'settingsUnavailableHonest': True,
        'settingsSyntheticHTTPRequests': len(requests),
        'realClientStarted': False, 'realMessagesRead': False, 'realNetworkRequests': False}
    qInstallMessageHandler(previous)
    if hashlib.sha256(Path(retention_module.__file__).read_bytes()).hexdigest() != retention_pin: raise AssertionError('Retention source changed during QA')
    (output / 'qml-proof.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    if warnings: raise AssertionError('Synthetic QML emitted warnings')
    faulthandler.cancel_dump_traceback_later()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
