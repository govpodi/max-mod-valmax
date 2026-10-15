"""Compile and render only the privacy panel using Qt stubs and synthetic data.

Never starts MAX, imports its DLLs, opens a real profile, or accesses its server.
The test HTTP endpoint binds an ephemeral localhost port and serves test JSON.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import threading


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--panel", type=Path, default=Path(__file__).with_name("ui_privacy_panel.qml"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "QML_DISK_CACHE_PATH"):
        target = args.output / "synthetic-profile" / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(target.resolve())
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QUICK_BACKEND"] = "software"
    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
    os.environ["QML_DISABLE_DISK_CACHE"] = "1"
    font_dir = args.output / "synthetic-fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    for font_name in ("arial.ttf", "arialbd.ttf"):
        shutil.copyfile(Path(r"C:\Windows\Fonts") / font_name, font_dir / font_name)
    os.environ["QT_QPA_FONTDIR"] = str(font_dir.resolve())
    from PySide6.QtCore import QEventLoop, QMetaObject, QObject, Property, QTimer, Qt, QtMsgType, QUrl, Slot, qInstallMessageHandler, qVersion
    from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine, qmlRegisterSingletonInstance
    from PySide6.QtQuick import QQuickItem

    current = {"schema": 1, "verified": True,
               "rules": [{"id": "tracer_main", "title": "Отчёты о сбоях приложения", "state": "enabled", "statusText": "Отключены", "detail": "Tracer в приложении отключён патчами."},
                         {"id": "tracer_service", "title": "Отчёты о сбоях звонков", "state": "enabled", "statusText": "Отключены", "detail": "Отключены отправка отчётов и сбор данных SDK."},
                         {"id": "statistics", "title": "Статистика интерфейса", "state": "enabled", "statusText": "Отключена", "detail": "Отключено создание сессии и отправка очереди."},
                         {"id": "logs", "title": "Данные в логах", "state": "enabled", "statusText": "Маскируются", "detail": "Маскировка включена в приложении и службе."},
                         {"id": "unconfirmed", "title": "Логи в отчётах", "state": "unknown", "statusText": "Не подтверждено", "detail": "Тест состояния, которое не подтверждено."}],
               "observation": {"active": True, "seconds": 142, "blockedCalls": 3,
                               "processes": 2, "tracerHooks": 15, "transportHooks": 3,
                               "coverage": "Tracer службы звонков, исходящие заголовки приложения и подключения. Поля разбираются только для инициализации сессии."},
               "dashboard": {"observedPackets": 47, "observedBytes": 18324, "requestTypes": 3, "requestTypesComplete": True,
                             "externalDestinations": 2, "localDestinations": 1, "parsedPackets": 2, "headerOnlyPackets": 45,
                             "excludedPackets": 0, "excludedBytes": 0},
               "connections": [{"host": "198.51.100.24", "port": 443, "count": 2, "scope": "external", "title": "Внешний узел"},
                               {"host": "2001:db8:1234:5678:90ab:cdef:1234:5678", "port": 443, "count": 1, "scope": "external", "title": "Внешний узел"},
                               {"host": "127.0.0.1", "port": 40803, "count": 3, "scope": "local", "title": "Локальное соединение"}],
               "requestGroups": [{"opcode": 6, "title": "Инициализация сессии", "category": "Сессия", "count": 2, "bytes": 1850, "minBytes": 900, "maxBytes": 950,
                                  "fields": ["userAgent", "timezone", "deviceName"], "fieldTitles": ["Сведения о клиенте", "Часовой пояс", "Название устройства"],
                                  "bodyState": "parsed", "detail": "Распознаны только названия полей. Значения не сохраняются."},
                                 {"opcode": 19, "title": "Авторизация", "category": "Аккаунт", "count": 1, "bytes": 200, "minBytes": 200, "maxBytes": 200,
                                  "fields": [], "fieldTitles": [], "bodyState": "header_only", "detail": "Тип запроса подтверждён; содержимое не изучалось. Видны только тип и размер."},
                                 {"opcode": 65000, "title": "Запрос №65000", "category": "Назначение не подтверждено", "count": 44, "bytes": 16274, "minBytes": 40, "maxBytes": 800,
                                  "fields": [], "fieldTitles": [], "bodyState": "header_only", "detail": "Назначение запроса не подтверждено. Содержимое не изучалось."}],
               "requests": [],
               "tracerCalls": [{"function": "tracer_upload_async", "title": "Попытка отправки отчёта о сбое", "count": 3}],
               "notes": ["Синтетические данные: MAX не запущен.", "Счётчик Tracer показывает обращения к отключённому SDK, не сетевые блокировки."],
               "errors": {}}
    baseline = copy.deepcopy(current)
    request_count = [0]
    stall = [False]
    release_stalled = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            request_count[0] += 1
            if stall[0]:
                release_stalled.wait(12)
            content = json.dumps(current, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    source_panel = args.panel.read_text(encoding="utf-8")
    panel = source_panel.replace("http://127.0.0.1:18973/status", f"http://127.0.0.1:{server.server_port}/status")
    # Production uses these existing MAX singletons. The fixture provides only
    # their referenced properties, never loads a DLL from MAX. Numeric spacing,
    # palette and font samples are illustrative; production resolves native
    # values. The 640px cap is independently confirmed in WindowState's ctor.
    class FontUtil(QObject):
        @Property("QVariantMap", constant=True)
        def Styles(self):
            return {"Body": 0, "BodyStrong": 1, "Description": 2, "DescriptionStrong": 3, "Label": 4, "LabelStrong": 5}
        @Property("QVariantMap", constant=True)
        def Sizes(self):
            return {"Default": 0}
        @Slot(int, int, result=QFont)
        def getFont(self, style, _size):
            font = QFont("Arial")
            font.setPixelSize(16 if style < 2 else 14)
            font.setWeight(QFont.Weight.DemiBold if style % 2 else QFont.Weight.Normal)
            return font
        @Slot(int, int, result=float)
        def getLineHeight(self, style, _size):
            return 22.0 if style < 2 else 20.0
    class DesignSystem(QObject):
        @Property("QVariantMap", constant=True)
        def SpacingCommon(self):
            return {"_Zero": 0, "_2XS": 4, "_XS": 6, "_S": 8, "_M": 12, "_L": 16, "_2XL": 24}
        @Property("QVariantMap", constant=True)
        def BorderRadiusCommon(self):
            return {"_S": 4, "_Mhalf": 6, "_L": 12}
        @Property("QVariantMap", constant=True)
        def SizeBase(self):
            return {"px32": 32, "px48": 48, "px56": 56, "px64": 64}
    source = '''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import UiKitModule as UiKit
import TypographyModule as Typography
ApplicationWindow {
    id: root
    width: 650
    height: 1100
    visible: true
    property bool testDark: false
    color: paletteTokens.colorBackgroundSurface
    signal backClicked()
    property var testedPage: null
    QtObject {
        id: windowState
        readonly property real sidePanelsMaximumWidth: 640
        readonly property real separatorsWidth: 1
    }
    QtObject {
        id: paletteTokens
        readonly property color colorTextPrimary: root.testDark ? "#edf0f2" : "#0c0d0e"
        readonly property color colorTextSecondary: root.testDark ? "#a7b0ba" : "#5e6269"
        readonly property color colorTextTertiary: root.testDark ? "#a7b0ba" : "#7a7f85"
        readonly property color colorTextThemed: root.testDark ? "#b98cff" : "#8a42ff"
        readonly property color colorTextPositive: root.testDark ? "#66d6a0" : "#168456"
        readonly property color colorTextAttention: root.testDark ? "#f2c773" : "#9c6813"
        readonly property color colorBackgroundSurface: root.testDark ? "#17212a" : "#edeef2"
        readonly property color colorBackgroundPrimary: root.testDark ? "#1e2933" : "#ffffff"
        readonly property color colorBackgroundCard: root.testDark ? "#222e39" : "#ffffff"
        readonly property color colorBackgroundTertiary: root.testDark ? "#111a22" : "#e2e4e9"
        readonly property color colorDividerPrimary: root.testDark ? "#34404c" : "#e2e4e8"
        readonly property color colorDividerSecondary: root.testDark ? "#34404c" : "#e2e4e8"
        readonly property color colorButtonGhost: "transparent"
    }
    component SettingsBaseItem: Control {
        id: settingsBase
        property string title
        signal backClicked()
        property var maxwellColorTokens: paletteTokens
        property color backgroundColor: maxwellColorTokens.colorBackgroundSurface
        property real contentTopPadding: 0
        property alias contentLoaderItem: loader.sourceComponent
        leftInset: -windowState.separatorsWidth
        background: Rectangle { objectName: "nativePageSurface"; color: settingsBase.backgroundColor }
        contentItem: ColumnLayout {
            spacing: UiKit.DesignSystem.SpacingCommon._Zero
            Control {
                Layout.fillWidth: true
                implicitHeight: UiKit.DesignSystem.SizeBase.px64
                background: Rectangle { color: paletteTokens.colorBackgroundPrimary }
                contentItem: Label {
                    text: settingsBase.title
                    color: paletteTokens.colorTextPrimary
                    font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.BodyStrong, Typography.FontUtil.Sizes.Default)
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: paletteTokens.colorDividerSecondary }
            }
            Loader {
                id: loader
                objectName: "nativeSettingsLoader"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.leftMargin: UiKit.DesignSystem.SpacingCommon._2XL
                Layout.rightMargin: Layout.leftMargin
                Layout.topMargin: settingsBase.contentTopPadding
                Layout.maximumWidth: windowState.sidePanelsMaximumWidth
                Layout.alignment: Qt.AlignHCenter | Qt.AlignTop
            }
        }
    }
''' + panel + '''
    Component.onCompleted: testedPage = settingsModPrivacy.createObject(root.contentItem)
    Binding { target: root.testedPage; property: "width"; value: root.width; when: root.testedPage !== null }
    Binding { target: root.testedPage; property: "height"; value: root.height; when: root.testedPage !== null }
}
'''
    qml_file = args.output / "SyntheticPrivacyPanel.qml"
    qml_file.write_text(source, encoding="utf-8")
    warnings = []
    def qt_message(message_type, _context, message):
        if message_type in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            warnings.append(message)
    previous_message_handler = qInstallMessageHandler(qt_message)
    app = QGuiApplication(sys.argv[:1])
    font_id = QFontDatabase.addApplicationFont(str(font_dir / "arial.ttf"))
    if font_id < 0:
        raise RuntimeError("Cannot load the system Arial font for the offscreen renderer")
    app.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0], 10))
    font_util, design_system = FontUtil(), DesignSystem()
    qmlRegisterSingletonInstance(FontUtil, "TypographyModule", 1, 0, "FontUtil", font_util)
    qmlRegisterSingletonInstance(DesignSystem, "UiKitModule", 1, 0, "DesignSystem", design_system)
    engine = QQmlEngine()
    engine.warnings.connect(lambda entries: warnings.extend(item.toString() for item in entries))
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(qml_file.resolve())))
    if component.isError():
        raise RuntimeError("QML compile failed: " + "\n".join(item.toString() for item in component.errors()))
    window = component.create()
    if window is None:
        raise RuntimeError("QML creation failed: " + "\n".join(item.toString() for item in component.errors()))
    def settle(milliseconds=2400):
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()
    def texts():
        values = []
        def visit(item):
            if not item.isVisible():
                return
            value = item.property("text")
            if value is not None:
                values.append(str(value))
            for child in item.childItems():
                visit(child)
        visit(window.contentItem())
        return values
    def require(fragment):
        if not any(fragment in value for value in texts()):
            print(json.dumps({"texts": texts(), "warnings": warnings}, ensure_ascii=False, indent=2))
            window.grabWindow().save(str(args.output / "failed-panel.png"))
            raise AssertionError("Missing expected label: " + fragment)
    checks = []
    screenshots = []
    settle(600)
    page = window.property("testedPage")
    def named(name):
        pending = [window.contentItem()]
        while pending:
            item = pending.pop()
            if item.objectName() == name:
                return item
            pending.extend(item.childItems())
        raise AssertionError("Missing QML object: " + name)
    def screenshot(name):
        path = args.output / (name + ".png")
        if not window.grabWindow().save(str(path)):
            raise RuntimeError("Cannot save synthetic panel screenshot")
        screenshots.append(str(path.resolve()))
    def click(name):
        if not QMetaObject.invokeMethod(named(name), "clicked", Qt.ConnectionType.DirectConnection):
            raise AssertionError("Cannot activate button: " + name)
        settle(100)
    def refresh():
        if not QMetaObject.invokeMethod(page, "refreshStatus", Qt.ConnectionType.DirectConnection):
            raise AssertionError("Cannot refresh synthetic report")
        settle(180)
    refresh_timer = next(item for item in page.children() if item.objectName() == "privacyRefreshTimer")
    refresh_timer.setProperty("running", False)
    for expected in ("Приватность ValMAX", "Наблюдение работает", "Меньше служебных данных", "Не подтверждено", "3", "2 мин 22 с наблюдения"):
        require(expected)
    assert named("nativeSettingsLoader").width() == 602
    assert named("privacyScroll").property("leftPadding") == 0
    checks.append("category-based Russian privacy view, no raw request or connection list")
    for width in (390, 650, 1200):
        window.setWidth(width)
        for dark in (False, True):
            window.setProperty("testDark", dark); settle(180)
            assert named("nativeSettingsLoader").width() == min(width - 48, 640)
            screenshot("privacy-" + ("dark" if dark else "light") + "-" + str(width))
    checks.append("native theme tokens and bounded settings layout at three widths")
    current["observation"]["blockedCalls"] = 0; refresh()
    assert named("tracerCardValue").property("text") == "0"
    current["observation"].update(active=False, blockedCalls=None); refresh()
    assert named("tracerCardValue").property("text") == "—"
    require("Нет активного наблюдения")
    checks.append("observed zero differs from unavailable observation")
    current["schema"] = 999; refresh(); require("Нет данных. Запусти MAX Mod с диагностикой.")
    current.clear(); current.update(copy.deepcopy(baseline)); stall[0] = True
    QMetaObject.invokeMethod(page, "refreshStatus", Qt.ConnectionType.DirectConnection)
    settle(7600); require("Диагностика не ответила")
    assert page.property("pendingRequest") is None
    release_stalled.set(); settle(180); assert page.property("report") is None
    stall[0] = False; refresh(); require("Наблюдение работает")
    checks.append("invalid schema, watchdog and late response are rejected; fresh request recovers")
    server.shutdown()
    server.server_close()
    window.close()
    del window
    qInstallMessageHandler(previous_message_handler)
    result = {"qt_version": qVersion(), "panel_source_sha256": hashlib.sha256(args.panel.read_bytes()).hexdigest(),
              "render_text_sha256": hashlib.sha256(source_panel.encode()).hexdigest(),
              "checks": checks, "warnings": warnings, "synthetic_requests": request_count[0],
              "screenshots": screenshots, "max_started": False, "actual_profile_accessed": False,
              "layout_fixture": {"header_height": 64, "side_panels_maximum_width": 640, "horizontal_margin_token": "SpacingCommon._2XL", "widths": [390, 650, 1200]},
              "limitations": ["SettingsBaseItem's header/Loader structure follows extracted MAX QML; 640px cap is confirmed statically. Native navigation and binary integration require separate validation.", "Synthetic FontUtil, palette and spacing samples exercise bindings without loading MAX DLLs. Production uses native singletons, so PNG font/palette details are illustrative."]}
    (args.output / "synthetic-test.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if warnings:
        raise RuntimeError("QML emitted warnings; inspect the report")


if __name__ == "__main__":
    main()
