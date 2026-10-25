#pragma once
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace valmax::hooks {
enum class Error { None, InvalidSpecification, ModuleMismatch, HookFailed, Busy };
struct Guard { const void *address; std::span<const uint8_t> expected; };
struct Replacement { Guard guard; void *callback; void **original; void *data = nullptr; };

// In-process, C-only interception. No JavaScript engine, host connection, network,
// profile discovery, or argument recording. The feature owner validates the full
// module hash/loaded path/layout before supplying exact instruction guards here.
// Hooks and their callbacks must outlive calls on instrumented threads. Mutating
// this set requires the feature's startup/configuration lock; never from a hook.
// reset() may return false while a callback still owns its original trampoline.
// Retry reset() until true before destroying the feature/callback state. During
// that drain, install() returns Busy and original slots remain unchanged.
class HookSet final {
public:
    HookSet();
    ~HookSet();
    HookSet(const HookSet &) = delete;
    HookSet &operator=(const HookSet &) = delete;
    Error install(std::span<const Replacement> replacements);
    bool reset();
    size_t size() const { return addresses_.size(); }
    static bool matches(const Guard &guard);
private:
    void *interceptor_ = nullptr;
    std::vector<void *> addresses_;
    std::vector<void **> originals_;
    bool draining_ = false;
};
} // namespace valmax::hooks
