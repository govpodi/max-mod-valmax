#pragma once

#include "retention_async_worker.h"

#include <array>
#include <cstdint>
#include <string_view>

namespace valmax::retention::passive {

enum class HostError : uint32_t {
    None = 0, NotInstalled, InvalidConfig, ImageMismatch, HookFailed,
    Store, OwnerScope, Worker, Shutdown
};

struct NativeStatus final {
    bool installed = false;
    bool images_exact = false;
    bool owner_join = false;
    bool store_ready = false;
    bool preference_enabled = false;
    bool applied = false;
    uint32_t listeners = 0;
    uint32_t gates = 0;
    uint64_t generation = 0;
    uint32_t active_owners = 0;
    uint64_t owner_constructed = 0;
    uint64_t joined_upserts = 0;
    uint64_t route_failures = 0;
    uint64_t bounded_skips = 0;
    uint64_t invalid_history_callbacks = 0;
    uint64_t no_owner_callbacks = 0;
    uint64_t invalid_owner_callbacks = 0;
    uint64_t unmatched_owner_callbacks = 0;
    std::array<char,64> revision{};
    HostError error = HostError::NotInstalled;
    pilot::AsyncCounters worker{};
    bool infrastructure_ready() const noexcept {
        return installed && images_exact && owner_join && store_ready
            && listeners == 3 && gates == 0 && error == HostError::None
            && worker.available && worker.error == pilot::AsyncError::None;
    }
    bool capture_enabled() const noexcept {
        return infrastructure_ready() && preference_enabled && worker.ready;
    }
};

// Call from the native QML plugin's initializeEngine before MAX account owner
// constructors. The caller passes only the trusted launcher profile and exact
// loaded core/storage image paths. Pins include full file hashes and code/slot
// guards. A failed install is terminal for this process.
bool install_early(std::wstring_view profile, std::wstring_view core_path,
                   std::wstring_view storage_path) noexcept;
bool profile_matches(std::wstring_view profile) noexcept;

// Call away from the UI thread after the Rust Runtime has opened. This only
// touches the explicit guarded profile. It does not enable capture by itself.
bool preflight() noexcept;

// Apply a confirmed preference snapshot using an increasing generation.
// Queued capture/mark jobs drain after disable; no new work is accepted.
bool set_enabled(bool enabled, std::string_view revision,
                 uint64_t generation) noexcept;

// Counts and finite status only. No path, account, chat, message ID or text.
NativeStatus status() noexcept;

// Process shutdown or isolated fixture only. An ordinary QML engine teardown
// must not detach the process-wide owner listener used by other engines.
void shutdown() noexcept;

#ifdef VALMAX_RETENTION_HOST_TESTING
struct TestTargets final {
    void *owner_create = nullptr;
    void *owner_destroy = nullptr;
    void *rows = nullptr;
    uintptr_t owner_vtable = 0;
    uintptr_t data_vtable = 0;
    uintptr_t history_owner_vtable = 0;
    uintptr_t factory_vtable = 0;
    uintptr_t chat_history_vtable = 0;
};
bool install_synthetic(std::wstring_view profile, const TestTargets &targets,
                       pilot::AsyncBackend backend) noexcept;
bool synthetic_store_ready() noexcept;
#endif

} // namespace valmax::retention::passive
