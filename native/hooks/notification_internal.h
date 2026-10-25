#pragma once
#include "notification_api.h"
#include "ghost_internal.h" // readonly PE/memory validation, not ghost runtime
#include <array>
#include <atomic>
#include <mutex>
#include <string>
#include <vector>
namespace valmax::notifications {
using ghost::CodePin; using ghost::SlotPin; using ghost::ImagePin; using ghost::VerifiedImage;
using ghost::read; using ghost::read_memory;
constexpr size_t kScopes = 256, kOwners = 64, kDataSize = 328;
constexpr uint64_t kCounterLimit = 1000000;
struct Targets {
    std::array<void *, 6> replacements{};
    std::array<void *, 2> listeners{};
    void *data_copy = nullptr, *string_copy = nullptr, *string_destroy = nullptr, *header_destroy = nullptr;
    uintptr_t marker = 0, identity_vtable = 0;
};
struct OwnerMap {
    struct Entry { uint64_t native = 0; int64_t owner = 0; bool used = false, ambiguous = false; };
    std::array<Entry, kOwners> entries{};
    bool overflow = false;
    void remember(uint64_t native, int64_t owner);
    int64_t resolve(uint64_t native) const;
};
enum class Decision { Pass, Mask, Suppress };
bool revision(const ValmaxNotificationScope *scopes, size_t count, char (&output)[64]);
bool valid_strings(const void *data);
bool clone_call(const Targets &, const char *image, void *original, void *self,
                void *data, bool with_flag, uint8_t flag, bool &cleanup_failed);
struct Asset {
    HANDLE file = INVALID_HANDLE_VALUE;
    std::vector<HANDLE> directories;
    std::string utf8;
    void release();
};
bool validate_asset(const wchar_t *path, const wchar_t *main_path, Asset &asset);
// All mutations below are serialized by the runtime policy lock. No foreign
// functions are called by the policy model.
struct PolicyState {
    ValmaxNotificationPolicy policy{};
    std::array<ValmaxNotificationScope, kScopes> scopes{};
    OwnerMap owners;
    uint64_t generation = 0, deadline = 0;
    bool known = false, fresh = false;
    uint32_t update(const ValmaxNotificationPolicy &, const ValmaxNotificationScope *, uint64_t now);
    Decision select(bool data_valid, uint64_t native, int64_t chat) const;
    bool leased(uint64_t now) const { return fresh && known && now < deadline; }
};
#ifdef VALMAX_NOTIFICATION_TESTING
uint32_t install_synthetic(const Targets &, const char *image);
void set_synthetic_after_flush_probe(void (*probe)()) noexcept;
void synthetic_remember(uint64_t native, int64_t owner);
void synthetic_clock(uint64_t now);
void synthetic_identity(void *context, int property);
#endif
}
