#include "notification_internal.h"
#include "notification_pins.generated.h"
#include <algorithm>
#include <cstring>
#include <filesystem>
namespace valmax::notifications {
namespace {
struct alignas(8) NativeString { uint8_t buffer[16]{}; uint64_t size = 0, capacity = 15; };
static_assert(sizeof(NativeString) == 32);
using Copy = void (*)(void *, const void *);
using Destroy = void (*)(void *);
using Display = void (*)(void *, void *);
using DisplayFlag = void (*)(void *, void *, uint8_t);
bool native_copy(void *function, void *target, const void *source) noexcept {
    __try { reinterpret_cast<Copy>(function)(target, source); return true; }
    __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool native_destroy(void *function, void *target) noexcept {
    __try { reinterpret_cast<Destroy>(function)(target); return true; }
    __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool native_display(void *function, void *self, void *data, bool with_flag, uint8_t flag) noexcept {
    __try {
        if (with_flag) reinterpret_cast<DisplayFlag>(function)(self, data, flag);
        else reinterpret_cast<Display>(function)(self, data);
        return true;
    } __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool readable(uintptr_t pointer, size_t count) {
    if (!pointer || !count || pointer > UINTPTR_MAX - count) return false;
    // Never copy or log text simply to validate its allocation. Check committed
    // page ranges, then the native guarded copy performs the actual operation.
    uintptr_t end = pointer + count;
    for (size_t regions = 0; pointer < end && regions < 64; ++regions) {
        MEMORY_BASIC_INFORMATION memory{};
        if (!VirtualQuery(reinterpret_cast<void *>(pointer), &memory, sizeof(memory)) || memory.State != MEM_COMMIT
            || (memory.Protect & (PAGE_NOACCESS | PAGE_GUARD)) || !(memory.Protect & (PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY))) return false;
        const auto base = reinterpret_cast<uintptr_t>(memory.BaseAddress);
        if (base > pointer || memory.RegionSize > UINTPTR_MAX - base || base + memory.RegionSize <= pointer) return false;
        pointer = base + memory.RegionSize;
    }
    return pointer >= end;
}
bool equal_path(std::wstring a, std::wstring b) {
    std::replace(a.begin(), a.end(), L'/', L'\\'); std::replace(b.begin(), b.end(), L'/', L'\\');
    return CompareStringOrdinal(a.c_str(), -1, b.c_str(), -1, TRUE) == CSTR_EQUAL;
}
bool final_matches(HANDLE handle, const std::wstring &expected) {
    std::wstring path(32768,L'\0');
    auto count = GetFinalPathNameByHandleW(handle,path.data(),static_cast<DWORD>(path.size()),FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    if (!count || count >= path.size()) return false;
    path.resize(count); if (path.starts_with(L"\\\\?\\")) path.erase(0,4);
    return equal_path(path, expected);
}
}
bool valid_strings(const void *data) {
    for (const size_t offset : {size_t(0x78),size_t(0xb8)}) {
        uintptr_t object = 0, vtable = 0;
        if (!read(reinterpret_cast<uintptr_t>(data)+offset,object)) return false;
        if (!object) continue;
        if (!read(object,vtable)) return false;
        for (size_t slot : {size_t(0),size_t(32)}) {
            uintptr_t function = 0; MEMORY_BASIC_INFORMATION memory{};
            if (!read(vtable+slot,function) || !VirtualQuery(reinterpret_cast<void *>(function),&memory,sizeof(memory))
                || memory.State != MEM_COMMIT || memory.Type != MEM_IMAGE
                || !(memory.Protect & (PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY))
                || (memory.Protect & (PAGE_GUARD | PAGE_NOACCESS))) return false;
        }
    }
    for (auto offset : pins::kStringOffsets) {
        NativeString text{};
        if (!read(reinterpret_cast<uintptr_t>(data) + offset,text) || text.size > text.capacity
            || text.size > 1024*1024 || text.capacity > 16*1024*1024) return false;
        if (text.capacity <= 15) { if (text.capacity != 15 || text.size > 15 || text.buffer[text.size] != 0) return false; continue; }
        uintptr_t address = 0; std::memcpy(&address,text.buffer,sizeof(address));
        if (!readable(address,static_cast<size_t>(text.size)+1)) return false;
        uint8_t terminator = 1;
        if (!read(address+text.size,terminator) || terminator) return false;
    }
    return true;
}
bool clone_call(const Targets &targets, const char *image, void *original, void *self, void *data, bool with_flag, uint8_t flag, bool &cleanup_failed) {
    cleanup_failed = false;
    if (!valid_strings(data)) return false;
    std::array<NativeString,4> replacements{};
    alignas(8) std::array<uint8_t,kDataSize> clone{};
    bool copied = false, ok = true;
    size_t built = 0;
    std::array<bool,4> uncertain_fields{};
    const char *values[] = {"ValMAX", "", "\xd0\x9d\xd0\xbe\xd0\xb2\xd0\xbe\xd0\xb5 \xd1\x81\xd0\xbe\xd0\xbe\xd0\xb1\xd1\x89\xd0\xb5\xd0\xbd\xd0\xb8\xd0\xb5", image};
    for (size_t i = 0; i < replacements.size(); ++i) {
        NativeString source; source.size = std::strlen(values[i]);
        if (source.size <= 15) std::memcpy(source.buffer,values[i],static_cast<size_t>(source.size)+1);
        else { auto pointer = values[i]; std::memcpy(source.buffer,&pointer,sizeof(pointer)); source.capacity = source.size; }
        if (!native_copy(targets.string_copy,&replacements[i],&source)) { ok = false; break; }
        ++built;
    }
    if (ok) copied = ok = native_copy(targets.data_copy,clone.data(),data);
    if (ok) for (size_t i = 0; i < replacements.size(); ++i) {
        auto field = clone.data() + pins::kStringOffsets[i];
        if (!native_destroy(targets.string_destroy,field)) { uncertain_fields[i] = true; cleanup_failed = true; ok = false; break; }
        std::memcpy(field,&replacements[i],sizeof(NativeString));
        replacements[i] = {}; // move ownership; no cross-CRT allocation/free
    }
    if (ok) ok = native_display(original,self,clone.data(),with_flag,flag);
    for (size_t i = 0; i < built; ++i) if (!native_destroy(targets.string_destroy,&replacements[i])) cleanup_failed = true;
    if (copied) {
        for (size_t i = replacements.size(); i > 0; --i)
            if (!uncertain_fields[i-1] && !native_destroy(targets.string_destroy,clone.data()+pins::kStringOffsets[i-1])) cleanup_failed = true;
        if (!native_destroy(targets.header_destroy,clone.data())) cleanup_failed = true;
    }
    SecureZeroMemory(clone.data(),clone.size());
    return ok && !cleanup_failed;
}
void Asset::release() {
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    file = INVALID_HANDLE_VALUE;
    for (auto it = directories.rbegin(); it != directories.rend(); ++it) CloseHandle(*it);
    directories.clear(); utf8.clear();
}
bool validate_asset(const wchar_t *path, const wchar_t *main_path, Asset &asset) {
    asset.release();
    const std::filesystem::path input(path), main(main_path);
    if (!input.is_absolute() || !equal_path(input.parent_path().wstring(),main.parent_path().wstring())
        || input.filename().wstring() != pins::kAssetName) return false;
    auto directory = input.parent_path();
    std::vector<std::filesystem::path> parents;
    while (!directory.empty()) {
        parents.push_back(directory); if (parents.size() > 256) return false;
        if (directory == directory.root_path()) break;
        directory = directory.parent_path();
    }
    for (auto it = parents.rbegin(); it != parents.rend(); ++it) {
        HANDLE held = CreateFileW(it->c_str(), FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
            OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, nullptr);
        if (held == INVALID_HANDLE_VALUE) return false;
        asset.directories.push_back(held);
        BY_HANDLE_FILE_INFORMATION info{};
        if (!GetFileInformationByHandle(held,&info) || !(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
            || (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) || !final_matches(held,it->wstring())) return false;
    }
    asset.file = CreateFileW(path,GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
    BY_HANDLE_FILE_INFORMATION info{};
    if (asset.file == INVALID_HANDLE_VALUE || !GetFileInformationByHandle(asset.file,&info)
        || info.nNumberOfLinks != 1 || info.nFileSizeHigh || info.nFileSizeLow != sizeof(pins::kAsset)
        || (info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)) || !final_matches(asset.file,input.wstring())) return false;
    std::array<uint8_t,sizeof(pins::kAsset)> content{}; DWORD read_count = 0;
    if (!ReadFile(asset.file,content.data(),static_cast<DWORD>(content.size()),&read_count,nullptr)
        || read_count != content.size() || !std::equal(content.begin(),content.end(),std::begin(pins::kAsset))) return false;
    const auto value = input.wstring();
    const auto count = WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,value.c_str(),-1,nullptr,0,nullptr,nullptr);
    if (count <= 1 || count > 32768) return false;
    asset.utf8.resize(static_cast<size_t>(count));
    if (WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,value.c_str(),-1,asset.utf8.data(),count,nullptr,nullptr) != count) return false;
    asset.utf8.pop_back(); return true;
}
}
