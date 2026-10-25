import QtQuick
import ValMAX.Native 1.0 as Native
Item {
    id: test
    property int checks: 0
    property string failure: ""
    property int step: 0
    function check(value, text) { ++checks; if (!value) { failure=text; Qt.exit(1); return false; } return true; }
    Native.MDataTransferEntry { id: entry }
    Native.MDataTransferEntry { id: other }
    Component.onCompleted: {
        if (!check(entry.available && !entry.busy, "native helper is available independently of a signed-in runtime")) return;
        entry.open();
        if (!check(entry.dialog !== null && Native.TransferLauncher.starts === 0, "opening confirmation does not launch or quit")) return;
        entry.confirm();
        if (!check(entry.busy && Native.TransferLauncher.starts === 1 && Native.TransferLauncher.quits === 0, "explicit confirmation admits launch only")) return;
        entry.confirm();
        if (!check(Native.TransferLauncher.starts === 1, "duplicate confirmation does not spawn twice")) return;
        Native.TransferLauncher.finish(false, "spawn_failed");
        if (!check(!entry.busy && Native.TransferLauncher.quits === 0 && entry.statusText.indexOf("остаётся открыта") >= 0, "spawn failure keeps client alive")) return;
        entry.confirm(); Native.TransferLauncher.finish(false, "handoff_timeout");
        if (!check(Native.TransferLauncher.quits === 0 && entry.statusText.indexOf("не подтверждён") >= 0, "timeout never closes client")) return;
        entry.confirm(); Native.TransferLauncher.finish(true, "");
        if (!check(Native.TransferLauncher.quits === 1 && entry.statusText.indexOf("импорт ещё не выполнялся") >= 0, "successful spawn queues own shutdown without claiming import success")) return;
        if (!check(other.statusText === "", "another page cannot take ownership of a handoff")) return;
        Native.TransferLauncher.available = false; Native.TransferLauncher.state = "idle";
        other.open(); other.confirm();
        if (!check(Native.TransferLauncher.starts === 3 && Native.TransferLauncher.quits === 1, "unavailable helper never launches or quits")) return;
        Qt.callLater(function() { Qt.exit(0); });
    }
}
