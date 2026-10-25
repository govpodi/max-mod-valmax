#include "ghost_internal.h"
#include <limits>
namespace valmax::ghost {
void increment(std::atomic<uint64_t> &counter) noexcept {
    auto value = counter.load(std::memory_order_relaxed);
    while (value < kCounterLimit && !counter.compare_exchange_weak(value, value + 1, std::memory_order_relaxed)) {}
}
void UnreadLedger::clear() {
    ++epoch_;
    if (epoch_ == 0) ++epoch_;
    for (auto &owner : owners_) { owner.identity = 0; owner.entries.clear(); }
}
UnreadLedger::Entry *UnreadLedger::entry(uintptr_t owner, int64_t chat, bool create) {
    if (owner == 0) return nullptr;
    Owner *empty = nullptr;
    for (auto &item : owners_) {
        if (item.identity == owner) {
            auto found = item.entries.find(chat);
            if (found != item.entries.end()) return &found->second;
            if (!create) return nullptr;
            if (item.entries.size() == kChats) {
                // Invalidate every frame before discarding any baseline.
                clear(); return entry(owner, chat, true);
            }
            return &item.entries.try_emplace(chat).first->second;
        }
        if (item.identity == 0 && empty == nullptr) empty = &item;
    }
    if (!create) return nullptr;
    if (empty == nullptr) { clear(); empty = &owners_[0]; }
    empty->identity = owner;
    return &empty->entries.try_emplace(chat).first->second;
}
void UnreadLedger::retire(uintptr_t owner, std::optional<int64_t> chat) {
    for (auto &item : owners_) if (item.identity == owner) {
        if (chat) item.entries.erase(*chat);
        else { item.entries.clear(); item.identity = 0; }
        // In-flight frames may refer to a retired owner/chat. Conservative epoch
        // invalidation is harmless for other chats and prevents pointer reuse.
        ++epoch_; if (epoch_ == 0) ++epoch_;
        return;
    }
}
UnreadLedger::Frame UnreadLedger::begin(uintptr_t owner, uintptr_t chat_ptr, uintptr_t state,
    int64_t chat, std::optional<int64_t> cursor, int32_t raw, bool deleted,
    int64_t state_id, int64_t state_cursor) {
    auto *value = entry(owner, chat, cursor.has_value() && raw >= 0);
    if (value && cursor && raw >= 0) { value->cursor = cursor; value->raw = raw; }
    if (value && value->credit) {
        const auto &credit = *value->credit;
        if (deleted || !cursor || *cursor != credit.base || raw < credit.raw
            || state_id != credit.id || state_cursor != credit.cursor) value->credit.reset();
    }
    return {epoch_, owner, chat_ptr, state, chat, cursor, value ? value->credit : std::nullopt};
}
void UnreadLedger::decrement(uintptr_t owner, int64_t chat, int64_t id, int64_t cursor,
    uint64_t before, uint64_t after) {
    if (before > kMaxCount || after > kMaxCount || after >= before) return;
    auto *value = entry(owner, chat, false);
    if (!value || !value->cursor || cursor <= *value->cursor) return;
    const auto &credit = value->credit;
    if (credit && (credit->base != *value->cursor || cursor <= credit->cursor)) return;
    const auto amount = (credit ? credit->amount : 0) + static_cast<int64_t>(before - after);
    if (amount > kMaxCount) { value->credit.reset(); return; }
    value->credit = Credit{amount, *value->cursor, id, cursor, value->raw};
}
std::optional<int64_t> UnreadLedger::count(Frame &frame, int32_t raw) {
    if (frame.epoch != epoch_) return std::nullopt;
    auto *value = entry(frame.owner, frame.chat, false);
    if (raw < 0) { if (value) value->credit.reset(); return std::nullopt; }
    if (value && frame.server_cursor) { value->cursor = frame.server_cursor; value->raw = raw; }
    if (!frame.credit || !value || !value->credit) return std::nullopt;
    if (raw < frame.credit->raw || value->credit->cursor != frame.credit->cursor
        || value->credit->id != frame.credit->id || value->credit->base != frame.credit->base) {
        value->credit.reset(); frame.credit.reset(); return std::nullopt;
    }
    value->credit->raw = raw;
    return value->credit->amount;
}
size_t UnreadLedger::owner_count() const {
    size_t total = 0; for (const auto &owner : owners_) if (owner.identity != 0) ++total; return total;
}
size_t UnreadLedger::entry_count() const {
    size_t total = 0; for (const auto &owner : owners_) total += owner.entries.size(); return total;
}
}
