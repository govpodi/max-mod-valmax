#include "privacy_internal.h"
#include <psapi.h>
#include <tlhelp32.h>
#include <algorithm>
#include <array>
#include <limits>
#include <string>
#include <vector>

namespace valmax::privacy {
namespace {
struct Handle final {
    HANDLE value = nullptr;
    explicit Handle(HANDLE input) : value(input) {}
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
    Handle(const Handle &) = delete;
    Handle &operator=(const Handle &) = delete;
    explicit operator bool() const { return value && value != INVALID_HANDLE_VALUE; }
};

std::wstring normalized(std::wstring value) {
    std::replace(value.begin(), value.end(), L'/', L'\\');
    if (value.starts_with(L"\\\\?\\")) value.erase(0, 4);
    return value;
}

bool same_path(const std::wstring &first, const std::wstring &second) {
    const auto left = normalized(first), right = normalized(second);
    return CompareStringOrdinal(left.c_str(), -1, right.c_str(), -1, TRUE) == CSTR_EQUAL;
}

bool read_remote(HANDLE process, uintptr_t address, void *output, size_t count) {
    if (!process || !address || !output || !count || address > UINTPTR_MAX - count) return false;
    SIZE_T copied = 0;
    return ReadProcessMemory(process, reinterpret_cast<const void *>(address), output, count, &copied)
        && copied == count;
}

template<class T> bool read_remote(HANDLE process, uintptr_t address, T &output) {
    return read_remote(process, address, &output, sizeof(output));
}

bool process_path(HANDLE process, std::wstring &output) {
    std::wstring value(32768, L'\0');
    DWORD length = static_cast<DWORD>(value.size());
    if (!QueryFullProcessImageNameW(process, 0, value.data(), &length) || !length || length >= value.size())
        return false;
    value.resize(length);
    output = normalized(std::move(value));
    return true;
}

bool module_path(HANDLE process, HMODULE module, std::wstring &output) {
    // MODULEENTRY32W::szExePath is limited to MAX_PATH, even on systems that
    // allow a longer install path. Ask for the full path using the held process.
    std::wstring value(32768, L'\0');
    const DWORD length = K32GetModuleFileNameExW(process, module, value.data(),
                                                 static_cast<DWORD>(value.size()));
    if (!length || length >= value.size()) return false;
    value.resize(length);
    output = normalized(std::move(value));
    return true;
}

bool current_direct_child(DWORD pid) {
    // The first snapshot can outlive its PID. Once OpenProcess succeeds, the
    // handle pins the process identity; check its parent in a fresh snapshot.
    Handle snapshot(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0));
    if (!snapshot) return false;
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    if (!Process32FirstW(snapshot.value, &entry)) return false;
    do {
        if (entry.th32ProcessID == pid)
            return entry.th32ParentProcessID == GetCurrentProcessId()
                && CompareStringOrdinal(entry.szExeFile, -1, L"max-service.exe", -1, TRUE) == CSTR_EQUAL;
    } while (Process32NextW(snapshot.value, &entry));
    return false;
}

uint32_t check_modules(HANDLE process, DWORD pid, const std::filesystem::path &root,
                       std::span<const FilePin> files) {
    HANDLE raw = INVALID_HANDLE_VALUE;
    for (unsigned attempt = 0; attempt < 3; ++attempt) {
        raw = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid);
        if (raw != INVALID_HANDLE_VALUE || GetLastError() != ERROR_BAD_LENGTH) break;
    }
    Handle snapshot(raw);
    if (!snapshot) return VALMAX_PRIVACY_IO_ERROR;
    MODULEENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    if (!Module32FirstW(snapshot.value, &entry)) return VALMAX_PRIVACY_IO_ERROR;
    std::vector<bool> seen(files.size(), false);
    do {
        for (size_t index = 0; index < files.size(); ++index) {
            const auto &pin = files[index];
            if (pin.role != VALMAX_PRIVACY_SERVICE) continue;
            const auto expected = root / pin.relative;
            if (CompareStringOrdinal(entry.szModule, -1, expected.filename().c_str(), -1, TRUE)
                != CSTR_EQUAL) continue;
            std::wstring actual;
            if (!module_path(process, entry.hModule, actual)) return VALMAX_PRIVACY_IO_ERROR;
            if (!same_path(actual, expected.wstring())) continue;
            if (seen[index] || entry.modBaseSize != pin.image_size
                || !remote_loaded_patches(process, reinterpret_cast<uintptr_t>(entry.modBaseAddr), pin))
                return VALMAX_PRIVACY_MEMORY_MISMATCH;
            seen[index] = true;
        }
    } while (Module32NextW(snapshot.value, &entry));
    if (GetLastError() != ERROR_NO_MORE_FILES) return VALMAX_PRIVACY_IO_ERROR;
    for (size_t index = 0; index < files.size(); ++index)
        if (files[index].role == VALMAX_PRIVACY_SERVICE && !seen[index])
            return VALMAX_PRIVACY_MODULE_NOT_LOADED;
    return VALMAX_PRIVACY_OK;
}
}

bool remote_loaded_patches(HANDLE process, uintptr_t base, const FilePin &pin) {
    if (!base || pin.image_size < sizeof(IMAGE_NT_HEADERS64)
        || base > UINTPTR_MAX - pin.image_size) return false;
    IMAGE_DOS_HEADER dos{};
    IMAGE_NT_HEADERS64 pe{};
    if (!read_remote(process, base, dos) || dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew <= 0
        || pin.image_size < sizeof(pe) || static_cast<uint32_t>(dos.e_lfanew) > pin.image_size - sizeof(pe)
        || !read_remote(process, base + static_cast<uint32_t>(dos.e_lfanew), pe)
        || pe.Signature != IMAGE_NT_SIGNATURE || pe.FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64
        || pe.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC
        || pe.OptionalHeader.SizeOfImage != pin.image_size) return false;
    for (const auto &patch : pin.patches) {
        if (patch.bytes.empty() || patch.bytes.size() > 32 || patch.rva >= pin.image_size
            || patch.bytes.size() > pin.image_size - patch.rva) return false;
        MEMORY_BASIC_INFORMATION memory{};
        if (!VirtualQueryEx(process, reinterpret_cast<const void *>(base + patch.rva), &memory, sizeof(memory))
            || memory.State != MEM_COMMIT || (memory.Protect & (PAGE_GUARD | PAGE_NOACCESS))
            || !(memory.Protect & (PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY))
            || memory.Type != MEM_IMAGE || memory.AllocationBase != reinterpret_cast<const void *>(base))
            return false;
        std::array<uint8_t, 32> actual{};
        if (!read_remote(process, base + patch.rva, actual.data(), patch.bytes.size())
            || !std::equal(patch.bytes.begin(), patch.bytes.end(), actual.begin())) return false;
    }
    return true;
}

uint32_t verify_service_processes(const std::filesystem::path &root, std::span<const FilePin> files,
                                  DWORD targetPid) {
    if (!root.is_absolute() || files.empty()) return VALMAX_PRIVACY_INVALID_ARGUMENT;
    const auto expected_exe = (root / L"max-service" / L"max-service.exe").wstring();
    bool has_exe_pin = false;
    for (const auto &pin : files)
        if (pin.role == VALMAX_PRIVACY_SERVICE && same_path((root / pin.relative).wstring(), expected_exe))
            has_exe_pin = true;
    if (!has_exe_pin) return VALMAX_PRIVACY_INVALID_ARGUMENT;
    Handle snapshot(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0));
    if (!snapshot) return VALMAX_PRIVACY_IO_ERROR;
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    if (!Process32FirstW(snapshot.value, &entry)) return VALMAX_PRIVACY_IO_ERROR;
    unsigned owned = 0;
    do {
        // The installed service is a direct child of this MAX process. Do not
        // open another user's or another installation's same-named process.
        if (targetPid ? entry.th32ProcessID != targetPid
                      : entry.th32ParentProcessID != GetCurrentProcessId()) continue;
        if (CompareStringOrdinal(entry.szExeFile, -1, L"max-service.exe", -1, TRUE) != CSTR_EQUAL)
            continue;
        Handle process(OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | SYNCHRONIZE,
                                   FALSE, entry.th32ProcessID));
        if (!process) return VALMAX_PRIVACY_IO_ERROR;
        std::wstring image;
        if (!process_path(process.value, image)) return VALMAX_PRIVACY_IO_ERROR;
        if (!same_path(image, expected_exe)) continue;
        if (!targetPid && !current_direct_child(entry.th32ProcessID)) return VALMAX_PRIVACY_IO_ERROR;
        if (++owned > 16 || WaitForSingleObject(process.value, 0) != WAIT_TIMEOUT)
            return VALMAX_PRIVACY_IO_ERROR;
        const auto result = check_modules(process.value, entry.th32ProcessID, root, files);
        if (result != VALMAX_PRIVACY_OK) return result;
        std::wstring after;
        if (!process_path(process.value, after) || !same_path(after, expected_exe)
            || WaitForSingleObject(process.value, 0) != WAIT_TIMEOUT)
            return VALMAX_PRIVACY_IO_ERROR;
    } while (Process32NextW(snapshot.value, &entry));
    if (GetLastError() != ERROR_NO_MORE_FILES) return VALMAX_PRIVACY_IO_ERROR;
    return owned ? VALMAX_PRIVACY_OK : VALMAX_PRIVACY_MODULE_NOT_LOADED;
}
}
