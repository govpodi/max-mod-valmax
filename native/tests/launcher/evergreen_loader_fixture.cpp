#include <windows.h>
#include <objbase.h>
#include <cstring>

extern "C" __declspec(dllexport) HRESULT STDAPICALLTYPE
GetAvailableCoreWebView2BrowserVersionString(PCWSTR browser_executable_folder, LPWSTR *version) {
    if (browser_executable_folder || !version) return E_INVALIDARG;
#ifdef VALMAX_FAKE_RUNTIME_MISSING
    *version = nullptr;
    return HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
#else
    constexpr wchar_t value[] = L"155.0.0.0";
    auto *buffer = static_cast<wchar_t *>(CoTaskMemAlloc(sizeof(value)));
    if (!buffer) return E_OUTOFMEMORY;
    std::memcpy(buffer, value, sizeof(value));
    *version = buffer;
    return S_OK;
#endif
}
