#include "transfer_mutex.h"
#include <array>
#include <atomic>
#include <iostream>
#include <exception>
#include <string>
#include <thread>
#include <type_traits>
using namespace valmax::launcher;
namespace {
int checks=0;
bool check(bool value){++checks;if(!value)std::cerr<<"failed_check="<<checks<<",win32="<<GetLastError()<<'\n';return value;}
#define CHECK(x) do{if(!check(x))return 1;}while(false)
struct Handle {HANDLE value=nullptr;~Handle(){if(value)CloseHandle(value);}Handle()=default;explicit Handle(HANDLE h):value(h){}Handle(const Handle&)=delete;};
std::wstring own_exe(){std::array<wchar_t,32768> path{};const auto count=GetModuleFileNameW(nullptr,path.data(),static_cast<DWORD>(path.size()));if(!count||count>=path.size())std::terminate();return {path.data(),count};}
struct Child {
    Handle process,thread;
    bool spawn(const std::wstring &mode,const std::wstring &name,const std::wstring &ready,const std::wstring &release){
        const auto exe=own_exe();auto args=L"\""+exe+L"\" "+mode+L" "+name+L" "+ready+L" "+release;
        STARTUPINFOW startup{};startup.cb=sizeof(startup);PROCESS_INFORMATION info{};
        if(!CreateProcessW(exe.c_str(),args.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW,nullptr,nullptr,&startup,&info))return false;
        process.value=info.hProcess;thread.value=info.hThread;return true;
    }
    bool done(DWORD expected=0){DWORD code=0;return WaitForSingleObject(process.value,8000)==WAIT_OBJECT_0&&GetExitCodeProcess(process.value,&code)&&code==expected;}
};
TransferCode on_other_thread(const std::wstring &name){TransferCode code=TransferCode::IoError;std::thread worker([&]{TransferGuard guard(TestTransferName{name});code=guard.code();});worker.join();return code;}
int child(int argc,wchar_t **argv){
    if(argc!=5)return 20;
    Handle ready(OpenEventW(EVENT_MODIFY_STATE,FALSE,argv[3])),release(OpenEventW(SYNCHRONIZE,FALSE,argv[4]));
    if(!ready.value||!release.value)return 21;
    if(std::wstring_view(argv[1])==L"--wrong-destructor") {
        // Test-only handler proves std::terminate was reached without CRT/WER UI.
        std::set_terminate([]{ExitProcess(86);});
        auto *guard=new TransferGuard(TestTransferName{argv[2]});if(guard->code()!=TransferCode::Ok){delete guard;return 22;}
        if(!SetEvent(ready.value)||WaitForSingleObject(release.value,6000)!=WAIT_OBJECT_0){delete guard;return 23;}
        std::thread misuse([&]{std::set_terminate([]{ExitProcess(86);});delete guard;});misuse.join();return 25;
    }
    TransferGuard guard(TestTransferName{argv[2]});if(guard.code()!=TransferCode::Ok)return 22;
    if(!SetEvent(ready.value)||WaitForSingleObject(release.value,6000)!=WAIT_OBJECT_0)return 23;
    if(std::wstring_view(argv[1])==L"--abandon")ExitProcess(0); // owned fixture intentionally exits without RAII
    if(std::wstring_view(argv[1])!=L"--holder")return 24;
    return 0;
}
}
int wmain(int argc,wchar_t **argv){
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    if(argc>1)return child(argc,argv);
    static_assert(!std::is_copy_constructible_v<TransferGuard> && !std::is_move_constructible_v<TransferGuard>);
    static_assert(!std::is_default_constructible_v<TransferGuard>);
    const auto base=L"Local\\ValMAXNativeTransferTestV1-"+std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(GetTickCount64());
    for(const auto *invalid:{L"",L"Local\\MAXModSessionTransferV1",L"Global\\ValMAXNativeTransferTestV1-test",L"Local\\ValMAXNativeTransferTestV1-",L"Local\\ValMAXNativeTransferTestV1-a\\b"}){
        TransferGuard guard(TestTransferName{invalid});CHECK(guard.code()==TransferCode::IoError&&!guard.owned_by_current_thread()&&guard.release()==TransferCode::NotOwned);}
    {
        TransferGuard outer(TestTransferName{base});CHECK(outer.code()==TransferCode::Ok&&outer.owned_by_current_thread()&&!outer.abandoned());
        TransferGuard inner(TestTransferName{base});CHECK(inner.code()==TransferCode::Ok&&inner.owned_by_current_thread()&&!inner.abandoned());
        CHECK(on_other_thread(base)==TransferCode::Busy);
        CHECK(outer.release()==TransferCode::Ok&&!outer.owned_by_current_thread());
        CHECK(outer.release()==TransferCode::NotOwned);
        CHECK(on_other_thread(base)==TransferCode::Busy); // one recursive acquisition remains
        TransferCode wrong=TransferCode::IoError;std::thread worker([&]{wrong=inner.release();});worker.join();
        CHECK(wrong==TransferCode::WrongThread&&inner.owned_by_current_thread());
        CHECK(on_other_thread(base)==TransferCode::Busy);
        CHECK(inner.release()==TransferCode::Ok);
        CHECK(on_other_thread(base)==TransferCode::Ok);
    }
    {TransferGuard guard(TestTransferName{base});CHECK(guard.owned_by_current_thread());}
    CHECK(on_other_thread(base)==TransferCode::Ok); // destructor releases
    bool concurrent=true;
    for(int attempt=0;attempt<64;++attempt){
        TransferGuard guard(TestTransferName{base});
        std::atomic<bool> ready=false,start=false;bool denied=true;
        std::thread observer([&]{ready.store(true);while(!start.load())std::this_thread::yield();
            for(int i=0;i<2048;++i)if(guard.owned_by_current_thread())denied=false;});
        while(!ready.load())std::this_thread::yield();start.store(true);
        if(guard.release()!=TransferCode::Ok)concurrent=false;
        observer.join();if(!denied)concurrent=false;
    }
    CHECK(concurrent); // stress only; immutable-owner-first order provides the race argument
    const auto conflict=base+L"-event-collision";Handle collision(CreateEventW(nullptr,TRUE,FALSE,conflict.c_str()));CHECK(collision.value);
    {TransferGuard guard(TestTransferName{conflict});CHECK(guard.code()==TransferCode::IoError&&!guard.owned_by_current_thread());}
    for(const auto *mode:{L"--holder",L"--abandon",L"--wrong-destructor"}){
        const bool orderly=std::wstring_view(mode)==L"--holder",wrong_destructor=std::wstring_view(mode)==L"--wrong-destructor";
        const auto name=base+(orderly?L"-normal":wrong_destructor?L"-destructor":L"-abandon"),ready_name=name+L"-ready",release_name=name+L"-release";
        Handle keepalive(CreateMutexW(nullptr,FALSE,name.c_str())); // retains abandoned object after child exit
        Handle ready(CreateEventW(nullptr,TRUE,FALSE,ready_name.c_str())),release(CreateEventW(nullptr,TRUE,FALSE,release_name.c_str()));
        CHECK(keepalive.value&&ready.value&&release.value);Child owned;
        CHECK(owned.spawn(mode,name,ready_name,release_name));
        CHECK(WaitForSingleObject(ready.value,5000)==WAIT_OBJECT_0);
        {TransferGuard busy(TestTransferName{name});CHECK(busy.code()==TransferCode::Busy&&!busy.owned_by_current_thread()&&!busy.abandoned());}
        CHECK(SetEvent(release.value));CHECK(owned.done(wrong_destructor?86:0));
        TransferGuard next(TestTransferName{name});CHECK(next.code()==TransferCode::Ok&&next.owned_by_current_thread());
        CHECK(next.abandoned()==!orderly);
        CHECK(next.release()==TransferCode::Ok);
        TransferGuard subsequent(TestTransferName{name});CHECK(subsequent.code()==TransferCode::Ok&&!subsequent.abandoned());
    }
    DWORD before=0,after=0;CHECK(GetProcessHandleCount(GetCurrentProcess(),&before));
    bool repeated=true;for(int i=0;i<100;++i){TransferGuard guard(TestTransferName{base});if(guard.code()!=TransferCode::Ok)repeated=false;}
    CHECK(GetProcessHandleCount(GetCurrentProcess(),&after));CHECK(repeated&&after<=before+1);
    std::cout<<"{\"schema\":1,\"ok\":true,\"checks\":"<<checks<<",\"productionMutexAcquired\":false,\"ownedChildProcesses\":3,\"scope\":\"renamed mutex only; contention, recursion, abandonment, ownership and cleanup\"}\n";
}
