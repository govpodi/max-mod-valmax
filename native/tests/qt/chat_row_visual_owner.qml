import QtQuick
import QtQuick.Window
import ValMAX.Native 1.0 as Native
import QmlSingletonesModule
import "RuntimeOptimization" as ValMAXOptimization
import "RuntimeRows"

// Lifecycle contract test. A stateful row body records the owner IDs used at
// construction. This does not assume that the shipped SDK has an immutable
// name/avatar cache, and is not a reproduction of that undocumented behavior.
Window {
    id: test
    visible: true
    opacity: 0
    flags: Qt.Tool | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    width: 320
    height: 200
    property int checks: 0
    property string failure: ""
    property int phase: 0
    property int rounds: 0
    property QtObject dependency: QtObject {
        property QtObject selfInfo: QtObject { property string id: "synthetic-account" }
    }
    function check(ok, label) {
        if (!ok) { failure = label; Qt.exit(1); throw new Error(label); }
        checks += 1;
    }
    function assertRows(label) {
        let observed = 0;
        for (let i = 0; i < chats.count; ++i) {
            const shell = chatList.itemAtIndex(i);
            if (!shell || !shell.eligible || !shell.ready) continue;
            observed += 1;
            const dto = chats.get(i);
            check(shell.item.initializedChat === dto.chatId, label + ":construction chat owner");
            check(shell.item.initializedClient === dto.chatClientId, label + ":construction client owner");
            check(shell.item.initializedContact === dto.contactId, label + ":construction contact owner");
            check(shell.item.initializedIndex === i, label + ":construction model index");
            check(shell.item.person === dto.person, label + ":live person data");
            check(shell.item.lastText === dto.lastText, label + ":live last message");
            check(shell.item.pinned === dto.pinned, label + ":live pin");
        }
        check(observed >= 3, label + ":actual loaded visible rows");
    }
    ListModel { id: chats }
    Component.onCompleted: {
        for (let i = 0; i < 80; ++i)
            chats.append({chatId: "chat-" + i, chatClientId: "client-" + i, contactId: "contact-" + i,
                person: "person-" + i, lastText: "message-" + i, pinned: i < 3});
    }
    ListView {
        id: chatList
        anchors.fill: parent
        model: chats
        /* ACTUAL_REUSE_BINDING */
        cacheBuffer: 0
        clip: true
        delegate: ValMAXAsyncListRow {
            id: shell
            required property int index
            required property string chatId
            required property string chatClientId
            required property string contactId
            required property string person
            required property string lastText
            required property bool pinned
            property var commonDependency: test.dependency
            width: chatList.width
            rowHeight: 40
            view: chatList
            workQueue: nativeQueue
            /* ACTUAL_IDENTITY_BINDING */
            ListView.onPooled: pooled = true
            ListView.onReused: pooled = false
            rowComponent: Component {
                Item {
                    property string initializedChat: ""
                    property string initializedClient: ""
                    property string initializedContact: ""
                    property int initializedIndex: -1
                    property string person: shell.person
                    property string lastText: shell.lastText
                    property bool pinned: shell.pinned
                    Component.onCompleted: {
                        initializedChat = shell.chatId;
                        initializedClient = shell.chatClientId;
                        initializedContact = shell.contactId;
                        initializedIndex = shell.index;
                    }
                    Text { text: parent.person + ":" + parent.lastText }
                }
            }
        }
    }
    Native.ListWorkQueue {
        id: nativeQueue
        view: chatList
        viewActive: true
        admissionBudgetMs: 1000
        quietMs: 0
        fastPixelsPerSecond: 1000000000
    }
    FrameAnimation { running: true; onTriggered: nativeQueue.pump() }
    Timer {
        interval: 150
        running: true
        repeat: true
        onTriggered: {
            test.assertRows("phase " + test.phase + " round " + test.rounds);
            if (test.phase === 0) {
                // Same server chat ID, new visual context IDs. The body must
                // initialize for these new owners before it is shown Ready.
                const stableChat = chats.get(0).chatId;
                chats.set(0, {chatId: stableChat, chatClientId: "rekeyed-client-" + test.rounds,
                    contactId: "rekeyed-contact-" + test.rounds, person: "new-person-" + test.rounds,
                    lastText: "new-message-" + test.rounds, pinned: true});
                nativeQueue.pump();
                test.phase = 1;
            } else if (test.phase === 1) {
                chatList.positionViewAtIndex(65, ListView.Beginning);
                test.phase = 2;
            } else if (test.phase === 2) {
                chatList.positionViewAtBeginning();
                test.phase = 3;
            } else if (test.phase === 3) {
                chats.setProperty(65, "pinned", true);
                chats.move(65, 0, 1);
                nativeQueue.pump();
                test.phase = 4;
            } else if (test.phase === 4) {
                chats.remove(0, 1);
                chats.insert(0, {chatId: "insert-chat-" + test.rounds, chatClientId: "insert-client-" + test.rounds,
                    contactId: "insert-contact-" + test.rounds, person: "insert-person-" + test.rounds,
                    lastText: "insert-message-" + test.rounds, pinned: true});
                nativeQueue.pump();
                test.phase = 5;
            } else {
                test.rounds += 1;
                if (test.rounds >= 6) Qt.exit(0);
                else { test.phase = 0; chatList.positionViewAtBeginning(); }
            }
        }
    }
}
