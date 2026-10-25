#include "startup_context.h"
#include <windows.h>
#include <appmodel.h>

namespace valmax::startup {
PackagePresence package_presence(int32_t status,uint32_t required) noexcept {
    if(status==APPMODEL_ERROR_NO_PACKAGE)return PackagePresence::Absent;
    if(status==ERROR_INSUFFICIENT_BUFFER&&required>1&&required<=32768)return PackagePresence::Present;
    return PackagePresence::Unknown;
}
ContainerPresence container_presence(bool opened,bool read,uint32_t returned,uint32_t value) noexcept {
    if(!opened||!read||returned!=sizeof(DWORD)||value>1)return ContainerPresence::Unknown;
    return value?ContainerPresence::Present:ContainerPresence::Absent;
}
Context classify(const Observation& value) noexcept {
    const auto valid_package=[](PackagePresence part){return part==PackagePresence::Absent||part==PackagePresence::Present;};
    if(!valid_package(value.family)||!valid_package(value.full_name)
        ||(value.container!=ContainerPresence::Absent&&value.container!=ContainerPresence::Present))return Context::Unknown;
    if(value.container==ContainerPresence::Present)return Context::AppContainer;
    if(value.family==PackagePresence::Present||value.full_name==PackagePresence::Present)return Context::Packaged;
    return Context::Desktop;
}
Observation observe_current() noexcept {
    Observation result;
    UINT32 family_characters=0,full_characters=0;
    // Presence-only first queries. Package names are never requested or logged.
    const LONG family_status=GetCurrentPackageFamilyName(&family_characters,nullptr);
    const LONG full_status=GetCurrentPackageFullName(&full_characters,nullptr);
    result.family=package_presence(family_status,family_characters);
    result.full_name=package_presence(full_status,full_characters);
    HANDLE token=nullptr;
    if(!OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&token))return result;
    DWORD container=0,returned=0;
    const bool read=GetTokenInformation(token,TokenIsAppContainer,&container,sizeof(container),&returned)!=FALSE;
    CloseHandle(token);
    result.container=container_presence(true,read,returned,container);
    return result;
}
const wchar_t* refusal_message() noexcept {
    return L"Не удалось подтвердить обычное окружение рабочего стола Windows. "
        L"Закройте это окно и откройте ValMAX обычным ярлыком из меню «Пуск». "
        L"Аккаунты не изменены.";
}
}
