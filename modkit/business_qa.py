"""Offscreen internal business page QA with a fake, network-free WebView."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    for name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "QML_DISK_CACHE_PATH"):
        path = output / "synthetic-profile" / name.lower(); path.mkdir(parents=True, exist_ok=True); os.environ[name] = str(path)
    fonts = output / "fonts"; fonts.mkdir(exist_ok=True)
    for name in ("arial.ttf", "arialbd.ttf"): shutil.copyfile(Path("C:/Windows/Fonts") / name, fonts / name)
    os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_QUICK_CONTROLS_STYLE="Basic", QML_DISABLE_DISK_CACHE="1", QT_QPA_FONTDIR=str(fonts))
    from PySide6.QtCore import QObject, Property, Slot, QUrl, qInstallMessageHandler, qVersion
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtQml import QQmlEngine, QQmlComponent, QJSEngine, qmlRegisterSingletonInstance
    warnings = []
    qInstallMessageHandler(lambda _kind, _context, message: warnings.append(message))
    app = QGuiApplication([])
    class FontUtil(QObject):
        @Property("QVariantMap", constant=True)
        def Styles(self): return {"Description": 2}
        @Property("QVariantMap", constant=True)
        def Sizes(self): return {"Default": 0}
        @Slot(int, int, result=QFont)
        def getFont(self, _style, _size):
            font = QFont("Arial"); font.setPixelSize(14); return font
    class DesignSystem(QObject):
        @Property("QVariantMap", constant=True)
        def SpacingCommon(self): return {"_S": 8, "_M": 12}
        @Property("QVariantMap", constant=True)
        def BorderRadiusCommon(self): return {"_S": 4}
    font, design = FontUtil(), DesignSystem()
    qmlRegisterSingletonInstance(FontUtil, "TypographyModule", 1, 0, "FontUtil", font)
    qmlRegisterSingletonInstance(DesignSystem, "UiKitModule", 1, 0, "DesignSystem", design)
    web = output / "qml" / "QtWebView"; web.mkdir(parents=True, exist_ok=True)
    (web / "qmldir").write_text("module QtWebView\nWebView 1.0 WebView.qml\n", encoding="utf8")
    (web / "WebView.qml").write_text('''import QtQuick
import QtQuick.Controls
Item {
 id: fakeWeb
 property url url: ""
 property bool canGoBack: false
 property bool canGoForward: false
 property int navigationCount: 0
 property int reloadCount: 0
 property int stopCount: 0
 property int scriptCount: 0
 property bool cancelled: false
 property string lastScript: ""
 signal loadingChanged(var request)
 enum LoadStatus { LoadStartedStatus, LoadSucceededStatus, LoadFailedStatus }
 onUrlChanged: {
  if (!url.toString().length) return;
  navigationCount++; cancelled=false;
  Qt.callLater(function() {
   if (cancelled) return;
   loadingChanged({status: WebView.LoadStartedStatus});
   Qt.callLater(function() { if (!cancelled) loadingChanged({status: WebView.LoadSucceededStatus}); });
  });
 }
 function reload() { reloadCount++; loadingChanged({status: WebView.LoadSucceededStatus}); }
 function stop() { stopCount++; cancelled=true; }
 function goBack() { canGoForward=true; }
 function goForward() { canGoBack=true; }
 function runJavaScript(script, callback) { lastScript=script;scriptCount++;if(callback)callback(); }
 function failFixture() { loadingChanged({status: WebView.LoadFailedStatus}); }
 Rectangle { anchors.fill: parent; color: "#ffffff"; Label { anchors.centerIn: parent; text: "Синтетическая веб-страница\nСетевые обращения отключены"; horizontalAlignment: Text.AlignHCenter; color: "#5e6269"; font.pixelSize: 18 } }
}
''', encoding="utf8")
    source = '''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import UiKitModule as UiKit
import TypographyModule as Typography
ApplicationWindow {
 id: root
 width: 1000; height: 780; visible: true
 property alias page: pageLoader.item
 property bool dark: false
 signal backClicked()
 QtObject { id: windowState; property bool isOneFrame: false }
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
 component MockPageHeader: Control {
  property string title
  property bool backButtonVisible
  property var backButtonAction
  implicitHeight: 64
  contentItem: Label { text: parent.title; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: tokens.colorTextPrimary; font.pixelSize: 18 }
 }
 __PANEL__
 Loader { id: pageLoader; anchors.fill: parent; sourceComponent: settingsValMAXBusiness }
}
'''
    panel = Path(__file__).with_name("business_panel.qml").read_text(encoding="utf8").replace("ClientAppearance.AppearanceTokens", "tokens").replace("UiKit.SettingsPageHeader {", "MockPageHeader {")
    source = source.replace("__PANEL__", panel)
    engine = QQmlEngine(); engine.addImportPath(str(output / "qml")); component = QQmlComponent(engine)
    component.setData(source.encode("utf8"), QUrl.fromLocalFile(str(output / "fixture.qml")))
    def pump(predicate, timeout=7):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate(): return
            time.sleep(0.01)
        raise AssertionError("Synthetic business QA timed out")
    pump(lambda: component.status() != QQmlComponent.Status.Loading)
    assert not component.isError(), [error.toString() for error in component.errors()]
    window = component.create(); assert window is not None, [error.toString() for error in component.errors()]
    page = window.property("page")
    pump(lambda: page.property("businessWebView") is not None and page.property("businessStatus") == "")
    view = page.property("businessWebView")
    assert view.property("url").toString() == "https://business.max.ru/self/"
    assert view.property("navigationCount") == 1 and view.property("scriptCount") == 1
    view.setProperty("url", QUrl("https://id.business.max.ru/auth?code=synthetic-secret&state=fixture"))
    pump(lambda: view.property("scriptCount") == 2)
    assert page.property("businessHost") == "id.business.max.ru"
    assert "synthetic-secret" not in page.property("businessHost") + page.property("businessStatus")
    screenshots = []
    def snapshot(name):
        for _ in range(20): app.processEvents(); time.sleep(0.01)
        path = output / (name + ".png"); assert window.grabWindow().save(str(path)); screenshots.append(str(path))
    snapshot("business-workspace-light")
    view.failFixture(); assert page.property("businessHasError")
    assert "synthetic-secret" not in page.property("businessStatus")
    page.reloadBusiness(); assert view.property("reloadCount") == 1 and not page.property("businessHasError")
    view.setProperty("url", QUrl("file:///D:/synthetic-no-file.html"))
    pump(lambda: view.property("stopCount") > 0)
    assert page.property("businessHasError")
    page.openBusinessHome(); pump(lambda: view.property("url").toString() == "https://business.max.ru/self/" and not page.property("businessHasError"))
    window.setProperty("width", 420); window.setProperty("height", 850); window.setProperty("dark", True)
    snapshot("business-workspace-narrow")
    js = QJSEngine()
    def evaluate(code):
        result = js.evaluate(code); assert not result.isError(), result.toString(); return result
    evaluate('var assignments=[];var listeners={};var window={location:{href:"https://business.max.ru/self/",assign:function(url){assignments.push(url);}}};var document={addEventListener:function(name,callback){listeners[name]=callback;}};var URL=function(value,base){this.protocol=/^([a-z]+:)/i.test(value)?/^([a-z]+:)/i.exec(value)[1]:"https:";this.href=/^[a-z]+:/i.test(value)?value:"https://business.max.ru"+value;};')
    script = page.property("popupNavigationScript")
    evaluate(script); evaluate(script)
    evaluate('window.open("https://id.business.max.ru/auth");window.open("http://unsupported.example/");')
    assert evaluate("assignments.length===1 && assignments[0]==='https://id.business.max.ru/auth'").toBool()
    evaluate('var link={target:"_blank"};listeners.click({target:{closest:function(){return link;}}});var form={target:"popup"};listeners.submit({target:form});')
    assert evaluate('link.target==="_self" && form.target==="_self"').toBool()
    result = {"schema": 1, "qtVersion": qVersion(), "network": "fake WebView only; zero real requests", "groupsPassed": ["lazy-webview-created", "fixed-official-home", "oauth-host-only-display", "retry-after-load-error", "unsupported-scheme-stops", "same-tab-popup-navigation", "target-blank-and-forms-stay-in-tab"], "screenshots": screenshots, "warnings": warnings}
    (output / "business-qa.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    assert not warnings, warnings
    print(json.dumps(result, ensure_ascii=False)); window.close()


if __name__ == "__main__":
    main()
