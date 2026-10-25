// System-only API-set resolution probe. Never invoke the four resolved functions.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cwchar>

namespace {
struct Symbol {
    const char* name;
    const wchar_t* provider;
};
constexpr std::array<Symbol, 3> kSynch{{
    {"WaitOnAddress", L"kernelbase.dll"},
    {"WakeByAddressAll", L"ntdll.dll"},
    {"WakeByAddressSingle", L"ntdll.dll"},
}};
constexpr std::array<Symbol, 1> kHandle{{
    {"CompareObjectHandles", L"kernelbase.dll"},
}};

void wide_json(const wchar_t* text) noexcept {
    std::fputc('"', stdout);
    for (const wchar_t* p = text; *p != L'\0'; ++p) {
        const auto value = static_cast<unsigned int>(*p);
        if (value >= 0x20U && value < 0x7fU && *p != L'"' && *p != L'\\') {
            std::fputc(static_cast<int>(value), stdout);
        } else {
            std::printf("\\u%04x", value);
        }
    }
    std::fputc('"', stdout);
}

template <std::size_t Count>
bool contract(const wchar_t* name, const std::array<Symbol, Count>& symbols,
              const wchar_t* system, bool& first, unsigned int& passed) noexcept {
    const HMODULE loaded = LoadLibraryExW(name, nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    const DWORD load_error = loaded == nullptr ? GetLastError() : ERROR_SUCCESS;
    bool complete = loaded != nullptr;
    for (const auto& symbol : symbols) {
        std::array<wchar_t, 32768> provider_path{};
        std::array<wchar_t, 32768> expected_path{};
        DWORD error = load_error;
        bool matched = false;
        if (loaded != nullptr) {
            const FARPROC address = GetProcAddress(loaded, symbol.name);
            error = address == nullptr ? GetLastError() : ERROR_SUCCESS;
            if (address != nullptr) {
                HMODULE provider = nullptr;
                const auto location = reinterpret_cast<LPCWSTR>(reinterpret_cast<std::uintptr_t>(address));
                if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                      GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, location, &provider)) {
                    const DWORD length = GetModuleFileNameW(provider, provider_path.data(),
                                                           static_cast<DWORD>(provider_path.size()));
                    if (length == 0U || length >= provider_path.size()) {
                        error = length == 0U ? GetLastError() : ERROR_INSUFFICIENT_BUFFER;
                    } else if (wcscpy_s(expected_path.data(), expected_path.size(), system) != 0 ||
                               wcscat_s(expected_path.data(), expected_path.size(), L"\\") != 0 ||
                               wcscat_s(expected_path.data(), expected_path.size(), symbol.provider) != 0) {
                        error = ERROR_INSUFFICIENT_BUFFER;
                    } else {
                        matched = _wcsicmp(provider_path.data(), expected_path.data()) == 0;
                        if (!matched) error = ERROR_INVALID_DLL;
                    }
                } else {
                    error = GetLastError();
                }
            }
        }
        if (matched) ++passed;
        complete = complete && matched;
        if (!first) std::fputc(',', stdout);
        first = false;
        std::fputs("{\"contract\":", stdout);
        wide_json(name);
        std::printf(",\"symbol\":\"%s\",\"expectedProvider\":", symbol.name);
        wide_json(symbol.provider);
        std::fputs(",\"providerPath\":", stdout);
        wide_json(provider_path.data());
        std::printf(",\"providerMatchesExpected\":%s,\"error\":%lu}",
                    matched ? "true" : "false", static_cast<unsigned long>(error));
    }
    if (loaded != nullptr && !FreeLibrary(loaded)) complete = false;
    return complete;
}
} // namespace

int wmain(int argc, wchar_t**) {
    if (argc != 1) return 2; // No path, module, symbol or policy overrides.
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    if (!SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32)) return 3;
    std::array<wchar_t, 32768> system{};
    const UINT length = GetSystemDirectoryW(system.data(), static_cast<UINT>(system.size()));
    if (length == 0U || length >= system.size()) return 4;
    bool first = true;
    unsigned int passed = 0U;
    std::fputs("{\"schema\":1,\"system32SearchOnly\":true,\"resolvedFunctionsCalled\":false,\"results\":[", stdout);
    const bool synch = contract(L"api-ms-win-core-synch-l1-2-0.dll", kSynch, system.data(), first, passed);
    const bool handle = contract(L"api-ms-win-core-handle-l1-1-0.dll", kHandle, system.data(), first, passed);
    const bool ok = synch && handle && passed == 4U;
    std::printf("],\"checks\":%u,\"ok\":%s}\n", passed, ok ? "true" : "false");
    return ok ? 0 : 1;
}
