#pragma once
#include "ghost.h"
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <optional>
#include <span>
#include <unordered_map>
#include <windows.h>
namespace valmax::ghost {
struct CodePin { uint32_t rva; std::span<const uint8_t> bytes; };
struct SlotPin { uint32_t rva, target; };
struct ImagePin {
    const char *sha256;
    uint64_t file_size;
    uint32_t image_size;
    std::span<const CodePin> code;
    std::span<const SlotPin> slots;
};
struct VerifiedImage {
    HMODULE module = nullptr;
    HANDLE file = INVALID_HANDLE_VALUE;
    uintptr_t base = 0;
    uint32_t size = 0;
    VerifiedImage() = default;
    ~VerifiedImage();
    VerifiedImage(const VerifiedImage &) = delete;
    VerifiedImage &operator=(const VerifiedImage &) = delete;
    void release();
};
bool read_memory(uintptr_t address, void *output, size_t size) noexcept;
template<class T> bool read(uintptr_t address, T &output) noexcept {
    return read_memory(address, &output, sizeof(T));
}
bool validate_image(const wchar_t *path, const ImagePin &pin, VerifiedImage &image);
bool validate_loaded(const VerifiedImage &image, const ImagePin &pin);
struct Targets {
    std::array<void *, 5> replacements{}; // typing/read/read-selector/stories/login
    std::array<void *, 7> probes{}; // ping/decrement/incoming/missing/count/dtor/delete
    void *encoder = nullptr;
    void *self_id = nullptr;
    uintptr_t core = 0;
};
#ifdef VALMAX_GHOST_TESTING
uint32_t install_synthetic(const Targets &targets);
void set_synthetic_after_flush_probe(void (*probe)()) noexcept;
void synthetic_probe(size_t site, void *cpu_context, uint32_t thread, bool leaving = false);
#endif
void increment(std::atomic<uint64_t> &counter) noexcept;
inline constexpr uint64_t kCounterLimit = 1000000;
inline constexpr int64_t kMaxCount = 0x7fffffff;

// A strictly bounded, session-only ledger. Identifiers never leave this class
// except in transient callback frames. Overflow invalidates credits instead of
// guessing an unread value. The caller serializes access with Runtime::mutex.
class UnreadLedger {
public:
    static constexpr size_t kOwners = 64, kChats = 4096;
    struct Credit { int64_t amount, base, id, cursor, raw; };
    struct Frame {
        uint64_t epoch = 0;
        uintptr_t owner = 0, chat_ptr = 0, state = 0;
        int64_t chat = 0;
        std::optional<int64_t> server_cursor;
        std::optional<Credit> credit;
    };
    uint64_t epoch() const { return epoch_; }
    void clear();
    void retire(uintptr_t owner, std::optional<int64_t> chat = std::nullopt);
    Frame begin(uintptr_t owner, uintptr_t chat_ptr, uintptr_t state, int64_t chat,
                std::optional<int64_t> cursor, int32_t raw, bool deleted,
                int64_t state_id, int64_t state_cursor);
    void decrement(uintptr_t owner, int64_t chat, int64_t id, int64_t cursor,
                   uint64_t before, uint64_t after);
    std::optional<int64_t> count(Frame &frame, int32_t raw);
    size_t owner_count() const;
    size_t entry_count() const;
private:
    struct Entry { std::optional<int64_t> cursor; int32_t raw = 0; std::optional<Credit> credit; };
    struct Owner { uintptr_t identity = 0; std::unordered_map<int64_t, Entry> entries; };
    Entry *entry(uintptr_t owner, int64_t chat, bool create);
    std::array<Owner, kOwners> owners_{};
    uint64_t epoch_ = 1;
};
}
