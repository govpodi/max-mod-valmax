#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace valmax::retention::rows {

// Exact immutable MAX MeTypes::Message layout. This codec installs no hook and
// makes no delete-policy decision. Its caller must pin the MAX images, validate
// the owner chain and write mode, and keep the borrowed vector alive.
inline constexpr size_t kMessageBytes = 0x1a8;
inline constexpr size_t kMaxTextBytes = 64 * 1024;
inline constexpr size_t kMaxBatchRows = 128;
using ReadMemory = bool (*)(uintptr_t address, void *output, size_t size, void *context) noexcept;

class SecureText final {
public:
    SecureText() = default;
    SecureText(const SecureText &) = delete;
    SecureText &operator=(const SecureText &) = delete;
    SecureText(SecureText &&other) noexcept;
    SecureText &operator=(SecureText &&other) noexcept;
    ~SecureText();
    bool assign(const uint8_t *bytes, size_t size) noexcept;
    void clear() noexcept;
    std::span<const uint8_t> bytes() const noexcept { return value_; }
private:
    std::vector<uint8_t> value_;
};

enum class Kind : uint8_t { Ignore, Text, Removed };
struct Snapshot final {
    Kind kind = Kind::Ignore;
    int64_t id = 0;
    int64_t order_key = 0;
    int64_t sender = 0;
    uint32_t status = 0;
    SecureText text;
};
struct OwnerScope final {
    int64_t owner = 0;
    uintptr_t database = 0;
    uintptr_t control = 0;
};
struct Route final { int64_t owner = 0; int64_t chat = 0; };

// A route is accepted only when one positive canonical owner matches both
// components of the shared DB pair.
bool unique_route(std::span<const OwnerScope> owners, uintptr_t database,
                  uintptr_t control, int64_t chat, Route &output) noexcept;

// Copies only a bounded text candidate into owned, zeroized memory. A
// zero-length normal/edited row and an unknown status become Ignore. A removed
// row carries no text; the caller determines whether it was deleted for all.
bool decode(uintptr_t row, ReadMemory read, void *context, Snapshot &output) noexcept;

// Verify a zero-sender normal/edited row is the codec's empty Ignore case.
// Reads one zeroized row snapshot; no heap text or SecureText is copied.
bool empty_zero_sender_ignore(uintptr_t row, uint32_t expected_status,
                              ReadMemory read, void *context) noexcept;

} // namespace valmax::retention::rows
