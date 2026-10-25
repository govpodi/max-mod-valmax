#include "host_callbacks.h"
#include <windows.h>
#include <stdexcept>

namespace valmax::transfer {
namespace {
constexpr uint32_t closed=0,running=1,failed=2,cancelled=3;
struct ActiveCall {
    bool &active;
    explicit ActiveCall(bool &value) noexcept:active(value){active=true;}
    ~ActiveCall(){active=false;}
};
uint32_t outcome(WaitState state) noexcept {
    switch(state) {
    case WaitState::Closed:return closed;
    case WaitState::Cancelled:return cancelled;
    case WaitState::Deadline:return running;
    default:return failed;
    }
}
}
NativeTransferHost::NativeTransferHost() noexcept:owner_(GetCurrentThreadId()){}
#ifdef VALMAX_TRANSFER_TESTING
NativeTransferHost::NativeTransferHost(TestApi api) noexcept:owner_(GetCurrentThreadId()),test_(api){}
#endif
ValmaxTransferHost NativeTransferHost::callbacks() noexcept {
    return {sizeof(ValmaxTransferHost),1,this,&wait_callback,&check_callback,&cancel_callback};
}
void NativeTransferHost::request_cancel() noexcept {cancelled_.store(true,std::memory_order_release);}
bool NativeTransferHost::on_owner() const noexcept {return owner_==GetCurrentThreadId();}
uint64_t NativeTransferHost::now() {
#ifdef VALMAX_TRANSFER_TESTING
    if(test_.now)return test_.now(test_.context);
#endif
    return GetTickCount64();
}
ClientScan NativeTransferHost::scan(const ScanControl &control) {
#ifdef VALMAX_TRANSFER_TESTING
    if(test_.now) {
        if(!test_.scan||!test_.sleep)throw std::runtime_error("incomplete synthetic callbacks");
        return test_.scan(test_.context,control);
    }
#endif
    return scan_closed_clients(control);
}
void NativeTransferHost::sleep(uint32_t delay) {
#ifdef VALMAX_TRANSFER_TESTING
    if(test_.now) {
        if(!test_.sleep)throw std::runtime_error("missing synthetic waiter");
        test_.sleep(test_.context,delay);return;
    }
#endif
    Sleep(delay);
}
uint32_t NativeTransferHost::wait_closed() noexcept {
    // Check the immutable owner first: no cross-thread reads of worker state.
    if(!on_owner())return failed;
    if(active_)return failed;
    if(cancelled_.load(std::memory_order_acquire)){wait_succeeded_=false;return cancelled;}
    // One context is one import. A second wait cannot restart its 120s budget
    // or reuse an earlier successful absence observation.
    if(wait_called_){wait_succeeded_=false;return failed;}
    wait_called_=true;wait_succeeded_=false;
    ActiveCall call(active_);
    try {
        ClosedWait wait(now());
        while(wait.state()==WaitState::Waiting) {
            const auto before=now();
            const bool stopped=cancelled_.load(std::memory_order_acquire);
            if(stopped||before>=wait.deadline_ms()) {
                wait.observe({},before,stopped);break;
            }
            const auto value=scan({&cancelled_,wait.deadline_ms()});
            const auto observed=now();
            wait.observe(value,observed,cancelled_.load(std::memory_order_acquire));
            if(wait.state()!=WaitState::Waiting)break;
            // Sample time again so the sleep never intentionally extends the
            // absolute wait deadline. Win32 calls themselves are not preempted.
            const auto current=now();
            if(cancelled_.load(std::memory_order_acquire))return cancelled;
            if(current<observed)return failed;
            if(current>=wait.deadline_ms())return running;
            const auto delay=wait.next_delay_ms(current);
            if(!delay)return failed;
            sleep(delay);
        }
        const auto result=outcome(wait.state());
        if(cancelled_.load(std::memory_order_acquire))return cancelled;
        wait_succeeded_=result==closed;
        return result;
    }catch(...){return failed;}
}
uint32_t NativeTransferHost::check_closed() noexcept {
    if(!on_owner())return failed;
    if(active_)return failed;
    if(cancelled_.load(std::memory_order_acquire)){wait_succeeded_=false;return cancelled;}
    if(!wait_succeeded_)return failed;
    ActiveCall call(active_);
    // A failed fresh scan revokes the previous success. There is no settle or
    // retry here. Archive decoding between callbacks does not consume the old
    // wait's deadline; this one scan has its own finite policy budget.
    wait_succeeded_=false;
    try {
        const auto start=now();
        if(start>UINT64_MAX-kWaitMs)return failed;
        const auto deadline=start+kWaitMs;
        const auto value=scan({&cancelled_,deadline});
        const auto end=now();
        if(cancelled_.load(std::memory_order_acquire)||value.code==ScanCode::Cancelled)return cancelled;
        if(end<start)return failed;
        if(end>=deadline||value.code==ScanCode::Deadline)return running;
        if(!value.complete||value.code!=ScanCode::Complete)return failed;
        if(value.candidates)return running;
        wait_succeeded_=true;
        return closed;
    }catch(...){return failed;}
}
uint32_t NativeTransferHost::wait_callback(void *context) noexcept {
    return context?static_cast<NativeTransferHost*>(context)->wait_closed():failed;
}
uint32_t NativeTransferHost::check_callback(void *context) noexcept {
    return context?static_cast<NativeTransferHost*>(context)->check_closed():failed;
}
uint32_t NativeTransferHost::cancel_callback(void *context) noexcept {
    return context&& !static_cast<NativeTransferHost*>(context)->cancelled_.load(std::memory_order_acquire)?0:1;
}
}
