// Owned synthetic import. DllMain only signals a test event; no MAX image,
// profile, hook, remote process, network client or authentication is involved.
#include <windows.h>

extern "C" __declspec(dllexport) int valmax_synthetic_target() noexcept
{
    return 7;
}

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        wchar_t name[192]{};
        const DWORD length = GetEnvironmentVariableW(L"VALMAX_SYNTHETIC_DLL_EVENT", name, 192);
        if (length > 0 && length < 192) {
            const HANDLE event = OpenEventW(EVENT_MODIFY_STATE, FALSE, name);
            if (event) {
                SetEvent(event);
                CloseHandle(event);
            }
        }
    }
    return TRUE;
}
