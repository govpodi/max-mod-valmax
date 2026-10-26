#include <windows.h>
#include <array>
#include <filesystem>
#include <string>
#include <string_view>
#include <cstdint>
#include "../../common/window_activation.h"

LRESULT CALLBACK activation_window_proc(HWND window,UINT message,WPARAM wparam,LPARAM lparam){
    static const UINT activation_message=RegisterWindowMessageW(valmax::activation::kMessageName);
    if(activation_message && message==activation_message){
        if(wparam==valmax::activation::kActivate){
            ShowWindow(window,SW_SHOW);
            return static_cast<LRESULT>(valmax::activation::kAck);
        }
        if(wparam==valmax::activation::kQueryVisible)
            return IsWindowVisible(window)?static_cast<LRESULT>(valmax::activation::kAck):0;
        return 0;
    }
    return DefWindowProcW(window,message,wparam,lparam);
}

std::wstring env(const wchar_t* key){
    std::array<wchar_t,32768> value{};const auto n=GetEnvironmentVariableW(key,value.data(),static_cast<DWORD>(value.size()));
    return n&&n<value.size()?std::wstring(value.data(),n):std::wstring{};
}
std::wstring json_string(std::wstring_view value){
    std::wstring result=L"\"";
    for(const auto c:value){if(c==L'\\'||c==L'\"')result.push_back(L'\\');result.push_back(c);}
    return result+L"\"";
}
int WINAPI wWinMain(HINSTANCE,HINSTANCE,PWSTR,int){
    SetErrorMode(0x8003);
    const auto report=env(L"VALMAX_LAUNCH_TEST_REPORT"),stop=env(L"VALMAX_LAUNCH_TEST_STOP");
    if(!(report.starts_with(L"D:\\ValMAXBuild\\fixtures\\native-launcher\\")||
         report.starts_with(L"E:\\ValMAXBuild\\fixtures\\native-launcher\\"))||stop.empty())return 2;
    const auto config=env(L"VALMAX_NATIVE_RUNTIME_CONFIG");
    const bool helper=std::wstring(GetCommandLineW()).find(L"valmax-transfer.exe")!=std::wstring::npos;
    const bool recovery=std::wstring(GetCommandLineW()).find(L"--recover")!=std::wstring::npos;
    const bool config_ok=helper?config.empty():config.find(L"\"existingClient\":true")!=config.npos;
    const auto expected_profile=env(L"VALMAX_LAUNCH_TEST_EXPECT_PROFILE"),expected_runtime=env(L"VALMAX_LAUNCH_TEST_EXPECT_RUNTIME");
    const bool paths_ok=helper?config.empty():(expected_profile.empty()&&expected_runtime.empty())||
        (config.find(L"\"profileDirectory\":"+json_string(expected_profile))!=config.npos
         &&config.find(L"\"runtimeDirectory\":"+json_string(expected_runtime))!=config.npos);
    const bool sanitized=env(L"QT_PLUGIN_PATH").empty();
    std::array<wchar_t,32768> cwd{},module{};
    const auto c=GetCurrentDirectoryW(static_cast<DWORD>(cwd.size()),cwd.data());
    const auto m=GetModuleFileNameW(nullptr,module.data(),static_cast<DWORD>(module.size()));
    const bool cwd_ok=c&&m&&c<cwd.size()&&m<module.size()
        &&std::filesystem::path(std::wstring(module.data(),m)).parent_path()==std::filesystem::path(std::wstring(cwd.data(),c));
    const auto instance=GetModuleHandleW(nullptr);
    WNDCLASSW wc{};wc.hInstance=instance;wc.lpszClassName=L"Qt6QWindowIcon";wc.lpfnWndProc=activation_window_proc;
    if(!RegisterClassW(&wc))return 7;
    // A deliberately hidden own window exercises the tray-restoration path.
    const auto window=CreateWindowExW(0,wc.lpszClassName,L"ValMAX owned launcher fixture",WS_OVERLAPPEDWINDOW,
        0,0,320,240,nullptr,nullptr,instance,nullptr);
    if(!window)return 8;
    const std::string output=std::string("{\"configOk\":")+(config_ok?"true":"false")+",\"sanitized\":"+(sanitized?"true":"false")
        +",\"pathsOk\":"+(paths_ok?"true":"false")
        +",\"cwdOk\":"+(cwd_ok?"true":"false")+",\"helper\":"+(helper?"true":"false")+",\"recovery\":"+(recovery?"true":"false")
        +",\"pid\":"+std::to_string(GetCurrentProcessId())+",\"window\":"+std::to_string(reinterpret_cast<uintptr_t>(window))+"}";
    const auto file=CreateFileW(report.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr);
    if(file==INVALID_HANDLE_VALUE)return 3;DWORD written=0;const bool ok=WriteFile(file,output.data(),static_cast<DWORD>(output.size()),&written,nullptr)
        &&written==output.size()&&FlushFileBuffers(file);CloseHandle(file);if(!ok)return 4;
    const auto event=OpenEventW(SYNCHRONIZE,FALSE,stop.c_str());if(!event)return 5;
    const auto deadline=GetTickCount64()+20000;
    bool stopped=false;
    while(GetTickCount64()<deadline){
        const auto result=MsgWaitForMultipleObjects(1,&event,FALSE,100,QS_ALLINPUT);
        if(result==WAIT_OBJECT_0){stopped=true;break;}
        if(result==WAIT_FAILED)break;
        MSG msg{};while(PeekMessageW(&msg,nullptr,0,0,PM_REMOVE)){TranslateMessage(&msg);DispatchMessageW(&msg);}
    }
    CloseHandle(event);DestroyWindow(window);return stopped?0:6;
}
