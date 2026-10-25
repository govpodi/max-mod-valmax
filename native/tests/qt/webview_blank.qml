import QtQuick
import QtQuick.Window
import QtWebView

Window {
    id: root
    visible: false
    width: 640
    height: 480
    property int checks: 0
    property string failure: ""
    property string verifiedUrl: ""
    property string verifiedUserAgent: ""
    WebView {
        id: browser
        anchors.fill: parent
        url: "about:blank"
        onLoadingChanged: function(request) {
            if (request.status === WebView.LoadFailedStatus) {
                root.failure = "blank_load_failed";
                Qt.exit(9);
            } else if (request.status === WebView.LoadSucceededStatus) {
                ++root.checks;
                browser.runJavaScript("JSON.stringify({url:location.href,ua:navigator.userAgent,body:document.body.textContent})", function(value) {
                    try {
                        const result = JSON.parse(value);
                        if (result.url !== "about:blank" || result.body !== "" || typeof result.ua !== "string") throw new Error();
                        root.verifiedUrl = result.url;
                        root.verifiedUserAgent = result.ua;
                        ++root.checks;
                        Qt.exit(0);
                    } catch (_) {
                        root.failure = "blank_result_invalid";
                        Qt.exit(10);
                    }
                });
            }
        }
    }
}
