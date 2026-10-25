#include "hook_set.h"
#include "frida-gum.h"
#include <windows.h>
#include <algorithm>
#include <mutex>
#include <set>

namespace valmax::hooks {
HookSet::HookSet()
{
    static std::once_flag initialized;
    std::call_once(initialized, [] { gum_init_embedded(); });
    interceptor_ = gum_interceptor_obtain();
}
HookSet::~HookSet()
{
    // A caller violating the explicit drain contract must not free Gum's
    // trampoline owner underneath an active callback. Leak this last reference
    // rather than unref it; normal owners always complete reset() first.
    if (reset()) g_object_unref(interceptor_);
}
bool HookSet::matches(const Guard &guard)
{
    if (!guard.address || guard.expected.empty() || guard.expected.size() > 65536) return false;
    const auto address = reinterpret_cast<uintptr_t>(guard.address);
    if (address > UINTPTR_MAX - guard.expected.size()) return false;
    MEMORY_BASIC_INFORMATION info{};
    if (!VirtualQuery(guard.address, &info, sizeof(info)) || info.State != MEM_COMMIT
        || (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) != 0
        || (info.Protect & (PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY)) == 0) return false;
    const auto start = reinterpret_cast<uintptr_t>(info.BaseAddress);
    if (address < start || address - start > info.RegionSize
        || guard.expected.size() > info.RegionSize - (address - start)) return false;
    std::vector<uint8_t> bytes(guard.expected.size());
    SIZE_T read = 0;
    if (!ReadProcessMemory(GetCurrentProcess(), guard.address, bytes.data(), bytes.size(), &read)
        || read != bytes.size()) return false;
    return std::equal(bytes.begin(), bytes.end(), guard.expected.begin());
}
Error HookSet::install(std::span<const Replacement> replacements)
{
    if (draining_) return Error::Busy;
    if (!addresses_.empty() || replacements.empty() || replacements.size() > 256) return Error::InvalidSpecification;
    std::set<const void *> targets;
    std::set<void **> slots;
    for (const auto &entry : replacements) {
        if (!entry.callback || !entry.original || !targets.insert(entry.guard.address).second
            || !slots.insert(entry.original).second)
            return Error::InvalidSpecification;
        if (!matches(entry.guard)) return Error::ModuleMismatch;
    }
    addresses_.reserve(replacements.size());
    originals_.reserve(replacements.size());
    auto interceptor = static_cast<GumInterceptor *>(interceptor_);
    gum_interceptor_begin_transaction(interceptor);
    Error error = Error::None;
    for (const auto &entry : replacements) {
        *entry.original = nullptr;
        GumReplaceOptions options{};
        options.replacement_data = entry.data;
        const auto result = gum_interceptor_replace(interceptor, const_cast<void *>(entry.guard.address),
            entry.callback, entry.original, &options);
        if (result != GUM_REPLACE_OK) { error = Error::HookFailed; break; }
        addresses_.push_back(const_cast<void *>(entry.guard.address));
        originals_.push_back(entry.original);
    }
    if (error != Error::None) {
        for (auto it = addresses_.rbegin(); it != addresses_.rend(); ++it) gum_interceptor_revert(interceptor, *it);
        draining_ = true;
    }
    gum_interceptor_end_transaction(interceptor);
    if (error != Error::None) {
        return reset() ? error : Error::Busy;
    }
    if (!gum_interceptor_flush(interceptor)) {
        return reset() ? Error::HookFailed : Error::Busy;
    }
    return error;
}
bool HookSet::reset()
{
    auto interceptor = static_cast<GumInterceptor *>(interceptor_);
    if (!draining_) {
        gum_interceptor_begin_transaction(interceptor);
        for (auto it = addresses_.rbegin(); it != addresses_.rend(); ++it) gum_interceptor_revert(interceptor, *it);
        gum_interceptor_end_transaction(interceptor);
        draining_ = true;
    }
    if (!gum_interceptor_flush(interceptor)) return false;
    for (auto slot : originals_) *slot = nullptr;
    originals_.clear();
    addresses_.clear();
    draining_ = false;
    return true;
}
} // namespace valmax::hooks
