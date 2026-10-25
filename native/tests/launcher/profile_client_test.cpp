#include "profile_client.h"
#include <iostream>
#include <string>
#include <thread>
using namespace valmax::launcher;
namespace {
int checks=0,calls=0;
std::string seen;
uint32_t mode=99;
DWORD thread_id=0;
int32_t returned=0;
ValmaxLauncherProfileResult response{16,1,0,2};
bool check(bool value){++checks;if(!value)std::cerr<<"failed_check="<<checks<<'\n';return value;}
#define CHECK(x) do{if(!check(x))return 1;}while(false)
extern "C" int32_t fake_prepare(const uint8_t *input,size_t length,uint32_t check_only,ValmaxLauncherProfileResult *out){
    ++calls;seen.assign(reinterpret_cast<const char *>(input),length);mode=check_only;thread_id=GetCurrentThreadId();
    *out=response;return returned;
}
}
int main(){
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    const auto name=L"Local\\ValMAXNativeTransferTestV1-profile-client-"+std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(GetTickCount64());
    TransferGuard guard(TestTransferName{name});CHECK(guard.owned_by_current_thread());
    const std::wstring local=L"C:/ValMAXBuild/fixtures/Тест-\U0001f600";
    auto result=prepare_profile(local,true,guard,fake_prepare);
    CHECK(result.code==ProfileCode::Ok&&result.state==ProfileState::Existing);
    CHECK(seen=="C:/ValMAXBuild/fixtures/Тест-\xf0\x9f\x98\x80"&&mode==1&&thread_id==GetCurrentThreadId());
    response.state=1;CHECK(prepare_profile(local,true,guard,fake_prepare).state==ProfileState::Absent);
    response.state=3;CHECK(prepare_profile(local,false,guard,fake_prepare).state==ProfileState::Created&&mode==0);
    response.state=2;CHECK(prepare_profile(local,false,guard,fake_prepare).state==ProfileState::Existing);
    int before=calls;CHECK(prepare_profile(local,true,guard,nullptr).code==ProfileCode::CoreUnavailable&&calls==before);
    for(const auto &bad: {std::wstring(),std::wstring(32001,L'x'),std::wstring(L"C:/x\0suffix",11),
            std::wstring(1,static_cast<wchar_t>(0xd800)),std::wstring(1,static_cast<wchar_t>(0xdc00)),
            std::wstring(1,static_cast<wchar_t>(0xd800))+L"x",std::wstring(L"C:/")+std::wstring(16000,L'я')}){
        const auto invalid=prepare_profile(bad,true,guard,fake_prepare);
        CHECK(invalid.code==ProfileCode::InvalidInput&&invalid.state==ProfileState::Unavailable&&calls==before);
    }
    std::wstring exact=L"C:/"+std::wstring(31997,L'x');
    CHECK(prepare_profile(exact,true,guard,fake_prepare).code==ProfileCode::Ok&&seen.size()==32000);
    for(uint32_t code=1;code<=5;++code){
        response={16,1,code,0};const auto error=prepare_profile(local,false,guard,fake_prepare);
        constexpr ProfileCode expected[]={ProfileCode::Ok,ProfileCode::InvalidInput,ProfileCode::ProfileGuardFailed,ProfileCode::TransferBusy,ProfileCode::RecoveryRequired,ProfileCode::InternalError};
        CHECK(error.code==expected[code]&&error.state==ProfileState::Unavailable);
        response.state=2;CHECK(prepare_profile(local,false,guard,fake_prepare).code==ProfileCode::AbiMismatch);
    }
    for(const auto bad: {ValmaxLauncherProfileResult{0,1,0,2},{15,1,0,2},{17,1,0,2},{16,0,0,2},{16,2,0,2},{16,1,6,0},{16,1,0,0},{16,1,0,4}}){
        response=bad;result=prepare_profile(local,true,guard,fake_prepare);
        CHECK(result.code==ProfileCode::AbiMismatch&&result.state==ProfileState::Unavailable);
    }
    response={16,1,0,3};CHECK(prepare_profile(local,true,guard,fake_prepare).code==ProfileCode::AbiMismatch);
    response.state=1;CHECK(prepare_profile(local,false,guard,fake_prepare).code==ProfileCode::AbiMismatch);
    response={16,1,0,2};for(const int32_t code:{-1,1,2,INT32_MAX}){returned=code;CHECK(prepare_profile(local,true,guard,fake_prepare).code==ProfileCode::AbiMismatch);}returned=0;
    before=calls;ProfileResult wrong;
    std::thread worker([&]{wrong=prepare_profile(local,false,guard,fake_prepare);});worker.join();
    CHECK(wrong.code==ProfileCode::GuardRequired&&calls==before&&guard.owned_by_current_thread());
    CHECK(guard.release()==TransferCode::Ok);
    CHECK(prepare_profile(local,false,guard,fake_prepare).code==ProfileCode::GuardRequired&&calls==before);
    CHECK(std::string(profile_code(ProfileCode::RecoveryRequired))=="recovery_required");
    std::cout<<"{\"schema\":1,\"ok\":true,\"checks\":"<<checks<<",\"productionMutexAcquired\":false,\"realCorePrepareCalled\":false,\"scope\":\"strict UTF-16/UTF-8, caller guard, injected typed ABI and finite failure mapping\"}\n";
}
