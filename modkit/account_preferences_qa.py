"""Offscreen synthetic account settings, HTTP and scheduler QA.

Never starts MAX, reads a real account or changes native notification settings.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "QML_DISK_CACHE_PATH"):
        destination = output / "synthetic-profile" / name.lower()
        destination.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(destination)
    os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_QUICK_CONTROLS_STYLE="Basic", QML_DISABLE_DISK_CACHE="1")
    fonts = output / "fonts"
    fonts.mkdir(exist_ok=True)
    for name in ("arial.ttf", "arialbd.ttf"):
        shutil.copyfile(Path("C:/Windows/Fonts") / name, fonts / name)
    os.environ["QT_QPA_FONTDIR"] = str(fonts)
    from PySide6.QtCore import QObject, Property, Slot, QUrl, qInstallMessageHandler, qVersion
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtQml import QQmlEngine, QQmlComponent, QJSEngine, qmlRegisterSingletonInstance
    from PySide6.QtQuick import QQuickWindow
    from account_preferences import AccountPreferencesStore, AccountPreferencesError, defaults, is_work_time
    from account_preferences_ui import SCHEDULE_FUNCTIONS, VALIDATE_ROWS

    warnings = []
    def message(_kind, _context, text):
        warnings.append(text)
    qInstallMessageHandler(message)
    app = QGuiApplication([])

    class FontUtil(QObject):
        @Property("QVariantMap", constant=True)
        def Styles(self): return {"Body": 0, "BodyStrong": 1, "Description": 2, "DescriptionStrong": 3}
        @Property("QVariantMap", constant=True)
        def Sizes(self): return {"Default": 0}
        @Slot(int, int, result=QFont)
        def getFont(self, style, _size):
            font = QFont("Arial"); font.setPixelSize(16 if style < 2 else 14)
            font.setWeight(QFont.Weight.DemiBold if style % 2 else QFont.Weight.Normal)
            return font
        @Slot(int, int, result=float)
        def getLineHeight(self, style, _size): return 22.0 if style < 2 else 20.0

    class DesignSystem(QObject):
        @Property("QVariantMap", constant=True)
        def SpacingCommon(self): return {"_Zero": 0, "_2XS": 4, "_S": 8, "_M": 12, "_2XL": 24}
        @Property("QVariantMap", constant=True)
        def BorderRadiusCommon(self): return {"_S": 4, "_L": 12}
        @Property("QVariantMap", constant=True)
        def SizeBase(self): return {"px6": 6, "px48": 48}

    class QmlUtils(QObject):
        @Slot("QVariant", result=str)
        def contactIdString(self, value): return str(value)

    font_util, design_system, qml_utils = FontUtil(), DesignSystem(), QmlUtils()
    qmlRegisterSingletonInstance(FontUtil, "TypographyModule", 1, 0, "FontUtil", font_util)
    qmlRegisterSingletonInstance(DesignSystem, "UiKitModule", 1, 0, "DesignSystem", design_system)
    qmlRegisterSingletonInstance(QmlUtils, "QmlSingletonesModule", 1, 0, "QmlUtils", qml_utils)
    from panel_scroll_qa import register_native_scroll
    register_native_scroll(output)
    stub = output / "qml" / "UiKitModule"
    stub.mkdir(parents=True, exist_ok=True)
    (stub / "qmldir").write_text("module UiKitModule\nMeSwitcher 1.0 MeSwitcher.qml\n", encoding="utf8")
    (stub / "MeSwitcher.qml").write_text("import QtQuick\nimport QtQuick.Controls\nSwitch {}\n", encoding="utf8")

    store = AccountPreferencesStore(output / "synthetic-store" / "account-settings.json")
    rows = [defaults("42"), defaults("77")]
    rows[0].update(displayName="Личный", color="#0088ff")
    rows[1].update(displayName="Работа", color="#22a06b")
    store.replace(store.snapshot()["revision"], rows)
    requests, flags = [], {"conflict": False}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append({"method": "GET", "path": self.path})
            self.reply(200, store.snapshot())
        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"method": "POST", "path": self.path, "write": self.headers.get("X-Max-Mod-Local-Write"), "origin": self.headers.get("Origin"), "keys": sorted(value)})
            if flags["conflict"]:
                self.reply(409, {"schema": 1, "error": "revision_conflict"}); return
            try:
                self.reply(200, store.replace(value["revision"], value["accounts"]))
            except AccountPreferencesError as error:
                self.reply(400, {"schema": 1, "error": error.code})
        def reply(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode("utf8")
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def log_message(self, *_args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()

    source = '''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import UiKitModule as UiKit
import TypographyModule as Typography
import QmlSingletonesModule
ApplicationWindow {
 id: root
 width: 650; height: 1050; visible: true
 property bool dark: false
 readonly property var selfInfo: selfOne
 property alias page: settingsLoader.item
 signal backClicked()
 QtObject {
  id: tokens
  readonly property color colorTextPrimary: root.dark ? "#edf0f2" : "#0c0d0e"
  readonly property color colorTextSecondary: root.dark ? "#a7b0ba" : "#5e6269"
  readonly property color colorTextTertiary: root.dark ? "#a7b0ba" : "#7a7f85"
  readonly property color colorTextThemed: root.dark ? "#b98cff" : "#8a42ff"
  readonly property color colorTextAttention: root.dark ? "#f2c773" : "#9c6813"
  readonly property color colorBackgroundSurface: root.dark ? "#17212a" : "#edeef2"
  readonly property color colorBackgroundCard: root.dark ? "#222e39" : "#ffffff"
  readonly property color colorDividerPrimary: root.dark ? "#34404c" : "#e2e4e8"
 }
 QtObject { id: selfOne; property string id: "42"; property string name: "Тестовый личный аккаунт" }
 QtObject { id: selfTwo; property string id: "77"; property string name: "Тестовый рабочий аккаунт" }
 QtObject { id: commonOne; readonly property var selfInfo: selfOne }
 QtObject { id: commonTwo; readonly property var selfInfo: selfTwo }
 QtObject { id: dependencyOne; readonly property var commonDependency: commonOne }
 QtObject { id: dependencyTwo; readonly property var commonDependency: commonTwo }
 ListModel { id: accountModel; dynamicRoles: true }
 QtObject { id: application; readonly property var accountModel: accountModel }
 component SettingsBaseItem: Control {
  id: base
  property string title
  property var maxwellColorTokens: tokens
  property alias contentLoaderItem: contentLoader.sourceComponent
  signal backClicked()
  background: Rectangle { color: tokens.colorBackgroundSurface }
  contentItem: ColumnLayout {
   Label { Layout.fillWidth: true; Layout.preferredHeight: 64; text: base.title; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: tokens.colorTextPrimary; font.pixelSize: 18 }
   Loader { id: contentLoader; Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: 24; Layout.rightMargin: 24 }
  }
 }
 Component.onCompleted: { accountModel.append({accountId: "42", dependency: dependencyOne}); accountModel.append({accountId: "77", dependency: dependencyTwo}); }
 __PANEL__
 Loader { id: settingsLoader; anchors.fill: parent; sourceComponent: settingsValMAXAccounts }
 function editFixture() { page.editorName = "ИП Тест"; page.editorColor = "#8b5cf6"; page.editorWorkEnabled = true; page.editorDays = [1, 2, 3, 4, 5]; page.editorStart = "22:00"; page.editorEnd = "06:00"; page.dirty = true; page.save(); }
 function selectSecond() { page.selectAccount("77", selfTwo.name); }
 function conflictFixture() { page.editorName = "Несохранённая правка"; page.dirty = true; page.save(); }
}
'''
    panel = Path(__file__).with_name("account_preferences_panel.qml").read_text(encoding="utf8")
    source = source.replace("__PANEL__", panel).replace("http://127.0.0.1:18973/account-settings", f"http://127.0.0.1:{server.server_port}/account-settings")
    engine = QQmlEngine(); engine.addImportPath(str(output / "qml"))
    component = QQmlComponent(engine)
    component.setData(source.encode("utf8"), QUrl.fromLocalFile(str(output / "fixture.qml")))
    def pump(predicate, timeout=7):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate(): return
            time.sleep(0.01)
        raise AssertionError("Synthetic QA timed out")
    pump(lambda: component.status() != QQmlComponent.Status.Loading)
    assert not component.isError(), [error.toString() for error in component.errors()]
    window = component.create()
    assert window is not None, [error.toString() for error in component.errors()]
    page = window.property("page")
    pump(lambda: page.property("loaded"))
    assert page.property("editorName") == "Личный"
    screenshots = []
    def snapshot(name):
        for _ in range(20): app.processEvents(); time.sleep(0.01)
        path = output / (name + ".png")
        assert window.grabWindow().save(str(path)); screenshots.append(str(path))
    snapshot("account-preferences-light")
    window.editFixture(); pump(lambda: page.property("pendingRequest") is None and not page.property("dirty"))
    first = store.snapshot()["accounts"][0]
    assert first["displayName"] == "ИП Тест" and first["workHours"]["start"] == "22:00"
    assert requests[-1]["write"] == "account-settings-v1" and requests[-1]["origin"] is None
    assert requests[-1]["keys"] == ["accounts", "revision", "schema"]
    window.selectSecond(); assert page.property("selectedAccountId") == "77" and page.property("editorName") == "Работа"
    flags["conflict"] = True; window.conflictFixture()
    pump(lambda: page.property("pendingRequest") is None and page.property("hasError"))
    assert page.property("editorName") == "Несохранённая правка" and page.property("dirty")
    assert store.snapshot()["accounts"][1]["displayName"] == "Работа"
    flags["conflict"] = False
    window.setProperty("dark", True); snapshot("account-preferences-dark")
    window.setProperty("width", 420); window.setProperty("height", 900); snapshot("account-preferences-narrow")

    js = QJSEngine()
    def evaluate(code):
        value = js.evaluate(code)
        assert not value.isError(), value.toString()
        return value
    evaluate(VALIDATE_ROWS + SCHEDULE_FUNCTIONS)
    evaluate('var NativeDate = Date; var fakeNow = new NativeDate(2026, 9, 12, 20, 0); Date = function() { return fakeNow; }; var pref = {workHours:{enabled:true,days:[1,2,3,4,5],start:"09:00",end:"18:00"}}; var root = {modAccountPrefFor:function(id){return pref;}}; var modScheduleAccountId="42"; var modNotificationContext={notificationMuted:false}; var modScheduleQuiet=false; var modScheduleOwned=false; var modScheduleOverride=false; var modSchedulePreviousMuted;')
    evaluate("modApplyWorkHours()")
    assert evaluate("modNotificationContext.notificationMuted && modScheduleOwned").toBool()
    evaluate("modNotificationContext.notificationMuted=false;modApplyWorkHours();modApplyWorkHours()")
    assert evaluate("!modNotificationContext.notificationMuted && !modScheduleOwned && modScheduleOverride").toBool()
    evaluate("fakeNow=new NativeDate(2026,9,13,10,0);modApplyWorkHours();fakeNow=new NativeDate(2026,9,13,20,0);modApplyWorkHours()")
    assert evaluate("modNotificationContext.notificationMuted && modScheduleOwned && !modScheduleOverride").toBool()
    evaluate("pref.workHours.enabled=false;modApplyWorkHours()")
    assert evaluate("!modNotificationContext.notificationMuted && !modScheduleOwned").toBool()
    evaluate("modNotificationContext.notificationMuted=true;pref.workHours.enabled=true;modApplyWorkHours();fakeNow=new NativeDate(2026,9,14,10,0);modApplyWorkHours()")
    assert evaluate("modNotificationContext.notificationMuted && !modScheduleOwned").toBool()
    evaluate("fakeNow=new NativeDate(2026,9,14,20,0);modApplyWorkHours();modNotificationContext.notificationMuted=false;modApplyWorkHours();modApplyWorkHours()")
    assert evaluate("!modNotificationContext.notificationMuted && !modScheduleOwned && modScheduleOverride").toBool()
    hours = {"enabled": True, "days": [7], "start": "22:00", "end": "06:00"}
    for stamp in ("2026-10-18T21:59", "2026-10-18T22:00", "2026-10-19T05:59", "2026-10-19T06:00"):
        now = datetime.fromisoformat(stamp)
        quiet = evaluate("modScheduleIsQuiet(" + json.dumps(hours) + f",new NativeDate({now.year},{now.month - 1},{now.day},{now.hour},{now.minute}))").toBool()
        assert quiet == (not is_work_time(hours, now))
    value = store.snapshot(); value["accounts"][0]["accountId"] = "9223372036854775808"
    assert not evaluate("modValidateAccountPrefs(" + json.dumps(value) + ")").toBool()
    assert evaluate("modValidateAccountPrefs(" + json.dumps(store.snapshot()) + ")").toBool()
    result = {"schema": 1, "qtVersion": qVersion(), "groupsPassed": ["native-page-load", "saved-local-preferences", "selected-account-switch", "revision-conflict-retains-draft", "schedule-manual-override", "schedule-owned-mute-release", "preexisting-mute-preserved", "overnight-reference-parity", "bounded-response-validation"], "screenshots": screenshots, "warnings": warnings}
    (output / "account-preferences-qa.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    assert not warnings, warnings
    print(json.dumps(result, ensure_ascii=False))
    window.close(); server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == "__main__":
    main()
