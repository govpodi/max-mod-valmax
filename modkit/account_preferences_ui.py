"""Native local account labels, colour rings and work-hour notification guard.

Transforms decoded, version-pinned resources only. The shared RCC generator
must separately pin every URL and invalidate each changed AOT registration.
No native binary, account, notification setting or credential is touched here.

Notification provenance: MAX 26.35.1 SettingsDev.qml obtains the active
account's leadingPanelDependency.settings.notificationContext and reads/writes
notificationMuted. The scheduler uses the same per-account dependency from
the existing AccountsStackView delegate. Its timers remain alive outside the
settings page. It never creates an additional StackLayout page.
"""
from __future__ import annotations

from pathlib import Path

from quick_replies_ui import once

REQUIRED_RESOURCES = ("LeadingSettingsBar.qml", "AccountsStackView.qml", "SettingsAccountSelectorDelegate.qml", "ProfileInfoCell.qml")
OPTIONAL_RESOURCES = ("AccountButton.qml",)


VALIDATE_ROWS = r'''
function modValidateAccountPrefs(value) {
    if (!value || value.schema !== 1 || !/^[0-9a-f]{64}$/.test(value.revision) || !Array.isArray(value.accounts) || value.accounts.length > 32) return false;
    var colors = ["", "#0088ff", "#8b5cf6", "#22a06b", "#e58b17", "#dc5260", "#d35ca5", "#149ca1"];
    var seen = {};
    var timePattern = /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/;
    for (var i = 0; i < value.accounts.length; ++i) {
        var row = value.accounts[i]; var h = row ? row.workHours : null;
        if (!row || typeof row.accountId !== "string" || !/^[1-9][0-9]{0,18}$/.test(row.accountId) || row.accountId.length === 19 && row.accountId > "9223372036854775807" || seen[row.accountId] || typeof row.displayName !== "string" || row.displayName.replace(/[\ud800-\udbff][\udc00-\udfff]/g, "x").length > 64 || /[\u0000-\u001f\u007f-\u009f\u2028-\u202e\u2066-\u2069]/.test(row.displayName) || colors.indexOf(row.color) < 0 || !h || typeof h.enabled !== "boolean" || !Array.isArray(h.days) || h.days.length > 7 || typeof h.start !== "string" || typeof h.end !== "string" || !timePattern.test(h.start) || !timePattern.test(h.end) || h.start === h.end || h.enabled && !h.days.length) return false;
        seen[row.accountId] = true;
        var days = {};
        for (var j = 0; j < h.days.length; ++j) { var day = h.days[j]; if (typeof day !== "number" || Math.floor(day) !== day || day < 1 || day > 7 || days[day]) return false; days[day] = true; }
    }
    return true;
}
'''


def reader(*, interval=3000):
    return '''
property var modAccountPrefs: []
property string modAccountPrefsRevision: ""
property var modAccountPrefsRequest: null
function modAccountPrefFor(id) {
    for (var i = 0; i < modAccountPrefs.length; ++i) if (modAccountPrefs[i].accountId === id) return modAccountPrefs[i];
    return null;
}
''' + VALIDATE_ROWS + '''
function modRefreshAccountPrefs() {
    if (modAccountPrefsRequest !== null) return;
    var xhr = new XMLHttpRequest(); modAccountPrefsRequest = xhr; modAccountPrefsWatchdog.restart();
    xhr.onreadystatechange = function() {
        if (xhr.readyState !== XMLHttpRequest.DONE || modAccountPrefsRequest !== xhr) return;
        modAccountPrefsRequest = null; modAccountPrefsWatchdog.stop();
        if (xhr.status !== 200) return;
        try {
            var value = JSON.parse(xhr.responseText);
            if (!modValidateAccountPrefs(value)) return;
            if (value.revision !== modAccountPrefsRevision) { modAccountPrefs = value.accounts; modAccountPrefsRevision = value.revision; }
        } catch (ignored) {}
    };
    xhr.open("GET", "http://127.0.0.1:18973/account-settings"); xhr.send();
}
Timer {
    id: modAccountPrefsWatchdog; interval: 7000
    onTriggered: { var xhr = modAccountPrefsRequest; modAccountPrefsRequest = null; if (xhr !== null) { xhr.onreadystatechange = function() {}; xhr.abort(); } }
}
Timer { interval: ''' + str(interval) + '''; repeat: true; running: true; triggeredOnStart: true; onTriggered: modRefreshAccountPrefs() }
Component.onDestruction: { if (modAccountPrefsRequest !== null) { modAccountPrefsRequest.onreadystatechange = function() {}; modAccountPrefsRequest.abort(); } }
'''


SCHEDULE_FUNCTIONS = '''
function modScheduleIsQuiet(hours, now) {
    if (!hours || !hours.enabled) return false;
    var day = now.getDay() || 7; var minute = now.getHours() * 60 + now.getMinutes();
    var start = Number(hours.start.substring(0, 2)) * 60 + Number(hours.start.substring(3));
    var end = Number(hours.end.substring(0, 2)) * 60 + Number(hours.end.substring(3));
    if (start < end) return !(hours.days.indexOf(day) >= 0 && minute >= start && minute < end);
    var previous = day === 1 ? 7 : day - 1;
    return !(hours.days.indexOf(day) >= 0 && minute >= start || hours.days.indexOf(previous) >= 0 && minute < end);
}
function modReleaseScheduleMute() {
    if (!modScheduleOwned) return;
    try { if (modNotificationContext.notificationMuted === true) modNotificationContext.notificationMuted = false; } catch (ignored) {}
    modScheduleOwned = false;
}
function modApplyWorkHours() {
    var pref = root.modAccountPrefFor(modScheduleAccountId);
    var quiet = modScheduleIsQuiet(pref ? pref.workHours : null, new Date());
    try {
        // A user who unmutes during quiet hours owns that decision until the
        // next boundary. An account muted before the schedule stays muted.
        if (modScheduleOwned && modNotificationContext.notificationMuted !== true) { modScheduleOwned = false; modScheduleOverride = true; }
        if (quiet && modScheduleQuiet && modSchedulePreviousMuted === true && modNotificationContext.notificationMuted === false) modScheduleOverride = true;
        if (quiet !== modScheduleQuiet) {
            if (!quiet) modReleaseScheduleMute();
            modScheduleQuiet = quiet; modScheduleOverride = false;
        }
        if (quiet && !modScheduleOverride && !modScheduleOwned && modNotificationContext.notificationMuted === false) {
            modNotificationContext.notificationMuted = true;
            modScheduleOwned = modNotificationContext.notificationMuted === true;
        }
        modSchedulePreviousMuted = modNotificationContext.notificationMuted;
    } catch (ignored) { /* An account being removed may lose its context. */ }
}
'''


SCHEDULER = '''
            id: modAccountView
            readonly property string modScheduleAccountId: String(QmlUtils.contactIdString(dependency.commonDependency.selfInfo.id))
            readonly property var modNotificationContext: dependency.leadingPanelDependency.settings.notificationContext
            property bool modScheduleQuiet: false
            property bool modScheduleOwned: false
            property bool modScheduleOverride: false
            property var modSchedulePreviousMuted: undefined
''' + SCHEDULE_FUNCTIONS + '''
            Timer { interval: 15000; repeat: true; running: true; triggeredOnStart: true; onTriggered: modAccountView.modApplyWorkHours() }
            Connections { target: root; function onModAccountPrefsChanged() { modAccountView.modApplyWorkHours(); } }
            Component.onDestruction: modReleaseScheduleMute()
'''


def _singleton_import(source):
    return source if "import QmlSingletonesModule\n" in source else once(source, "import QtQuick\n", "import QtQuick\nimport QmlSingletonesModule\n")


def _label_reader(source):
    source = _singleton_import(source)
    return once(source, "    required property var commonDependency\n", "    required property var commonDependency\n" + reader() + '''
readonly property string modLocalAccountId: String(QmlUtils.contactIdString(commonDependency.selfInfo.id))
readonly property var modLocalAccountPref: modAccountPrefFor(modLocalAccountId)
readonly property string modAccountAlias: modLocalAccountPref ? modLocalAccountPref.displayName : ""
readonly property string modAccountColor: modLocalAccountPref ? modLocalAccountPref.color : ""
''')


RING = '''
        Rectangle {
            anchors.fill: parent; anchors.margins: -3; radius: width / 2
            color: "transparent"; border.width: root.modAccountColor.length ? 2 : 0; border.color: root.modAccountColor || "transparent"
        }
'''


def transform_sources(sources, directory=None):
    """Preserve all input keys; require full lifetime scheduler and both labels."""
    if any(name not in sources for name in REQUIRED_RESOURCES):
        raise ValueError("Account preferences require all native resource URLs")
    if any("modAccountPrefs" in sources[name] or "settingsValMAXAccounts" in sources[name]
           for name in REQUIRED_RESOURCES):
        raise ValueError("Account preferences transform already applied")
    directory = Path(directory) if directory is not None else Path(__file__).parent
    result = dict(sources)
    leading = sources["LeadingSettingsBar.qml"].replace("\r\n", "\n")
    panel = (directory / "account_preferences_panel.qml").read_text(encoding="utf8")
    anchor = "    Component {\n        id: settingsDev\n"
    leading = once(leading, anchor, panel + "\n" + anchor)
    entry = '''                OpenPageSettingsCellImpl {
                    title: "Аккаунты ValMAX"
                    iconSource: "qrc:/valmax/icons/accounts.svg"
                    settingsPage: settingsValMAXAccounts
                }

'''
    anchor = "                UiKit.MeLoader {\n                    id: accountSelector\n"
    result["LeadingSettingsBar.qml"] = once(leading, anchor, entry + anchor)

    stack = _singleton_import(sources["AccountsStackView.qml"].replace("\r\n", "\n"))
    stack = once(stack, "    id: root\n", "    id: root\n" + reader(interval=5000))
    result["AccountsStackView.qml"] = once(stack, "        Ui.MessengerView {\n", "        Ui.MessengerView {\n" + SCHEDULER)

    selector = _label_reader(sources["SettingsAccountSelectorDelegate.qml"].replace("\r\n", "\n"))
    selector = once(selector, "    captionText: contactDisplayName.get().displayText", "    captionText: root.modAccountAlias || contactDisplayName.get().displayText")
    result["SettingsAccountSelectorDelegate.qml"] = once(selector, "        avatarSize: UiKit.DesignSystem.AvatarSize.px28\n", "        avatarSize: UiKit.DesignSystem.AvatarSize.px28\n" + RING)

    profile = _label_reader(sources["ProfileInfoCell.qml"].replace("\r\n", "\n"))
    profile = once(profile, "                text: root.commonDependency.selfInfo.name", "                text: root.modAccountAlias || root.commonDependency.selfInfo.name")
    result["ProfileInfoCell.qml"] = once(profile, "            avatarSize: root.avatarImageSize\n", "            avatarSize: root.avatarImageSize\n" + RING)

    if "AccountButton.qml" in sources:
        button = sources["AccountButton.qml"].replace("\r\n", "\n")
        if "modAccountPrefs" in button:
            raise ValueError("Account preferences transform already applied")
        button = once(button, "UiKit.MeAbstractButton {\n", "UiKit.MeAbstractButton {\n    id: root\n")
        button = _label_reader(button)
        button = once(button, "            text: contactDisplayName.get().displayText", "            text: root.modAccountAlias || contactDisplayName.get().displayText")
        result["AccountButton.qml"] = once(button, "            avatarSize: UiKit.DesignSystem.AvatarSize.px28\n", "            avatarSize: UiKit.DesignSystem.AvatarSize.px28\n" + RING)
    return result
