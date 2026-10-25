#include "startup_context.h"
#include <windows.h>
#include <array>
#include <iostream>
#include <stdexcept>
using namespace valmax::startup;
namespace {
int checks=0;
void check(bool value){++checks;if(!value)throw std::runtime_error("context_check_failed");}
}
int main(){
    try{
        check(package_presence(APPMODEL_ERROR_NO_PACKAGE,0)==PackagePresence::Absent);
        check(package_presence(ERROR_INSUFFICIENT_BUFFER,2)==PackagePresence::Present);
        check(package_presence(ERROR_INSUFFICIENT_BUFFER,32768)==PackagePresence::Present);
        for(auto status:std::array<int32_t,4>{ERROR_SUCCESS,ERROR_ACCESS_DENIED,ERROR_INVALID_PARAMETER,-1})
            check(package_presence(status,128)==PackagePresence::Unknown);
        for(auto length:{0u,1u,32769u,UINT32_MAX})
            check(package_presence(ERROR_INSUFFICIENT_BUFFER,length)==PackagePresence::Unknown);
        check(container_presence(true,true,sizeof(DWORD),0)==ContainerPresence::Absent);
        check(container_presence(true,true,sizeof(DWORD),1)==ContainerPresence::Present);
        check(container_presence(false,true,sizeof(DWORD),0)==ContainerPresence::Unknown);
        check(container_presence(true,false,sizeof(DWORD),0)==ContainerPresence::Unknown);
        for(auto length:{0u,3u,5u,UINT32_MAX})check(container_presence(true,true,length,0)==ContainerPresence::Unknown);
        check(container_presence(true,true,sizeof(DWORD),2)==ContainerPresence::Unknown);
        const std::array packages={PackagePresence::Absent,PackagePresence::Present,PackagePresence::Unknown};
        const std::array containers={ContainerPresence::Absent,ContainerPresence::Present,ContainerPresence::Unknown};
        int desktop=0;
        for(auto family:packages)for(auto full:packages)for(auto container:containers){
            const auto actual=classify({family,full,container});
            const bool unknown=family==PackagePresence::Unknown||full==PackagePresence::Unknown||container==ContainerPresence::Unknown;
            const auto expected=unknown?Context::Unknown:container==ContainerPresence::Present?Context::AppContainer:
                family==PackagePresence::Present||full==PackagePresence::Present?Context::Packaged:Context::Desktop;
            check(actual==expected);if(actual==Context::Desktop)++desktop;
        }
        check(desktop==1);
        check(classify({static_cast<PackagePresence>(99),PackagePresence::Absent,ContainerPresence::Absent})==Context::Unknown);
        check(classify({PackagePresence::Absent,PackagePresence::Absent,static_cast<ContainerPresence>(99)})==Context::Unknown);
        check(classify({})==Context::Unknown);
        check(refusal_message()!=nullptr&&std::wstring_view(refusal_message()).find(L"Пуск")!=std::wstring_view::npos);
        // Own-process read-only metadata only. A packaged/unknown host is a valid
        // observation; this executable never proceeds to KnownFolder or profiles.
        const auto observed=classify(observe_current());
        check(observed==Context::Desktop||observed==Context::Packaged||observed==Context::AppContainer||observed==Context::Unknown);
        std::cout<<"{\"ok\":true,\"checks\":"<<checks<<",\"ownContext\":"<<static_cast<uint32_t>(observed)
            <<",\"packageNamesOrSidRead\":false,\"profileOrCredentialCalls\":false,\"processesSpawned\":false}\n";return 0;
    }catch(...){std::cerr<<"startup_context_test_failed\n";return 1;}
}
