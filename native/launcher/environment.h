#pragma once
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>
#include <vector>

namespace valmax::launcher {
// Project bounds, not claims about the Windows Unicode environment limit.
inline constexpr size_t kEnvironmentMaximumUnits = 1024 * 1024;
inline constexpr size_t kEnvironmentMaximumEntries = 4096;
inline constexpr size_t kEnvironmentMaximumEntryUnits = 32767;
inline constexpr size_t kEnvironmentMaximumPathUnits = 32767;

struct EnvironmentEntry {
    std::wstring_view name;
    std::wstring_view value;
};
enum class WebView2Mode : uint32_t { Fixed, Evergreen };
// These strings are checked LEXICALLY ONLY. This type is not a proof of path
// ownership. The host must independently verify the fixed runtime or system
// Evergreen availability, prepare MXMOD/business-webview2 and retain the
// appropriate directory/payload guards through startup. No discovery or FS
// access here.
struct LexicalEnvironmentPaths {
    std::wstring_view fixed_webview_runtime;
    std::wstring_view prepared_mxmod_root;
    WebView2Mode webview2_mode = WebView2Mode::Fixed;
};
enum class EnvironmentError : uint32_t {
    Ok, InvalidBlock, InvalidEntry, InvalidUnicode, DuplicateVariable,
    InvalidPath, TooLarge, AllocationFailure, NativeFailure,
    InvalidHiddenEntry, MissingEntrySeparator
};
const char *environment_code(EnvironmentError) noexcept;

struct ChildEnvironment {
    EnvironmentError error = EnvironmentError::NativeFailure;
    // On success: sorted, unique, UTF-16 entries followed by two NUL units.
    // On failure: empty. May contain inherited secrets: never log the block.
    // Pass only to a future explicit child CreateProcessW environment with
    // CREATE_UNICODE_ENVIRONMENT; never install it into the parent/global env.
    std::vector<wchar_t> block;
    bool ok() const noexcept { return error == EnvironmentError::Ok; }
};

// Ordinary names/values (including PATH, TEMP and render settings) are kept
// byte-for-byte in UTF-16. Known browser/plugin overrides and inherited trusted
// host config/receipt IDs are removed before canonical values are inserted.
// Known shell-private pseudoentries (=::, =ExitCode, =ExitCodeAscii) are validated
// for size/Unicode and omitted; valid =C: per-drive entries remain unchanged.
// This is NOT complete launch-ready host assembly: the future verified host
// must insert its own VALMAX_NATIVE_RUNTIME_CONFIG/current startup operation.
// Retained duplicate names are rejected
// using Windows case-insensitive ordinal comparison. Special =C: drive entries
// are supported. No environment, registry, process or filesystem API is used.
ChildEnvironment build_child_environment(std::span<const EnvironmentEntry>,
                                        const LexicalEnvironmentPaths &) noexcept;
// Input must be exactly one complete double-NUL-terminated Windows block.
// Invalid pointers are outside the trusted C++ span contract; lengths/content
// are bounded and validated. No unbounded wcslen or parent env lookup is used.
ChildEnvironment build_child_environment(std::span<const wchar_t>,
                                        const LexicalEnvironmentPaths &) noexcept;
}
