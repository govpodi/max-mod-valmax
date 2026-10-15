"""Pinned QML transform and isolated synthetic contact UI checks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import profile_details_ui as target


BASELINE_QML = Path(r"D:\CodexLabs\max-mod-26.35.1\analysis\ui\resource_qml\qt\qml\OneMeUi\SidebarContactBrief.qml")


class ProfileTransformTests(unittest.TestCase):
    def source(self):
        if not BASELINE_QML.is_file():
            self.skipTest("Immutable extracted contact QML fixture absent")
        return BASELINE_QML.read_text(encoding="utf8")

    def test_actual_native_source_adds_details_and_returned_user_link(self):
        result = target.transform(self.source())
        self.assertEqual(result.count(target.MARKER), 1)
        self.assertIn("text: root.isBot ? contactLink.get().link : valmaxProfileDetails.publicLink", result)
        self.assertEqual(result.count("visible: root.isBot && link.active"), 2)
        self.assertIn("root.chatIsBlocked || valmaxProfileDetails.visible", result)
        self.assertIn("registrationDateTime", result)
        self.assertIn("onemeDisplayName", result)
        self.assertIn("textFormat: Text.PlainText", result)

    def test_foreign_or_already_transformed_sources_are_rejected(self):
        original = self.source()
        for value in ("", None, original.replace('text: root.isBot ? contactLink.get().link : ""', "text: untrusted"),
                      original.replace("visible: link.active", "visible: true", 1),
                      target.transform(original)):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    target.transform(value)

    def test_native_refresh_and_bot_components_are_not_replaced(self):
        source = self.source()
        result = target.transform(source)
        self.assertIn('invitationLink: qsTranslate("Sidebar", "Use the bot by link: %1").arg(contactLink.get().link)', result)
        self.assertEqual(result.count("contactLink.get().link"), source.count("contactLink.get().link"))
        self.assertEqual(Path(target.__file__).with_name("profile_details_panel.qml").read_text(encoding="utf8").count("refreshContactInfo(contactId)"), 1)
        for forbidden in ("XMLHttpRequest", "Qt.openUrlExternally",
                          "createPhoneNumberContext(", "Image {", "WebView", "access_token"):
            self.assertNotIn(forbidden, Path(target.__file__).with_name("profile_details_panel.qml").read_text(encoding="utf8"))

    def test_synthetic_qml_binding_plain_text_limits_dates_and_contact_switch(self):
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "--qml-smoke"],
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(result.stdout)
        self.assertTrue(evidence["collapsedLazy"])
        self.assertTrue(evidence["contactSwitchCollapses"])
        self.assertTrue(evidence["unsafeLinkRejected"])
        self.assertTrue(evidence["untrustedTextPlain"])
        self.assertTrue(evidence["ownLinkExactIdOnly"])
        self.assertTrue(evidence["ownLinkNativeNotify"])
        self.assertTrue(evidence["accountModelChanges"])
        self.assertTrue(evidence["linkSourceOwnership"])
        self.assertTrue(evidence["nativeRefreshThrottled"])
        self.assertEqual(evidence["rows"], 25)
        self.assertEqual(evidence["warnings"], [])


def qml_smoke():
    scratch = Path(r"D:\ValMAX\audit\profile-ui")
    scratch.mkdir(parents=True, exist_ok=True)
    font_dir = scratch / "fonts"; font_dir.mkdir(exist_ok=True)
    for name in ("arial.ttf", "arialbd.ttf"):
        shutil.copyfile(Path(r"C:\Windows\Fonts") / name, font_dir / name)
    os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1",
                      QT_QUICK_CONTROLS_STYLE="Basic", QML_DISK_CACHE_PATH=str(scratch / "cache"), QT_QPA_FONTDIR=str(font_dir))
    from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, Signal, Slot, Qt, QUrl, qInstallMessageHandler
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression, qmlRegisterSingletonInstance
    from PySide6.QtQuick import QQuickItem, QQuickWindow
    from PySide6.QtTest import QTest

    app = QGuiApplication([])
    warnings = []
    qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
    with tempfile.TemporaryDirectory(prefix="synthetic-", dir=scratch) as directory:
        directory = Path(directory)
        ui = directory / "UiKitModule"; ui.mkdir()
        (ui / "qmldir").write_text("module UiKitModule\nMeCellAction 1.0 MeCellAction.qml\n", encoding="utf8")
        (ui / "MeCellAction.qml").write_text('''import QtQuick
import QtQuick.Controls
Button {
    property bool compact: false
    property var maxwellColorTokens
    property color backgroundColor
    property string captionText
    property bool captionUseLinkFormats: false
    property int captionLineCount: 1
    property string trailingElementText
    property bool trailingRightShevronVisible: false
    text: captionText + " · " + trailingElementText
}''', encoding="utf8")
        class DesignSystem(QObject):
            @Property("QVariantMap", constant=True)
            def SpacingCommon(self): return {"_Mhalf":16,"_Shalf":4}
        class FontUtil(QObject):
            @Property("QVariantMap", constant=True)
            def Styles(self): return {"Body":0,"ActionSmall":1}
            @Property("QVariantMap", constant=True)
            def Sizes(self): return {"Default":0}
            @Slot(int, int, result=QFont)
            def getFont(self, style, _size):
                font = QFont("Arial"); font.setPixelSize(16 if style == 0 else 13); return font
        design, fonts = DesignSystem(), FontUtil()
        qmlRegisterSingletonInstance(DesignSystem, "UiKitModule", 1, 0, "DesignSystem", design)
        qmlRegisterSingletonInstance(FontUtil, "TypographyModule", 1, 0, "FontUtil", fonts)
        class NativeInvites(QObject):
            changed = Signal()
            def __init__(self, short, long):
                super().__init__(); self.short, self.long = short, long
            @Property(str, notify=changed)
            def inviteShort(self): return self.short
            @Property(str, notify=changed)
            def inviteLink(self): return self.long
            def update(self, short, long):
                self.short, self.long = short, long; self.changed.emit()
        class NativeSelfInfo(QObject):
            def __init__(self, value): super().__init__(); self.value = value
            @Property(str, constant=True)
            def id(self): return self.value
        class NativeCommon(QObject):
            def __init__(self, value, invites):
                super().__init__(); self.self_info, self.invites = NativeSelfInfo(value), invites
            @Property(QObject, constant=True)
            def selfInfo(self): return self.self_info
            @Property(QObject, constant=True)
            def inviteLinkContext(self): return self.invites
        class NativeDependency(QObject):
            def __init__(self, common): super().__init__(); self.common = common
            @Property(QObject, constant=True)
            def commonDependency(self): return self.common
        class NativeAccounts(QAbstractListModel):
            def __init__(self, values): super().__init__(); self.values = values
            def roleNames(self): return {Qt.UserRole + 1:b"dependency"}
            def rowCount(self, parent=QModelIndex()): return 0 if parent.isValid() else len(self.values)
            def data(self, index, role):
                if index.isValid() and role == Qt.UserRole + 1: return self.values[index.row()]
                return None
            def append(self, dependency):
                at = len(self.values); self.beginInsertRows(QModelIndex(), at, at)
                self.values.append(dependency); self.endInsertRows()
            def remove_last(self):
                at = len(self.values) - 1; self.beginRemoveRows(QModelIndex(), at, at)
                self.values.pop(); self.endRemoveRows()
        unrelated = NativeDependency(NativeCommon("1", NativeInvites("https://max.ru/u/OtherOwn", "")))
        large_id = NativeDependency(NativeCommon("9007199254740993", NativeInvites("https://max.ru/u/ExactLarge", "")))
        target_invites = NativeInvites("https://max.ru/u/SecondOwn", "https://max.ru/u/SecondOwnLong")
        target_dep = NativeDependency(NativeCommon("42", target_invites))
        model = NativeAccounts([unrelated, large_id])
        class NativeApplication(QObject):
            @Property(QObject, constant=True)
            def accountModel(self): return model
        native_app = NativeApplication()
        template = Path(target.__file__).with_name("profile_details_panel.qml").read_text(encoding="utf8")
        source = '''import QtQuick
import QtQuick.Layouts
import UiKitModule as UiKit
import TypographyModule as Typography
ColumnLayout {
    id: root
    width: 480
    property string contactId: "42"
    property var maxwellColorTokens: ({colorBackgroundCard:"white",colorTextPrimary:"black",colorTextMute:"#666666"})
    property var contactLink: ({get: function() { return {link:"https://max.ru/u/TestOpaqueId_123456",nickWithAt:"@synthetic"}; }})
    property var contactPhone: ({get: function() { return {phone:""}; }})
    property var contactCountry: ({get: function() { return {country:""}; }})
    property var contactFlags: ({get: function() { return {isBot:false,isOfficial:true}; }})
    property int refreshCallCount: 0
    property string refreshContactId: ""
    property bool refreshThrows: false
    property var contactProperties: ({
        refreshContactInfo: function(id) {
            if (root.refreshThrows) throw new Error("synthetic failure");
            root.refreshCallCount += 1;
            root.refreshContactId = id;
        },
        createDisplayNameContext: function(id) { return {get: function() { return {firstName:"<img src=https://example.invalid/x>",lastName:"Тест",onemeDisplayName:"Серверное имя",displayText:"Локальное имя"}; }}; },
        createContactStatusContext: function(id) { return {get: function() { return {isBlocked:false,isAccountBlocked:false,isAccountRemoved:false}; }}; },
        createRegistrationTimeContext: function(id) { return {get: function() { return {registrationDateTime: new Date("2025-01-02T10:30:00Z")}; }}; },
        createContactOrganizationsContext: function(id) { return {get: function() { return {hasOrganizationId:true,organizationId:"123"}; }}; }
    })
    property string currentSelfId: "1"
    property var currentInvites: ({inviteShort:"https://max.ru/u/CurrentOwn",inviteLink:"https://max.ru/u/CurrentOwnLong"})
    property var commonDependency: ({contactProperties: root.contactProperties,selfInfo: {id:root.currentSelfId},inviteLinkContext:root.currentInvites})
''' + template + "\n}\n"
        class QmlUtils(QObject):
            @Slot(str, result=str)
            def contactIdString(self, value): return value
        utils = QmlUtils()
        engine = QQmlEngine(); engine.addImportPath(str(directory)); engine.rootContext().setContextProperty("QmlUtils", utils)
        engine.rootContext().setContextProperty("application", native_app)
        component = QQmlComponent(engine); component.setData(source.encode("utf8"), QUrl.fromLocalFile(str(directory / "Synthetic.qml")))
        instance = component.create()
        if instance is None: raise AssertionError([error.toString() for error in component.errors()])
        engine.rootContext().setContextProperty("syntheticRoot", instance)
        panel = instance.findChild(QQuickItem, "valmaxProfileDetails")
        def evaluate(expression):
            expr = QQmlExpression(engine.rootContext(), panel, expression)
            value = expr.evaluate()
            if expr.hasError(): raise AssertionError(expr.error().toString())
            if isinstance(value, tuple): value = value[0]
            if hasattr(value, "toVariant"): value = value.toVariant()
            return value
        assert evaluate("nameContext === null && statusContext === null && registrationContext === null && organizationsContext === null") is True
        assert panel.property("publicLink") == "https://max.ru/u/TestOpaqueId_123456"
        assert panel.property("publicLinkSource") == "server_contact"
        panel.setProperty("expanded", True); app.processEvents()
        assert evaluate("nameContext !== null && statusContext !== null && registrationContext !== null && organizationsContext !== null") is True
        rows = panel.property("rows").toVariant()
        assert len(rows) == 25, len(rows)
        assert rows[1]["value"] == "<img src=https://example.invalid/x>"
        assert rows[7]["value"] == "Не указано"
        assert rows[8]["value"] == "Не указано"
        assert rows[9]["value"] != "Неизвестно"
        for text in ("https://evil.invalid/u/x", "https://max.ru@evil.invalid/u/x", "http://max.ru/u/x", "https://max.ru/u/x\n", "https://max.ru/u/" + "x" * 600):
            assert evaluate("safePublicLink(" + json.dumps(text) + ")") == ""
        assert evaluate('boundedText("abc\\u202edef\\u0000ghi", 8)') == "abc def"
        assert len(evaluate('boundedText("x".repeat(10000), 99999)')) == 4096
        for expression in ('registrationDate(undefined)', 'registrationDate(null)', 'registrationDate("")', 'registrationDate("nonsense")', 'registrationDate(0)', 'registrationDate(true)', 'registrationDate(new Date("2099-01-01"))'):
            assert evaluate(expression) == "Неизвестно", expression
        assert evaluate("yesNo(undefined)") == "Неизвестно"
        assert evaluate("yesNo(false)") == "Нет"
        assert evaluate("yesNo(true)") == "Да"
        assert evaluate("displayValue({secret:'not rendered'})") == "Не указано"
        # A missing ordinary Contact.link must never reuse the current account's
        # URL. Only an exact native self ID match can use invitation settings.
        evaluate('syntheticRoot.contactLink = ({get:function(){return {link:"",nickWithAt:""};}})')
        app.processEvents(); assert panel.property("publicLink") == ""
        assert panel.property("publicLinkSource") == "unavailable"
        assert evaluate("refreshPublicLink()") is True
        assert instance.property("refreshCallCount") == 1
        assert instance.property("refreshContactId") == "42"
        assert evaluate("refreshPublicLink()") is False
        assert instance.property("refreshCallCount") == 1
        instance.setProperty("currentSelfId", "42"); app.processEvents()
        assert panel.property("publicLink") == "https://max.ru/u/CurrentOwn"
        assert panel.property("publicLinkSource") == "own_invitation"
        evaluate('syntheticRoot.currentInvites = ({inviteShort:"https://evil.invalid/",inviteLink:"https://max.ru/u/CurrentOwnLong"})')
        app.processEvents(); assert panel.property("publicLink") == "https://max.ru/u/CurrentOwnLong"
        instance.setProperty("currentSelfId", "1"); app.processEvents()
        assert panel.property("publicLink") == ""
        model.append(target_dep); app.processEvents()
        assert panel.property("publicLink") == "https://max.ru/u/SecondOwn"
        target_invites.update("https://max.ru/u/SecondChanged", "https://max.ru/u/SecondOwnLong")
        app.processEvents(); assert panel.property("publicLink") == "https://max.ru/u/SecondChanged"
        # Prefer an actual Contact.link over an account invitation fallback.
        evaluate('syntheticRoot.contactLink = ({get:function(){return {link:"https://max.ru/u/NativeContact",nickWithAt:""};}})')
        app.processEvents(); assert panel.property("publicLink") == "https://max.ru/u/NativeContact"
        assert panel.property("publicLinkSource") == "server_contact"
        evaluate('syntheticRoot.contactLink = ({get:function(){return {link:"",nickWithAt:""};}})')
        app.processEvents(); model.remove_last(); app.processEvents()
        assert panel.property("publicLink") == ""
        instance.setProperty("contactId", "9007199254740993"); app.processEvents()
        assert panel.property("publicLink") == "https://max.ru/u/ExactLarge"
        instance.setProperty("contactId", "9007199254740992"); app.processEvents()
        assert panel.property("publicLink") == ""
        for value in ("0", "-1", "", "undefined", "9223372036854775808"):
            assert evaluate("nativeIdKey(" + json.dumps(value) + ")") == ""
        instance.setProperty("contactId", "43"); app.processEvents()
        assert panel.property("expanded") is False
        assert panel.property("rows").toVariant() == []
        assert evaluate("refreshPublicLink()") is True
        assert instance.property("refreshCallCount") == 2
        assert instance.property("refreshContactId") == "43"
        instance.setProperty("contactId", "44"); app.processEvents()
        instance.setProperty("refreshThrows", True)
        assert evaluate("refreshPublicLink()") is False
        assert panel.property("linkRefreshUnavailable") is True
        instance.setProperty("contactId", "45"); app.processEvents()
        assert panel.property("linkRefreshUnavailable") is False
        panel.setProperty("expanded", True); app.processEvents()
        window = QQuickWindow()
        window.setColor("white")
        window.resize(480, 2200)
        instance.setParentItem(window.contentItem())
        window.show(); QTest.qWait(100)
        assert instance.implicitHeight() > 1000, instance.implicitHeight()
        instance.setHeight(instance.implicitHeight())
        window.resize(480, min(2200, int(instance.implicitHeight()) + 32))
        QTest.qWait(50)
        snapshot = window.grabWindow()
        assert not snapshot.isNull()
        assert snapshot.save(str(scratch / "profile-details.png"))
        window.close(); instance.setParentItem(None)
        assert warnings == [], warnings
        instance.deleteLater(); app.processEvents()
        del component, engine
    print(json.dumps({"collapsedLazy":True,"contactSwitchCollapses":True,"unsafeLinkRejected":True,
                      "untrustedTextPlain":True,"ownLinkExactIdOnly":True,"ownLinkNativeNotify":True,
                      "accountModelChanges":True,"linkSourceOwnership":True,"nativeRefreshThrottled":True,
                      "rows":len(rows),"warnings":warnings}, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1:] == ["--qml-smoke"]:
        qml_smoke()
    else:
        unittest.main()
