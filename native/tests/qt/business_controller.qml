import QtQuick
Item {
    id: test
    property int checks: 0
    property string failure: ""
    BusinessController { id: page }
    function check(value, message) {
        ++checks;
        if (!value) { failure = message; Qt.exit(1); return false; }
        return true;
    }
    Timer {
        running: true; interval: 1
        onTriggered: {
            if (!test.check(page.businessWebView === null, "view remains lazy")) return;
            page.createBusinessView();
            var view = page.businessWebView;
            if (!test.check(view !== null && view.isSynthetic, "explicit fake WebView created")) return;
            if (!test.check(view.url.toString() === page.businessHome && view.bridge === page, "home navigation and controller wired")) return;
            page.createBusinessView();
            if (!test.check(page.businessWebView === view, "single view per page")) return;
            view.loadingChanged({status: 0, errorString: ""});
            if (!test.check(page.businessLoading && !page.businessHasError, "started state")) return;
            view.loadingChanged({status: 1, errorString: ""});
            if (!test.check(!page.businessLoading && !page.businessHasError && view.scripts === 1, "success installs navigation handler")) return;
            view.loadingChanged({status: 2, errorString: "server detail with secret=value"});
            if (!test.check(page.businessHasError && !page.businessInitializationFailed && page.businessStatus.indexOf("secret") < 0, "network error never displays raw details")) return;
            page.reloadBusiness();
            if (!test.check(page.businessWebView === view && view.reloads === 1, "ordinary failure reloads existing view")) return;
            view.loadingChanged({status: 2, errorString: page.businessInitError});
            if (!test.check(page.businessInitializationFailed && page.businessStatus === page.businessInitError, "guard failure has finite repair message")) return;
            page.reloadBusiness();
            if (!test.check(page.businessWebView !== view && view.stops === 1 && view.bridge === null, "initialization retry replaces failed controller")) return;
            view = page.businessWebView;
            if (!test.check(view !== null && !page.businessInitializationFailed && !page.businessHasError, "replacement resets failure state")) return;
            view.loadingChanged({status: 2, errorString: page.businessInitError});
            page.openBusinessHome();
            if (!test.check(page.businessWebView !== view && view.stops === 1, "home also recreates failed controller")) return;
            view = page.businessWebView;
            page.businessUrlChanged("https://id.max.ru/oauth?code=private&state=value");
            if (!test.check(page.businessHost === "id.max.ru", "URL presentation includes host only")) return;
            page.businessUrlChanged("http://invalid.test/");
            if (!test.check(page.businessHasError && view.stops === 1, "non-HTTPS navigation stopped")) return;
            page.businessUrlChanged("about:blank");
            if (!test.check(page.businessHost === "id.max.ru", "blank URL is ignored")) return;
            page.releaseBusinessView();
            if (!test.check(page.businessWebView === null && view.bridge === null && view.stops === 2, "explicit release clears view and callback")) return;
            page.releaseBusinessView();
            if (!test.check(page.businessWebView === null, "release is idempotent")) return;
            Qt.exit(0);
        }
    }
}
