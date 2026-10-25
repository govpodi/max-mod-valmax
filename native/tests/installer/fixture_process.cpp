#include <windows.h>
#include <cwchar>

// Owned installer fixture only. Never loads MAX, a profile, Qt or the network.
static bool write_fixture_marker(const wchar_t* variable) {
    wchar_t path[4096];
    const DWORD length = GetEnvironmentVariableW(variable, path, 4096);
    if (length == 0 || length >= 4096 ||
        (wcsstr(path, L"D:\\ValMAXBuild\\fixtures\\installer-") != path &&
         wcsstr(path, L"E:\\ValMAXBuild\\fixtures\\installer-") != path)) {
        return false;
    }
    HANDLE file = CreateFileW(path, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    CloseHandle(file);
    return true;
}

int wmain(int argc, wchar_t** argv) {
    if (argc == 2 && wcscmp(argv[1], L"/fixture-short") == 0) {
        Sleep(5000);
        return 0;
    }
    if (argc == 3 && wcscmp(argv[1], L"/silent") == 0 &&
        wcscmp(argv[2], L"/install") == 0) {
        if (!write_fixture_marker(L"VALMAX_FIXTURE_WEBVIEW2_CALLED")) return 90;
        wchar_t mode[64];
        const DWORD length = GetEnvironmentVariableW(L"VALMAX_FIXTURE_WEBVIEW2_MODE",
                                                      mode, 64);
        if (length == 0 || length >= 64) return 91;
        if (wcscmp(mode, L"failure") == 0) return 42;
        if (wcscmp(mode, L"restart") == 0) return 3010;
        if (wcscmp(mode, L"restart_initiated") == 0) return 1641;
        if (wcscmp(mode, L"success_no_registry") == 0) return 0;
        if (wcscmp(mode, L"success") == 0 &&
            write_fixture_marker(L"VALMAX_FIXTURE_WEBVIEW2_PRESENT")) return 0;
        return 92;
    }
    Sleep(60000);
    return 0;
}
