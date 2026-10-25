// Only linked into the distinct fault-test DLL, never production backend.
#include <windows.h>
#include <WebView2.h>
#include <wrl/client.h>
#include <QTimer>
#include <cstdio>
#include "webview_failed_controller.h"
STDAPI CreateCoreWebView2EnvironmentWithOptions(PCWSTR, PCWSTR,
    ICoreWebView2EnvironmentOptions *, ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler *handler)
{
    std::fputs("VALMAX_FAKE_ENV_CALLED\n",stderr);std::fflush(stderr);
    if (qgetenv("VALMAX_WEBVIEW_FAULT") == QByteArrayLiteral("sync-error")) return E_FAIL;
    if (qgetenv("VALMAX_WEBVIEW_FAULT") == QByteArrayLiteral("failed-controller")) {
        const auto environment = Microsoft::WRL::Make<ValmaxFailedControllerEnvironment>();
        if (!environment) return E_OUTOFMEMORY;
        return handler->Invoke(S_OK, environment.Get());
    }
    Microsoft::WRL::ComPtr<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler> callback = handler;
    // The 10-second production deadline must fail once; the later native
    // callback must not crash, recreate state, or emit another failure.
    QTimer::singleShot(11000, [callback] {
        std::fputs("VALMAX_FAKE_ENV_CALLBACK\n",stderr);std::fflush(stderr);
        callback->Invoke(E_FAIL, nullptr);
    });
    return S_OK;
}
