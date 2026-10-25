#pragma once
#include <QString>
#include <cstdint>
#include <chrono>
#include <mutex>
#include <thread>
struct ValmaxRuntime;

namespace valmax {
// Trusted host only. Not registered in QML; paths and hook receipts are never
// selected by a page. Rust owns the store -> registry -> host lock ordering.
class NotificationHost final {
public:
    explicit NotificationHost(ValmaxRuntime *runtime);
    ~NotificationHost();
    QString refresh();
    NotificationHost(const NotificationHost &) = delete;
    NotificationHost &operator=(const NotificationHost &) = delete;
private:
    static int32_t prepare(void *, const uint8_t *, size_t, uint64_t *);
    static void release(void *, uint64_t);
    int32_t prepareCatalog(const uint8_t *, size_t, uint64_t *);
    bool install();
    void revokeFreshness();
    ValmaxRuntime *runtime_;
    std::mutex transaction_;
    bool attached_ = false;
    bool owned_ = false;
    bool attempted_ = false;
    bool installed_ = false;
    unsigned installAttempts_ = 0;
    std::chrono::steady_clock::time_point retryAt_{};
    uint64_t generation_ = 0;
    uint64_t lease_ = 0;
    std::thread::id leaseThread_;
};
}
