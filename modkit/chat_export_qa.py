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
    parser.add_argument("--native-beta", action="store_true", help="Render the native beta badge variant")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.drive.upper() != "E:": raise ValueError("Synthetic outputs belong on E")
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
        @Property(str, constant=True)
        def colorTextThemed(self): return "#ffffff"
        @Property(str, constant=True)
        def colorStatesBackgroundHighlighted(self): return "#7046e8"
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
    (ui / "qmldir").write_text("module UiKitModule\nBaseModalDialog 1.0 BaseModalDialog.qml\nMeText 1.0 MeText.qml\nMeHighlightedText 1.0 MeHighlightedText.qml\n", encoding="utf8")
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
    header: Item {
        implicitHeight: 38
        Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.verticalCenter: parent.verticalCenter;
            text: mockDialog.title; font.family: "Arial"; font.pixelSize: 14; font.bold: true }
    }
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
    (ui / "MeHighlightedText.qml").write_text('''import QtQuick
Rectangle {
 property string text: ""
 property bool highlightedTrailing: false
 property color textColor: "white"
 property color backgroundColor: "#7046e8"
 color: backgroundColor
 radius: 5
 implicitWidth: caption.implicitWidth + 14
 implicitHeight: caption.implicitHeight + 6
 Text { id: caption; anchors.centerIn: parent; text: parent.text; color: parent.textColor; font.family: "Arial"; font.pixelSize: 14 }
}
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
 QtObject { id: fakeDownloader; signal videoDownloadSucceed(string filePath); signal videoDownloadFailed() }
  property var fakeCommon: ({selfInfo: {id: "9007199254740995", accountId: "synthetic-account"}, mediaDownloader: fakeDownloader})
  property var fakeProperties: ({get: function() {return {chatId: "123", dialogContactId: "9007199254740997", isPreventCopyingEnabled: false}}})
  ExportModels.ListModel {
   id: fakeAccountModel
   property var currentAccountId: "synthetic-account"
   property var activeAccountDependency: ({commonDependency: host.fakeCommon})
  }
  property var application: ({accountModel: fakeAccountModel})
 ExportModels.DelegateModel { id: modelProbe; model: fixtureModel; delegate: QtObject {} }
 function modelProof() { return modelProbe.items.get(0).model.messageContext.get().messageId; }
 Component {
  id: callerComponent
  QtObject {
   function launch() {
     const scope={owner: host.fakeCommon, propertiesContext: host.fakeProperties, accountKey: "synthetic-account",
       ownId: "9007199254740995", nativeChatId: "123", chatId: "123",
       contactId: "9007199254740997", title: "Синтетический чат"};
     host.openValMAXChatExport({commonDependency: host.fakeCommon, nativeChatId: "123", nativeChatProperties: host.fakeProperties,
       chatTitle: "Синтетический чат", selectedScope: scope});
   }
  }
 }
 Component.onCompleted: { caller=callerComponent.createObject(host.contentItem); caller.launch(); }
 function destroyCaller() { caller.destroy(); caller=null; }
 function dialogProof() { return dialog.canonicalId("9007199254740997", "contact") + "|" + dialog.base64Chunk(new Uint8Array([0,1,2,255])); }
  function switchAccountProof() {
   const panel=dialog;
   fakeAccountModel.currentAccountId="another-synthetic-account";
   return panel && panel.scopeInvalidated;
  }
}'''
    source = source.replace(' property var dialog: valmaxChatExportLoader.item', WINDOW_HOST.replace('root.', 'host.').replace('qrc:/valmax/ChatExportPanel.qml', QUrl.fromLocalFile(str(output / 'ExportFactory.qml')).toString()) + '\n property var dialog: valmaxChatExportLoader.item', 1)
    panel_source = factory_source()
    if args.native_beta:
        from optimization_ui import transform_export_panel
        panel_source = transform_export_panel(panel_source)
    (output / "ExportFactory.qml").write_text(panel_source, encoding="utf8")
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
    scope_valid = expression("dialog.validSelectedScope()")
    api2_default_client = expression('dialog.exportMode === "client" && dialog.nativeApi2ExportAvailable === false')
    progress = {"schema": 1, "jobId": "a"*32, "state": "exporting",
                "counts": {"messages": 100, "attachments": 2, "ready": 0, "unavailable": 2, "mediaBytes": 0},
                "downloadsPending": False, "api2": {"windowsFetched": 1, "mediaProcessed": 0,
                    "mediaTotal": 0, "phase": "history", "done": False}}
    terminal = {**progress, "state": "partial", "api2": {"windowsFetched": 2,
                    "mediaProcessed": 0, "mediaTotal": 0, "phase": "done", "done": True},
                "htmlPath": "D:\\synthetic\\index.html", "htmlFileURL": "file:///D:/synthetic/index.html"}
    large_media_progress = {**progress,
        "counts": {**progress["counts"], "attachments": 20_001, "unavailable": 0},
        "api2": {**progress["api2"], "mediaTotal": 20_001}}
    over_limit_progress = {**large_media_progress,
        "counts": {**large_media_progress["counts"], "attachments": 1_000_001},
        "api2": {**large_media_progress["api2"], "mediaTotal": 1_000_001}}
    api2_receipt_validation = expression('(function() { const d=dialog; d.nativeApi2ExportAvailable=true; '
        'd.exportMode="api2-text"; d.jobId="' + "a"*32 + '"; d.jobToken="' + "b"*64 + '"; '
        'd.phase="api2-next"; return d.validApi2Receipt(' + json.dumps(progress) + ') '
        '&& d.validApi2Receipt(' + json.dumps(large_media_progress) + ') '
        '&& !d.validApi2Receipt(' + json.dumps(over_limit_progress) + ') '
        '&& !d.validApi2Receipt(' + json.dumps({**progress, "htmlFileURL": "file:///D:/synthetic/index.html"}) + ') '
        '&& !d.validApi2Receipt(' + json.dumps({**terminal, "api2": {"windowsFetched": 2,
            "mediaProcessed": 0, "mediaTotal": 0, "phase": "done", "done": False}}) + '); })()')
    stages = json.loads(expression('(function() { const d=dialog; d.exportMode="api2-text"; '
        'd.phase="api2-start"; const api2First=d.progressStageText.indexOf("Шаг 1/3") >= 0; '
        'd.phase="api2-next"; d.api2Stage="history"; const api2Second=d.progressStageText.indexOf("Шаг 2/3") >= 0 '
        '&& d.progressStageText.indexOf("История и вложения") >= 0; '
        'd.exportMode="client"; d.phase="seed"; const clientFirst=d.progressStageText.indexOf("Шаг 1/3") >= 0; '
        'd.phase="history"; const clientSecond=d.progressStageText.indexOf("Шаг 2/3") >= 0; '
        'd.phase="rendering"; const clientThird=d.progressStageText.indexOf("Шаг 3/3") >= 0; '
        'd.exportMode="api2-text"; d.phase="api2-next"; d.exportStartedAt=Date.now()-2000; d.elapsedSeconds=2; '
        'd.api2Pacing=true; d.api2PauseUntil=Date.now()+500; d.api2PauseRemainingMs=500; '
        'd.statusText="Загрузка истории выбранного чата…"; '
        'return JSON.stringify({api2:api2First&&api2Second,client:clientFirst&&clientSecond&&clientThird}); })()'))
    QTest.qWait(120)
    dialog = window.property("dialog")
    warning = dialog.findChild(QObject, "valmaxChatExportPanelBeta" if args.native_beta else "valmaxExportBetaWarning")
    native_warning = dialog.findChild(QObject, "valmaxExportBetaExplanation") if args.native_beta else None
    stage = dialog.findChild(QObject, "valmaxExportProgressStage")
    pace = dialog.findChild(QObject, "valmaxExportPace")
    beta_warning_visible = warning is not None and warning.property("visible") is True \
        and ((args.native_beta and warning.property("text") == "BETA" \
              and native_warning is not None and native_warning.property("visible") is True \
              and "неполным" in native_warning.property("text") \
              and "после включения функции" in native_warning.property("text") \
              and "Возможны повторы и пропуски" in native_warning.property("text")) \
             or (not args.native_beta and "Бета" in warning.property("text") \
                 and "неполным" in warning.property("text") \
                 and "после включения функции" in warning.property("text") \
                 and "Возможны повторы и пропуски" in warning.property("text")))
    warning_text = native_warning if args.native_beta else warning
    api2_warning_scoped = warning_text is not None and \
        "Отдельный раздел содержит только входящие сообщения" in warning_text.property("text")
    expression('dialog.exportMode="client"')
    QTest.qWait(20)
    client_warning_scoped = warning_text is not None and \
        "Локально сохранённые удаления в этот архив не входят" in warning_text.property("text") \
        and "Отдельный раздел содержит" not in warning_text.property("text")
    expression('dialog.exportMode="api2-text"')
    QTest.qWait(20)
    progress_visible = stages["api2"] is True and stages["client"] is True \
        and stage is not None and stage.property("visible") is True \
        and "Шаг 2/3" in stage.property("text") and "История и вложения" in stage.property("text") \
        and "2 с" in stage.property("text")
    pace_visible = pace is not None and pace.property("visible") is True \
        and "Пауза" in pace.property("text")
    busy_geometry = expression('JSON.stringify({dialogY:dialog.y,dialogHeight:dialog.height,'
        'headerY:dialog.header.y,headerHeight:dialog.header.height,'
        'contentY:dialog.contentItem.y,contentHeight:dialog.contentItem.height,'
        'itemY:dialog.contentItem.item.y,itemHeight:dialog.contentItem.item.height,'
        'itemImplicitHeight:dialog.contentItem.item.implicitHeight})')
    progress_screenshot = output / "synthetic-chat-export-progress.png"
    window.grabWindow().save(str(progress_screenshot))
    expression('(function() { const d=dialog; d.api2Pacing=false; d.queueApi2Window(); '
        'd.pendingRequest={syntheticHold:true}; return true; })()')
    QTest.qWait(575)
    pace_timer_releases = expression('dialog.api2Pacing === false '
        '&& dialog.api2PauseRemainingMs === 0 && dialog.phase === "api2-next"')
    expression('dialog.pendingRequest=null')
    cancel_pace_started = expression('dialog.queueApi2Window(); dialog.api2Pacing === true')
    cancel_pace_stopped = expression('dialog.stopApi2Export("Экспорт остановлен.", false); '
        'dialog.api2Pacing === false && dialog.api2PauseRemainingMs === 0 '
        '&& dialog.phase === "cancelling"')
    QTest.qWait(575)
    cancel_pace_no_request = expression('dialog.phase === "cancelling" '
        '&& dialog.pendingRequest === null && dialog.api2Pacing === false')
    expression('dialog.jobId="' + "a"*32 + '"; dialog.jobToken="' + "b"*64 + '"; '
        'dialog.cancelRequested=false; dialog.api2StopPending=false; dialog.phase="api2-next"; '
        'dialog.api2WindowsFetched=0')
    api2_terminal_path_gated = expression('(function() { const d=dialog; d.applyApi2Receipt('
        + json.dumps(terminal) + '); return d.phase === "partial" '
        '&& d.htmlFileURL === "file:///D:/synthetic/index.html" '
        '&& d.messageCount === 100 && d.api2WindowsFetched === 2 '
        '&& d.statusText.toLowerCase().indexOf("полнота истории не подтверждена") >= 0; })()')
    interleaved = {"schema": 1, "jobId": "a"*32, "state": "exporting",
                   "counts": {"messages": 1, "attachments": 1, "ready": 0,
                              "unavailable": 1, "mediaBytes": 0},
                   "downloadsPending": False,
                   "api2": {"windowsFetched": 1, "mediaProcessed": 1,
                            "mediaTotal": 1, "phase": "history", "done": False}}
    interleaved_media_status = expression('(function() { const d=dialog; '
        'd.jobId="' + "a"*32 + '"; d.jobToken="' + "b"*64 + '"; d.phase="api2-next"; '
        'd.api2WindowsFetched=1; d.api2MediaProcessed=0; d.api2MediaTotal=1; d.api2Stage="history"; '
        'const value=' + json.dumps(interleaved) + '; '
        'if (!d.validApi2Receipt(value)) return false; d.applyApi2Receipt(value); '
        'return d.api2Stage === "history" && d.api2MediaProcessed === 1 '
        '&& d.statusText.indexOf("обработано 1 из 1") >= 0 && d.api2Pacing === true; })()')
    try:
        bar = window.property("dialog").findChild(QObject, "valmaxExportMediaProgressBar")
        bar_state = {"found": bar is not None,
                     "visible": bar.property("visible") if bar is not None else None,
                     "indeterminate": bar.property("indeterminate") if bar is not None else None,
                     "value": bar.property("value") if bar is not None else None}
        interleaved_media_bar = bar_state == {"found": True, "visible": True,
                                               "indeterminate": True, "value": 1}
    except RuntimeError:
        bar_state = {"found": False}
        interleaved_media_bar = False
    expression("destroyCaller()")
    QTest.qWait(50)
    later = expression("dialogProof()")
    screenshot = output / "synthetic-chat-export.png"
    window.grabWindow().save(str(screenshot))
    scope_invalidated = expression("switchAccountProof()")
    report = {"synthetic": True, "nativeBetaVariant": args.native_beta,
              "nativeClientStarted": False, "qtVersion": qVersion(),
              "delegateRoleAccess": model_proof == "9007199254740993",
              "creationSurvivesCallerDestruction": first == later == "9007199254740997|AAEC/w==",
               "selectedScopeValid": scope_valid is True,
               "api2DefaultKeepsClientMode": api2_default_client is True,
               "api2ReceiptRejectsPrematurePath": api2_receipt_validation is True,
               "interleavedMediaStatusVisible": interleaved_media_status is True,
               "interleavedMediaBarIndeterminate": interleaved_media_bar,
               "api2TerminalPathOnlyOnPartial": api2_terminal_path_gated is True,
               "betaWarningVisible": beta_warning_visible,
                "api2WarningMentionsLocalDeleted": api2_warning_scoped,
                "clientWarningOmitsLocalDeleted": client_warning_scoped,
               "progressStagesVisible": progress_visible,
               "api2TwoRealStages": stages["api2"] is True,
               "clientThreeStages": stages["client"] is True,
               "paceCountdownVisible": pace_visible,
               "paceTimerReleasesAfterDelay": pace_timer_releases is True,
               "cancelDuringPaceStops": cancel_pace_started is True and cancel_pace_stopped is True
                    and cancel_pace_no_request is True,
               "scopeInvalidatedOnAccountSwitch": scope_invalidated is True,
              "warnings": warnings, "busyGeometry": json.loads(busy_geometry),
              "screenshot": str(screenshot),
              "progressScreenshot": str(progress_screenshot)}
    (output / "qml-proof.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    print(json.dumps(report, ensure_ascii=False))
    window.close(); window.deleteLater(); app.processEvents(); qInstallMessageHandler(previous)
    faulthandler.cancel_dump_traceback_later()
    return 0 if all(report[key] for key in ("delegateRoleAccess", "creationSurvivesCallerDestruction",
        "selectedScopeValid", "api2DefaultKeepsClientMode", "api2ReceiptRejectsPrematurePath",
        "interleavedMediaStatusVisible", "interleavedMediaBarIndeterminate",
        "api2TerminalPathOnlyOnPartial", "betaWarningVisible",
        "api2WarningMentionsLocalDeleted", "clientWarningOmitsLocalDeleted", "progressStagesVisible",
        "api2TwoRealStages", "clientThreeStages",
        "paceCountdownVisible", "paceTimerReleasesAfterDelay", "cancelDuringPaceStops",
        "scopeInvalidatedOnAccountSwitch")) and not warnings else 1


if __name__ == "__main__": raise SystemExit(main())
