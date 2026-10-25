import QtQuick
import ValMAX.Native 1.0 as Native
Item {
    id: test
    property int checks: 0
    property string failure: ""
    property var label: null
    property var scheduler: null
    property var preserved: null
    readonly property string firstId: "9007199254740993"
    readonly property string secondId: "9223372036854775807"
    function check(value, message) {
        ++checks; if (!value) { failure = message; Qt.exit(1); throw new Error(message); }
    }
    function snapshot(revision, quiet) {
        const otherDay = ((new Date().getDay() || 7) + 2) % 7 + 1;
        return {schema: 1, revision: revision.repeat(64), accounts: [
            {accountId: firstId, displayName: "Алиса 😀 " + revision, color: "#8b5cf6",
             workHours: {enabled: false, days: [], start: "09:00", end: "18:00"}},
            {accountId: secondId, displayName: "Бизнес " + revision, color: "#22a06b",
             workHours: {enabled: quiet, days: [otherDay], start: "09:00", end: "18:00"}}
        ]};
    }
    function complete(reader, value) {
        const request = reader.modAccountPrefsRequest;
        check(request !== null && request.requestId.length > 0, "reader has native admission");
        Native.Bridge.operationFinished(request.requestId, JSON.stringify({schema: 1, ok: true, result: value}));
        check(reader.modAccountPrefsRequest === null, "terminal releases reader request");
    }
    function update(reader, value) { reader.modRefreshAccountPrefs(); complete(reader, value); }
    Component { id: labelType; AccountReader { commonDependency: ({selfInfo: {id: test.firstId}}) } }
    Component { id: scheduleType; ScheduleReader {} }
    Component.onCompleted: Qt.callLater(function() {
        label = labelType.createObject(test);
        scheduler = scheduleType.createObject(test);
        check(label !== null && scheduler !== null, "actual reader declarations compile on Qt");
        label.modRefreshAccountPrefs(); scheduler.modRefreshAccountPrefs();
        check(Native.Bridge.requests.length === 0 && Native.SettingsTransport.waiting.length === 2, "opening queues readers without HTTP fallback");
        label.modRefreshAccountPrefs();
        check(Native.SettingsTransport.waiting.length === 2, "pending refresh coalesces");
        Native.Bridge.runtimeState = "ready";
        check(Native.Bridge.requests.length === 2, "ready dispatches both readers");
        check(Native.Bridge.requests.every(function(v) { return v === '{"operation":"accounts.get"}'; }), "readers send only native accounts.get");
        complete(label, snapshot("a", false)); complete(scheduler, snapshot("a", false));
        check(label.modLocalAccountId === firstId && label.modAccountAlias === "Алиса 😀 a" && label.modAccountColor === "#8b5cf6", "large exact account ID selects name and ring");
        check(scheduler.work.modScheduleAccountId === secondId && !scheduler.notification.notificationMuted, "scheduler is scoped to other exact native ID");
        check(label.modAccountPrefFor("9007199254740992") === null, "neighboring large ID cannot alias");
        update(label, snapshot("b", true)); update(scheduler, snapshot("b", true));
        check(label.modAccountAlias === "Алиса 😀 b", "new revision refreshes label");
        check(scheduler.notification.notificationMuted && scheduler.work.modScheduleOwned, "new native snapshot applies account work hours");
        update(scheduler, snapshot("c", false));
        check(!scheduler.notification.notificationMuted && !scheduler.work.modScheduleOwned, "disabling work hours releases only owned mute");
        update(scheduler, snapshot("d", true)); scheduler.notification.notificationMuted = false;
        scheduler.work.modApplyWorkHours(); scheduler.work.modApplyWorkHours();
        check(!scheduler.notification.notificationMuted && scheduler.work.modScheduleOverride, "manual unmute remains respected");
        const weekdays = {enabled: true, days: [1], start: "22:00", end: "06:00"};
        check(!scheduler.work.modScheduleIsQuiet(weekdays, {getDay: function(){return 2;}, getHours: function(){return 1;}, getMinutes: function(){return 0;}}), "overnight schedule keeps previous weekday");
        const invalid = snapshot("e", false); invalid.accounts[0].accountId = 9007199254740992;
        update(label, invalid);
        check(label.modAccountPrefsRevision === "b".repeat(64), "invalid numeric ID snapshot cannot replace cache");
        const repeated = snapshot("b", false); repeated.accounts[0].displayName = "must not replace same revision";
        update(label, repeated);
        check(label.modAccountAlias === "Алиса 😀 b", "same revision preserves cached rows");
        label.commonDependency = {selfInfo: {id: secondId}};
        check(label.modAccountAlias === "Бизнес b" && label.modAccountColor === "#22a06b", "account switch immediately selects correct cached row");
        label.modRefreshAccountPrefs(); const stale = label.modAccountPrefsRequest;
        label.expireRequest();
        check(stale.aborted && !Native.SettingsTransport.pending[stale.requestId], "watchdog revokes admitted read");
        label.modRefreshAccountPrefs(); const current = label.modAccountPrefsRequest;
        Native.Bridge.operationFinished(stale.requestId, JSON.stringify({schema: 1, ok: true, result: snapshot("f", false)}));
        check(label.modAccountPrefsRequest === current && label.modAccountPrefsRevision === "b".repeat(64), "late stale response cannot replace newer request");
        complete(label, snapshot("0", false));
        check(label.modAccountAlias === "Бизнес 0", "fresh read succeeds after timeout");
        Native.Bridge.runtimeState = "error";
        label.modRefreshAccountPrefs();
        Qt.callLater(function() {
            check(label.modAccountPrefsRequest === null && label.modAccountAlias === "Бизнес 0", "unavailable runtime preserves cache and completes finitely");
            Native.Bridge.runtimeState = "ready";
            label.modRefreshAccountPrefs(); complete(label, snapshot("1", false));
            check(label.modAccountAlias === "Бизнес 1", "runtime recovery refreshes through same native adapter");
            label.modRefreshAccountPrefs(); preserved = label.modAccountPrefsRequest;
            label.destroy(); label = null;
            Qt.callLater(function() {
                check(preserved.aborted && !Native.SettingsTransport.pending[preserved.requestId], "destruction aborts pending reader");
                Native.Bridge.operationFinished(preserved.requestId, JSON.stringify({schema: 1, ok: true, result: snapshot("2", false)}));
                check(Object.keys(Native.SettingsTransport.pending).length === 0, "destroyed reader ignores later completion");
                scheduler.destroy(); scheduler = null; Qt.exit(0);
            });
        });
    })
}
