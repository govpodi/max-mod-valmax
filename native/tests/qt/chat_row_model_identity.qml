import QtQuick
import QtQuick.Window
import ValMAX.Native 1.0 as Native
import "RuntimeRows"

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
            check(shell.chatKey === dto.chatKey, label + ":outer model identity");
            check(shell.item.ownerIdentity === shell.identityKey, label + ":created identity");
            check(shell.item.ownerSerial === shell.valmaxRequestSerial, label + ":created serial");
            check(shell.item.chatKey === dto.chatKey, label + ":inner model identity");
            check(shell.item.person === dto.person, label + ":inner person");
            check(shell.item.lastText === dto.lastText, label + ":inner last message");
            check(shell.item.pinned === dto.pinned, label + ":inner pin");
        }
        check(observed >= 3, label + ":actual loaded visible rows");
    }
    ListModel { id: chats }
    Component.onCompleted: {
        for (let i = 0; i < 80; ++i)
            chats.append({chatKey: "chat-" + i, person: "person-" + i, lastText: "message-" + i, pinned: i < 3});
    }
    ListView {
        id: chatList
        anchors.fill: parent
        model: chats
        reuseItems: true
        cacheBuffer: 0
        clip: true
        delegate: ValMAXAsyncListRow {
            id: shell
            required property int index
            required property string chatKey
            required property string person
            required property string lastText
            required property bool pinned
            width: chatList.width
            rowHeight: 40
            view: chatList
            workQueue: nativeQueue
            identityKey: "account:" + chatKey
            ListView.onPooled: pooled = true
            ListView.onReused: pooled = false
            rowComponent: Component {
                Item {
                    property string ownerIdentity: ""
                    property int ownerSerial: -1
                    property string chatKey: shell.chatKey
                    property string person: shell.person
                    property string lastText: shell.lastText
                    property bool pinned: shell.pinned
                    Component.onCompleted: {
                        ownerIdentity = shell.identityKey;
                        ownerSerial = shell.valmaxRequestSerial;
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
                chatList.positionViewAtIndex(65, ListView.Beginning);
                test.phase = 1;
            } else if (test.phase === 1) {
                chatList.positionViewAtIndex(0, ListView.Beginning);
                test.phase = 2;
            } else if (test.phase === 2) {
                // Promote a distant row over the three existing pinned rows.
                chats.setProperty(65, "pinned", true);
                chats.move(65, 0, 1);
                nativeQueue.pump();
                test.phase = 3;
            } else if (test.phase === 3) {
                chats.set(0, {chatKey: "replacement-" + test.rounds,
                    person: "new-person-" + test.rounds, lastText: "new-message-" + test.rounds, pinned: true});
                nativeQueue.pump();
                test.phase = 4;
            } else if (test.phase === 4) {
                chats.remove(0, 1);
                chats.insert(0, {chatKey: "insert-" + test.rounds,
                    person: "insert-person-" + test.rounds, lastText: "insert-message-" + test.rounds, pinned: true});
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
