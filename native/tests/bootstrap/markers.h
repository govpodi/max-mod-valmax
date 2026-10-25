#pragma once
#include <windows.h>
#include <cstdio>
// Constant test markers only. No Qt logging, profile data or file discovery.
inline void marker(const char *name) noexcept {
    LARGE_INTEGER ticks{};QueryPerformanceCounter(&ticks);
    char line[192]{};
    const int length=std::snprintf(line,sizeof(line),"{\"marker\":\"%s\",\"thread\":%lu,\"tick\":%lld}\n",
        name,GetCurrentThreadId(),static_cast<long long>(ticks.QuadPart));
    if(length<=0 || length>=static_cast<int>(sizeof(line)))return;
    DWORD written=0;WriteFile(GetStdHandle(STD_OUTPUT_HANDLE),line,static_cast<DWORD>(length),&written,nullptr);
}
