#pragma once
#include "privacy_api.h"
#include <array>
#include <span>
#include <string>
#include <vector>
#include <windows.h>
namespace valmax::privacy {
struct PatchPin { uint32_t rva, offset, rule; std::span<const uint8_t> bytes; };
struct FilePin {
    const wchar_t *relative;
    const char *sha256;
    uint64_t file_size;
    uint32_t image_size, role;
    std::span<const PatchPin> patches;
};
bool loaded_patches(uintptr_t base,const FilePin &pin,bool require_image=true);
bool file_matches(HANDLE file,const FilePin &pin);
#ifdef VALMAX_PRIVACY_TESTING
uint32_t verify_synthetic(const wchar_t *root,uint32_t role,std::span<const FilePin> files,
                          std::span<const uintptr_t> loaded);
#endif
}
