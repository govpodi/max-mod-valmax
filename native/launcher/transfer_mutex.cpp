#include "transfer_mutex.h"
#include <exception>
#include <string>
namespace valmax::launcher {
const char *transfer_code(TransferCode code) noexcept {
    switch(code){case TransferCode::Ok:return "ok";case TransferCode::Busy:return "busy";
        case TransferCode::WrongThread:return "wrong_thread";case TransferCode::NotOwned:return "not_owned";
        default:return "io_error";}
}
#ifdef VALMAX_TRANSFER_TESTING
TransferGuard::TransferGuard(TestTransferName name) noexcept {
    // The test binary cannot acquire the real launcher/importer mutex.
    constexpr std::wstring_view prefix=L"Local\\ValMAXNativeTransferTestV1-";
    if(!name.value.starts_with(prefix)||name.value.size()<=prefix.size()||name.value.size()>220)return;
    for(const auto ch:name.value.substr(prefix.size()))
        if(!((ch>=L'0'&&ch<=L'9')||(ch>=L'a'&&ch<=L'z')||(ch>=L'A'&&ch<=L'Z')||ch==L'-'))return;
    try{const std::wstring value(name.value);acquire(value.c_str());}catch(...){close();}
}
#else
TransferGuard::TransferGuard() noexcept {acquire(L"Local\\MAXModSessionTransferV1");}
#endif
void TransferGuard::acquire(const wchar_t *name) noexcept {
    const auto process=GetCurrentProcess();
    // Retaining the thread object also prevents reuse of its Windows thread ID.
    if(!DuplicateHandle(process,GetCurrentThread(),process,&owner_thread_,SYNCHRONIZE,FALSE,0))return;
    owner_id_=GetCurrentThreadId();
    mutex_=CreateMutexW(nullptr,FALSE,name);
    if(!mutex_){close();return;}
    const auto result=WaitForSingleObject(mutex_,0);
    if(result==WAIT_OBJECT_0||result==WAIT_ABANDONED){owned_=true;abandoned_=result==WAIT_ABANDONED;code_=TransferCode::Ok;return;}
    code_=result==WAIT_TIMEOUT?TransferCode::Busy:TransferCode::IoError;close();
}
void TransferGuard::close() noexcept {
    if(mutex_)CloseHandle(mutex_);mutex_=nullptr;
    if(owner_thread_)CloseHandle(owner_thread_);owner_thread_=nullptr;
}
bool TransferGuard::owned_by_current_thread() const noexcept {
    // Reject foreign threads before touching state that release() mutates.
    if(owner_id_!=GetCurrentThreadId())return false;
    return owned_&&owner_thread_
        &&WaitForSingleObject(owner_thread_,0)==WAIT_TIMEOUT;
}
TransferCode TransferGuard::release() noexcept {
    // Check the immutable owner ID first. A wrong-thread call does not inspect
    // or mutate lifetime state that the owning thread may be releasing.
    if(owner_id_&&owner_id_!=GetCurrentThreadId())return TransferCode::WrongThread;
    if(!owned_)return TransferCode::NotOwned;
    if(!owned_by_current_thread())return TransferCode::WrongThread;
    if(!ReleaseMutex(mutex_))return TransferCode::IoError;
    owned_=false;close();return TransferCode::Ok;
}
TransferGuard::~TransferGuard() noexcept {
    if(owned_&&release()!=TransferCode::Ok)std::terminate();
    close();
}
}
