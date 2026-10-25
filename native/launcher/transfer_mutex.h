#pragma once
#include <windows.h>
#include <string_view>
#include <cstdint>
namespace valmax::launcher {
enum class TransferCode:uint32_t {Ok,Busy,IoError,WrongThread,NotOwned};
const char *transfer_code(TransferCode) noexcept;
#ifdef VALMAX_TRANSFER_TESTING
struct TestTransferName {std::wstring_view value;};
#endif
// Stack ownership is thread-affine, like the underlying recursive Windows
// mutex. One guard owns exactly one acquisition. Different guards on the same
// thread may recurse, matching the existing Python transfer_lock contract.
// Acquiring an abandoned mutex does NOT clear a recovery requirement: startup
// must independently inspect the recovery marker before touching profile data.
class TransferGuard final {
public:
#ifdef VALMAX_TRANSFER_TESTING
    explicit TransferGuard(TestTransferName) noexcept;
#else
    TransferGuard() noexcept;
#endif
    ~TransferGuard() noexcept;
    TransferGuard(const TransferGuard &)=delete;
    TransferGuard &operator=(const TransferGuard &)=delete;
    TransferGuard(TransferGuard &&)=delete;
    TransferGuard &operator=(TransferGuard &&)=delete;
    TransferCode code() const noexcept {return code_;}
    bool abandoned() const noexcept {return abandoned_;}
    bool owned_by_current_thread() const noexcept;
    // Wrong-thread release leaves ownership intact. Destroying a live guard on
    // a different thread is a programming error and terminates the process;
    // silently closing its handle would leave an unobservable owned mutex.
    TransferCode release() noexcept;
private:
    void acquire(const wchar_t *) noexcept;
    void close() noexcept;
    HANDLE mutex_=nullptr,owner_thread_=nullptr;
    DWORD owner_id_=0;
    TransferCode code_=TransferCode::IoError;
    bool owned_=false,abandoned_=false;
};
}
