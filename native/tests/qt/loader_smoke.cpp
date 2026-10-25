// Loader-only smoke probe. No Qt headers, instances, exports, QML or application APIs.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <psapi.h>
#include <bcrypt.h>
#include <array>
#include <cstdint>
#include <exception>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include "loader_smoke_pins.h"

namespace {
unsigned checks = 0;
void check(bool value, const char* reason) { ++checks; if (!value) throw std::runtime_error(reason); }
std::string json(const std::wstring& text) {
    check(text.size() < 32768, "text bound");
    const int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    check(count >= 0, "UTF8 size");
    std::string utf8(static_cast<size_t>(count), '\0');
    check(WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), utf8.data(), count, nullptr, nullptr) == count, "UTF8 conversion");
    std::string result = "\"";
    for (const auto ch : utf8) {
        if (ch == '\\' || ch == '"') result += '\\';
        check(static_cast<unsigned char>(ch) >= 32, "unexpected control character");
        result += ch;
    }
    return result + "\"";
}
std::wstring module_name(HMODULE module) {
    std::array<wchar_t, 32768> name{};
    const auto count = GetModuleFileNameW(module, name.data(), static_cast<DWORD>(name.size()));
    check(count > 0 && count < name.size(), "module filename bound");
    return {name.data(), count};
}
bool equal(const std::wstring& a, const std::wstring& b) {
    return CompareStringOrdinal(a.data(), static_cast<int>(a.size()), b.data(), static_cast<int>(b.size()), TRUE) == CSTR_EQUAL;
}
bool below(const std::wstring& path, const std::wstring& directory) {
    return path.size() > directory.size() && path[directory.size()] == L'\\' && equal(path.substr(0, directory.size()), directory);
}
struct File {
    HANDLE handle = INVALID_HANDLE_VALUE;
    explicit File(const std::wstring& path) {
        // Keep the observed image pathname stable across hashing and loading.
        handle = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        check(handle != INVALID_HANDLE_VALUE, "open pinned file");
        BY_HANDLE_FILE_INFORMATION info{};
        check(GetFileInformationByHandle(handle, &info) != 0, "file information");
        check(!(info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)), "regular pinned file");
    }
    File(const File&) = delete;
    File& operator=(const File&) = delete;
    ~File() { if (handle != INVALID_HANDLE_VALUE) CloseHandle(handle); }
};
std::string digest(HANDLE file) {
    LARGE_INTEGER offset{};
    check(SetFilePointerEx(file, offset, nullptr, FILE_BEGIN) != 0, "rewind hash input");
    BCRYPT_ALG_HANDLE algorithm{};
    BCRYPT_HASH_HANDLE hash{};
    check(BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0, "SHA provider");
    check(BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0, "SHA context");
    std::array<unsigned char, 65536> block{};
    uint64_t total = 0;
    for (;;) {
        DWORD count = 0;
        check(ReadFile(file, block.data(), static_cast<DWORD>(block.size()), &count, nullptr) != 0, "hash read");
        if (!count) break;
        total += count;
        check(total <= (uint64_t{1} << 31), "hash input bound");
        check(BCryptHashData(hash, block.data(), count, 0) >= 0, "SHA update");
    }
    std::array<unsigned char, 32> value{};
    check(BCryptFinishHash(hash, value.data(), static_cast<ULONG>(value.size()), 0) >= 0, "SHA finish");
    BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    constexpr char hex[] = "0123456789abcdef";
    std::string result;
    for (const auto ch : value) { result += hex[ch >> 4]; result += hex[ch & 15]; }
    return result;
}
std::string digest(const std::wstring& path) { File file(path); return digest(file.handle); }
std::string modules(const std::wstring& self, const std::wstring& windows) {
    std::array<HMODULE, 256> values{};
    DWORD needed = 0;
    check(EnumProcessModules(GetCurrentProcess(), values.data(), static_cast<DWORD>(sizeof(values)), &needed) != 0,
          "enumerate own modules");
    check(needed && needed <= sizeof(values) && needed % sizeof(HMODULE) == 0, "module inventory bound");
    std::ostringstream output;
    output << '[';
    bool first = true;
    for (size_t index = 0; index < needed / sizeof(HMODULE); ++index) {
        const auto path = module_name(values[index]);
        const char* expected = nullptr;
        for (const auto& pin : kAllowed) {
            if (equal(path, std::wstring(kRuntime) + L"\\" + pin.relative)) { expected = pin.sha256; break; }
        }
        const bool system = below(path, windows + L"\\System32") || below(path, windows + L"\\WinSxS");
        const bool own = equal(path, self);
        check(expected || system || own, "module outside exact FINAL Qt/VC allowlist and Windows");
        const auto hash = digest(path);
        if (expected) check(hash == expected, "loaded FINAL module SHA256 mismatch");
        if (!first) output << ',';
        first = false;
        output << "{\"path\":" << json(path) << ",\"sha256\":\"" << hash
               << "\",\"source\":\"" << (expected ? "FINAL" : system ? "Windows" : "probe") << "\"}";
    }
    output << ']';
    return output.str();
}
}

int wmain(int argc, wchar_t**) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    try {
        check(argc == 1, "probe accepts no runtime/path/config arguments");
        std::array<wchar_t, 32768> windows_buffer{};
        const auto windows_count = GetWindowsDirectoryW(windows_buffer.data(), static_cast<UINT>(windows_buffer.size()));
        check(windows_count > 0 && windows_count < windows_buffer.size(), "Windows directory");
        const std::wstring windows(windows_buffer.data(), windows_count), self = module_name(nullptr);
        check(SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_USER_DIRS | LOAD_LIBRARY_SEARCH_SYSTEM32) != 0, "strict default DLL directories");
        check(SetDllDirectoryW(L"") != 0, "remove current directory search");
        const auto cookie = AddDllDirectory((std::wstring(kRuntime) + L"\\mod").c_str());
        check(cookie != nullptr, "add only FINAL mod user directory");
        std::vector<HMODULE> handles;
        std::ostringstream targets;
        targets << '[';
        bool first = true;
        for (const auto& target : kTargets) {
            const auto path = std::wstring(kRuntime) + L"\\" + target.relative;
            File pin(path);
            check(digest(pin.handle) == target.sha256, "pre-load target SHA mismatch");
            const auto loaded = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_USER_DIRS | LOAD_LIBRARY_SEARCH_SYSTEM32);
            if (!loaded) { std::cerr << "LoadLibraryExW failed with Windows code " << GetLastError() << '\n'; }
            check(loaded != nullptr, "exact Qt library load");
            handles.push_back(loaded);
            check(equal(module_name(loaded), path), "loaded target path mismatch");
            check(digest(pin.handle) == target.sha256, "post-load pinned target SHA mismatch");
            // Inspect after each load. No plugin instance or export is invoked.
            (void)modules(self, windows);
            if (!first) targets << ',';
            first = false;
            targets << "{\"path\":" << json(path) << ",\"sha256\":\"" << target.sha256 << "\",\"loaded\":true}";
        }
        targets << ']';
        const auto loaded_modules = modules(self, windows);
        for (auto it = handles.rbegin(); it != handles.rend(); ++it) check(FreeLibrary(*it) != 0, "unload own Qt handle");
        check(RemoveDllDirectory(cookie) != 0, "remove user directory");
        std::ostringstream output;
        output << "{\"schema\":1,\"ok\":true,\"checks\":" << checks
               << ",\"targets\":" << targets.str() << ",\"modules\":" << loaded_modules
               << ",\"pluginInstancesCreated\":false,\"qtEnginesCreated\":false,\"exportsInvoked\":false,\"hooksLoaded\":false}";
        check(output.str().size() <= (1 << 20), "output bound");
        std::cout << output.str() << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "loader_smoke: " << error.what() << '\n';
        return 1;
    }
}
