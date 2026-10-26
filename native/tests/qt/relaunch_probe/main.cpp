#include "../../../qt/window_activation.h"
#include "../../../common/window_activation.h"
#include <QCoreApplication>
#include <QGuiApplication>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QWindow>
#include <windows.h>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <thread>

namespace {
void check(bool ok, const char *what)
{
    if (!ok) {
        std::fprintf(stderr, "FAIL %s\n", what);
        std::exit(1);
    }
}
void settle()
{
    for (int i = 0; i < 30; ++i) {
        QCoreApplication::processEvents();
        Sleep(10);
    }
}
uintptr_t send(HWND window, uintptr_t mode)
{
    std::atomic<bool> done = false;
    uintptr_t reply = 0;
    std::thread requester([&] {
        const UINT message = RegisterWindowMessageW(valmax::activation::kMessageName);
        DWORD_PTR nativeReply = 0;
        const bool delivered = message && SendMessageTimeoutW(window, message, mode, 0,
            SMTO_ABORTIFHUNG | SMTO_BLOCK, 500, &nativeReply) != 0;
        reply = delivered ? static_cast<uintptr_t>(nativeReply) : 0;
        done = true;
    });
    const auto deadline = GetTickCount64() + 1000;
    while (!done && GetTickCount64() < deadline) {
        QCoreApplication::processEvents();
        Sleep(1);
    }
    requester.join();
    return reply;
}
}

int main(int argc, char **argv)
{
    QGuiApplication app(argc, argv);
    app.setQuitOnLastWindowClosed(false);
    valmax::WindowActivationReceiver receiver;
    check(receiver.installed(), "Qt receiver installed");

    QQmlEngine engine;
    QQmlComponent component(&engine);
    component.setData(R"(
import QtQuick
Window {
    objectName: "MainMaxWindow"
    x: -3000; y: -3000; width: 100; height: 100; visible: true
    property bool rowEligible: visible
    function forceActivateWindow() { showNormal(); raise(); requestActivate(); }
}
)", QUrl());
    check(component.isReady(), "synthetic QML loads");
    std::unique_ptr<QObject> object(component.create());
    auto *window = qobject_cast<QWindow *>(object.get());
    check(window != nullptr, "synthetic main window exists");
    settle();
    const auto hwnd = reinterpret_cast<HWND>(window->winId());
    check(window->isVisible() && IsWindowVisible(hwnd), "initial Qt and Win32 visibility");

    window->hide();
    settle();
    check(!window->isVisible() && !IsWindowVisible(hwnd), "tray-style hide");
    check(send(hwnd, valmax::activation::kQueryVisible) == 0, "hidden Qt state rejected");
    check(send(hwnd, valmax::activation::kActivate) == valmax::activation::kAck,
        "launcher request acknowledged");
    settle();
    check(window->isVisible() && IsWindowVisible(hwnd) && object->property("rowEligible").toBool(),
        "Qt and chat-row eligibility restored");
    check(send(hwnd, valmax::activation::kQueryVisible) == valmax::activation::kAck,
        "Qt logical visibility confirmed");

    window->hide();
    settle();
    std::thread legacy([hwnd] { ShowWindowAsync(hwnd, SW_SHOW); });
    legacy.join();
    settle();
    check(IsWindowVisible(hwnd) && !window->isVisible() && !object->property("rowEligible").toBool(),
        "legacy launcher reproduces blank chat rows");
    check(send(hwnd, valmax::activation::kQueryVisible) == 0,
        "native visibility cannot claim Qt readiness");
    check(send(hwnd, valmax::activation::kActivate) == valmax::activation::kAck,
        "already-visible HWND still gets Qt activation");
    settle();
    check(window->isVisible() && object->property("rowEligible").toBool(),
        "already-visible HWND recovers chat rows");
    window->hide();
    std::puts("{\"passed\":true,\"syntheticQtOnly\":true,\"realProfileTouched\":false}");
    return 0;
}
