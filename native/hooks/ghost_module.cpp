#include "ghost_internal.h"
#include "hook_set.h"
#include <bcrypt.h>
#include <algorithm>
#include <string>
#include <vector>
namespace valmax::ghost {
bool read_memory(uintptr_t address, void *output, size_t size) noexcept {
    if (address == 0 || output == nullptr || size == 0 || address > UINTPTR_MAX - size) return false;
    SIZE_T copied = 0;
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void *>(address), output, size, &copied)
        && copied == size;
}
VerifiedImage::~VerifiedImage() { release(); }
void VerifiedImage::release() {
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (module) FreeLibrary(module);
    module = nullptr; file = INVALID_HANDLE_VALUE; base = 0; size = 0;
}
namespace {
bool equal_path(std::wstring a, std::wstring b) {
    std::replace(a.begin(), a.end(), L'/', L'\\');
    std::replace(b.begin(), b.end(), L'/', L'\\');
    return CompareStringOrdinal(a.data(), static_cast<int>(a.size()), b.data(), static_cast<int>(b.size()), TRUE) == CSTR_EQUAL;
}
std::wstring final_path(HANDLE file) {
    std::wstring value(32768, L'\0');
    auto count = GetFinalPathNameByHandleW(file, value.data(), static_cast<DWORD>(value.size()), FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    if (count == 0 || count >= value.size()) return {};
    value.resize(count);
    if (value.starts_with(L"\\\\?\\")) value.erase(0, 4);
    return value;
}
bool hash_file(HANDLE file, const char *expected) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return false;
    BCRYPT_HASH_HANDLE hash = nullptr;
    bool ok = BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0;
    std::array<uint8_t, 65536> buffer{};
    DWORD count = 0;
    while (ok) {
        if (!ReadFile(file, buffer.data(), static_cast<DWORD>(buffer.size()), &count, nullptr)) { ok = false; break; }
        if (count == 0) break;
        ok = BCryptHashData(hash, buffer.data(), count, 0) >= 0;
    }
    std::array<uint8_t, 32> digest{};
    if (ok) ok = BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) >= 0;
    if (hash) BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    constexpr char hex[] = "0123456789abcdef";
    for (size_t i = 0; ok && i < digest.size(); ++i)
        ok = expected[i * 2] == hex[digest[i] >> 4] && expected[i * 2 + 1] == hex[digest[i] & 15];
    return ok;
}
}
bool validate_loaded(const VerifiedImage &image, const ImagePin &pin) {
    IMAGE_DOS_HEADER dos{};
    IMAGE_NT_HEADERS64 pe{};
    if (!read(image.base, dos) || dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew <= 0
        || static_cast<uint32_t>(dos.e_lfanew) > pin.image_size - sizeof(pe)
        || !read(image.base + dos.e_lfanew, pe) || pe.Signature != IMAGE_NT_SIGNATURE
        || pe.FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 || pe.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC
        || pe.OptionalHeader.SizeOfImage != pin.image_size) return false;
    MEMORY_BASIC_INFORMATION memory{};
    if (!VirtualQuery(reinterpret_cast<void *>(image.base), &memory, sizeof(memory))
        || memory.Type != MEM_IMAGE || memory.AllocationBase != reinterpret_cast<void *>(image.base)) return false;
    for (const auto &guard : pin.code) {
        if (guard.rva >= pin.image_size || guard.bytes.size() > pin.image_size - guard.rva
            || !hooks::HookSet::matches({reinterpret_cast<void *>(image.base + guard.rva), guard.bytes})) return false;
    }
    for (const auto &slot : pin.slots) {
        uintptr_t target = 0;
        if (slot.rva > pin.image_size - sizeof(target) || slot.target >= pin.image_size
            || !read(image.base + slot.rva, target) || target != image.base + slot.target) return false;
    }
    return true;
}
bool validate_image(const wchar_t *path, const ImagePin &pin, VerifiedImage &image) {
    image.release();
    const auto length = wcsnlen(path, 32768);
    if (length < 4 || length >= 32768 || path[1] != L':' || (path[2] != L'\\' && path[2] != L'/')) return false;
    const std::wstring expected(path, length);
    std::wstring full(32768, L'\0');
    const auto count = GetFullPathNameW(path, static_cast<DWORD>(full.size()), full.data(), nullptr);
    if (count == 0 || count >= full.size()) return false;
    full.resize(count);
    if (!equal_path(expected, full)) return false;
    if (!GetModuleHandleExW(0, path, &image.module)) return false;
    std::wstring loaded(32768, L'\0');
    auto loaded_size = GetModuleFileNameW(image.module, loaded.data(), static_cast<DWORD>(loaded.size()));
    if (loaded_size == 0 || loaded_size >= loaded.size()) return false;
    loaded.resize(loaded_size);
    if (!equal_path(loaded, expected)) return false;
    image.file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (image.file == INVALID_HANDLE_VALUE) return false;
    BY_HANDLE_FILE_INFORMATION info{};
    if (GetFileType(image.file) != FILE_TYPE_DISK || !GetFileInformationByHandle(image.file, &info)
        || (info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) != 0
        || info.nNumberOfLinks != 1 || !equal_path(final_path(image.file), expected)
        || ((uint64_t(info.nFileSizeHigh) << 32) | info.nFileSizeLow) != pin.file_size
        || !hash_file(image.file, pin.sha256)) return false;
    image.base = reinterpret_cast<uintptr_t>(image.module);
    image.size = pin.image_size;
    return validate_loaded(image, pin);
}
}
