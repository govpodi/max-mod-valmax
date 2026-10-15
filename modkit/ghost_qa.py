"""Offscreen ghost page and HTTP/CAS QA. No MAX, accounts or message traffic."""
from __future__ import annotations

import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    for name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "QML_DISK_CACHE_PATH"):
        path = output / "synthetic-profile" / name.lower(); path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_QUICK_CONTROLS_STYLE="Basic", QML_DISABLE_DISK_CACHE="1")
    fonts = output / "fonts"; fonts.mkdir(exist_ok=True)
    for name in ("arial.ttf", "arialbd.ttf"):
        shutil.copyfile(Path("C:/Windows/Fonts") / name, fonts / name)
    os.environ["QT_QPA_FONTDIR"] = str(fonts)
    from PySide6.QtCore import QObject, Property, Slot, QMetaObject, QUrl, Qt, QtMsgType, Q_ARG, Q_RETURN_ARG, qInstallMessageHandler, qVersion
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtQml import QQmlEngine, QQmlComponent, qmlRegisterSingletonInstance
    from PySide6.QtQuick import QQuickWindow
    from ghost_store import GhostSettingsStore, GhostSettingsError
    warnings = []
    def message(kind, _context, text):
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg): warnings.append(text)
    previous = qInstallMessageHandler(message)
    app = QGuiApplication([])
    class FontUtil(QObject):
        @Property("QVariantMap", constant=True)
        def Styles(self): return {"Body": 0, "BodyStrong": 1, "Description": 2, "DescriptionStrong": 3}
        @Property("QVariantMap", constant=True)
        def Sizes(self): return {"Default": 0}
        @Slot(int, int, result=QFont)
        def getFont(self, style, _size):
            font = QFont("Arial"); font.setPixelSize(16 if style < 2 else 14); font.setWeight(QFont.Weight.DemiBold if style % 2 else QFont.Weight.Normal); return font
        @Slot(int, int, result=float)
        def getLineHeight(self, style, _size): return 22.0 if style < 2 else 20.0
    class DesignSystem(QObject):
        @Property("QVariantMap", constant=True)
        def SpacingCommon(self): return {"_S": 8, "_M": 12, "_2XL": 24}
        @Property("QVariantMap", constant=True)
        def BorderRadiusCommon(self): return {"_L": 12}
        @Property("QVariantMap", constant=True)
        def SizeBase(self): return {"px6": 6, "px48": 48}
    font_util, design_system = FontUtil(), DesignSystem()
    qmlRegisterSingletonInstance(FontUtil, "TypographyModule", 1, 0, "FontUtil", font_util)
    qmlRegisterSingletonInstance(DesignSystem, "UiKitModule", 1, 0, "DesignSystem", design_system)
    from panel_scroll_qa import register_native_scroll
    register_native_scroll(output)
    stub = output / "qml" / "UiKitModule"; stub.mkdir(parents=True, exist_ok=True)
    (stub / "qmldir").write_text("module UiKitModule\nMeSwitcher 1.0 MeSwitcher.qml\nMeCheckbox 1.0 MeCheckbox.qml\n", encoding="utf8")
    (stub / "MeSwitcher.qml").write_text("import QtQuick\nimport QtQuick.Controls\nSwitch {}\n", encoding="utf8")
    (stub / "MeCheckbox.qml").write_text("import QtQuick\nimport QtQuick.Controls\nCheckBox {}\n", encoding="utf8")
    store = GhostSettingsStore(output / "synthetic-store" / "ghost-settings.json")
    requests, flags = [], {"ready": True, "ack": True, "conflict": False, "invalid": False, "stall_get": False, "stall_post": False}
    release_get, release_post = threading.Event(), threading.Event()
    def response():
        value = store.snapshot(); settings = value["settings"]
        ready, applied = flags["ready"], flags["ready"] and flags["ack"]
        state = "active" if applied and settings["enabled"] and any(settings[key] for key in ("hideTyping", "hideRead", "hideOnline", "hideStories")) else "off" if applied else "pending" if ready else "unavailable"
        value["runtime"] = {"scope": "all_accounts", "state": state, "ready": ready, "applied": applied,
                            "features": {"hideTyping": ready, "hideRead": ready, "hideOnline": ready, "hideStories": ready},
                            "blocked": {"typing": 5, "read": 8, "presence": 3, "stories": 2}}
        if flags["invalid"]: value["runtime"]["applied"] = "true"
        return value
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append({"method": "GET", "path": self.path})
            value = response()
            if flags["stall_get"]: release_get.wait(12)
            self.reply(200, value)
        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"method": "POST", "path": self.path, "keys": sorted(value), "body": copy.deepcopy(value), "write": self.headers.get("X-Max-Mod-Local-Write"), "content_type": self.headers.get("Content-Type"), "origin": self.headers.get("Origin")})
            if flags["conflict"]:
                self.reply(409, {"schema": 1, "error": "revision_conflict"}); return
            try:
                store.replace(value["revision"], value["settings"]); flags["ack"] = False
            except GhostSettingsError as error:
                self.reply(409 if error.code == "revision_conflict" else 400, {"schema": 1, "error": error.code}); return
            result = response()
            if flags["stall_post"]: release_post.wait(15)
            self.reply(200, result)
        def reply(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode("utf8")
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError): pass
        def log_message(self, *_args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    source = '''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import UiKitModule as UiKit
import TypographyModule as Typography
ApplicationWindow {
 id: root
 width: 650; height: 900; visible: true
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
  readonly property color colorDividerPrimary: root.dark ? "#34404c" : "#e2e4e8"
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
 Loader { id: settingsLoader; anchors.fill: parent; sourceComponent: settingsValMAXGhost }
}
'''
    panel = Path(__file__).with_name("ghost_panel.qml").read_text(encoding="utf8")
    source = source.replace("__PANEL__", panel).replace("http://127.0.0.1:18973/ghost-settings", f"http://127.0.0.1:{server.server_port}/ghost-settings")
    (output / "SyntheticGhost.qml").write_text(source, encoding="utf8")
    engine = QQmlEngine(); engine.addImportPath(str(output / "qml"))
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(output / "SyntheticGhost.qml")))
    def pump(predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate(): return
            time.sleep(0.01)
        raise AssertionError("Synthetic ghost QA timed out")
    def settle(ms=120):
        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline: app.processEvents(); time.sleep(0.005)
    pump(lambda: component.status() != QQmlComponent.Status.Loading)
    assert not component.isError(), [e.toString() for e in component.errors()]
    window = component.create(); assert window is not None, [e.toString() for e in component.errors()]
    page = window.property("page")
    def named(name):
        if name in ("ghostPoll", "ghostWatchdog"):
            item = page.findChild(QObject, name)
            assert item is not None, name
            return item
        pending = [window.contentItem()]
        while pending:
            item = pending.pop()
            if item.objectName() == name: return item
            pending.extend(item.childItems())
        raise AssertionError("Missing " + name)
    def call(obj, name, *args):
        assert QMetaObject.invokeMethod(obj, name, Qt.DirectConnection, *[Q_ARG("QVariant", arg) for arg in args])
        settle()
    def refresh(quiet=False):
        pump(lambda: page.property("pendingRequest") is None)
        call(page, "request", "GET", "", quiet)
        pump(lambda: page.property("pendingRequest") is None)
    def toggle(name, value):
        item = named(name); assert item.isEnabled(); item.setProperty("checked", value); call(item, "toggled")
    checks, screenshots = [], []
    def snapshot(name):
        settle(250); path = output / (name + ".png"); assert window.grabWindow().save(str(path)); screenshots.append(str(path))
    pump(lambda: page.property("loaded")); named("ghostPoll").setProperty("running", False)
    assert not page.property("editorEnabled") and page.property("editorHideTyping") and page.property("editorHideRead")
    assert not page.property("editorHideOnline") and not page.property("editorHideStories")
    assert page.property("canConfigure") and not page.property("confirmedActive") and page.property("runtimeTitle") == "Режим выключен"
    assert not any(r["method"] == "POST" for r in requests)
    assert not store.path.exists()
    window.setWidth(390); snapshot("ghost-off-light-390")
    checks.append("default master off, both filters selected; verified runtime required; initial GET creates no preference file or POST")
    toggle("ghostEnabled", True); toggle("ghostHideTyping", False)
    assert page.property("dirty")
    call(page, "save"); pump(lambda: page.property("pendingRequest") is None)
    assert not page.property("dirty") and not page.property("confirmedActive") and page.property("runtimeTitle") == "Ожидание применения"
    post = [r for r in requests if r["method"] == "POST"][-1]
    assert post["keys"] == ["revision", "schema", "settings"] and post["write"] == "ghost-settings-v1" and post["origin"] is None and post["content_type"].split(";", 1)[0] == "application/json", post
    assert post["body"]["settings"] == {"enabled": True, "hideTyping": False, "hideRead": True, "hideOnline": False, "hideStories": False}
    assert len(post["body"]["revision"]) == 64
    snapshot("ghost-pending-light-390")
    flags["ack"] = True; refresh(True)
    assert page.property("confirmedActive") and page.property("runtimeTitle") == "Фильтры работают"
    window.setWidth(650); window.setProperty("dark", True); snapshot("ghost-active-dark-650")
    checks.append("explicit Save sends exact CAS body/marker; persistence alone stays pending; a later valid GET ACK activates status")
    toggle("ghostHideTyping", True)
    stale_revision = page.property("revision")
    store.replace(store.snapshot()["revision"], {"enabled": True, "hideTyping": False, "hideRead": False})
    assert store.snapshot()["revision"] != stale_revision
    call(page, "save"); pump(lambda: page.property("pendingRequest") is None)
    assert page.property("dirty") and page.property("needsRefresh") and page.property("editorHideTyping")
    refresh(True)
    assert page.property("dirty") and page.property("needsRefresh")
    before = len([r for r in requests if r["method"] == "POST"]); call(page, "save")
    assert len([r for r in requests if r["method"] == "POST"]) == before
    refresh(False)
    assert page.property("dirty") and not page.property("needsRefresh") and page.property("editorHideTyping")
    assert page.property("revision") == store.snapshot()["revision"]
    call(page, "save"); pump(lambda: page.property("pendingRequest") is None); assert not page.property("dirty")
    checks.append("409 and background refresh preserve draft; conflict requires explicit refresh before retry; exact CAS revision then saves")
    flags["ready"] = False; refresh()
    assert not page.property("canConfigure") and not page.property("confirmedActive")
    assert not named("ghostEnabled").isEnabled() and not named("ghostHideTyping").isEnabled() and not named("ghostHideRead").isEnabled() and not named("saveGhostSettings").isEnabled()
    before = len([r for r in requests if r["method"] == "POST"]); page.setProperty("editorEnabled", False); call(page, "save")
    assert len([r for r in requests if r["method"] == "POST"]) == before
    snapshot("ghost-unavailable-dark-650")
    checks.append("unavailable runtime disables both filters, master and Save; saved enabled preference cannot claim active protection")
    flags["ready"] = True; flags["ack"] = True; refresh(); call(page, "resetDraft")
    flags["invalid"] = True; refresh()
    assert page.property("hasError") and not page.property("runtimeTrusted") and not page.property("confirmedActive")
    flags["invalid"] = False; refresh(True); assert page.property("canConfigure") and not page.property("hasError")
    checks.append("invalid runtime confirmation fails closed; valid background refresh restores verified controls and removes stale transport warning")
    def validates(value):
        return QMetaObject.invokeMethod(page, "validResponse", Qt.DirectConnection, Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", value))
    valid = response(); assert validates(valid)
    cases = []
    for path, value in ((('runtime', 'scope'), 'current_account'), (('runtime', 'features', 'hideRead'), False),
                        (('runtime', 'applied'), 'true'), (('runtime', 'state'), 'off'),
                        (('runtime', 'blocked', 'read'), -1), (('runtime', 'blocked', 'typing'), 1000001),
                        (('runtime', 'blocked', 'presence'), -1), (('runtime', 'blocked', 'stories'), 1000001),
                        (('runtime', 'features', 'hideOnline'), 'true'), (('runtime', 'features', 'hideStories'), None),
                        (('settings', 'enabled'), 1), (('settings', 'hideTyping'), None), (('revision',), 'a' * 63)):
        sample = copy.deepcopy(valid); target = sample
        for key in path[:-1]: target = target[key]
        target[path[-1]] = value; cases.append(sample)
    assert all(not validates(sample) for sample in cases)
    checks.append("runtime parser rejects mismatched state, partial filters, wrong scope, non-boolean settings/ACK, invalid revision and unbounded counters")
    toggle("ghostEnabled", False); flags["stall_post"] = True; call(page, "save")
    assert page.property("saving") and named("ghostWatchdog").property("interval") == 10000 and not named("ghostEnabled").isEnabled()
    pump(lambda: page.property("pendingRequest") is None, timeout=11)
    assert page.property("needsRefresh") and not page.property("runtimeTrusted") and page.property("dirty") and "10 секунд" in page.property("statusText")
    status = page.property("statusText"); release_post.set(); flags["stall_post"] = False; settle(300)
    assert page.property("statusText") == status and not page.property("runtimeTrusted")
    refresh(); assert not page.property("needsRefresh") and not page.property("dirty") and not page.property("editorEnabled")
    checks.append("real 10-second POST watchdog preserves editor and invalidates confirmation; late write response ignored; explicit refresh discovers saved outcome")
    flags["ack"] = True; refresh()
    flags["stall_get"] = True; call(page, "request", "GET", "", False)
    assert named("ghostWatchdog").property("interval") == 7000
    pump(lambda: page.property("pendingRequest") is None, timeout=8)
    assert not page.property("runtimeTrusted") and "7 секунд" in page.property("statusText")
    status = page.property("statusText"); release_get.set(); flags["stall_get"] = False; settle(300)
    assert page.property("statusText") == status and not page.property("runtimeTrusted")
    refresh(); assert page.property("canConfigure")
    checks.append("real 7-second GET watchdog invalidates protection status; stale response cannot restore it; valid refresh recovers")
    # Exercise the actual periodic Timer without waiting for user interaction.
    named("ghostPoll").setProperty("running", True); before = len(requests)
    pump(lambda: len(requests) > before, timeout=3); named("ghostPoll").setProperty("running", False)
    pump(lambda: page.property("pendingRequest") is None)
    checks.append("native page background Timer refreshes every two seconds; no account IDs, message contents or auth data in the HTTP contract")
    assert all(r["path"] == "/ghost-settings" for r in requests)
    assert not warnings, warnings
    release_get.set(); release_post.set(); server.shutdown(); server.server_close(); window.close(); settle(30)
    qInstallMessageHandler(previous)
    result = {"qt_version": qVersion(), "checks": checks, "warnings": warnings, "screenshots": screenshots,
              "max_started": False, "real_profile_accessed": False, "messages_sent": False,
              "sources": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in ("ghost_panel.qml", "ghost_ui.py", "ghost_qa.py")},
              "limitations": ["SettingsBaseItem, native toggles and font tokens are synthetic fixtures. No native hook or server-side invisibility is proven by this UI QA.", "HTTP uses a temporary local GhostSettingsStore and simulated runtime confirmations; no client account or credential is read."]}
    (output / "ghost-qa.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
