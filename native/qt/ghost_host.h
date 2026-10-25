#pragma once
#include <QString>
#include <chrono>
struct ValmaxRuntime;

namespace valmax {
// Host-only lifecycle. Never registered as a QML type or invokable method.
// Call refresh serially on the bridge worker pool; destroy after workers stop.
class GhostHost final {
public:
    explicit GhostHost(ValmaxRuntime *runtime);
    ~GhostHost();
    QString refresh();
    GhostHost(const GhostHost &) = delete;
    GhostHost &operator=(const GhostHost &) = delete;
private:
    ValmaxRuntime *runtime_;
    bool lease_ = false;
    bool installed_ = false;
    bool attempted_ = false;
    unsigned installAttempts_ = 0;
    std::chrono::steady_clock::time_point retryAt_{};
};
}
