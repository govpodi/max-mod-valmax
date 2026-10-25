// Synthetic child with a source-owned gate before its simulated producer.
// The real MAX executable has no corresponding entrypoint contract.
#include <windows.h>

extern "C" __declspec(dllimport) int valmax_synthetic_target() noexcept;

namespace {
HANDLE event_from_environment(const wchar_t *key, DWORD access)
{
    wchar_t name[192]{};
    const DWORD length = GetEnvironmentVariableW(key, name, 192);
    return length > 0 && length < 192 ? OpenEventW(access, FALSE, name) : nullptr;
}

bool signal(const wchar_t *key)
{
    const HANDLE event = event_from_environment(key, EVENT_MODIFY_STATE);
    if (!event) return false;
    const BOOL signaled = SetEvent(event);
    CloseHandle(event);
    return signaled != FALSE;
}
}

int wmain()
{
    if (!signal(L"VALMAX_SYNTHETIC_MAIN_EVENT")) return 2;
    const HANDLE proceed = event_from_environment(L"VALMAX_SYNTHETIC_PROCEED_EVENT", SYNCHRONIZE);
    if (!proceed) return 3;
    const DWORD waited = WaitForSingleObject(proceed, 4000);
    CloseHandle(proceed);
    if (waited != WAIT_OBJECT_0) return 4;
    if (!signal(L"VALMAX_SYNTHETIC_PRODUCER_EVENT")) return 5;
    return valmax_synthetic_target() == 7 ? 0 : 6;
}
