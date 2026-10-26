#include "retention_row_codec.h"

#include <array>
#include <cstring>
#include <utility>

namespace valmax::retention::rows {
namespace {
constexpr size_t kIdOffset = 0x00;
// Pinned storage binder maps MeTypes::Message+0x18 to SQL `time`.
constexpr size_t kOrderKeyOffset = 0x18;
constexpr size_t kTextOffset = 0x30;
constexpr size_t kTextLengthOffset = 0x40;
constexpr size_t kTextCapacityOffset = 0x48;
constexpr size_t kSenderOffset = 0x80;
constexpr size_t kStatusOffset = 0x128;
constexpr uint64_t kInlineCapacity = 15;
constexpr uint64_t kMaximumCapacity = 1024 * 1024;

void erase(void *address, size_t size) noexcept {
    auto *bytes = static_cast<volatile uint8_t *>(address);
    for (size_t i = 0; i < size; ++i) bytes[i] = 0;
}
struct RawRow final {
    std::array<uint8_t,kMessageBytes> bytes{};
    ~RawRow() { erase(bytes.data(),bytes.size()); }
};
template <typename T>
T field(const RawRow &row, size_t offset) noexcept {
    T value{};
    std::memcpy(&value,row.bytes.data()+offset,sizeof(value));
    return value;
}
bool valid_text_shape(uint64_t size, uint64_t capacity) noexcept {
    return size <= kMaxTextBytes && capacity >= size && capacity <= kMaximumCapacity
        && (capacity >= 16 || capacity == kInlineCapacity);
}
}

SecureText::SecureText(SecureText &&other) noexcept : value_(std::move(other.value_)) {}
SecureText &SecureText::operator=(SecureText &&other) noexcept {
    if (this != &other) { clear(); value_ = std::move(other.value_); }
    return *this;
}
SecureText::~SecureText() { clear(); }
bool SecureText::assign(const uint8_t *bytes, size_t size) noexcept {
    clear();
    if (!bytes || size == 0 || size > kMaxTextBytes) return false;
    try { value_.assign(bytes,bytes+size); return true; }
    catch (...) { clear(); return false; }
}
void SecureText::clear() noexcept {
    if (!value_.empty()) erase(value_.data(),value_.size());
    value_.clear();
}

bool unique_route(std::span<const OwnerScope> owners, uintptr_t database,
                  uintptr_t control, int64_t chat, Route &output) noexcept {
    output = {};
    if (database == 0 || control == 0 || chat == 0 || owners.size() > 128) return false;
    size_t matches = 0;
    int64_t owner = 0;
    for (const auto &candidate : owners) {
        if (candidate.database != database || candidate.control != control) continue;
        if (candidate.owner <= 0 || ++matches != 1) return false;
        owner = candidate.owner;
    }
    if (matches != 1) return false;
    output = {owner,chat};
    return true;
}

InvalidSenderReason inspect_invalid_sender(uintptr_t address, uint32_t expected_status,
                                           int64_t expected_sender, ReadMemory read,
                                           void *context) noexcept {
    if (expected_status > 1 || expected_sender > 0)
        return InvalidSenderReason::HeaderMismatch;
    if (address == 0 || !read || address > UINTPTR_MAX-kMessageBytes)
        return InvalidSenderReason::UnreadableRow;
    RawRow row;
    if (!read(address,row.bytes.data(),row.bytes.size(),context))
        return InvalidSenderReason::UnreadableRow;
    const auto sender = field<int64_t>(row,kSenderOffset);
    const auto status = field<uint32_t>(row,kStatusOffset);
    if (sender != expected_sender || status != expected_status)
        return InvalidSenderReason::HeaderMismatch;
    if (sender < 0) return InvalidSenderReason::NegativeSender;
    if (field<int64_t>(row,kIdOffset) <= 0) return InvalidSenderReason::InvalidId;
    const auto size = field<uint64_t>(row,kTextLengthOffset);
    const auto capacity = field<uint64_t>(row,kTextCapacityOffset);
    if (!valid_text_shape(size,capacity)) return InvalidSenderReason::InvalidTextShape;
    if (size != 0) {
        // Check pointer metadata without dereferencing heap text or retaining
        // decoded content. A malformed heap string remains terminal.
        if (capacity != kInlineCapacity) {
            const auto text = field<uintptr_t>(row,kTextOffset);
            if (text == 0 || text > UINTPTR_MAX-size)
                return InvalidSenderReason::InvalidTextShape;
        }
        return InvalidSenderReason::ZeroSenderNonempty;
    }
    return InvalidSenderReason::EmptyIgnore;
}

bool decode(uintptr_t address, ReadMemory read, void *context, Snapshot &output) noexcept {
    output.text.clear();
    output.kind = Kind::Ignore; output.id = 0; output.order_key = 0;
    output.sender = 0; output.status = 0;
    if (address == 0 || !read || address > UINTPTR_MAX-kMessageBytes) return false;
    RawRow row;
    if (!read(address,row.bytes.data(),row.bytes.size(),context)) return false;
    const int64_t id = field<int64_t>(row,kIdOffset);
    const int64_t sender = field<int64_t>(row,kSenderOffset);
    const uint32_t status = field<uint32_t>(row,kStatusOffset);
    if (id <= 0 || sender < 0) return false;
    if (status > 2) return true;
    if (status == 2) {
        output.id = id; output.sender = sender; output.status = status;
        output.kind = Kind::Removed;
        return true;
    }
    const uint64_t size = field<uint64_t>(row,kTextLengthOffset);
    const uint64_t capacity = field<uint64_t>(row,kTextCapacityOffset);
    if (!valid_text_shape(size,capacity)) return false;
    if (size == 0) return true;
    const int64_t order_key = field<int64_t>(row,kOrderKeyOffset);
    if (capacity == kInlineCapacity) {
        if (size > kInlineCapacity || !output.text.assign(row.bytes.data()+kTextOffset,
                                                        static_cast<size_t>(size))) return false;
    } else {
        const uintptr_t text = field<uintptr_t>(row,kTextOffset);
        if (text == 0 || text > UINTPTR_MAX-size) return false;
        std::vector<uint8_t> buffer;
        try { buffer.resize(static_cast<size_t>(size)); }
        catch (...) { return false; }
        const bool copied = read(text,buffer.data(),buffer.size(),context)
            && output.text.assign(buffer.data(),buffer.size());
        erase(buffer.data(),buffer.size());
        if (!copied) return false;
    }
    output.id = id; output.order_key = order_key > 0 ? order_key : 0;
    output.sender = sender; output.status = status;
    output.kind = Kind::Text;
    return true;
}

} // namespace valmax::retention::rows
