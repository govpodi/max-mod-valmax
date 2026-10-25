import QtQuick
import QtQuick.Window
import ValMAX.Native 1.0 as Native
import QmlSingletonesModule
import "RuntimeRows" as ValMAX
import "RuntimeOptimization" as ValMAXOptimization

// Actual generated shell/controllers with only the visual SDK body stubbed.
// Construction snapshots are a lifecycle contract, not a claim about stock SDK
// caching internals or a reproduction of the private user's rendered pixels.
Window {
    id: root
    visible: true
    opacity: 0
    flags: Qt.Tool | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    width: 320
    height: 200
    property int checks: 0
    property string failure: ""
    property int phase: 0
    property int rounds: 0
    property bool pausePump: false
    property var currentChatId: ""
    property var selectedFolderId: "synthetic-folder"
    property string valmaxPageAccount: "synthetic-account"
    property string lastOpened: ""
    property int openCount: 0
    property int openCountBeforePending: 0
    property var commonDependency: dependencyOne
    signal createFolderForChat(var chatId)
    signal closeChatRequested(var chatId)
    QtObject {
        id: dispatcherObject
        function onChatOpenClicked(chatId) { root.lastOpened = String(chatId); root.openCount += 1; }
        function onChatPreviewClicked(chatId, message) {}
    }
    QtObject {
        id: dependencyOne
        property string contextLabel: "context-one"
        property QtObject selfInfo: QtObject { property string id: "synthetic-account" }
        property QtObject serverParameters: QtObject { property bool storiesEnabled: false }
        property QtObject userAppearanceContext: QtObject { property int typographySize: 0 }
        property var clickDispatcher: dispatcherObject
    }
    QtObject {
        id: dependencyTwo
        property string contextLabel: "context-two"
        property QtObject selfInfo: QtObject { property string id: "synthetic-account" }
        property QtObject serverParameters: QtObject { property bool storiesEnabled: false }
        property QtObject userAppearanceContext: QtObject { property int typographySize: 0 }
        property var clickDispatcher: dispatcherObject
    }
    function check(ok, description) {
        if (!ok) { failure = description; Qt.exit(1); throw new Error(description); }
        checks += 1;
    }
    function text(value) { return value === null || value === undefined ? "<null>" : String(value); }
    function dto(i, tag) {
        return {chatId: "chat-" + i, chatClientId: "client-" + i + tag, contactId: "contact-" + i + tag,
            ownerId: "owner-" + i, newMessagesCount: 0, chatType: 1, messageContext: {isNull:false},
            isFavorites: i < 3, hasReactions:false, hasMentions:false, draftText:"",
            lastReadMessage:"last-" + i, isSavedMessagesChat:false};
    }
    function assertRows(label) {
        let observed = 0;
        for (let i = 0; i < allChatsListView.count; ++i) {
            const shell = allChatsListView.itemAtIndex(i);
            if (!shell || !shell.eligible || !shell.ready) continue;
            observed += 1;
            const modelDto = allChatsListView.model.get(i);
            check(shell.item.initializedChat === text(modelDto.chatId), label + ":body construction chat");
            check(shell.item.initializedClient === text(modelDto.chatClientId), label + ":body construction client");
            check(shell.item.initializedContact === text(modelDto.contactId), label + ":body construction contact");
            check(shell.item.initializedIdentity === shell.identityKey, label + ":body construction identity");
            check(shell.item.initializedDependency === commonDependency.contextLabel, label + ":body dependency generation");
            check(text(shell.item.chatId) === text(modelDto.chatId), label + ":live forwarded chat");
            check(text(shell.item.contactId) === text(modelDto.contactId), label + ":live forwarded contact");
        }
        check(observed >= 3, label + ":real visible shell bodies loaded");
    }
    ListModel { id: chats; dynamicRoles: true }
    ListModel { id: chatsReplacement; dynamicRoles: true }
    Component.onCompleted: {
        for (let i = 0; i < 80; ++i) { chats.append(dto(i, "")); chatsReplacement.append(dto(i, "")); }
    }
    ListView {
        id: allChatsListView
        anchors.fill: parent
        model: chats
        cacheBuffer: 0
        clip: true
        property int valmaxNavigationGeneration: 0
        property real valmaxPrefetchRowHeight: 40
        readonly property var valmaxWorkQueue: nativeQueue
        signal delegateRemoved(var chatId)
        /* ACTUAL_REUSE_BINDING */
        /* ACTUAL_DELEGATE */
    }
    Native.ListWorkQueue {
        id: nativeQueue
        view: allChatsListView
        viewActive: true
        admissionBudgetMs: 1000
        quietMs: 0
        fastPixelsPerSecond: 1000000000
    }
    FrameAnimation { running: !root.pausePump; onTriggered: nativeQueue.pump() }
    Timer {
        interval: 180
        running: true
        repeat: true
        onTriggered: {
            if (root.phase !== 8) root.assertRows("phase " + root.phase + " round " + root.rounds);
            if (root.phase === 0) {
                const shell = allChatsListView.itemAtIndex(0);
                root.lastOpened = "";
                shell.valmaxOpen();
                root.check(root.lastOpened === root.text(chats.get(0).chatId), "actual ready click routes current chat");
                const replacement = root.dto(0, "-rekeyed-" + root.rounds);
                chats.set(0, replacement);
                nativeQueue.pump();
                root.phase = 1;
            } else if (root.phase === 1) {
                chats.setProperty(0, "contactId", null);
                nativeQueue.pump();
                root.phase = 2;
            } else if (root.phase === 2) {
                chats.setProperty(0, "contactId", "restored-contact-" + root.rounds);
                allChatsListView.positionViewAtIndex(65, ListView.Beginning);
                root.phase = 3;
            } else if (root.phase === 3) {
                allChatsListView.positionViewAtBeginning();
                root.phase = 4;
            } else if (root.phase === 4) {
                chats.move(65, 0, 1);
                nativeQueue.pump();
                root.phase = 5;
            } else if (root.phase === 5) {
                root.commonDependency = root.commonDependency === dependencyOne ? dependencyTwo : dependencyOne;
                nativeQueue.pump();
                root.phase = 6;
            } else if (root.phase === 6) {
                allChatsListView.model = chatsReplacement;
                nativeQueue.pump();
                root.phase = 7;
            } else if (root.phase === 7) {
                const shell = allChatsListView.itemAtIndex(0);
                root.pausePump = true;
                shell.valmaxResetIdentity();
                root.openCountBeforePending = root.openCount;
                shell.valmaxOpen();
                root.check(shell.valmaxPendingOpen, "actual pending click queued");
                // Same account and chat IDs, different model/dependency owner.
                root.commonDependency = root.commonDependency === dependencyOne ? dependencyTwo : dependencyOne;
                root.pausePump = false;
                root.phase = 8;
            } else if (root.phase === 8) {
                root.assertRows("pending settled");
                root.check(root.openCount === root.openCountBeforePending, "stale pending click cleared on dependency replacement");
                const shell = allChatsListView.itemAtIndex(0);
                shell.valmaxOpen();
                root.check(root.lastOpened === root.text(chatsReplacement.get(0).chatId), "post replacement click opens current chat");
                root.rounds += 1;
                if (root.rounds >= 3) Qt.exit(0);
                else { allChatsListView.model = chats; allChatsListView.positionViewAtBeginning(); root.phase = 0; }
            }
        }
    }
}
