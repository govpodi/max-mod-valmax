#include "ghost_internal.h"
#include "ghost_pins.generated.h"
#include "hook_set.h"
#include "frida-gum.h"
#include <array>
#include <cstring>
#include <iostream>
#include <thread>
#include <vector>
using namespace valmax::ghost;
namespace hooks = valmax::hooks;
namespace {
int checks = 0;
bool check(bool ok) { ++checks; if (!ok) std::cerr << "failed_check=" << checks << '\n'; return ok; }
#define CHECK(x) do { if (!check(x)) return 1; } while (0)
std::array<std::atomic<uint64_t>, 6> calls{};
std::atomic<uint64_t> last_id{0}, last_detail{0}, last_self{0};
HANDLE entered_event = nullptr, release_event = nullptr;
std::atomic<bool> hold_original{false};
std::atomic<bool> reentrant_self{false};
std::atomic<bool> after_flush_called{false}, after_flush_protected{false};
__declspec(noinline) void typing(void *self, int64_t id, int32_t detail) {
    ++calls[0]; last_self = uintptr_t(self); last_id = id; last_detail = detail;
    if (hold_original.load()) { SetEvent(entered_event); WaitForSingleObject(release_event, 5000); }
}
__declspec(noinline) void pair(void *self, int64_t id, void *detail) { ++calls[1]; last_self = uintptr_t(self); last_id = id; last_detail = uintptr_t(detail); }
__declspec(noinline) void selector(void *self, int64_t id, int32_t detail) { ++calls[2]; last_self = uintptr_t(self); last_id = id; last_detail = detail; }
__declspec(noinline) void stories(void *self, void *detail, int64_t id) { ++calls[3]; last_self = uintptr_t(self); last_id = id; last_detail = uintptr_t(detail); }
__declspec(noinline) void login(void *self, void *output) { ++calls[4]; last_self = uintptr_t(self); last_detail = uintptr_t(output); }
__declspec(noinline) void encode(void *byte, void *output) { ++calls[5]; *static_cast<uint8_t *>(output) = *static_cast<uint8_t *>(byte); }
void *self_id(void *, void *output) {
    if (reentrant_self.load()) valmax_ghost_invalidate_unread();
    *static_cast<int64_t *>(output) = 77; return output;
}
void foreign_login(void *, void *) {}
void on_after_flush() {
    const auto before = calls[0].load();
    auto target = static_cast<void (* volatile)(void *, int64_t, int32_t)>(&typing);
    target(nullptr, 77, 0);
    after_flush_protected.store(calls[0].load() == before);
    after_flush_called.store(true);
}
void *guarded_slot = reinterpret_cast<void *>(&typing);
template<class T, size_t N> void write(std::array<uint8_t, N> &buffer, size_t at, T value) { std::memcpy(buffer.data() + at, &value, sizeof(value)); }
ValmaxGhostStatus status() { ValmaxGhostStatus result{}; result.size = sizeof(result); result.version = 1; valmax_ghost_status(&result); return result; }
}
int main() {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    static_assert(sizeof(ValmaxGhostPolicy) == 80 && sizeof(ValmaxGhostStatus) == 144);
    CHECK(valmax_ghost_set_policy(nullptr) == VALMAX_GHOST_ARGUMENT);
    CHECK(valmax_ghost_status(nullptr) == VALMAX_GHOST_ARGUMENT);
    CHECK(!status().ready);
    UnreadLedger ledger;
    const uintptr_t owner = 0x100;
    auto frame = ledger.begin(owner, 0x200, 0x300, 17, 10, 5, false, 100, 20);
    CHECK(!ledger.count(frame, 5));
    ledger.decrement(owner, 17, 100, 20, 5, 0);
    frame = ledger.begin(owner, 0x200, 0x300, 17, 10, 5, false, 100, 20);
    CHECK(ledger.count(frame, 5) == 5);
    ledger.decrement(owner, 17, 100, 20, 5, 0); // duplicate cursor
    CHECK(ledger.count(frame, 5) == 5);
    frame = ledger.begin(owner, 0x200, 0x300, 17, 20, 5, false, 100, 20);
    CHECK(!ledger.count(frame, 5)); // server ACK invalidates credit
    ledger.decrement(owner, 17, 100, 30, 5, 0);
    frame = ledger.begin(owner, 0x200, 0x300, 17, 20, 4, false, 100, 30);
    CHECK(!ledger.count(frame, 4)); // partial ACK/raw counter drop
    ledger.decrement(owner, 17, 100, 40, 4, 0);
    frame = ledger.begin(owner, 0x200, 0x300, 17, 20, 4, false, 100, 40);
    ledger.clear(); CHECK(!ledger.count(frame, 4));
    for (size_t i = 0; i < UnreadLedger::kChats + 1; ++i) ledger.begin(owner, 0, 0, static_cast<int64_t>(i), 1, 1, false, 0, 0);
    CHECK(ledger.entry_count() <= UnreadLedger::kChats);
    for (size_t i = 1; i < UnreadLedger::kOwners + 3; ++i) ledger.begin(i, 0, 0, 1, 1, 1, false, 0, 0);
    CHECK(ledger.owner_count() <= UnreadLedger::kOwners);
    ledger.clear(); ledger.begin(owner, 0, 0, 1, 1, 1, false, 0, 0);
    ledger.decrement(owner, 1, 1, 2, kMaxCount, 0);
    ledger.decrement(owner, 1, 1, 3, 2, 0);
    frame = ledger.begin(owner, 0, 0, 1, 1, 1, false, 1, 3);
    CHECK(!ledger.count(frame, 1));
    std::atomic<uint64_t> counter{kCounterLimit - 1}; increment(counter); increment(counter); CHECK(counter == kCounterLimit);

    auto *code = static_cast<uint8_t *>(VirtualAlloc(nullptr, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    CHECK(code != nullptr);
    std::memset(code, 0x90, 4096);
    for (size_t i = 0; i < 7; ++i) code[i * 64 + 32] = 0xc3;
    // Actual interior ping probe: preserve every high RAX bit, clear only AL.
    code[0] = 0x48; code[1] = 0xb8;
    uint64_t ping_value = UINT64_C(0xfedcba98765432ab); std::memcpy(code + 2, &ping_value, 8);
    DWORD old = 0; CHECK(VirtualProtect(code, 4096, PAGE_EXECUTE_READ, &old));
    FlushInstructionCache(GetCurrentProcess(), code, 4096);
    auto *fake_core = static_cast<uint8_t *>(VirtualAlloc(nullptr, 0x2000000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    CHECK(fake_core != nullptr);
    Targets targets{};
    targets.replacements = {reinterpret_cast<void *>(&typing), reinterpret_cast<void *>(&pair), reinterpret_cast<void *>(&selector), reinterpret_cast<void *>(&stories), reinterpret_cast<void *>(&login)};
    for (size_t i = 0; i < 7; ++i) targets.probes[i] = code + i * 64 + 10;
    targets.encoder = reinterpret_cast<void *>(&encode); targets.self_id = reinterpret_cast<void *>(&self_id); targets.core = reinterpret_cast<uintptr_t>(fake_core);
    CHECK(install_synthetic(targets) == VALMAX_GHOST_OK);
    CHECK(status().ready && status().replacements == 5 && status().listeners == 7);
    auto call_typing = static_cast<void (* volatile)(void *, int64_t, int32_t)>(&typing);
    auto call_pair = static_cast<void (* volatile)(void *, int64_t, void *)>(&pair);
    auto call_selector = static_cast<void (* volatile)(void *, int64_t, int32_t)>(&selector);
    auto call_story = static_cast<void (* volatile)(void *, void *, int64_t)>(&stories);
    auto call_login = static_cast<void (* volatile)(void *, void *)>(&login);
    const auto id = static_cast<int64_t>(UINT64_C(0xfedcba9876543210));
    auto *self = reinterpret_cast<void *>(uintptr_t(0x1234));
    auto *detail = reinterpret_cast<void *>(UINT64_C(0x123456789abc));
    call_typing(self, id, -2); CHECK(calls[0] == 1 && last_id == static_cast<uint64_t>(id) && last_self == uintptr_t(self));
    call_pair(self, id, detail); CHECK(calls[1] == 1 && last_detail == uintptr_t(detail));
    call_story(self, detail, id); CHECK(calls[3] == 1 && last_id == static_cast<uint64_t>(id));
    ValmaxGhostPolicy policy{}; policy.size = sizeof(policy); policy.version = 1; std::fill(std::begin(policy.revision), std::end(policy.revision), 'a');
    policy.enabled = policy.hide_typing = policy.hide_read = policy.hide_online = policy.hide_stories = 1;
    CHECK(valmax_ghost_set_policy(&policy) == VALMAX_GHOST_OK);
    auto bad = policy; bad.enabled = 2; CHECK(valmax_ghost_set_policy(&bad) == VALMAX_GHOST_ARGUMENT);
    bad = policy; bad.hide_read = 0; CHECK(valmax_ghost_set_policy(&bad) == VALMAX_GHOST_POLICY_CONFLICT);
    call_typing(self, id, 0); call_pair(self, id, detail); call_selector(self, id, 0); call_story(self, detail, id);
    CHECK(calls[0] == 1 && calls[1] == 1 && calls[2] == 0 && calls[3] == 1);
    call_selector(self, id, 1); CHECK(calls[2] == 1 && last_id == static_cast<uint64_t>(id));
    uint8_t output = 99; call_login(self, &output); CHECK(output == 0 && calls[4] == 0 && calls[5] == 1);
    CHECK(reinterpret_cast<uint64_t (*)()>(code)() == (ping_value & UINT64_C(0xffffffffffffff00)));
    std::vector<std::thread> workers;
    for (int i = 0; i < 4; ++i) workers.emplace_back([&] { for (int j = 0; j < 1000; ++j) call_typing(self, id, 0); });
    for (auto &worker : workers) worker.join();
    CHECK(status().blocked_typing == 4001 && status().blocked_read == 2 && status().blocked_stories == 1 && status().blocked_presence == 2);

    std::array<uint8_t, 0x100> owner_data{};
    std::array<uint8_t, 0x600> chat{};
    std::array<uint8_t, 0x40> state{}, reader{}, model{}, head{}, node{}, pair_data{};
    const auto core_base = reinterpret_cast<uintptr_t>(fake_core);
    uintptr_t pointer = core_base + pins::kReaderThunkRva;
    std::memcpy(fake_core + pins::kReaderVtableRva + pins::kReaderSlot, &pointer, 8);
    pointer = core_base + pins::kModelThunkRva;
    std::memcpy(fake_core + pins::kModelVtableRva + pins::kModelSlot, &pointer, 8);
    write(owner_data, 0x68, reinterpret_cast<uintptr_t>(reader.data()));
    write(reader, 0, core_base + pins::kReaderVtableRva); write(reader, pins::kModelOffset, reinterpret_cast<uintptr_t>(model.data()));
    write(model, 0, core_base + pins::kModelVtableRva);
    write(chat, 0, int64_t(17)); write(chat, 0x18, reinterpret_cast<uintptr_t>(head.data())); write(chat, 0x260, int32_t(5));
    write(head, 8, reinterpret_cast<uintptr_t>(node.data())); write(node, 0x20, int64_t(77)); write(node, 0x28, int64_t(10));
    write(state, 0x10, int64_t(17)); write(state, 0x18, int64_t(100)); write(state, 0x20, int64_t(20));
    write(pair_data, 0, int64_t(100)); write(pair_data, 8, int64_t(20));
    const auto thread = GetCurrentThreadId();
    GumCpuContext enter{}; enter.rcx = uintptr_t(owner_data.data()); enter.rdx = uintptr_t(chat.data()); enter.r8 = uintptr_t(state.data());
    GumCpuContext count{}; count.rdi = enter.rcx; count.r15 = enter.rdx; count.r14 = enter.r8; count.r9 = 0x123456;
    synthetic_probe(2, &enter, thread); synthetic_probe(4, &count, thread); CHECK(count.rdx == count.r9);
    synthetic_probe(2, &enter, thread, true);
    GumCpuContext decrement{}; decrement.rsi = enter.rcx; decrement.rbp = enter.r8; decrement.r14 = uintptr_t(pair_data.data()); decrement.rcx = 5; decrement.rbx = 0;
    synthetic_probe(1, &decrement, thread);
    synthetic_probe(2, &enter, thread); synthetic_probe(4, &count, thread);
    int64_t credit = 0; CHECK(count.rdx != count.r9 && read(count.rdx + 0x28, credit) && credit == 5);
    synthetic_probe(3, &count, thread); CHECK(count.rbx == UINT64_MAX);
    valmax_ghost_invalidate_unread(); count.rdx = 999; synthetic_probe(4, &count, thread); CHECK(count.rdx == 999);
    synthetic_probe(2, &enter, thread, true);
    write(node, 0x28, int64_t(20)); synthetic_probe(2, &enter, thread); synthetic_probe(4, &count, thread); CHECK(count.rdx == count.r9);
    synthetic_probe(2, &enter, thread, true);
    reentrant_self.store(true);
    synthetic_probe(2, &enter, thread); count.rdx = 999; synthetic_probe(4, &count, thread);
    CHECK(count.rdx == 999); // getter reentry invalidated epoch without deadlock
    synthetic_probe(2, &enter, thread, true); reentrant_self.store(false);
    // Invalid model memory must fail closed without a crash or invented credits.
    write(owner_data, 0x68, uintptr_t(1)); synthetic_probe(2, &enter, thread); synthetic_probe(4, &count, thread); CHECK(count.rdx == count.r9);
    synthetic_probe(2, &enter, thread, true);
    CHECK(valmax_ghost_shutdown() == VALMAX_GHOST_OK && !status().ready);
    call_typing(self, id, 0); CHECK(calls[0] == 2);
    CHECK(reinterpret_cast<uint64_t (*)()>(code)() == ping_value);
    CHECK(valmax_ghost_install(L"C:/missing/core.dll", L"C:/missing/max.exe") == VALMAX_GHOST_NOT_READY);
    CHECK(status().error == VALMAX_GHOST_NOT_READY);
    CHECK(!status().ready);
    CHECK(valmax_ghost_install(L"C:/missing/core.dll", L"D:/different/max.exe") == VALMAX_GHOST_ARGUMENT);
    // Reinstallation retains the last policy and counters; no false ready state
    // is published when a separate owner already intercepts one required entry.
    hooks::HookSet foreign;
    std::array<uint8_t, 24> original_login{};
    std::memcpy(original_login.data(), targets.replacements[4], original_login.size());
    void *foreign_original = nullptr;
    hooks::Replacement foreign_item{{targets.replacements[4], original_login}, reinterpret_cast<void *>(&foreign_login), &foreign_original};
    CHECK(foreign.install(std::span(&foreign_item, 1)) == hooks::Error::None);
    CHECK(install_synthetic(targets) == VALMAX_GHOST_HOOK_FAILED && !status().ready);
    call_typing(self, id, 0); CHECK(calls[0] == 3); // rollback restored earlier entries
    CHECK(foreign.reset());
    set_synthetic_after_flush_probe(&on_after_flush);
    CHECK(install_synthetic(targets) == VALMAX_GHOST_OK && status().ready);
    set_synthetic_after_flush_probe(nullptr);
    CHECK(after_flush_called.load() && after_flush_protected.load());
    call_typing(self, id, 0); CHECK(calls[0] == 3 && status().blocked_typing == 4003);
    auto disabled = policy; disabled.enabled = 0; std::fill(std::begin(disabled.revision), std::end(disabled.revision), 'b');
    CHECK(valmax_ghost_set_policy(&disabled) == VALMAX_GHOST_OK);
    entered_event = CreateEventW(nullptr, TRUE, FALSE, nullptr); release_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    CHECK(entered_event != nullptr && release_event != nullptr);
    hold_original.store(true);
    std::thread in_flight([&] { call_typing(self, id, 0); });
    const bool entered = WaitForSingleObject(entered_event, 3000) == WAIT_OBJECT_0;
    const auto shutdown_in_flight = valmax_ghost_shutdown();
    const auto reinstall_in_flight = install_synthetic(targets);
    SetEvent(release_event); in_flight.join(); hold_original.store(false);
    CHECK(entered && shutdown_in_flight == VALMAX_GHOST_BUSY && !status().ready);
    CHECK(reinstall_in_flight == VALMAX_GHOST_BUSY);
    CHECK(valmax_ghost_shutdown() == VALMAX_GHOST_OK);
    CHECK(calls[0] == 4);
    CloseHandle(entered_event); CloseHandle(release_event);
    // Exercise production code/layout/vtable verification against this loaded
    // synthetic executable. Caller-supplied pins exist only in this test target.
    VerifiedImage test_image;
    test_image.base = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    IMAGE_DOS_HEADER dos{}; IMAGE_NT_HEADERS64 nt{};
    CHECK(read(test_image.base, dos) && read(test_image.base + dos.e_lfanew, nt));
    test_image.size = nt.OptionalHeader.SizeOfImage;
    std::array<uint8_t, 24> current_bytes{}; std::memcpy(current_bytes.data(), targets.replacements[0], current_bytes.size());
    const auto function_rva = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(targets.replacements[0]) - test_image.base);
    const auto slot_rva = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(&guarded_slot) - test_image.base);
    CodePin code_pin{function_rva, current_bytes}; SlotPin slot_pin{slot_rva, function_rva};
    ImagePin image_pin{"", 0, test_image.size, std::span(&code_pin, 1), std::span(&slot_pin, 1)};
    CHECK(validate_loaded(test_image, image_pin));
    current_bytes[0] ^= 1; CHECK(!validate_loaded(test_image, image_pin)); current_bytes[0] ^= 1;
    ++slot_pin.target; CHECK(!validate_loaded(test_image, image_pin)); --slot_pin.target;
    ++image_pin.image_size; CHECK(!validate_loaded(test_image, image_pin)); --image_pin.image_size;
    code_pin.rva = image_pin.image_size; CHECK(!validate_loaded(test_image, image_pin)); code_pin.rva = function_rva;
    code_pin.rva = slot_rva; CHECK(!validate_loaded(test_image, image_pin)); code_pin.rva = function_rva;
    const auto saved_base = test_image.base; test_image.base = 1;
    CHECK(!validate_loaded(test_image, image_pin)); test_image.base = saved_base;
    // Read-model callbacks, policy changes and status snapshots can arrive on
    // different native threads. Epoch invalidation must never expose stale nodes.
    CHECK(install_synthetic(targets) == VALMAX_GHOST_OK);
    write(owner_data, 0x68, reinterpret_cast<uintptr_t>(reader.data()));
    std::atomic<bool> concurrent_ok{true};
    std::thread policy_writer([&] {
        for (unsigned i = 0; i < 500; ++i) {
            auto selected = policy;
            char revision[65]{}; sprintf_s(revision, "%064x", i + 1);
            std::memcpy(selected.revision, revision, 64);
            selected.enabled = static_cast<uint8_t>(i & 1);
            if (valmax_ghost_set_policy(&selected) != VALMAX_GHOST_OK) concurrent_ok.store(false);
        }
    });
    std::array<std::thread, 2> readers;
    for (auto &worker : readers) worker = std::thread([&] {
        auto local_enter = enter; auto local_count = count;
        for (int i = 0; i < 500; ++i) {
            synthetic_probe(2, &local_enter, GetCurrentThreadId());
            local_count.rdx = local_count.r9;
            synthetic_probe(4, &local_count, GetCurrentThreadId());
            synthetic_probe(2, &local_enter, GetCurrentThreadId(), true);
            const auto snapshot = status();
            if (!snapshot.ready || snapshot.policy.enabled > 1 || snapshot.unread_faults > kCounterLimit) concurrent_ok.store(false);
        }
    });
    policy_writer.join(); for (auto &worker : readers) worker.join();
    CHECK(concurrent_ok.load());
    CHECK(valmax_ghost_shutdown() == VALMAX_GHOST_OK);
    VirtualFree(code, 0, MEM_RELEASE); VirtualFree(fake_core, 0, MEM_RELEASE);
    std::cout << "{\"schema\":1,\"ok\":true,\"checks\":" << checks << ",\"scope\":\"synthetic native hooks, register probes, bounded unread ledger; no live MAX/server claim\"}\n";
    return 0;
}
