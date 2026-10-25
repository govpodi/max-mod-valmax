#include "launch_session.h"
#include "launcher_pins.generated.h"
#include "../common/startup_context.h"
#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#include <array>
#include <filesystem>
#include <vector>

namespace {
const wchar_t* message(valmax::launcher::LaunchCode code) {
    using C=valmax::launcher::LaunchCode;
    switch(code){
    case C::InvalidOptions:return L"Неизвестная команда ValMAX. Запустите обычный ярлык, «Войти с MData» или «Восстановление MData».";
    case C::PayloadInvalid:case C::CoreUnavailable:return L"Не удалось проверить файлы ValMAX. Повторите установку из полного установщика.";
    case C::TransferBusy:return L"Сейчас выполняется перенос аккаунтов или другой запуск ValMAX. Дождитесь его завершения.";
    case C::RecoveryRequired:return L"Требуется восстановление MData. Откройте «Восстановление MData» в меню «Пуск».";
    case C::ActivationFailed:case C::ExistingUnavailable:return L"ValMAX уже работает, но её окно пока недоступно. Дождитесь запуска или откройте её через значок в трее.";
    case C::ProcessUnknown:return L"Не удалось проверить уже работающий клиент. Повторите запуск после его закрытия.";
    case C::ProfileFailed:return L"Не удалось безопасно подготовить настройки MXMOD. Существующие данные не заменены.";
    case C::ProfileInUse:return L"Другая копия изменённого MAX может использовать те же аккаунты. Полностью закройте её через значок в трее → «Выход» и повторите запуск. Аккаунты не изменены.";
    case C::UnsupportedContext:return valmax::startup::refusal_message();
    case C::RuntimeFailed:return L"Не удалось подготовить нативные настройки ValMAX. Клиент не запущен.";
    case C::EnvironmentFailed:return L"Не удалось подготовить окружение ValMAX. Клиент не запущен.";
    case C::ChildExited:return L"Запущенная программа завершилась до открытия окна. Повторите запуск; при повторении ошибки переустановите полный пакет ValMAX.";
    default:return L"Не удалось запустить ValMAX. Повторите запуск; при повторении ошибки переустановите полный пакет.";
    }
}
}
int WINAPI wWinMain(HINSTANCE,HINSTANCE,PWSTR,int) {
    using namespace valmax::launcher;
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32);
    LaunchResult result{LaunchCode::InternalError};
    try {
        int count=0;wchar_t** argv=CommandLineToArgvW(GetCommandLineW(),&count);
        struct FreeArgs{wchar_t** value;~FreeArgs(){if(value)LocalFree(value);}}args{argv};
        if(!argv||count<1)throw 0;
        std::vector<std::wstring_view> arguments;
        for(int i=1;i<count;++i)arguments.emplace_back(argv[i]);
        LaunchAction action;
        if(!parse_action(arguments,action))result.code=LaunchCode::InvalidOptions;
        else if(valmax::startup::classify(valmax::startup::observe_current())!=valmax::startup::Context::Desktop)
            result.code=LaunchCode::UnsupportedContext;
        else {
            std::array<wchar_t,32768> module{};
            const DWORD length=GetModuleFileNameW(nullptr,module.data(),static_cast<DWORD>(module.size()));
            if(!length||length>=module.size())throw 0;
            PWSTR local=nullptr;
            const HRESULT status=SHGetKnownFolderPath(FOLDERID_LocalAppData,
                KF_FLAG_DONT_VERIFY|KF_FLAG_NO_PACKAGE_REDIRECTION,nullptr,&local);
            struct FreePath{PWSTR value;~FreePath(){CoTaskMemFree(value);}}folder{local};
            if(FAILED(status)||!local)throw 0;
            LaunchOptions options{std::filesystem::path(std::wstring(module.data(),length)).parent_path().wstring(),local};
            // Driver destruction releases package/module/mutex handles BEFORE
            // displaying an error. No caller-provided runtime path or hash.
            Win32LaunchDriver driver(std::move(options),pins::kFiles);
            result=run_launch(driver,action);
        }
    }catch(...){result.code=LaunchCode::InternalError;}
    if(result.success())return 0;
    std::wstring detail=message(result.code);
    if(result.code==LaunchCode::EnvironmentFailed&&result.environment_error!=EnvironmentError::Ok){
        detail+=L"\n\nКод: ";
        const std::string_view finite=environment_code(result.environment_error);
        detail.append(finite.begin(),finite.end());
    }
    MessageBoxW(nullptr,detail.c_str(),L"ValMAX",MB_OK|MB_ICONERROR);
    return static_cast<int>(result.code)+1;
}
