"""Real Qt SVG cache reuse with bundled widgets and static graphics only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

import menu_icons_ui as target

BASE = Path("D:/CodexLabs/max-mod-26.35.1/analysis/ui/resource_qml/qt/qml")
OUTPUT = Path("D:/ValMAX/analysis/menu-icons-v1/prewarm")


class MenuIconUiTests(unittest.TestCase):
    def test_exact_window_anchor_preserves_existing_source_and_rejects_duplicates(self):
        source=(BASE/"OneMeUi/MeWindow.qml").read_text("utf8")
        transformed=target.transform_window(source)
        anchor="ApplicationWindow {\n    id: root\n"
        self.assertEqual(transformed.replace(anchor+"\n"+target.prewarm_source(),anchor,1),source)
        with self.assertRaises(ValueError):target.transform_window(transformed)
        with self.assertRaises(ValueError):target.transform_window(source.replace("id: root","id: other",1))
        with self.assertRaises(ValueError):target.transform_window(source.replace("import UiKitModule as UiKit\n",""))
        self.assertNotIn("popupType",transformed)

    def test_bounded_coverage_matches_native_contact_folder_colors_and_sizes(self):
        contact=(BASE/"OneMeUi/ContactMoreMenu.qml").read_text("utf8")
        folder=(BASE/"OneMeUi/FoldersSubMenu.qml").read_text("utf8")
        actual=set(re.findall(r'(?:iconSource|icon\.name): "(qrc:/icon_pack/[^\"]+)"',contact+folder))
        paths={item[0] for item in target.ICON_SPECS}
        self.assertTrue(actual.issubset(paths))
        self.assertIn("qrc:/valmax/icons/chat-export.svg",paths)
        self.assertEqual(len(target.ICON_SPECS),16)
        self.assertEqual(len(set(target.ICON_SPECS)),16)
        primary=[item for item in target.ICON_SPECS if item[2]==20]
        self.assertEqual(len(primary),14)
        self.assertEqual({item[0].rsplit('/',1)[1] for item in primary if item[1]=="colorIconNegative"},
                         {"block.svg","delete.svg","minus_round.svg"})
        menu=(BASE/"UiKitModule/MenuItem.qml").read_text("utf8")
        self.assertIn("readonly property int iconSize: DesignSystem.SizeBase.px20",menu)
        self.assertIn("asynchronous: false",menu)

    def test_hidden_native_svgs_ready_and_real_menu_reuses_cache_at_both_dpis(self):
        OUTPUT.mkdir(parents=True,exist_ok=True)
        for scale in (1,2):
            result=subprocess.run([sys.executable,"-B","-X","utf8",__file__,"--qml-smoke",str(scale)],
                                  capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr[-6000:])
            proof=json.loads(result.stdout)
            self.assertEqual(proof["warnings"],[])
            self.assertTrue(proof["hiddenImagesReady"])
            self.assertTrue(proof["matchingDpiSourceSize"])
            self.assertEqual(proof["menuAdditionalLoadsLight"],0)
            self.assertEqual(proof["menuAdditionalLoadsDark"],0)
            self.assertEqual(proof["warmLoadsPerTheme"],[16,16])
            self.assertTrue(proof["allRealSvgsValidAndNontransparent"])


def qml_smoke(scale):
    import hashlib
    output=OUTPUT/("dpi-"+str(scale));output.mkdir(parents=True,exist_ok=True)
    fonts=output/"fonts";fonts.mkdir(exist_ok=True)
    for name in ("arial.ttf","arialbd.ttf"):
        shutil.copyfile(Path("C:/Windows/Fonts")/name,fonts/name)
    os.environ.update(TEMP=str(output),TMP=str(output),TMPDIR=str(output),QT_QPA_PLATFORM="offscreen",
                      QT_QUICK_BACKEND="software",QT_QUICK_CONTROLS_STYLE="Basic",QML_DISABLE_DISK_CACHE="1",
                      QT_QPA_FONTDIR=str(fonts),QT_SCALE_FACTOR=str(scale))
    from PySide6.QtCore import QObject,QUrl,qInstallMessageHandler
    from PySide6.QtGui import QGuiApplication,QImage,QPainter,QColor
    from PySide6.QtQml import QQmlEngine,QQmlComponent,QQmlExpression
    from PySide6.QtQuick import QQuickWindow,QQuickItem,QQuickImageProvider
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtTest import QTest
    from test_chat_lock_native_ui import prepare_native_fixture
    from icon_resources import native_payloads
    from valmax_icons import resource_payloads
    app=QGuiApplication([]);warnings=[]
    previous=qInstallMessageHandler(lambda _kind,_context,message:warnings.append(message))
    qml=prepare_native_fixture(output/"qml");ui=qml/"UiKitModule"
    for name in ("Action","ActionMenuItem","MenuItem","ScaledImage"):
        shutil.copyfile(BASE/"UiKitModule"/(name+".qml"),ui/(name+".qml"))
        with (ui/"qmldir").open("a",encoding="utf8") as stream:stream.write(name+" 1.0 "+name+".qml\n")
    custom=qml/"MeCustomText/Text.qml"
    custom.write_text(custom.read_text("utf8").replace("Text {",'''Text {
 property size previewSize
 property size avatarSize
 property size resourceImageSize
 property bool needSkipNonPrintable:false
 property var formatIntervals
 property string formattedText:text
''',1),"utf8")
    animoji=qml/"MeAnimojisModule";animoji.mkdir(exist_ok=True)
    (animoji/"qmldir").write_text("module MeAnimojisModule\nAnimojiForMeText 1.0 AnimojiForMeText.qml\n","utf8")
    (animoji/"AnimojiForMeText.qml").write_text("import QtQuick\nItem {property var commonDependency}\n","utf8")
    native_svg=BASE/"UiKitModule/SvgIcon.qml"
    assert (ui/"SvgIcon.qml").read_bytes()==native_svg.read_bytes()
    data=native_payloads(Path("D:/CodexLabs/max-mod-26.35.1/runtime/baseline/max.exe").read_bytes())
    data.update(resource_payloads())
    calls=[]
    class Provider(QQuickImageProvider):
        def __init__(self):super().__init__(QQuickImageProvider.Image)
        def requestImage(self,identifier,size,requested):
            color,separator,path=identifier.partition("/qrc:/")
            assert separator
            payload=data[":/"+path]
            renderer=QSvgRenderer(payload);assert renderer.isValid()
            assert QColor(color).alpha()>0
            image=QImage(requested.width(),requested.height(),QImage.Format_ARGB32_Premultiplied);image.fill(0)
            painter=QPainter(image);renderer.render(painter)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn);painter.fillRect(image.rect(),QColor(color));painter.end()
            assert any(image.pixelColor(x,y).alpha()>0 for y in range(image.height()) for x in range(image.width()))
            size.setWidth(image.width());size.setHeight(image.height())
            calls.append((identifier,image.width(),image.height()))
            return image
    engine=QQmlEngine();engine.addImportPath(str(qml));provider=Provider();engine.addImageProvider("svg_icon",provider)
    source='''import QtQuick
import QtQuick.Controls
import UiKitModule as UiKit
import ClientAppearanceModule as ClientAppearance
ApplicationWindow {
    id: root
    width:640;height:640;visible:true
    property bool showMenu:false
    readonly property var menuSpecs: MENUSPECS
    Column {
        Repeater {
            model:root.showMenu?root.menuSpecs:[]
            delegate: UiKit.ActionMenuItem {
                required property var modelData
                text:"Пункт меню"
                iconSource:modelData.source
                isAccent:modelData.colorKey==="colorIconNegative"
            }
        }
    }
    function darkColors() {
        ClientAppearance.AppearanceTokens.colorIconSecondary="#b4aebe";
        ClientAppearance.AppearanceTokens.colorIconNegative="#ff7689";
        ClientAppearance.AppearanceTokens.colorIconPrimary="#ece9f2";
    }
}'''.replace("MENUSPECS",json.dumps([{"source":source,"colorKey":key} for source,key,size in target.ICON_SPECS if size==20]))
    source=target.transform_window(source)
    component=QQmlComponent(engine);component.setData(source.encode(),QUrl.fromLocalFile(str(output/"Synthetic.qml")))
    host=component.create()
    if host is None:raise AssertionError([e.toString() for e in component.errors()])
    assert isinstance(host,QQuickWindow)
    def evaluate(code):
        call=QQmlExpression(QQmlEngine.contextForObject(host),host,code);value=call.evaluate()
        if call.hasError():raise AssertionError(call.error().toString())
        return value[0] if isinstance(value,tuple) else value
    QTest.qWait(30)
    warm=host.findChild(QQuickItem,"valmaxMenuIconPrewarm")
    assert warm is not None and not warm.isVisible() and not warm.isEnabled()
    svgs=[item for item in warm.childItems() if str(item.property("source") or "").startswith("qrc:/")]
    assert len(svgs)==16 and all(item.property("status")==1 for item in svgs),(len(svgs),[(item.property("source"),item.property("status")) for item in svgs],len(calls),warnings)
    assert len(calls)==16,calls
    ratio=evaluate("Screen.devicePixelRatio")
    assert ratio==scale,(ratio,scale)
    sizes={source:size for source,_key,size in target.ICON_SPECS}
    for svg in svgs:
        native_image=svg.property("contentItem").property("item")
        declared=native_image.property("sourceSize")
        expected=sizes[svg.property("source")]*scale
        assert declared.width()==declared.height()==expected,(declared,expected)
    for identifier,width,height in calls:
        url="qrc:/"+identifier.partition("/qrc:/")[2]
        # Qt applies image-provider DPR to the SDK's already scaled sourceSize.
        # Preserve this SDK behavior; cache-key equality is tested below.
        assert width==height==sizes[url]*scale*scale,(identifier,width,height,scale)
    light=len(calls);host.setProperty("showMenu",True);QTest.qWait(30)
    light_added=len(calls)-light;assert light_added==0,calls[light:]
    host.setProperty("showMenu",False);QTest.qWait(20)
    before_dark=len(calls);evaluate("darkColors()");QTest.qWait(30)
    dark_warm=len(calls)-before_dark;assert dark_warm==16
    dark=len(calls);host.setProperty("showMenu",True);QTest.qWait(30)
    dark_added=len(calls)-dark;assert dark_added==0,calls[dark:]
    assert all(item.property("status")==1 for item in svgs)
    assert not warnings,warnings
    proof={"actualNativeSvgQmlSha256":hashlib.sha256(native_svg.read_bytes()).hexdigest(),
           "cppTextAndSvgProviderSimulated":True,"maxClientStarted":False,"scale":scale,
           "hiddenImagesReady":True,"matchingDpiSourceSize":True,"warmLoadsPerTheme":[light,dark_warm],
           "declaredMainSourceSize":20*scale,"providerMainRequestedSize":20*scale*scale,
           "menuAdditionalLoadsLight":light_added,"menuAdditionalLoadsDark":dark_added,
           "allRealSvgsValidAndNontransparent":True,"gpuFirstPaintNotMeasured":True,"warnings":warnings}
    (output/"proof.json").write_text(json.dumps(proof,indent=2)+"\n","utf8")
    host.close();host.deleteLater();app.processEvents();qInstallMessageHandler(previous)
    print(json.dumps(proof))


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--qml-smoke":qml_smoke(int(sys.argv[2]))
    else:unittest.main()
