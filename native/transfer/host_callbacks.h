#pragma once
#include "closed_clients.h"
#include "../core/valmax_core.h"
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace valmax::transfer {
static_assert(sizeof(ValmaxTransferHost)==40);
static_assert(offsetof(ValmaxTransferHost,context)==8);
static_assert(offsetof(ValmaxTransferHost,wait_closed)==16);
static_assert(offsetof(ValmaxTransferHost,check_closed)==24);
static_assert(offsetof(ValmaxTransferHost,cancelled)==32);

// Construct on the importing worker and keep this object alive until the Rust
// call has returned. Neither context nor callbacks may be destroyed in flight.
// request_cancel() alone may run concurrently on another thread; it does not
// promise interruption of Windows I/O, archive decoding or commit/rollback.
// No callback acquires the transfer mutex: the Rust import owner holds it.
class NativeTransferHost final {
public:
    NativeTransferHost() noexcept;
    ~NativeTransferHost()=default;
    NativeTransferHost(const NativeTransferHost&)=delete;
    NativeTransferHost& operator=(const NativeTransferHost&)=delete;
    NativeTransferHost(NativeTransferHost&&)=delete;
    NativeTransferHost& operator=(NativeTransferHost&&)=delete;

    ValmaxTransferHost callbacks() noexcept;
    void request_cancel() noexcept;

#ifdef VALMAX_TRANSFER_TESTING
    // Test target only. Production compilation contains no injected clock,
    // scanner or waiter. The caller owns this synthetic context's lifetime.
    struct TestApi {
        void *context=nullptr;
        uint64_t (*now)(void*)=nullptr;
        ClientScan (*scan)(void*,const ScanControl&)=nullptr;
        void (*sleep)(void*,uint32_t)=nullptr;
    };
    explicit NativeTransferHost(TestApi) noexcept;
#endif

private:
    const uint32_t owner_;
    std::atomic_bool cancelled_{false};
    bool active_=false,wait_called_=false,wait_succeeded_=false;
#ifdef VALMAX_TRANSFER_TESTING
    TestApi test_{};
#endif
    uint64_t now();
    ClientScan scan(const ScanControl&);
    void sleep(uint32_t);
    bool on_owner() const noexcept;
    uint32_t wait_closed() noexcept;
    uint32_t check_closed() noexcept;
    static uint32_t wait_callback(void*) noexcept;
    static uint32_t check_callback(void*) noexcept;
    static uint32_t cancel_callback(void*) noexcept;
};
}
