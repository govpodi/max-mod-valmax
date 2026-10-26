#include "window_activation.h"
#include "../common/window_activation.h"
#include <QCoreApplication>
#include <QGuiApplication>
#include <QMetaObject>
#include <QThread>
#include <QWindow>
#include <windows.h>

namespace valmax {
WindowActivationReceiver::WindowActivationReceiver()
{
    auto *app = QCoreApplication::instance();
    if (!app || QThread::currentThread() != app->thread()) return;
    messageId_ = RegisterWindowMessageW(activation::kMessageName);
    if (!messageId_) return;
    app->installNativeEventFilter(this);
    installed_ = true;
}

WindowActivationReceiver::~WindowActivationReceiver()
{
    if (installed_) {
        if (auto *app = QCoreApplication::instance()) app->removeNativeEventFilter(this);
    }
}

bool WindowActivationReceiver::nativeEventFilter(const QByteArray &eventType,
                                                  void *message, qintptr *result)
{
    if (!installed_ || eventType != QByteArrayLiteral("windows_generic_MSG") || !message) return false;
    const auto *native = static_cast<const MSG *>(message);
    if (native->message != messageId_) return false;
    if (result) *result = 0;
    if (native->wParam != activation::kActivate && native->wParam != activation::kQueryVisible) return true;
    DWORD pid = 0;
    if (!native->hwnd || !GetWindowThreadProcessId(native->hwnd, &pid)
        || pid != GetCurrentProcessId()) return true;
    for (QWindow *window : QGuiApplication::topLevelWindows()) {
        if (!window || window->objectName() != QStringLiteral("MainMaxWindow")
            || reinterpret_cast<HWND>(window->winId()) != native->hwnd) continue;
        if (native->wParam == activation::kQueryVisible) {
            if (result && window->isVisible()) *result = static_cast<qintptr>(activation::kAck);
        } else if (QMetaObject::invokeMethod(window, "forceActivateWindow", Qt::QueuedConnection)) {
            if (result) *result = static_cast<qintptr>(activation::kAck);
        }
        return true;
    }
    return true;
}
}
