import QtQuick
import ValMAX.Native 1.0 as Native
Item {
    id: test
    property int checks: 0
    property string failure: ""
    property string phase: "@PHASE@"
    property int step: 0
    property string staleRevision: ""
    property var savedRows: []
    property int cancelledCallbacks: 0
    property int completed: 0
    property int overflow: 0
    QuickReplySettings { id: settings }
    QuickReplyComposer { id: composer }
    function check(value, message) {
        ++checks;
        if (!value) { failure = message; Qt.exit(1); return false; }
        return true;
    }
    function post(rows, revision) { settings.request("POST", JSON.stringify({schema: 1, revision: revision || settings.revision, replies: rows})); }
    function row(id, command, text, account) {
        const value = {id: id.repeat(32), command: command, title: "Synthetic title", text: text};
        if (account) value.accountId = account;
        return value;
    }
    function exerciseDraft() {
        composer.query("/hello");
        if (!check(composer.modFilteredQuickReplies.length === 1 && composer.modFilteredQuickReplies[0].accountId === "101", "specific scope shadows common")) return false;
        composer.modQuickReplyAttachmentIntegration = true;
        composer.autoObserve = false;
        composer.modQuickReplySelected = composer.modFilteredQuickReplies[0];
        composer.modApplyQuickReplyPart(0);
        if (!check(composer.draftText === "Primary 🛠" && composer.stageCalls === 1 && composer.stagedFiles.length === 1 && composer.stagedMedia.length === 1, "actual part selection sets text and stages split file/media metadata")) return false;
        composer.modAddQuickReplyFiles();
        if (!check(composer.stageCalls === 1 && composer.manualSendCalls === 0, "attachments stage once without automatic send")) return false;
        composer.emptyDraft();
        composer.modApplyQuickReplyPart(1);
        if (!check(composer.modQuickReplyPartIndex === 0 && composer.modQuickReplyDraftStatus.length > 0, "next part waits for pending native upload observation")) return false;
        composer.observeFiles();
        if (!check(composer.modQuickReplyNativeFilesObserved && !composer.modQuickReplyPartCleared && composer.modQuickReplySelected !== null, "upload observation does not retire preview")) return false;
        composer.nativeClear();
        if (!check(composer.modQuickReplySelected !== null && composer.modQuickReplyPartCleared, "native clear retains remaining parts")) return false;
        composer.modApplyQuickReplyPart(1);
        if (!check(composer.draftText === "Second synthetic part" && composer.modQuickReplyPartIndex === 1, "explicit next part uses actual draft function")) return false;
        composer.nativeClear();
        if (!check(composer.modQuickReplySelected === null && composer.manualSendCalls === 0, "final native clear retires preview without sending")) return false;
        composer.modQuickReplySelected = composer.modQuickReplyRows[0];
        composer.currentOpenedChatId = "502";
        if (!check(composer.modQuickReplySelected === null, "chat switch resets selection")) return false;
        composer.query("/");
        composer.modQuickReplySelected = composer.modQuickReplyRows[0];
        composer.modQuickReplyAccountId = "202";
        if (!check(composer.modQuickReplySelected === null && composer.modFilteredQuickReplies.length === 2 && composer.modFilteredQuickReplies.some(function(r) { return r.accountId === "202"; }) && !composer.modFilteredQuickReplies.some(function(r) { return r.accountId === "101"; }), "account switch excludes foreign templates")) return false;
        composer.modQuickReplyAccountId = "303";
        if (!check(composer.modFilteredQuickReplies.length === 2 && composer.modFilteredQuickReplies.every(function(r) { return !r.accountId; }), "third account sees common only")) return false;
        composer.modQuickReplyAccountId = "101";
        composer.emptyDraft();
        return true;
    }
    Timer {
        interval: 5; running: true; repeat: true
        onTriggered: {
            if (test.failure.length) return;
            if (test.step === 0) {
                if (!settings.loaded || composer.modQuickRepliesRequest !== null || !composer.modQuickReplyRevision.length) return;
                if (!test.check(Native.Bridge.runtimeAvailable && !settings.hasError, "actual controllers read native store")) return;
                if (test.phase === "restart") {
                    if (!test.check(settings.replies.length === 4 && composer.modQuickReplyRows.length === 4, "native file survives process restart")) return;
                    if (!test.exerciseDraft()) return;
                    Qt.exit(0); return;
                }
                if (!test.check(settings.replies.length === 0 && composer.modQuickReplyRows.length === 0, "missing quick reply store returns empty snapshot")) return;
                settings.showEditor(null);
                settings.editorCommand = "Hello"; settings.editorTitle = "Synthetic reply"; settings.editorText = "Primary 🛠"; settings.editorAccountId = "101";
                settings.chooseFiles(-1, "file");
                settings.addFiles(["file:///Z:/synthetic-only/one.txt"]);
                settings.chooseFiles(-1, "media");
                settings.addFiles(["file:///Z:/synthetic-only/two.png"]);
                settings.addPart(); settings.changePart(0, "Second synthetic part");
                if (!test.check(settings.pickerCalls === 2 && settings.editorAttachments.length === 2, "explicit picker callback stores metadata only")) return;
                test.staleRevision = settings.revision;
                settings.saveReply(false); test.step = 1;
            } else if (test.step === 1) {
                if (settings.pendingRequest !== null) return;
                if (!test.check(!settings.hasError && !settings.editing && settings.revision !== test.staleRevision && settings.replies[0].parts.length === 1, "actual settings save preserves extended schema")) return;
                const rows = settings.replies.slice();
                rows.push(test.row("a", "hello", "Common", ""));
                rows.push(test.row("b", "other", "Other common", ""));
                rows.push(test.row("c", "hello", "Foreign", "202"));
                test.post(rows); test.step = 2;
            } else if (test.step === 2) {
                if (settings.pendingRequest !== null) return;
                if (!test.check(!settings.hasError && settings.replies.length === 4 && settings.visibleReplies.length === 3, "settings keeps foreign rows for full replacement")) return;
                composer.modLoadQuickReplies(); test.step = 3;
            } else if (test.step === 3) {
                if (composer.modQuickRepliesRequest !== null) return;
                if (!test.check(composer.modQuickReplyRevision === settings.revision, "composer refresh accepts same native revision")) return;
                if (!test.exerciseDraft()) return;
                test.savedRows = settings.replies.slice();
                settings.showEditor(settings.replies[0]); settings.editorText = "Unsaved local draft";
                test.post(settings.replies, test.staleRevision); test.step = 4;
            } else if (test.step === 4) {
                if (settings.pendingRequest !== null) return;
                if (!test.check(settings.hasError && settings.editing && settings.editorText === "Unsaved local draft" && settings.statusText.indexOf("Список изменился") === 0, "revision conflict preserves editor content")) return;
                test.post([{id: "invalid"}]); test.step = 5;
            } else if (test.step === 5) {
                if (settings.pendingRequest !== null) return;
                if (!test.check(settings.hasError && settings.statusText.indexOf("Шаблон не прошёл") === 0, "invalid_replies maps to existing page validation branch")) return;
                const rows = [];
                for (let i=0; i<18; ++i) rows.push({id: i.toString(16).padStart(32,"0"), command: "big_" + i, title:"Big synthetic", text:"x".repeat(5000)});
                test.post(rows); test.step = 6;
            } else if (test.step === 6) {
                if (settings.pendingRequest !== null) return;
                if (!test.check(!settings.hasError && settings.replies.length === 18, "quick reply bridge accepts valid payload above old 64 KiB adapter limit")) return;
                const over = [test.row("e", "large", "я".repeat(140000), "")];
                test.post(over); test.step = 7;
            } else if (test.step === 7) {
                if (settings.pendingRequest !== null) return;
                if (!test.check(settings.hasError && settings.replies.length === 18, "UTF-8 body over 256 KiB rejected without mutation")) return;
                test.post(test.savedRows); test.step = 8;
            } else if (test.step === 8) {
                if (settings.pendingRequest !== null) return;
                if (!test.check(!settings.hasError && settings.replies.length === 4, "saved synthetic fixtures restored")) return;
                const request = Native.SettingsTransport.createRequest("quick_replies");
                request.onreadystatechange = function() { ++test.cancelledCallbacks; };
                request.open("GET"); request.send(); request.abort();
                test.step = 9;
            } else if (test.step === 9) {
                if (Native.Bridge.pendingOperations !== 0) return;
                if (!test.check(test.cancelledCallbacks === 0 && Object.keys(Native.SettingsTransport.pending).length === 0, "cancelled controller cannot receive late completion")) return;
                for (let i=0; i<10; ++i) {
                    const request = Native.SettingsTransport.createRequest("quick_replies");
                    request.onreadystatechange = function() { ++test.completed; if (request.status === 503) ++test.overflow; };
                    request.open("GET"); request.send();
                }
                test.step = 10;
            } else if (test.step === 10 && test.completed === 10) {
                if (!test.check(test.overflow === 2 && Object.keys(Native.SettingsTransport.pending).length === 0, "native quick reply queue bounded and drained")) return;
                Qt.exit(0);
            }
        }
    }
}
