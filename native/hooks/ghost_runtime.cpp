#include "ghost_internal.h"
#include "ghost_pins.generated.h"
#include "frida-gum.h"
#include <algorithm>
#include <cstring>
#include <string>
#include <utility>

namespace valmax::ghost {
namespace {
constexpr uint32_t kEnabled = 1, kTyping = 2, kRead = 4, kOnline = 8, kStories = 16;
uint32_t mask(const ValmaxGhostPolicy &policy) {
    return (policy.enabled ? kEnabled : 0) | (policy.hide_typing ? kTyping : 0)
        | (policy.hide_read ? kRead : 0) | (policy.hide_online ? kOnline : 0) | (policy.hide_stories ? kStories : 0);
}
using IdCall = void (*)(void *, int64_t, int32_t);
using PairCall = void (*)(void *, int64_t, void *);
using StoryCall = void (*)(void *, void *, int64_t);
using BoolCall = void (*)(void *, void *);
using SelfCall = void *(*)(void *, void *);
struct ThreadSlot {
    uint32_t thread = 0;
    size_t depth = 0, overflow = 0;
    std::array<UnreadLedger::Frame, 16> frames{};
    alignas(8) std::array<uint8_t, 0x40> scratch{};
};
struct Marker { ThreadSlot *slot = nullptr; size_t depth = 0; bool overflow = false; };
struct Runtime {
    std::mutex lifecycle, mutex;
    std::atomic<bool> ready{false};
    // The hook transaction can publish callbacks before readiness is reported.
    // Keep the requested policy armed throughout that interval and until detach
    // has flushed every callback.
    std::atomic<bool> callbacksArmed{false};
    std::atomic<uint32_t> flags{0};
    std::atomic<uint32_t> error{VALMAX_GHOST_NOT_READY};
    std::array<std::atomic<uint64_t>, 5> counters{};
    ValmaxGhostPolicy policy{sizeof(ValmaxGhostPolicy), 1, {}, 0, 0, 0, 0, 0, {}};
    UnreadLedger ledger;
    std::array<ThreadSlot, 64> threads{};
    Targets targets;
    std::array<void *, 5> originals{};
    std::array<GumInvocationListener *, 7> listeners{};
    size_t replaced = 0, attached = 0;
    GumInterceptor *interceptor = nullptr;
    VerifiedImage core, main;
    bool pinned = false, draining = false, installed = false;
    Runtime() { std::fill(std::begin(policy.revision), std::end(policy.revision), '0'); }
    bool active(uint32_t feature) const noexcept {
        return callbacksArmed.load(std::memory_order_acquire)
            && (flags.load(std::memory_order_acquire) & (kEnabled | feature)) == (kEnabled | feature);
    }
    ThreadSlot *slot(uint32_t thread) {
        if (thread == 0) return nullptr;
        ThreadSlot *empty = nullptr;
        for (auto &value : threads) {
            if (value.thread == thread) return &value;
            if (!value.thread && !empty) empty = &value;
        }
        if (empty) empty->thread = thread;
        return empty;
    }
};
// Callbacks, scratch and original trampolines stay reachable for process lifetime.
// No destructor runs under DLL_PROCESS_DETACH / loader lock.
Runtime &runtime() { static Runtime *value = new Runtime; return *value; }
#ifdef VALMAX_GHOST_TESTING
void (*afterFlushProbe)() = nullptr;
#endif
void typing(void *self, int64_t id, int32_t detail) {
    auto &r = runtime();
    if (r.active(kTyping)) { increment(r.counters[0]); return; }
    reinterpret_cast<IdCall>(r.originals[0])(self, id, detail);
}
void read_pair(void *self, int64_t id, void *detail) {
    auto &r = runtime();
    if (r.active(kRead)) { increment(r.counters[1]); return; }
    reinterpret_cast<PairCall>(r.originals[1])(self, id, detail);
}
void read_selector(void *self, int64_t id, int32_t detail) {
    auto &r = runtime();
    if (detail != 1 && r.active(kRead)) { increment(r.counters[1]); return; }
    reinterpret_cast<IdCall>(r.originals[2])(self, id, detail);
}
void stories(void *self, void *pair, int64_t id) {
    auto &r = runtime();
    if (r.active(kStories)) { increment(r.counters[3]); return; }
    reinterpret_cast<StoryCall>(r.originals[3])(self, pair, id);
}
void login(void *self, void *output) {
    auto &r = runtime();
    if (r.active(kOnline)) {
        static uint8_t false_byte = 0;
        reinterpret_cast<BoolCall>(r.targets.encoder)(&false_byte, output);
        increment(r.counters[2]); return;
    }
    reinterpret_cast<BoolCall>(r.originals[4])(self, output);
}
// SEH leaf: foreign model getter can race teardown; never unwind its access
// violation through a C++ mutex guard. All normal pointer reads use RPM.
bool get_self(SelfCall function, uintptr_t reader, void *output) noexcept {
    __try { return function(reinterpret_cast<void *>(reader), output) == output; }
    __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
std::optional<int64_t> server_cursor(Runtime &r, uintptr_t owner, uintptr_t chat) {
    uintptr_t reader = 0, table = 0, target = 0, account = 0;
    if (!read(owner + 0x68, reader) || !read(reader, table) || table != r.targets.core + pins::kReaderVtableRva
        || !read(table + pins::kReaderSlot, target) || target != r.targets.core + pins::kReaderThunkRva
        || !read(reader + pins::kModelOffset, account) || !read(account, table)
        || table != r.targets.core + pins::kModelVtableRva
        || !read(table + pins::kModelSlot, target) || target != r.targets.core + pins::kModelThunkRva) return std::nullopt;
    int64_t id = 0;
    if (!get_self(reinterpret_cast<SelfCall>(r.targets.self_id), reader, &id)) return std::nullopt;
    if (id <= 0) return std::nullopt;
    uintptr_t head = 0, candidate = 0;
    if (!read(chat + 0x18, head) || !read(head + 8, candidate)) return std::nullopt;
    for (size_t depth = 0; depth < 128; ++depth) {
        uint8_t sentinel = 0; int64_t key = 0;
        if (candidate == head || !read(candidate + 0x19, sentinel) || sentinel != 0
            || !read(candidate + 0x20, key)) return std::nullopt;
        if (key == id) { int64_t result = 0; return read(candidate + 0x28, result) ? std::optional(result) : std::nullopt; }
        if (!read(candidate + (key > id ? 0 : 0x10), candidate)) return std::nullopt;
    }
    return std::nullopt;
}
UnreadLedger::Frame *current(Runtime &r, ThreadSlot *slot, const GumCpuContext &cpu) {
    if (!slot || slot->depth == 0 || slot->overflow != 0) return nullptr;
    auto &frame = slot->frames[slot->depth - 1];
    return frame.epoch == r.ledger.epoch() && frame.owner == cpu.rdi
        && frame.chat_ptr == cpu.r15 && frame.state == cpu.r14 ? &frame : nullptr;
}
void on_probe(size_t site, GumCpuContext &cpu, uint32_t thread_id, Marker *marker, bool leaving) noexcept {
    auto &r = runtime();
    try {
        if (site == 0) {
            if (r.active(kOnline)) { cpu.rax &= UINT64_C(0xffffffffffffff00); increment(r.counters[2]); }
            return;
        }
        if (site == 2 && !leaving) {
            if (marker) *marker = {};
            if (!r.active(kRead)) return;
            uint64_t captured_epoch = 0;
            { std::lock_guard guard(r.mutex); captured_epoch = r.ledger.epoch(); }
            int64_t chat = 0, state_id = 0, state_cursor = 0;
            int32_t raw = 0; uint8_t deleted = 0;
            const bool valid_reads = read(cpu.rdx, chat) && read(cpu.rdx + 0x260, raw) && read(cpu.rdx + 0x580, deleted)
                && read(cpu.r8 + 0x18, state_id) && read(cpu.r8 + 0x20, state_cursor);
            // The native getter acquires a MAX model mutex. Never hold our
            // ledger mutex across foreign calls (including a reentrant hook).
            // Policy/account changes during that call invalidate this snapshot.
            auto cursor = valid_reads ? server_cursor(r, cpu.rcx, cpu.rdx) : std::nullopt;
            std::lock_guard guard(r.mutex);
            if (!r.active(kRead) || captured_epoch != r.ledger.epoch()) return;
            auto *slot = r.slot(thread_id);
            if (!slot || !marker) { increment(r.counters[4]); r.ledger.clear(); return; }
            marker->slot = slot;
            if (slot->depth == slot->frames.size() || slot->overflow != 0) {
                ++slot->overflow; marker->overflow = true; increment(r.counters[4]); r.ledger.clear(); return;
            }
            marker->depth = slot->depth;
            auto &frame = slot->frames[slot->depth++]; frame = {};
            if (!valid_reads) { increment(r.counters[4]); r.ledger.clear(); return; }
            frame = r.ledger.begin(cpu.rcx, cpu.rdx, cpu.r8, chat, cursor, raw, deleted != 0, state_id, state_cursor);
            return;
        }
        std::lock_guard guard(r.mutex);
        if (site == 2 && leaving) {
            if (marker && marker->slot) {
                auto &slot = *marker->slot;
                if (marker->overflow) { if (slot.overflow) --slot.overflow; }
                else if (slot.depth == marker->depth + 1) --slot.depth;
                else { slot.depth = 0; slot.overflow = 0; r.ledger.clear(); }
                *marker = {};
            }
            return;
        }
        if (site == 5) { r.ledger.retire(cpu.rcx); return; }
        if (site == 6) {
            if (cpu.rcx >= 8) r.ledger.retire(cpu.rcx - 8, static_cast<int64_t>(cpu.rdx));
            return;
        }
        if (!r.active(kRead)) return;
        if (site == 1) {
            int64_t chat = 0, id = 0, cursor = 0;
            if (read(cpu.rbp + 0x10, chat) && read(cpu.r14, id) && read(cpu.r14 + 8, cursor))
                r.ledger.decrement(cpu.rsi, chat, id, cursor, cpu.rcx, cpu.rbx);
            else { increment(r.counters[4]); r.ledger.clear(); }
            return;
        }
        auto *slot = r.slot(thread_id);
        auto *frame = current(r, slot, cpu);
        if (!frame) return;
        if (site == 3) { cpu.rbx = UINT64_MAX; return; }
        if (site == 4) {
            int32_t raw = 0;
            if (!read(frame->chat_ptr + 0x260, raw)) { increment(r.counters[4]); r.ledger.clear(); cpu.rdx = cpu.r9; return; }
            auto credit = r.ledger.count(*frame, raw);
            if (raw < 0) return;
            if (credit && slot) {
                std::memcpy(slot->scratch.data() + 0x28, &*credit, sizeof(*credit));
                cpu.rdx = reinterpret_cast<uintptr_t>(slot->scratch.data());
            } else cpu.rdx = cpu.r9;
        }
    } catch (...) {
        increment(r.counters[4]);
        // Allocation failure must not escape native callbacks or leave credits
        // that suppress real unread messages. No hook retains a map reference.
        try { std::lock_guard guard(r.mutex); r.ledger.clear(); } catch (...) {}
    }
}
void probe_enter(GumInvocationContext *context, gpointer data) {
    const auto site = reinterpret_cast<uintptr_t>(data);
    Marker *marker = site == 2 ? static_cast<Marker *>(gum_invocation_context_get_listener_invocation_data(context, sizeof(Marker))) : nullptr;
    if (marker) *marker = {};
    on_probe(site, *context->cpu_context, gum_invocation_context_get_thread_id(context), marker, false);
}
void probe_leave(GumInvocationContext *context, gpointer data) {
    const auto site = reinterpret_cast<uintptr_t>(data);
    auto *marker = static_cast<Marker *>(gum_invocation_context_get_listener_invocation_data(context, sizeof(Marker)));
    on_probe(site, *context->cpu_context, gum_invocation_context_get_thread_id(context), marker, true);
}
bool pin_module(uintptr_t address) {
    HMODULE ignored = nullptr;
    return GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN | GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
        reinterpret_cast<const wchar_t *>(address), &ignored) != 0;
}
uint32_t detach(Runtime &r) {
    r.ready.store(false, std::memory_order_release);
    { std::lock_guard guard(r.mutex); r.ledger.clear(); }
    if (!r.interceptor) {
        r.callbacksArmed.store(false, std::memory_order_release);
        r.flags.store(0, std::memory_order_release);
        return VALMAX_GHOST_OK;
    }
    if (!r.draining) {
        gum_interceptor_begin_transaction(r.interceptor);
        for (size_t i = 0; i < r.attached; ++i) gum_interceptor_detach(r.interceptor, r.listeners[i]);
        for (size_t i = r.replaced; i != 0; --i) gum_interceptor_revert(r.interceptor, r.targets.replacements[i - 1]);
        gum_interceptor_end_transaction(r.interceptor);
        r.draining = true;
    }
    if (!gum_interceptor_flush(r.interceptor)) return VALMAX_GHOST_BUSY;
    for (auto &listener : r.listeners) { if (listener) g_object_unref(listener); listener = nullptr; }
    // Trampolines cannot be cleared until flush confirms no active callbacks.
    r.originals.fill(nullptr);
    r.replaced = 0; r.attached = 0; r.draining = false; r.installed = false;
    r.callbacksArmed.store(false, std::memory_order_release);
    r.flags.store(0, std::memory_order_release);
    return VALMAX_GHOST_OK;
}
uint32_t install_targets(Runtime &r, const Targets &targets) {
    if (r.installed || r.draining || r.replaced || r.attached) return VALMAX_GHOST_BUSY;
    for (auto target : targets.replacements) if (!target) return VALMAX_GHOST_ARGUMENT;
    for (auto target : targets.probes) if (!target) return VALMAX_GHOST_ARGUMENT;
    if (!targets.encoder || !targets.self_id || !targets.core) return VALMAX_GHOST_ARGUMENT;
    if (!r.interceptor) { gum_init_embedded(); r.interceptor = gum_interceptor_obtain(); }
    r.targets = targets;
    const std::array<void *, 5> callbacks{reinterpret_cast<void *>(&typing), reinterpret_cast<void *>(&read_pair),
        reinterpret_cast<void *>(&read_selector), reinterpret_cast<void *>(&stories), reinterpret_cast<void *>(&login)};
    for (size_t i = 0; i < r.listeners.size(); ++i) {
        auto data = reinterpret_cast<void *>(i);
        r.listeners[i] = i == 2 ? gum_make_call_listener(probe_enter, probe_leave, data, nullptr)
            : gum_make_probe_listener(probe_enter, data, nullptr);
        if (!r.listeners[i]) { detach(r); return VALMAX_GHOST_HOOK_FAILED; }
    }
    gum_interceptor_begin_transaction(r.interceptor);
    bool ok = true;
    for (size_t i = 0; ok && i < callbacks.size(); ++i) {
        GumReplaceOptions options{};
        ok = gum_interceptor_replace(r.interceptor, targets.replacements[i], callbacks[i], &r.originals[i], &options) == GUM_REPLACE_OK;
        if (ok) ++r.replaced;
    }
    for (size_t i = 0; ok && i < r.listeners.size(); ++i) {
        GumAttachOptions options{};
        ok = gum_interceptor_attach(r.interceptor, targets.probes[i], r.listeners[i], &options) == GUM_ATTACH_OK;
        if (ok) ++r.attached;
    }
    if (!ok) {
        // Revert inside the same transaction: a partial set never becomes live.
        for (size_t i = 0; i < r.attached; ++i) gum_interceptor_detach(r.interceptor, r.listeners[i]);
        for (size_t i = r.replaced; i != 0; --i) gum_interceptor_revert(r.interceptor, targets.replacements[i - 1]);
        r.draining = true;
    } else {
        // A completed transaction may expose callbacks before the following
        // flush/readiness acknowledgement. They must already use this policy.
        std::lock_guard guard(r.mutex);
        r.flags.store(mask(r.policy), std::memory_order_release);
        r.callbacksArmed.store(true, std::memory_order_release);
    }
    gum_interceptor_end_transaction(r.interceptor);
    if (!ok || !gum_interceptor_flush(r.interceptor)) { detach(r); return VALMAX_GHOST_HOOK_FAILED; }
#ifdef VALMAX_GHOST_TESTING
    if (afterFlushProbe) afterFlushProbe();
#endif
    r.installed = true;
    r.error.store(VALMAX_GHOST_OK, std::memory_order_release);
    r.ready.store(true, std::memory_order_release);
    return VALMAX_GHOST_OK;
}
}
#ifdef VALMAX_GHOST_TESTING
uint32_t install_synthetic(const Targets &targets) {
    auto &r = runtime(); std::lock_guard guard(r.lifecycle); return install_targets(r, targets);
}
void set_synthetic_after_flush_probe(void (*probe)()) noexcept { afterFlushProbe = probe; }
void synthetic_probe(size_t site, void *cpu_context, uint32_t thread, bool leaving) {
    static thread_local Marker marker;
    on_probe(site, *static_cast<GumCpuContext *>(cpu_context), thread, site == 2 ? &marker : nullptr, leaving);
}
#endif
}

extern "C" uint32_t valmax_ghost_install(const wchar_t *core_path, const wchar_t *main_path) {
    using namespace valmax::ghost;
    try {
        auto &r = runtime(); std::lock_guard guard(r.lifecycle);
        if (!core_path || !main_path) return VALMAX_GHOST_ARGUMENT;
        if (r.installed || r.draining) return VALMAX_GHOST_BUSY;
        const auto core_length = wcsnlen(core_path, 32768), main_length = wcsnlen(main_path, 32768);
        if (core_length >= 32768 || main_length >= 32768) return VALMAX_GHOST_ARGUMENT;
        std::wstring core(core_path, core_length), main(main_path, main_length);
        std::replace(core.begin(), core.end(), L'/', L'\\'); std::replace(main.begin(), main.end(), L'/', L'\\');
        const auto a = core.find_last_of(L'\\'), b = main.find_last_of(L'\\');
        if (a == std::wstring::npos || b != a || _wcsicmp(core.substr(a + 1).c_str(), L"core.dll") != 0
            || _wcsicmp(main.substr(b + 1).c_str(), L"max.exe") != 0
            || _wcsicmp(core.substr(0, a).c_str(), main.substr(0, b).c_str()) != 0) return VALMAX_GHOST_ARGUMENT;
        // The window/plugin can be created before core.dll has finished
        // loading. Keep that transient state separate from a loaded image at
        // the wrong path, which must remain a permanent module mismatch.
        for (const auto &item : {std::pair{core.c_str(), core.c_str() + a + 1},
                                 std::pair{main.c_str(), main.c_str() + b + 1}}) {
            if (!GetModuleHandleW(item.first)) {
                const auto code = GetModuleHandleW(item.second)
                    ? VALMAX_GHOST_MODULE_MISMATCH : VALMAX_GHOST_NOT_READY;
                r.error.store(code); return code;
            }
        }
        if (!validate_image(core.c_str(), pins::kCore, r.core) || !validate_image(main.c_str(), pins::kMain, r.main)) {
            r.core.release(); r.main.release(); r.error.store(VALMAX_GHOST_MODULE_MISMATCH); return VALMAX_GHOST_MODULE_MISMATCH;
        }
        // Pin before publishing callbacks. File handles remain open read-only;
        // therefore the pinned image bytes cannot be replaced underneath us.
        if (!pin_module(reinterpret_cast<uintptr_t>(&valmax_ghost_install)) || !pin_module(r.core.base) || !pin_module(r.main.base)) {
            r.error.store(VALMAX_GHOST_MODULE_MISMATCH); return VALMAX_GHOST_MODULE_MISMATCH;
        }
        r.pinned = true;
        Targets targets{};
        for (size_t i = 0; i < 4; ++i) targets.replacements[i] = reinterpret_cast<void *>(r.core.base + pins::kWrapperRvas[i]);
        targets.replacements[4] = reinterpret_cast<void *>(r.core.base + pins::kLoginRva);
        targets.probes[0] = reinterpret_cast<void *>(r.core.base + pins::kPingRva);
        const std::array<uint32_t, 6> sites{pins::kDecrementRva, pins::kIncomingRva, pins::kMissingRva,
            pins::kCountRva, pins::kDestructorRva, pins::kChatDeleteRva};
        for (size_t i = 0; i < sites.size(); ++i) targets.probes[i + 1] = reinterpret_cast<void *>(r.main.base + sites[i]);
        targets.encoder = reinterpret_cast<void *>(r.core.base + pins::kEncoderRva);
        targets.self_id = reinterpret_cast<void *>(r.core.base + pins::kGetterRva);
        targets.core = r.core.base;
        const auto result = install_targets(r, targets);
        r.error.store(result);
        return result;
    } catch (...) { return VALMAX_GHOST_HOOK_FAILED; }
}
extern "C" uint32_t valmax_ghost_set_policy(const ValmaxGhostPolicy *policy) {
    using namespace valmax::ghost;
    if (!policy || policy->size != sizeof(ValmaxGhostPolicy) || policy->version != 1
        || policy->enabled > 1 || policy->hide_typing > 1 || policy->hide_read > 1 || policy->hide_online > 1 || policy->hide_stories > 1
        || std::any_of(std::begin(policy->reserved), std::end(policy->reserved), [](uint8_t c) { return c != 0; })
        || std::any_of(std::begin(policy->revision), std::end(policy->revision), [](char c) { return !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')); })) return VALMAX_GHOST_ARGUMENT;
    try {
        auto &r = runtime(); std::lock_guard guard(r.mutex);
        if (std::memcmp(policy->revision, r.policy.revision, 64) == 0 && mask(*policy) != mask(r.policy)) return VALMAX_GHOST_POLICY_CONFLICT;
        if ((r.policy.enabled && r.policy.hide_read) != (policy->enabled && policy->hide_read)) r.ledger.clear();
        r.policy = *policy;
        r.flags.store(mask(*policy), std::memory_order_release);
        return VALMAX_GHOST_OK;
    } catch (...) { return VALMAX_GHOST_HOOK_FAILED; }
}
extern "C" uint32_t valmax_ghost_status(ValmaxGhostStatus *status) {
    using namespace valmax::ghost;
    if (!status || status->size != sizeof(ValmaxGhostStatus) || status->version != 1) return VALMAX_GHOST_ARGUMENT;
    try {
        auto &r = runtime(); std::scoped_lock guard(r.lifecycle, r.mutex);
        ValmaxGhostStatus value{}; value.size = sizeof(value); value.version = 1;
        value.error = r.error.load(); value.ready = r.ready.load(); value.policy = r.policy;
        value.blocked_typing = r.counters[0].load(); value.blocked_read = r.counters[1].load();
        value.blocked_presence = r.counters[2].load(); value.blocked_stories = r.counters[3].load(); value.unread_faults = r.counters[4].load();
        value.replacements = static_cast<uint32_t>(r.replaced); value.listeners = static_cast<uint32_t>(r.attached);
        *status = value; return VALMAX_GHOST_OK;
    } catch (...) { return VALMAX_GHOST_HOOK_FAILED; }
}
extern "C" void valmax_ghost_invalidate_unread(void) {
    using namespace valmax::ghost;
    try { auto &r = runtime(); std::lock_guard guard(r.mutex); r.ledger.clear(); } catch (...) {}
}
extern "C" uint32_t valmax_ghost_shutdown(void) {
    using namespace valmax::ghost;
    try {
        auto &r = runtime(); std::lock_guard guard(r.lifecycle);
        const auto result = detach(r); r.error.store(result == VALMAX_GHOST_OK ? VALMAX_GHOST_NOT_READY : result); return result;
    } catch (...) { return VALMAX_GHOST_HOOK_FAILED; }
}
