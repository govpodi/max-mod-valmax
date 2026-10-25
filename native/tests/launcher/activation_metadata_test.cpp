#include "activation.h"
#include <windows.h>
#include <iostream>
using namespace valmax::launcher;
namespace {unsigned checks=0;void check(bool value,const char *name){if(!value){std::cerr<<"FAIL "<<name<<'\n';std::exit(1);}++checks;}}
int main() {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    const wchar_t *name=L"Qt6112QWindowIcon{48274eed-7afd-4af9-b8fc-f0acd38b0baf}";
    WNDCLASSW wc{};wc.lpfnWndProc=DefWindowProcW;wc.hInstance=GetModuleHandleW(nullptr);wc.lpszClassName=name;
    check(RegisterClassW(&wc)!=0,"register own synthetic class");
    const HWND window=CreateWindowExW(0,name,L"",WS_OVERLAPPEDWINDOW,0,0,900,700,nullptr,nullptr,wc.hInstance,nullptr);
    check(window!=nullptr && !IsWindowVisible(window),"create own hidden window without WS_VISIBLE");
    Win32WindowApi api;WindowInfo info;
    check(api.info(reinterpret_cast<Window>(window),info) && info.pid==GetCurrentProcessId()
        && !info.visible && !info.iconic && info.owner==0 && eligible_main_window(info),"native metadata exact own HWND");
    const auto process=api.open_process(GetCurrentProcessId());check(process!=0,"limited own-process metadata handle");
    std::wstring image;check(api.alive(process) && api.image(process,image) && !image.empty(),"own process image/liveness only");
    api.close(process);
    check(!IsWindowVisible(window) && GetForegroundWindow()!=window,"no show/foreground call made");
    check(DestroyWindow(window)!=0,"destroy only owned hidden fixture");
    check(UnregisterClassW(name,wc.hInstance)!=0,"unregister owned synthetic class");
    std::cout<<"{\"schema\":1,\"checks\":"<<checks<<",\"passed\":true,\"windowApi\":\"Win32-own-hidden-window\",\"enumWindowsCalled\":false,\"showCalls\":0,\"foregroundCalls\":0,\"realUserWindowsTouched\":false}\n";
}
