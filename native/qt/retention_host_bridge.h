#pragma once

#include <QByteArray>
#include <QString>
#include <QVariantMap>
#include <mutex>
#include <utility>

struct ValmaxRuntime;

namespace valmax {

// Trusted, worker-thread-only binding between the process-wide passive hook
// and one profile-scoped Rust Runtime. No QML methods or private row data.
class RetentionHostBridge final {
public:
    RetentionHostBridge(ValmaxRuntime *runtime, QString profile)
        : runtime_(runtime), profile_(std::move(profile)) {}
    ~RetentionHostBridge();
    RetentionHostBridge(const RetentionHostBridge &) = delete;
    RetentionHostBridge &operator=(const RetentionHostBridge &) = delete;

    void initialize();
    void refresh();
    // Only bounded aggregate counters; returns an empty map for another profile.
    QVariantMap diagnostics() const;

    struct Change final {
        bool selected = false;
        bool enabled = false;
        bool proceed = true;
        std::unique_lock<std::mutex> lock;
    };
    // For disable, close native admission before the Rust preference CAS.
    // The lock spans dispatch and completion so polls cannot reorder ACKs.
    Change before_dispatch(const QByteArray &request);
    void after_dispatch(Change &change, const QByteArray &response);

private:
    bool read_saved(bool &enabled, QByteArray &revision) const;
    bool profile_matches() const;
    void publish() const;
    ValmaxRuntime *runtime_;
    QString profile_;
};

} // namespace valmax
