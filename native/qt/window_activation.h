#pragma once
#include <QAbstractNativeEventFilter>
#include <cstdint>

namespace valmax {
class WindowActivationReceiver final : public QAbstractNativeEventFilter {
public:
    WindowActivationReceiver();
    ~WindowActivationReceiver() override;
    WindowActivationReceiver(const WindowActivationReceiver &) = delete;
    WindowActivationReceiver &operator=(const WindowActivationReceiver &) = delete;
    bool nativeEventFilter(const QByteArray &eventType, void *message, qintptr *result) override;
    bool installed() const noexcept { return installed_; }
private:
    uint32_t messageId_ = 0;
    bool installed_ = false;
};
}
