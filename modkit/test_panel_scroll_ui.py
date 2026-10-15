"""Actual Qt wheel events in copied native MAX settings/scroll components.

Panel viewport declarations are the production bytes. Their content is replaced
by owned synthetic controls; account state, timers and HTTP never run here.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

HERE = Path(__file__).parent
BASE = Path('D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml')
OUTPUT = Path('D:/ValMAX/analysis/panel-scroll-v1/wheel')
PAGES = {
    'ui_privacy_panel.qml': ('modPrivacyPage', 'modPrivacyScroll', 'modPrivacyContent'),
    'ghost_panel.qml': ('ghostPage', 'ghostScroll', 'ghostScrollContent'),
    'account_preferences_panel.qml': ('accountPreferencesPage', 'accountScroll', 'accountScrollContent'),
    'quick_replies_panel.qml': ('quickReplySettings', 'settingsScroll', 'settingsScrollContent'),
    'message_retention_settings_panel.qml': ('retentionPage', 'retentionSettingsScroll', 'retentionScrollContent'),
    'session_transfer_panel.qml': ('transferPage', 'transferScroll', 'transferScrollContent'),
    'mdata_panel.qml': ('mdataPage', 'mdataScroll', 'mdataScrollContent'),
}


def block_end(source, start):
    """Find balanced QML braces while ignoring strings and comments."""
    depth, quote, escape, comment = 0, None, False, None
    position = start
    while position < len(source):
        char, next_char = source[position], source[position + 1:position + 2]
        if comment == 'line':
            if char == '\n':
                comment = None
        elif comment == 'block':
            if char == '*' and next_char == '/':
                comment = None
                position += 1
        elif quote is not None:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == quote:
                quote = None
        elif char in ('"', "'", '`'):
            quote = char
        elif char == '/' and next_char in ('/', '*'):
            comment = 'line' if next_char == '/' else 'block'
            position += 1
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    raise ValueError('Unbalanced owned panel viewport')


def viewport_fixture(name):
    import re
    source = (HERE / name).read_text('utf8')
    marker = 'contentLoaderItem: UiKit.MeFlickable {'
    if source.count(marker) != 1:
        raise ValueError('Missing unique native viewport')
    start = source.index(marker) + len('contentLoaderItem: ')
    end = block_end(source, source.index('{', start))
    viewport = source[start:end]
    column = viewport.index('ColumnLayout {')
    opening = viewport.index('{', column)
    column_end = block_end(viewport, opening)
    body = viewport[opening + 1:column_end - 1]
    first_row = re.search(r'\b(?:DashboardText|GhostText|AccountText|ReplyText|RetentionText|TransferText|MDataText)\s*\{', body)
    if first_row is None:
        raise ValueError('Missing synthetic insertion boundary')
    header = viewport[:column]
    # Signals/functions belong to feature behaviour, not scroll geometry.
    # Remove only Connections blocks in this test-only viewport fixture.
    while 'Connections {' in header:
        connection = header.index('Connections {')
        close = block_end(header, header.index('{', connection))
        header = header[:connection] + header[close:]
    header = header.replace('ScrollBar.vertical: UiKit.MeScrollBar {}',
                            'ScrollBar.vertical: UiKit.MeScrollBar { objectName: "testNativeBar" }')
    controls = '''
        TextField { objectName:"testField"; Layout.fillWidth:true; text:"Owned synthetic input" }
        Button { objectName:"testButton_0"; Layout.fillWidth:true; implicitHeight:48
            text:"Synthetic action"; onClicked:fixture.clicks++ }
        ScrollView { id:testNestedScroll; objectName:"testNestedScroll"; Layout.fillWidth:true
            Layout.preferredHeight:100; clip:true; contentWidth:availableWidth
            TextArea { width:testNestedScroll.availableWidth; text:new Array(30).join("Owned synthetic line\\n")
                wrapMode:TextEdit.Wrap; textFormat:TextEdit.PlainText }
        }
        Repeater { model:fixture.rows
            Button { required property int index; objectName:"testRow_"+index
                Layout.fillWidth:true; implicitHeight:48; text:"Synthetic row "+index
                onClicked:fixture.clicks++
            }
        }
    '''
    return header + 'ColumnLayout {' + body[:first_row.start()] + controls + '}}'


class PanelScrollTests(unittest.TestCase):
    def test_all_mod_settings_use_explicit_native_vertical_viewport(self):
        for name, (_page, viewport, content) in PAGES.items():
            with self.subTest(panel=name):
                source = (HERE / name).read_text('utf8')
                self.assertEqual(1, source.count('contentLoaderItem: UiKit.MeFlickable {'))
                self.assertIn('contentWidth: width; contentHeight: ' + content + '.implicitHeight + 2 * ', source)
                self.assertIn('flickableDirection: Flickable.VerticalFlick', source)
                self.assertIn('ScrollBar.vertical: UiKit.MeScrollBar {}', source)
                self.assertIn('width: ' + viewport + '.width', source)
                self.assertNotIn(viewport + '.availableWidth', source)
                self.assertTrue(viewport_fixture(name).startswith('UiKit.MeFlickable {'))

    def test_quick_reply_editor_keeps_nested_text_scroll_and_correct_focus_target(self):
        source = (HERE / 'quick_replies_panel.qml').read_text('utf8')
        self.assertIn('id: replyTextScroll', source)
        self.assertIn('width: replyTextScroll.availableWidth', source)
        self.assertIn('var viewport = settingsScroll;', source)
        self.assertIn('commandInput.mapToItem(viewport.contentItem, 0, 0)', source)
        self.assertNotIn('var viewport = settingsScroll.contentItem;', source)

    def test_actual_native_viewports_scroll_by_wheel_and_preserve_clicks_when_small(self):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()), '--qt-owned'],
                                cwd=HERE, capture_output=True, text=True, timeout=45)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        proof = json.loads((OUTPUT / 'proof.json').read_text('utf8'))
        self.assertEqual(set(PAGES), set(proof['pages']))
        self.assertEqual([], proof['warnings'])
        self.assertFalse(proof['maxClientStarted'])
        for value in proof['pages'].values():
            self.assertTrue(value['angleWheelScrolled'])
            self.assertTrue(value['pixelWheelScrolled'])
            self.assertTrue(value['textFieldWheelScrolled'])
            self.assertTrue(value['buttonClickPreserved'])
            self.assertTrue(value['nestedTextAreaScrollsIndependently'])
            self.assertTrue(value['bottomReachable'])
            self.assertTrue(value['topReachable'])
            self.assertTrue(value['resizeAndDynamicContentClamped'])


def qt_owned():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for variable in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'QML_DISK_CACHE_PATH'):
        directory = OUTPUT / 'synthetic-profile' / variable.lower()
        directory.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(directory)
    fonts = OUTPUT / 'fonts'
    fonts.mkdir(exist_ok=True)
    for name in ('arial.ttf', 'arialbd.ttf'):
        shutil.copyfile(Path('C:/Windows/Fonts') / name, fonts / name)
    os.environ.update(QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                      QML_DISABLE_DISK_CACHE='1', QT_QUICK_CONTROLS_STYLE='Basic', QT_QPA_FONTDIR=str(fonts))
    from PySide6.QtCore import QObject, Property, QPoint, QPointF, QCoreApplication, QMetaObject, QUrl, Qt, qInstallMessageHandler
    from PySide6.QtGui import QGuiApplication, QWheelEvent
    from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest

    imports = OUTPUT / 'qml-v2'
    ui, appearance = imports / 'UiKitModule', imports / 'ClientAppearanceModule'
    ui.mkdir(parents=True, exist_ok=True)
    appearance.mkdir(parents=True, exist_ok=True)
    pinned = {}
    for relative, target in (('UiKitModule/MeFlickable.qml', ui / 'MeFlickable.qml'),
                             ('UiKitModule/MeScrollBar.qml', ui / 'MeScrollBar.qml'),
                             ('OneMeUi/SettingsBaseItem.qml', imports / 'SettingsBaseItem.qml')):
        raw = (BASE / relative).read_bytes()
        target.write_bytes(raw)
        pinned[relative] = hashlib.sha256(raw).hexdigest()
    (ui / 'qmldir').write_text('module UiKitModule\nMeFlickable 1.0 MeFlickable.qml\nMeScrollBar 1.0 MeScrollBar.qml\nSettingsPageHeader 1.0 SettingsPageHeader.qml\n', 'utf8')
    (ui / 'SettingsPageHeader.qml').write_text('import QtQuick\nRectangle { required property string title; property bool backButtonVisible; property var backButtonAction; implicitHeight:56; color:"#eee" }', 'utf8')
    (appearance / 'qmldir').write_text('module ClientAppearanceModule\nsingleton AppearanceTokens 1.0 AppearanceTokens.qml\n', 'utf8')
    (appearance / 'AppearanceTokens.qml').write_text('pragma Singleton\nimport QtQuick\nQtObject { readonly property color colorBackgroundSurface:"#f0f0f0"; readonly property color colorStatesFloatScrollBarPressed:"#555"; readonly property color colorStatesFloatScrollBarHover:"#666"; readonly property color colorFloatScrollBar:"#888" }', 'utf8')
    class WindowState(QObject):
        @Property(float, constant=True)
        def separatorsWidth(self): return 1.0
        @Property(bool, constant=True)
        def isOneFrame(self): return False
        @Property(float, constant=True)
        def sidePanelsMaximumWidth(self): return 640.0

    class DesignSystem(QObject):
        @Property('QVariantMap', constant=True)
        def SpacingCommon(self): return {'_Zero': 0, '_2XL': 24}
        @Property('QVariantMap', constant=True)
        def SizeBase(self): return {'px6': 6}

    app = QGuiApplication([])
    warnings = []
    qInstallMessageHandler(lambda _kind, _context, message: warnings.append(message))
    state = WindowState()
    design = DesignSystem()
    qmlRegisterSingletonInstance(DesignSystem, 'UiKitModule', 1, 0, 'DesignSystem', design)
    pages = {}

    def wheel(window, point, *, angle=0, pixel=0):
        event = QWheelEvent(point, point, QPoint(0, pixel), QPoint(0, angle),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        event.setTimestamp(int(time.monotonic() * 1000))
        QCoreApplication.sendEvent(window, event)
        QTest.qWait(90)

    def visual_item(window, name):
        pending = [window.contentItem()]
        while pending:
            item = pending.pop()
            if item.objectName() == name:
                return item
            pending.extend(item.childItems())
        return None

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(imports))
    engine.rootContext().setContextProperty('windowState', state)
    for name, (page_id, viewport_id, _content_id) in PAGES.items():
        qml = '''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import UiKitModule as UiKit
ApplicationWindow { id:fixture; width:500; height:260; visible:true
    property int rows:30; property int clicks:0
    QtObject { id:PAGEID; property real sectionGap:24; property real sectionSpacing:24; property real gap:12; property real innerPadding:12 }
    SettingsBaseItem { anchors.fill:parent; title:"Owned synthetic settings"; contentLoaderItem:VIEWPORT }
}'''.replace('PAGEID', page_id).replace('VIEWPORT', viewport_fixture(name))
        engine.loadData(qml.encode(), QUrl.fromLocalFile(str(imports / ('fixture-' + name))))
        if not engine.rootObjects():
            raise AssertionError(name + ': ' + '\n'.join(warnings))
        window = engine.rootObjects()[-1]
        QTest.qWait(50)
        viewport = window.findChild(QQuickItem, viewport_id)
        # QML id is not an objectName. The production objectName differs on
        # several pages, so find the owner of this fixture's native scrollbar.
        bar = window.findChild(QQuickItem, 'testNativeBar')
        if viewport is None:
            viewport = bar.parentItem()
        field = visual_item(window, 'testField')
        first_button = visual_item(window, 'testButton_0')
        if not (0 < viewport.height() < window.height() and viewport.property('contentHeight') > viewport.height()):
            raise AssertionError(name + ': viewport did not fit the small native settings loader')
        field_point = field.mapToScene(QPointF(field.width()/2, field.height()/2))
        wheel(window, field_point, angle=-120)
        text_field_scrolled = viewport.property('contentY') > 0
        QMetaObject.invokeMethod(viewport, 'cancelFlick')
        viewport.setProperty('contentY', 0)
        nested = visual_item(window, 'testNestedScroll')
        inner_viewport = nested.property('contentItem')
        nested_point = nested.mapToScene(QPointF(nested.width()/2, 10))
        wheel(window, nested_point, angle=-120)
        nested_scrolled = inner_viewport.property('contentY') > 0 and abs(viewport.property('contentY')) < 1
        QMetaObject.invokeMethod(inner_viewport, 'cancelFlick')
        QMetaObject.invokeMethod(viewport, 'cancelFlick')
        viewport.setProperty('contentY', 0)
        button_point = first_button.mapToScene(QPointF(first_button.width()/2, first_button.height()/2))
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, button_point.toPoint())
        click_preserved = window.property('clicks') == 1
        wheel(window, button_point, angle=-120)
        angle_scrolled = viewport.property('contentY') > 0
        previous = viewport.property('contentY')
        center = viewport.mapToScene(QPointF(viewport.width()/2, viewport.height()/2))
        wheel(window, center, pixel=-40)
        pixel_scrolled = viewport.property('contentY') > previous
        down_point = viewport.mapToScene(QPointF(viewport.width()/2, viewport.height()-2))
        up_point = viewport.mapToScene(QPointF(viewport.width()/2, 2))
        for _ in range(20):
            if viewport.property('contentY') + viewport.height() >= viewport.property('contentHeight') - 1:
                break
            wheel(window, down_point, angle=-1200)
        bottom = viewport.property('contentY') + viewport.height() >= viewport.property('contentHeight') - 1
        for _ in range(20):
            if viewport.property('contentY') <= 1:
                break
            wheel(window, up_point, angle=1200)
        top = viewport.property('contentY') <= 1
        viewport.setProperty('contentY', max(0, viewport.property('contentHeight') - viewport.height()))
        window.setHeight(420)
        window.setWidth(340)
        QTest.qWait(80)
        window.setProperty('rows', 1)
        QTest.qWait(180)
        bounded = -1 <= viewport.property('contentY') <= max(0, viewport.property('contentHeight')-viewport.height()) + 1
        bounded = bounded and abs(viewport.property('contentWidth')-viewport.width()) < 1
        pages[name] = {'angleWheelScrolled': angle_scrolled, 'pixelWheelScrolled': pixel_scrolled,
                       'textFieldWheelScrolled': text_field_scrolled, 'buttonClickPreserved': click_preserved,
                       'nestedTextAreaScrollsIndependently': nested_scrolled,
                       'bottomReachable': bottom, 'topReachable': top, 'resizeAndDynamicContentClamped': bounded}
        window.close()
        del window, viewport, field, first_button, bar, nested, inner_viewport
        app.processEvents()
    engine.deleteLater()
    app.processEvents()
    proof = {'schema': 1, 'pages': pages, 'nativeQmlSha256': pinned, 'warnings': warnings,
             'headerAndColorSingletonSimulated': True, 'syntheticContent': True, 'maxClientStarted': False}
    (OUTPUT / 'proof.json').write_text(json.dumps(proof, indent=2), 'utf8')
    if warnings or not all(all(result.values()) for result in pages.values()):
        raise AssertionError(json.dumps(proof, indent=2))


if __name__ == '__main__':
    if '--qt-owned' in sys.argv:
        qt_owned()
    else:
        unittest.main()
