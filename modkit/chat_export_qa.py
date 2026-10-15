"""Offscreen synthetic QML/lifetime/model proof; never starts MAX or reads a profile."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import faulthandler

from chat_export_ui import factory_source, IMPORTS, WINDOW_HOST


def main():
    faulthandler.dump_traceback_later(20, exit=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.drive.upper() != "D:": raise ValueError("Synthetic outputs belong on D")
    output.mkdir(parents=True, exist_ok=False)
    for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP", "QML_DISK_CACHE_PATH"):
        path = output / "profile" / name.lower(); path.mkdir(parents=True)
        os.environ[name] = str(path)
    fonts = output / "fonts"; fonts.mkdir()
    for name in ("arial.ttf", "arialbd.ttf"): shutil.copyfile(Path("C:/Windows/Fonts") / name, fonts / name)
    os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_QUICK_CONTROLS_STYLE="Basic", QML_DISABLE_DISK_CACHE="1", QT_QPA_FONTDIR=str(fonts))
    from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Signal, Slot, Qt, QDateTime, QUrl, qInstallMessageHandler, qVersion
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtQml import QQmlEngine, QQmlComponent, QQmlExpression, qmlRegisterSingletonInstance
    from PySide6.QtTest import QTest
    app = QGuiApplication([])
    warnings = []
    previous = qInstallMessageHandler(lambda _kind, _context, text: warnings.append(text))

    class Utils(QObject):
        @Slot("QVariant", result=str)
        def contactIdString(self, value): return str(value)
        @Slot("QVariant", result=str)
        def chatIdString(self, value): return str(value)
        @Slot("QVariant", result=str)
        def messageIdString(self, value): return str(value)
        @Slot("QVariant", result=QDateTime)
        def toDateTime(self, value): return QDateTime.fromMSecsSinceEpoch(int(value))
    class Design(QObject):
        @Property("QVariantMap", constant=True)
        def SpacingCommon(self): return {"_L": 16, "_M": 12}
    class Appearance(QObject):
        @Property(str, constant=True)
        def colorTextAttention(self): return "#a46a12"
        @Property(str, constant=True)
        def colorTextPrimary(self): return "#17212b"
        @Property(str, constant=True)
        def colorTextSecondary(self): return "#687889"
    class FontUtil(QObject):
        @Property("QVariantMap", constant=True)
        def Styles(self): return {"Body": 0, "Description": 1}
    class BubbleTypes(QObject):
        @Property("QVariantMap", constant=True)
        def Scope(self): return {"General": 0}
    class GridTypes(QObject):
        @Property("QVariantMap", constant=True)
        def AttachType(self): return {"Photo": 0, "Gif": 1, "Video": 2}
    class PhotoTypes(QObject):
        @Property(int, constant=True)
        def Photo(self): return 0
        @Property(int, constant=True)
        def Gif(self): return 1
    objects = [Utils(), Design(), Appearance(), FontUtil(), BubbleTypes(), GridTypes(), PhotoTypes()]
    for obj, module, name in zip(objects, ("QmlSingletonesModule", "UiKitModule", "ClientAppearanceModule", "TypographyModule", "ru.oneme.components", "ru.oneme.components", "ru.oneme.components"), ("QmlUtils", "DesignSystem", "AppearanceTokens", "FontUtil", "MessageBubbleTypes", "AttachmentsPreviewGrid", "MediaDownloaderPhotoType")):
        qmlRegisterSingletonInstance(type(obj), module, 1, 0, name, obj)

    qml = output / "qml"
    ui = qml / "UiKitModule"; ui.mkdir(parents=True)
    (ui / "qmldir").write_text("module UiKitModule\nBaseModalDialog 1.0 BaseModalDialog.qml\nMeText 1.0 MeText.qml\n", encoding="utf8")
    (ui / "BaseModalDialog.qml").write_text('''import QtQuick
import QtQuick.Controls
Dialog {
    id: mockDialog
    property var chatAppearance
    property real minimumWidth: 520
    property string subtitle
    property string confirmText
    property string cancelText
    property bool confirmButtonEnabled: true
    property Component dialogContentItem
    signal confirmClicked()
    signal cancelClicked()
    signal closeClicked()
    width: minimumWidth
    contentItem: Loader { sourceComponent: mockDialog.dialogContentItem }
    footer: DialogButtonBox {
        Button { text: mockDialog.confirmText; enabled: mockDialog.confirmButtonEnabled; onClicked: mockDialog.confirmClicked() }
        Button { text: mockDialog.cancelText; onClicked: mockDialog.cancelClicked() }
    }
}
''', encoding="utf8")
    (ui / "MeText.qml").write_text('''import QtQuick
Text { property int typography: 0; textFormat: Text.PlainText; font.family: "Arial"; font.pixelSize: typography === 0 ? 16 : 14 }
''', encoding="utf8")
    preview = qml / "PreviewGridModule"; preview.mkdir()
    (preview / "qmldir").write_text("module PreviewGridModule\nPreviewGridContext 1.0 PreviewGridContext.qml\n", encoding="utf8")
    (preview / "PreviewGridContext.qml").write_text("import QtQuick\nQtObject { property var previews: []; property var previewModel: []; property real panelWidth: 0; property real extraWidth: 0 }\n", encoding="utf8")

    class Message(QObject):
        @Property(str, constant=True)
        def messageId(self): return "9007199254740993"
    message = Message()
    class Wrapper(QObject):
        @Property(bool, constant=True)
        def isNull(self): return False
        @Slot(result=QObject)
        def get(self): return message
    wrapper = Wrapper()
    class Model(QAbstractListModel):
        def rowCount(self, parent=QModelIndex()): return 0 if parent.isValid() else 1
        def roleNames(self): return {Qt.ItemDataRole.UserRole+1: b"messageContext"}
        def data(self, index, role): return wrapper if index.isValid() and role == Qt.ItemDataRole.UserRole+1 else None
    model = Model()
    engine = QQmlEngine(); engine.addImportPath(str(qml))
    engine.rootContext().setContextProperty("fixtureModel", model)
    # All fixtures below are synthetic; no HTTP request is made by this proof.
    source = IMPORTS + '''ApplicationWindow {
 id: host
 width: 720; height: 500; visible: true
 property var dialog: valmaxChatExportLoader.item
 property var caller: null
 property var application: ({accountModel: []})
 QtObject { id: fakeDownloader; signal videoDownloadSucceed(string filePath); signal videoDownloadFailed() }
 property var fakeCommon: ({selfInfo: {id: "9007199254740995"}, mediaDownloader: fakeDownloader})
 ExportModels.DelegateModel { id: modelProbe; model: fixtureModel; delegate: QtObject {} }
 function modelProof() { return modelProbe.items.get(0).model.messageContext.get().messageId; }
 Component {
  id: callerComponent
  QtObject {
   function launch() {
    host.openValMAXChatExport({commonDependency: host.fakeCommon, nativeChatId: "123", nativeChatProperties: {get: function() {return {isPreventCopyingEnabled: false}}}, chatTitle: "Синтетический чат"});
   }
  }
 }
 Component.onCompleted: { caller=callerComponent.createObject(host.contentItem); caller.launch(); }
 function destroyCaller() { caller.destroy(); caller=null; }
 function dialogProof() { return dialog.canonicalId("9007199254740997", "contact") + "|" + dialog.base64Chunk(new Uint8Array([0,1,2,255])); }
}'''
    source = source.replace(' property var dialog: valmaxChatExportLoader.item', WINDOW_HOST.replace('root.', 'host.').replace('qrc:/valmax/ChatExportPanel.qml', QUrl.fromLocalFile(str(output / 'ExportFactory.qml')).toString()) + '\n property var dialog: valmaxChatExportLoader.item', 1)
    (output / "ExportFactory.qml").write_text(factory_source(), encoding="utf8")
    panel_component = QQmlComponent(engine, QUrl.fromLocalFile(str(output / "ExportFactory.qml")))
    if panel_component.isError(): raise ValueError("Synthetic panel compile: " + " | ".join(e.toString() for e in panel_component.errors()))
    (output / "SyntheticChatExport.qml").write_text(source, encoding="utf8")
    component = QQmlComponent(engine)
    component.setData(source.encode("utf8"), QUrl.fromLocalFile(str(output / "SyntheticChatExport.qml")))
    if component.isError(): raise ValueError("Synthetic QML compile: " + " | ".join(e.toString() for e in component.errors()))
    # A Python message callback must not be called by Qt's nested QML compiler
    # thread while createQmlObject holds the GIL. Fixtures contain no real data.
    qInstallMessageHandler(previous)
    window = component.create()
    qInstallMessageHandler(lambda _kind, _context, text: warnings.append(text))
    if window is None: raise ValueError("Synthetic QML creation failed")
    QTest.qWait(100)
    def expression(text):
        call = QQmlExpression(engine.rootContext(), window, text)
        value = call.evaluate()
        if call.hasError(): raise ValueError(call.error().toString())
        return value[0] if isinstance(value, tuple) else value
    model_proof = expression("modelProof()")
    if window.property("dialog") is None:
        raise ValueError("Synthetic export creation: " + " | ".join(warnings))
    first = expression("dialogProof()")
    expression("destroyCaller()")
    QTest.qWait(50)
    later = expression("dialogProof()")
    screenshot = output / "synthetic-chat-export.png"
    window.grabWindow().save(str(screenshot))
    report = {"synthetic": True, "nativeClientStarted": False, "qtVersion": qVersion(),
              "delegateRoleAccess": model_proof == "9007199254740993",
              "creationSurvivesCallerDestruction": first == later == "9007199254740997|AAEC/w==",
              "warnings": warnings, "screenshot": str(screenshot)}
    (output / "qml-proof.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    print(json.dumps(report, ensure_ascii=False))
    window.close(); window.deleteLater(); app.processEvents(); qInstallMessageHandler(previous)
    faulthandler.cancel_dump_traceback_later()
    return 0 if report["delegateRoleAccess"] and report["creationSurvivesCallerDestruction"] and not warnings else 1


if __name__ == "__main__": raise SystemExit(main())
