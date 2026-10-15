"""Real Qt timer and source guards; no host windows, profile or mouse access."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import performance_ui as target

BASELINE = Path(r"D:\CodexLabs\max-mod-26.35.1\runtime\baseline\max.exe")


class PerformanceUiTests(unittest.TestCase):
    def test_native_window_source_anchors_and_geometry_handlers(self):
        if not BASELINE.is_file():
            self.skipTest("Pinned immutable baseline absent")
        import ui_multi_resource_patch
        source = ui_multi_resource_patch.read_source(BASELINE.read_bytes(), ":/qt/qml/OneMeUi/MeWindow.qml").decode("utf8")
        changed = target.transform_window(source)
        self.assertIn("onWidthChanged: windowState.width = width", changed)
        self.assertIn("onHeightChanged: windowState.height = height", changed)
        self.assertNotIn("valmaxWindowSizeTimer", changed)
        self.assertNotIn("commitValMAXWindowSize", changed)
        self.assertIn("onClosing: closeEvent => {\n        root.commitValMAXWindowPosition();", changed)
        self.assertEqual(changed.count(target.MARKER), 1)
        with self.assertRaises(ValueError):
            target.transform_window(changed)
        with self.assertRaises(ValueError):
            target.transform_window(source.replace("onXChanged: windowState.x = x", "onXChanged: unknown()"))
        with self.assertRaises(ValueError):
            target.transform_sources({})

    def test_all_layout_consumers_keep_native_geometry_bindings(self):
        if not BASELINE.is_file():
            self.skipTest("Pinned immutable baseline absent")
        import ui_multi_resource_patch as resources
        binary = BASELINE.read_bytes()
        sources = {path.rsplit("/", 1)[1]: resources.read_source(binary, path).decode("utf8") for path in resources.RESOURCES}
        changed = target.transform_sources(sources)
        self.assertEqual(set(changed), set(sources))
        for name in sources:
            if name != "MeWindow.qml":
                self.assertEqual(changed[name], sources[name], name)
        with self.assertRaises(ValueError):
            target.transform_sources({name: value for name, value in sources.items() if name != "MeWindow.qml"})

    def test_real_qt_coalesces_moves_and_flushes_final_coordinates(self):
        with tempfile.TemporaryDirectory(prefix="position-", dir=r"D:\ValMAX\analysis\test-temp") as directory:
            environment = dict(os.environ)
            for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP", "QML_DISK_CACHE_PATH"):
                path = Path(directory) / key
                path.mkdir()
                environment[key] = str(path)
            environment.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1")
            program = r'''
import json,sys
from PySide6.QtCore import QObject,Property,QEventLoop,QTimer,QUrl,QMetaObject,Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine,QQmlComponent
import performance_ui
class State(QObject):
    def __init__(self): super().__init__();self.xv=0;self.yv=0;self.wv=80;self.hv=80;self.writes=0
    def sx(self,v): self.xv=v;self.writes+=1
    def sy(self,v): self.yv=v;self.writes+=1
    x=Property(int,lambda s:s.xv,sx);y=Property(int,lambda s:s.yv,sy)
    width=Property(int,lambda s:s.wv,lambda s,v:setattr(s,'wv',v));height=Property(int,lambda s:s.hv,lambda s,v:setattr(s,'hv',v))
def settle(ms):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
app=QGuiApplication([]);engine=QQmlEngine();state=State()
engine.rootContext().setContextProperty('windowState',state)
template=''' + "'''" + '''import QtQuick
import QtQuick.Window
Window {
    id: root
    width: 80; height: 80
    readonly property int windowVisibility: visibility
    onWindowVisibilityChanged: {
    }
    onWidthChanged: windowState.width = width
    onHeightChanged: windowState.height = height
    onXChanged: windowState.x = x
    onYChanged: windowState.y = y
    onClosing: closeEvent => {
        closeEvent.accepted = true;
    }
}
''' + "'''" + r'''
component=QQmlComponent(engine);component.setData(performance_ui.transform_window(template).encode(),QUrl('file:///D:/synthetic-position.qml'))
assert not component.isError(),[x.toString() for x in component.errors()]
window=component.create();assert window is not None
state.writes=0
for n in range(1,101):window.setX(n);window.setY(n+100)
assert state.writes==0,state.writes
assert window.x()==100 and window.y()==200
settle(220)
assert state.writes==2 and (state.xv,state.yv)==(100,200),(state.writes,state.xv,state.yv)
window.setX(333);window.setY(444)
assert QMetaObject.invokeMethod(window,'commitValMAXWindowPosition',Qt.DirectConnection)
assert state.writes==4 and (state.xv,state.yv)==(333,444)
settle(220);assert state.writes==4
window.setVisible(True);settle(20);state.writes=0
window.setX(555);window.setY(666);window.setVisible(False)
assert state.writes==2 and (state.xv,state.yv)==(window.x(),window.y()),(state.writes,state.xv,state.yv,window.x(),window.y())
settle(220);assert state.writes==2
window.setVisible(True);settle(20);state.writes=0
window.setX(777);window.setY(888);assert window.close()
assert state.writes==2 and (state.xv,state.yv)==(777,888),(state.writes,state.xv,state.yv)
settle(220);assert state.writes==2
window.deleteLater();app.processEvents();print(json.dumps({'timer':'pass','moves':100,'coalescedWrites':2,'flush':'pass'}))
'''
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", program],
                                    cwd=Path(__file__).parent, env=environment,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr[-2500:])
            self.assertIn('"timer": "pass"', result.stdout)
            self.assertNotIn("ReferenceError", result.stderr)
            self.assertNotIn("TypeError", result.stderr)

    def test_real_qt_geometry_and_native_consumers_are_synchronous_during_resize(self):
        template = '''import QtQuick
import QtQuick.Window
Window {
    id: root
    width: 1000; height: 700
    readonly property int windowVisibility: visibility
    property int lastVisibleVisibility: visibility
    onWindowVisibilityChanged: {
        if (visibility !== Window.Hidden && visibility !== Window.Minimized)
            lastVisibleVisibility = visibility;
    }
    onWidthChanged: windowState.width = width
    onHeightChanged: windowState.height = height
    onXChanged: windowState.x = x
    onYChanged: windowState.y = y
    onClosing: closeEvent => {
        closeEvent.accepted = true;
    }
}
'''
        layout = '''import QtQuick
Item {
    id: root
    property real half: windowState.width / 2
    property real minimum: windowState.width
    property real preferred: windowState.width
    property real maximum: windowState.width
    property bool narrow: windowState.isOneFrame
    property bool narrowAgain: windowState.isOneFrame
}
'''
        program = r'''
import json,sys
from PySide6.QtCore import QObject,Property,Signal,QEventLoop,QTimer,QUrl,QCoreApplication,QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine,QQmlComponent
from PySide6.QtQuick import QQuickItem,QQuickWindow
import performance_ui
TEMPLATE=json.loads(sys.argv[1]);LAYOUT=json.loads(sys.argv[2])
class State(QObject):
    changed=Signal()
    def __init__(self):
        super().__init__();self.w=1000;self.h=700;self.xv=0;self.yv=0;self.storage=[]
    def sw(self,value):
        if value!=self.w:self.w=value;self.changed.emit();self.saved()
    def sh(self,value):
        if value!=self.h:self.h=value;self.changed.emit();self.saved()
    def saved(self):
        # Mirrors only the statically proved native positive-size callback:
        # each changed coordinate writes both stored sizes, with no visibility guard.
        if self.w>0 and self.h>0:self.storage.extend((('w',self.w),('h',self.h)))
    width=Property(int,lambda s:s.w,sw,notify=changed)
    height=Property(int,lambda s:s.h,sh,notify=changed)
    isOneFrame=Property(bool,lambda s:s.w<=916,notify=changed)
    x=Property(int,lambda s:s.xv,lambda s,v:setattr(s,'xv',v))
    y=Property(int,lambda s:s.yv,lambda s,v:setattr(s,'yv',v))
def settle(ms):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
def build(engine,source,label):
    component=QQmlComponent(engine)
    component.setData(source.encode(),QUrl('file:///D:/'+label+'.qml'))
    assert not component.isError(),[e.toString() for e in component.errors()]
    item=component.create();assert item is not None,[e.toString() for e in component.errors()]
    return item,component
app=QGuiApplication([]);app.setQuitOnLastWindowClosed(False)
records={};write_counts={};layout_checks=0
for changed in (False,True):
    engine=QQmlEngine();state=State();engine.rootContext().setContextProperty('windowState',state)
    window,component=build(engine,performance_ui.transform_window(TEMPLATE) if changed else TEMPLATE,'synthetic-resize')
    live,live_component=build(engine,LAYOUT,'synthetic-native-width')
    live.setParentItem(window.contentItem())
    window.showNormal();settle(220);state.storage=[]
    for n in range(1,101):
        window.setWidth(1000+n)
        window.setHeight(700+n)
        app.processEvents()
        assert state.w==window.width(),('stale-native-width',state.w,window.width())
        assert state.h==window.height(),('stale-native-height',state.h,window.height())
        assert live.property('half')==window.width()/2
        assert live.property('minimum')==window.width()
        assert live.property('preferred')==window.width()
        assert live.property('maximum')==window.width()
        assert live.property('narrow')==(window.width()<=916)
        assert live.property('narrowAgain')==(window.width()<=916)
        layout_checks+=6
    self_count=len(state.storage)
    assert self_count==400,self_count
    settle(220)
    assert (state.w,state.h)==(window.width(),window.height())
    write_counts['synchronous' if changed else 'baseline']=len(state.storage)
    assert len(state.storage)==400
    if changed:
        # Unmodified native consumers see the current breakpoint immediately.
        state.storage=[]
        for size,expected in ((915,True),(916,True),(917,False)):
            window.setWidth(size)
            app.processEvents()
            assert state.w==size and state.isOneFrame==expected
            assert live.property('narrow')==expected
            assert live.property('half')==size/2
        assert not state.isOneFrame and len(state.storage)==6
        settle(220);assert state.w==917
        # Single-axis resize preserves native notification/storage behavior.
        for horizontal in (True,False):
            state.storage=[]
            for n in range(1,101):
                window.setWidth(1000+n) if horizontal else window.setHeight(900+n)
                app.processEvents()
            assert len(state.storage)==200,(horizontal,len(state.storage))
            settle(220);assert len(state.storage)==200
    window.setWidth(1111);window.setHeight(777)
    app.processEvents()
    window.showMaximized();settle(220)
    phases=[]
    def capture(label):
        assert (state.w,state.h)==(window.width(),window.height()),(label,state.w,state.h,window.width(),window.height())
        phases.append((label,window.width(),window.height(),int(window.visibility().value),window.property('lastVisibleVisibility')))
    capture('maximized')
    window.showMinimized();settle(20);capture('minimized')
    window.showNormal();settle(220);capture('normal-restored')
    assert (window.width(),window.height())==(1111,777),(window.width(),window.height())
    window.setWidth(1199);window.setHeight(799);app.processEvents();window.hide();capture('final-hide')
    settle(220);capture('hidden-settled')
    window.showNormal();settle(220)
    window.setWidth(1222);window.setHeight(822);app.processEvents();assert window.close();capture('final-close')
    settle(220);capture('closed-settled')
    records['synchronous' if changed else 'baseline']=phases
    live.deleteLater();window.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    component.deleteLater();live_component.deleteLater();engine.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
assert records['baseline']==records['synchronous'],records
print(json.dumps({'resize':'pass','liveChecks':layout_checks,'storageWrites':write_counts,'visibilityAndRestore':'match'}))
'''
        base = Path(r"D:\ValMAX\analysis\test-temp")
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="resize-", dir=base) as directory:
            environment = dict(os.environ)
            for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP", "QML_DISK_CACHE_PATH"):
                path = Path(directory) / key
                path.mkdir()
                environment[key] = str(path)
            environment.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1")
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", program,
                                     json.dumps(template), json.dumps(layout)],
                                    cwd=Path(__file__).parent, env=environment,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr[-3500:])
            value = json.loads(result.stdout)
            self.assertEqual(value["storageWrites"], {"baseline": 400, "synchronous": 400})
            self.assertEqual(value["liveChecks"], 1200)
            self.assertEqual(value["visibilityAndRestore"], "match")
            self.assertNotIn("ReferenceError", result.stderr)
            self.assertNotIn("TypeError", result.stderr)


if __name__ == "__main__":
    unittest.main()
