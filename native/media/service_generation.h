#pragma once
#include <atomic>
#include <cstdint>
#include <memory>

namespace valmax::media {
// Native-only cancellation capability, independent of QObject lifetime. The
// facade alone changes the counter. Values never repeat; zero means revoked.
// Check again on the GUI thread immediately before publication/native dispatch.
struct SearchGeneration final {
    std::shared_ptr<const std::atomic<uint64_t>> current;
    uint64_t captured = 0;
    bool valid() const noexcept {
        return current && captured != 0 && current->load(std::memory_order_acquire) == captured;
    }
};
} // namespace valmax::media
