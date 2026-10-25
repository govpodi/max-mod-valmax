import QtQuick
import QtQuick.Window
import QtWebView
Window {
    id: root
    visible: false
    width: 640; height: 480
    property int checks: 0
    property string failure: ""
    property string verifiedUrl: ""
    property string verifiedUserAgent: ""
    WebView {
        anchors.fill: parent
        url: "about:blank"
        onLoadingChanged: function(request) {
            if (request.status === WebView.LoadFailedStatus) {
                ++root.checks;
                root.failure = "blank_load_failed";
                if (root.checks !== 1) Qt.exit(10);
                finish.start();
            } else if (request.status === WebView.LoadSucceededStatus) Qt.exit(11);
        }
    }
    Timer { id: finish; interval: 1600; onTriggered: Qt.exit(root.checks === 1 ? 9 : 10) }
}
