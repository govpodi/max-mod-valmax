// Native bundle: WebView is created only when this page opens.
Component {
    id: settingsValMAXBusiness
    Control {
        id: businessPage
        property var selfComponent: settingsValMAXBusiness
        readonly property var maxwellColorTokens: ClientAppearance.AppearanceTokens
        readonly property string businessHome: "https://business.max.ru/self/"
        property var businessWebView: null
        property bool businessLoading: false
        property bool businessHasError: false
        property bool businessInitializationFailed: false
        readonly property string businessInitError: "Не удалось открыть вкладку бизнеса. Попробуйте ещё раз."
        property string businessStatus: "Открытие кабинета организации…"
        property string businessHost: "business.max.ru"
        readonly property real gap: UiKit.DesignSystem.SpacingCommon._M
        readonly property string popupNavigationScript: `
            (function () {
                if (window.__valmaxInternalNavigation) return;
                window.__valmaxInternalNavigation = true;
                // WebView2 does not expose popup windows in Qt WebView. Keep
                // user-initiated links and ordinary OAuth redirects in this tab.
                window.open = function (url) {
                    if (url) {
                        try {
                            var target = new URL(String(url), window.location.href);
                            if (target.protocol === "https:") window.location.assign(target.href);
                        } catch (ignored) {}
                    }
                    return window;
                };
                document.addEventListener("click", function (event) {
                    var link = event.target && event.target.closest ? event.target.closest("a[target]") : null;
                    if (link && link.target !== "_self") link.target = "_self";
                }, true);
                document.addEventListener("submit", function (event) {
                    if (event.target && event.target.target) event.target.target = "_self";
                }, true);
            })();
        `
        function createBusinessView() {
            if (businessWebView !== null) return;
            businessHasError = false;
            businessInitializationFailed = false;
            try {
                var view = Qt.createQmlObject(`
                    import QtQuick
                    import QtWebView
                    WebView {
                        anchors.fill: parent
                        property var bridge: null
                        onUrlChanged: { if (bridge) bridge.businessUrlChanged(url); }
                        onLoadingChanged: request => {
                            if (bridge) bridge.businessLoadChanged(request.status === WebView.LoadStartedStatus, request.status === WebView.LoadSucceededStatus, request.status === WebView.LoadFailedStatus, request.errorString);
                        }
                    }
                `, businessWebHost, "ValMAXBusinessWebView.qml");
                businessWebView = view;
                view.bridge = businessPage;
                view.url = businessHome;
            } catch (ignored) {
                businessHasError = true; businessLoading = false; businessInitializationFailed = true;
                businessStatus = businessInitError;
            }
        }
        function businessUrlChanged(url) {
            var value = url.toString();
            if (value === "about:blank" || !value.length) return;
            var match = /^https:\/\/([^\/?#]+)/i.exec(value);
            if (!match) {
                if (businessWebView !== null) businessWebView.stop();
                businessLoading = false; businessHasError = true;
                businessStatus = "Эта ссылка не поддерживается во вкладке бизнеса. Вернись в кабинет организации.";
                return;
            }
            // Show only the host. OAuth codes and state stay in the webview.
            businessHost = match[1].toLowerCase();
        }
        function businessLoadChanged(started, succeeded, failed, errorString) {
            if (started) { businessLoading = true; businessHasError = false; businessStatus = "Загрузка кабинета…"; }
            if (failed) {
                businessLoading = false; businessHasError = true;
                businessInitializationFailed = errorString === businessInitError;
                businessStatus = businessInitializationFailed ? businessInitError : "Кабинет не загрузился. Проверь подключение и попробуй ещё раз.";
            }
            if (succeeded) {
                businessLoading = false; businessHasError = false; businessInitializationFailed = false; businessStatus = "";
                if (businessWebView !== null) businessWebView.runJavaScript(popupNavigationScript, function () {});
            }
        }
        function releaseBusinessView() {
            var view = businessWebView;
            businessWebView = null;
            if (view !== null) { view.bridge = null; view.stop(); view.destroy(); }
        }
        function openBusinessHome() {
            if (businessInitializationFailed) releaseBusinessView();
            if (businessWebView === null) { createBusinessView(); return; }
            businessHasError = false; businessWebView.url = businessHome;
        }
        function reloadBusiness() {
            if (businessInitializationFailed) releaseBusinessView();
            if (businessWebView === null) { createBusinessView(); return; }
            businessHasError = false; businessWebView.reload();
        }
        Component.onCompleted: createBusinessView()
        Component.onDestruction: { if (businessWebView !== null) { businessWebView.bridge = null; businessWebView.stop(); } }
        background: Rectangle { color: businessPage.maxwellColorTokens.colorBackgroundSurface }
        component BusinessText: Label {
            font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default)
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: businessPage.maxwellColorTokens.colorTextSecondary
        }
        component BusinessButton: Button {
            padding: businessPage.gap
            implicitHeight: Math.max(40, contentItem.implicitHeight + 2 * padding)
            font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Description, Typography.FontUtil.Sizes.Default)
            contentItem: BusinessText { text: parent.text; color: parent.enabled ? businessPage.maxwellColorTokens.colorTextThemed : businessPage.maxwellColorTokens.colorTextTertiary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: UiKit.DesignSystem.BorderRadiusCommon._S; color: businessPage.maxwellColorTokens.colorBackgroundCard }
        }
        contentItem: ColumnLayout {
            spacing: 0
            UiKit.SettingsPageHeader {
                Layout.fillWidth: true
                title: "Бизнес"
                backButtonVisible: windowState.isOneFrame
                backButtonAction: () => root.backClicked()
            }
            Control {
                Layout.fillWidth: true
                padding: businessPage.gap
                contentItem: ColumnLayout {
                    spacing: UiKit.DesignSystem.SpacingCommon._S
                    RowLayout {
                        Layout.fillWidth: true; spacing: UiKit.DesignSystem.SpacingCommon._S
                        BusinessButton { text: "←"; Accessible.name: "Назад по страницам"; enabled: businessPage.businessWebView !== null && businessPage.businessWebView.canGoBack; onClicked: businessPage.businessWebView.goBack() }
                        BusinessButton { text: "→"; Accessible.name: "Вперёд по страницам"; enabled: businessPage.businessWebView !== null && businessPage.businessWebView.canGoForward; onClicked: businessPage.businessWebView.goForward() }
                        BusinessButton { text: "Обновить"; onClicked: businessPage.reloadBusiness() }
                        BusinessButton { text: "Кабинет"; onClicked: businessPage.openBusinessHome() }
                        Item { Layout.fillWidth: true }
                    }
                    BusinessText { Layout.fillWidth: true; text: businessPage.businessHost + " · общий вход в кабинет для ValMAX"; elide: Text.ElideRight; maximumLineCount: 1 }
                    BusinessText { objectName: "businessStatus"; Layout.fillWidth: true; visible: businessPage.businessStatus.length > 0; text: businessPage.businessStatus; color: businessPage.businessHasError ? businessPage.maxwellColorTokens.colorTextAttention : businessPage.maxwellColorTokens.colorTextSecondary }
                }
            }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: businessPage.maxwellColorTokens.colorDividerPrimary }
            ProgressBar { Layout.fillWidth: true; visible: businessPage.businessLoading; indeterminate: true; implicitHeight: 3 }
            Item {
                id: businessWebHost
                objectName: "businessWebHost"
                Layout.fillWidth: true; Layout.fillHeight: true
                visible: !businessPage.businessHasError
            }
        }
    }
}
