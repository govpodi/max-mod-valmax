#include "hook_set.h"
#include <windows.h>
#include <array>
#include <atomic>
#include <cstring>
#include <iostream>
#include <thread>
#include <vector>

using namespace valmax::hooks;
namespace {
using Function = uint64_t (*)(void *, uint64_t, uint32_t);
std::atomic<uint64_t> calls{0}, blocked{0};
std::atomic<bool> enabled{false};
std::atomic<bool> holdCallback{false}, callbackEntered{false}, releaseCallback{false};
void *original = nullptr;
void *otherOriginal = nullptr;
__declspec(noinline) uint64_t target(void *self, uint64_t id, uint32_t kind)
{
    calls.fetch_add(1, std::memory_order_relaxed);
    return reinterpret_cast<uintptr_t>(self) ^ id ^ kind;
}
__declspec(noinline) uint64_t other(void *self, uint64_t id, uint32_t kind)
{
    calls.fetch_add(2, std::memory_order_relaxed);
    return reinterpret_cast<uintptr_t>(self) + id + kind;
}
uint64_t replacement(void *self, uint64_t id, uint32_t kind)
{
    if (holdCallback.load(std::memory_order_acquire)) {
        callbackEntered.store(true, std::memory_order_release);
        while (!releaseCallback.load(std::memory_order_acquire)) std::this_thread::yield();
        return reinterpret_cast<Function>(original)(self, id, kind);
    }
    if (enabled.load(std::memory_order_acquire) && kind != 1) {
        blocked.fetch_add(1, std::memory_order_relaxed);
        return 0;
    }
    return reinterpret_cast<Function>(original)(self, id, kind);
}
uint64_t otherReplacement(void *, uint64_t, uint32_t) { return 99; }
std::array<uint8_t, 24> bytes(void *pointer)
{
    std::array<uint8_t, 24> result{};
    std::memcpy(result.data(), pointer, result.size());
    return result;
}
int checks = 0;
bool check(bool value) { ++checks; if (!value) std::cerr << "failed_check=" << checks << '\n'; return value; }
}
int main()
{
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    auto address = reinterpret_cast<void *>(&target);
    auto second = reinterpret_cast<void *>(&other);
    const auto code = bytes(address), otherCode = bytes(second);
    const uint64_t id = 0xfedcba9876543210ULL;
    void *self = reinterpret_cast<void *>(uintptr_t(0x1234));
    Function volatile call = &target;
    const uint64_t expected = uintptr_t(0x1234) ^ id ^ 3;
    if (!check(call(self, id, 3) == expected)) return 1;
    HookSet set;
    Replacement item{{address, code}, reinterpret_cast<void *>(&replacement), &original};
    if (!check(set.install(std::span(&item, 1)) == Error::None)) return 1;
    if (!check(set.size() == 1 && call(self, id, 3) == expected)) return 1;
    enabled.store(true);
    if (!check(call(self, id, 3) == 0 && blocked == 1)) return 1;
    if (!check(call(self, id, 1) == (uintptr_t(0x1234) ^ id ^ 1))) return 1;
    std::vector<std::thread> threads;
    for (int i = 0; i < 4; ++i) threads.emplace_back([&] { for (int j = 0; j < 1000; ++j) call(self, id, 3); });
    for (auto &thread : threads) thread.join();
    if (!check(blocked == 4001 && calls == 3)) return 1;
    if (!check(set.reset() && set.size() == 0 && call(self, id, 3) == expected)) return 1;
    if (!check(bytes(address) == code)) return 1;
    auto wrong = code; wrong[0] ^= 1;
    Replacement bad{{address, wrong}, reinterpret_cast<void *>(&replacement), &original};
    if (!check(set.install(std::span(&bad, 1)) == Error::ModuleMismatch && bytes(address) == code)) return 1;
    if (!check(!HookSet::matches({reinterpret_cast<void *>(1), code}))) return 1;
    std::array<Replacement, 2> duplicates{item, item};
    if (!check(set.install(duplicates) == Error::InvalidSpecification && set.size() == 0)) return 1;
    // Force the second replacement to fail after the first was installed. The
    // failed set must roll itself back and preserve the independently owned hook.
    HookSet owner;
    Replacement owned{{second, otherCode}, reinterpret_cast<void *>(&otherReplacement), &otherOriginal};
    if (!check(owner.install(std::span(&owned, 1)) == Error::None)) return 1;
    const auto patched = bytes(second);
    void *unused = nullptr;
    std::array<Replacement, 2> conflict{item, Replacement{{second, patched}, reinterpret_cast<void *>(&otherReplacement), &unused}};
    if (!check(set.install(conflict) == Error::HookFailed && set.size() == 0 && original == nullptr)) return 1;
    if (!check(bytes(address) == code && call(self, id, 3) == expected)) return 1;
    Function volatile callOther = &other;
    if (!check(callOther(nullptr, 0, 0) == 99 && owner.size() == 1)) return 1;
    if (!check(owner.reset() && bytes(second) == otherCode)) return 1;
    // reset cannot release/replace a trampoline while an admitted callback is
    // paused before its original call. Reinstallation remains blocked until a
    // later flush observes that callback exiting.
    enabled.store(false);
    if (!check(set.install(std::span(&item, 1)) == Error::None)) return 1;
    auto preserved = original;
    holdCallback.store(true);
    uint64_t concurrentResult = 0;
    std::thread pending([&] { concurrentResult = call(self, id, 3); });
    while (!callbackEntered.load(std::memory_order_acquire)) std::this_thread::yield();
    const bool busy = !set.reset();
    const bool retained = original == preserved && set.size() == 1;
    const auto reinstall = set.install(std::span(&item, 1));
    releaseCallback.store(true, std::memory_order_release);
    pending.join();
    if (!check(busy && retained && reinstall == Error::Busy)) return 1;
    if (!check(concurrentResult == expected)) return 1;
    if (!check(set.reset() && original == nullptr && set.size() == 0)) return 1;
    holdCallback.store(false);
    if (!check(set.install(std::span(&item, 1)) == Error::None)) return 1;
    if (!check(call(self, id, 3) == expected && set.reset())) return 1;
    std::cout << "{\"schema\":1,\"ok\":true,\"checks\":" << checks
              << ",\"scope\":\"synthetic in-process C hooks; no MAX integration claimed\"}\n";
    return 0;
}
