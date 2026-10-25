#include "webview2_probe.h"
#include <windows.h>
#include <objbase.h>
#include <cstring>
#include <string>

namespace valmax::launcher {
EvergreenProbeResult probe_evergreen_runtime(std::wstring_view loader_path) noexcept {
    if (loader_path.empty() || loader_path.size() >= 32768 || loader_path.find(L'\0') != loader_path.npos)
        return EvergreenProbeResult::LoaderUnavailable;
    try {
        const std::wstring path(loader_path);
        const HMODULE module = LoadLibraryExW(path.c_str(), nullptr,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!module) return EvergreenProbeResult::LoaderUnavailable;
        struct LoadedModule { HMODULE value; ~LoadedModule() { FreeLibrary(value); } } loaded_module{module};
        const FARPROC proc = GetProcAddress(module, "GetAvailableCoreWebView2BrowserVersionString");
        using VersionFunction = HRESULT (STDAPICALLTYPE *)(PCWSTR, LPWSTR *);
        VersionFunction version_function = nullptr;
        static_assert(sizeof(version_function) == sizeof(proc));
        std::memcpy(&version_function, &proc, sizeof(proc));
        if (!version_function) return EvergreenProbeResult::RuntimeUnavailable;
        LPWSTR version = nullptr;
        const HRESULT result = version_function(nullptr, &version);
        const bool available = SUCCEEDED(result) && version && version[0] != L'\0';
        if (version) CoTaskMemFree(version);
        return available ? EvergreenProbeResult::Available : EvergreenProbeResult::RuntimeUnavailable;
    } catch (...) {
        return EvergreenProbeResult::RuntimeUnavailable;
    }
}
}
