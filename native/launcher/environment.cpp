#include "environment.h"
#include <windows.h>
#include <algorithm>
#include <array>
#include <new>
#include <string>

namespace valmax::launcher {
namespace {
struct Failure { EnvironmentError error; };
[[noreturn]] void fail(EnvironmentError error) { throw Failure{error}; }
int compare(std::wstring_view a, std::wstring_view b) {
    // All call sites pass bounded nonempty names/path components.
    const int result = CompareStringOrdinal(a.data(), static_cast<int>(a.size()),
                                           b.data(), static_cast<int>(b.size()), TRUE);
    if (!result) fail(EnvironmentError::NativeFailure);
    return result - CSTR_EQUAL;
}
bool equal(std::wstring_view a, std::wstring_view b) {
    return a.size() == b.size() && (a.empty() || compare(a, b) == 0);
}
bool prefix(std::wstring_view text, std::wstring_view start) {
    return text.size() >= start.size() && equal(text.substr(0, start.size()), start);
}
bool alpha(wchar_t c) {
    return (c >= L'a' && c <= L'z') || (c >= L'A' && c <= L'Z');
}
bool unicode(std::wstring_view text) {
    for (size_t i = 0; i < text.size(); ++i) {
        const uint16_t c = static_cast<uint16_t>(text[i]);
        if (!c) return false;
        if (c >= 0xd800 && c <= 0xdbff) {
            if (++i == text.size()) return false;
            const uint16_t next = static_cast<uint16_t>(text[i]);
            if (next < 0xdc00 || next > 0xdfff) return false;
        } else if (c >= 0xdc00 && c <= 0xdfff) return false;
    }
    return true;
}
bool reserved_component(std::wstring_view part) {
    const auto stem = part.substr(0, part.find(L'.'));
    for (const auto name : {L"CON", L"PRN", L"AUX", L"NUL", L"CONIN$", L"CONOUT$"})
        if (equal(stem, name)) return true;
    if (stem.size() == 4 && (prefix(stem, L"COM") || prefix(stem, L"LPT"))) {
        const wchar_t digit = stem[3];
        return (digit >= L'1' && digit <= L'9') || digit == L'\u00b9'
            || digit == L'\u00b2' || digit == L'\u00b3';
    }
    return false;
}
std::wstring path(std::wstring_view text) {
    if (text.size() < 4 || text.size() >= kEnvironmentMaximumPathUnits
            || !unicode(text) || !alpha(text[0]) || text[1] != L':'
            || (text[2] != L'/' && text[2] != L'\\')) fail(EnvironmentError::InvalidPath);
    std::wstring result(text);
    std::replace(result.begin(), result.end(), L'/', L'\\');
    size_t begin = 3;
    for (size_t end = begin; end <= result.size(); ++end) {
        if (end != result.size() && result[end] != L'\\') continue;
        const auto part = std::wstring_view(result).substr(begin, end - begin);
        if (part.empty() || part == L"." || part == L".." || part.back() == L'.'
                || part.back() == L' ' || equal(part, L"ONEME") || reserved_component(part))
            fail(EnvironmentError::InvalidPath);
        for (const auto c : part)
            if (c < 32 || c == L'<' || c == L'>' || c == L':' || c == L'"'
                    || c == L'|' || c == L'?' || c == L'*') fail(EnvironmentError::InvalidPath);
        begin = end + 1;
    }
    return result;
}
bool private_shell_entry(std::wstring_view name) {
    // Shell bookkeeping is neither a normal variable nor a per-drive CWD.
    // Accept only these known spellings as bounded input, then omit them.
    return equal(name, L"=::") || equal(name, L"=ExitCode") || equal(name, L"=ExitCodeAscii");
}
bool removed(std::wstring_view name) {
    if (private_shell_entry(name)) return true;
    if (prefix(name, L"WEBVIEW2_")) return true;
    // Bridge configuration and startup receipt identity must be assembled by
    // the verified host for this launch, never trusted from an inherited env.
    if (equal(name, L"VALMAX_NATIVE_RUNTIME_CONFIG") || equal(name, L"MAX_MOD_STARTUP_OPERATION")
            || equal(name, L"VALMAX_WEBVIEW2_MODE")) return true;
    // Explicit Qt path/plugin-selection overrides. Do not blanket-strip QT_ or
    // QSG_: DPI/render preferences keep their existing behavior. These options
    // are not needed for the application-local, verified qt.conf/plugin tree.
    constexpr std::array names{
        L"QT_PLUGIN_PATH", L"QT_QPA_PLATFORM_PLUGIN_PATH", L"QML_IMPORT_PATH",
        L"QML2_IMPORT_PATH", L"QT_QPA_PLATFORM", L"QT_QPA_PLATFORMTHEME",
        L"QT_QPA_GENERIC_PLUGINS", L"QT_IM_MODULE", L"QT_IM_MODULES",
        L"QT_STYLE_OVERRIDE", L"QT_QUICK_CONTROLS_STYLE",
        L"QT_QUICK_CONTROLS_FALLBACK_STYLE", L"QT_QUICK_CONTROLS_CONF",
        L"QML_DISK_CACHE_PATH", L"QT_QPA_FONTDIR",
        L"QT_WEBVIEW_PLUGIN", L"QML_XHR_ALLOW_FILE_READ"
    };
    return std::any_of(names.begin(), names.end(), [&](auto item) { return equal(name, item); });
}
void entry(const EnvironmentEntry &item) {
    if (item.name.empty() || item.name.size() > kEnvironmentMaximumEntryUnits
            || item.value.size() > kEnvironmentMaximumEntryUnits
            || item.name.size() + item.value.size() + 1 > kEnvironmentMaximumEntryUnits)
        fail(item.name.empty() ? EnvironmentError::InvalidEntry : EnvironmentError::TooLarge);
    if (!unicode(item.name) || !unicode(item.value)) fail(EnvironmentError::InvalidUnicode);
    if (item.name[0] == L'=') {
        // Windows preserves per-drive current directories as '=C:=C:\path'.
        // The value is opaque: the builder never uses it to resolve any path.
        if (!private_shell_entry(item.name)
                && (item.name.size() != 3 || !alpha(item.name[1]) || item.name[2] != L':'
                    || item.value.empty())) fail(EnvironmentError::InvalidHiddenEntry);
    } else if (item.name.find(L'=') != std::wstring_view::npos) fail(EnvironmentError::InvalidEntry);
}
ChildEnvironment build(std::span<const EnvironmentEntry> input, const LexicalEnvironmentPaths &paths) {
    if (input.size() > kEnvironmentMaximumEntries) fail(EnvironmentError::TooLarge);
    if (paths.webview2_mode != WebView2Mode::Fixed && paths.webview2_mode != WebView2Mode::Evergreen)
        fail(EnvironmentError::InvalidPath);
    const bool evergreen = paths.webview2_mode == WebView2Mode::Evergreen;
    if (evergreen && !paths.fixed_webview_runtime.empty()) fail(EnvironmentError::InvalidPath);
    std::wstring runtime = evergreen ? std::wstring{} : path(paths.fixed_webview_runtime);
    std::wstring profile = path(paths.prepared_mxmod_root);
    if (!equal(std::wstring_view(profile).substr(profile.find_last_of(L'\\') + 1), L"MXMOD"))
        fail(EnvironmentError::InvalidPath);
    std::wstring data = profile + L"\\business-webview2";
    if (data.size() >= kEnvironmentMaximumPathUnits) fail(EnvironmentError::InvalidPath);
    // Runtime and mutable browser data may not nest or coincide. Otherwise a
    // purported fixed runtime could be replaced by normal browser writes.
    const auto within = [](std::wstring_view child, std::wstring_view parent) {
        return equal(child, parent) || (child.size() > parent.size() && child[parent.size()] == L'\\'
            && prefix(child, parent));
    };
    if (!evergreen && (within(runtime, profile) || within(profile, runtime))) fail(EnvironmentError::InvalidPath);
    std::vector<EnvironmentEntry> kept;
    kept.reserve(input.size() + 5);
    size_t input_units = input.empty() ? 2 : 1;
    for (const auto &item : input) {
        entry(item);
        const auto units = item.name.size() + item.value.size() + 2;
        if (units > kEnvironmentMaximumUnits - input_units) fail(EnvironmentError::TooLarge);
        input_units += units;
        if (!removed(item.name)) kept.push_back(item);
    }
    if (!evergreen) kept.push_back({L"WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", runtime});
    kept.push_back({L"WEBVIEW2_USER_DATA_FOLDER", data});
    kept.push_back({L"QT_WEBVIEW_PLUGIN", L"webview2"});
    kept.push_back({L"QML_XHR_ALLOW_FILE_READ", L"1"});
    kept.push_back({L"VALMAX_WEBVIEW2_MODE", evergreen ? L"evergreen" : L"fixed"});
    if (kept.size() > kEnvironmentMaximumEntries) fail(EnvironmentError::TooLarge);
    std::sort(kept.begin(), kept.end(), [](const auto &a, const auto &b) { return compare(a.name, b.name) < 0; });
    size_t output_units = 1;
    std::wstring_view previous;
    for (const auto &item : kept) {
        entry(item);
        if (!previous.empty() && equal(previous, item.name)) fail(EnvironmentError::DuplicateVariable);
        previous = item.name;
        const auto units = item.name.size() + item.value.size() + 2;
        if (units > kEnvironmentMaximumUnits - output_units) fail(EnvironmentError::TooLarge);
        output_units += units;
    }
    ChildEnvironment result{EnvironmentError::Ok, {}};
    result.block.reserve(output_units);
    for (const auto &item : kept) {
        result.block.insert(result.block.end(), item.name.begin(), item.name.end());
        result.block.push_back(L'=');
        result.block.insert(result.block.end(), item.value.begin(), item.value.end());
        result.block.push_back(0);
    }
    result.block.push_back(0);
    return result;
}
template<class Work> ChildEnvironment checked(Work work) noexcept {
    try { return work(); }
    catch (const Failure &failure) { return {failure.error, {}}; }
    catch (const std::bad_alloc &) { return {EnvironmentError::AllocationFailure, {}}; }
    catch (...) { return {EnvironmentError::NativeFailure, {}}; }
}
}
const char *environment_code(EnvironmentError error) noexcept {
    switch (error) {
    case EnvironmentError::Ok: return "ok";
    case EnvironmentError::InvalidBlock: return "invalid_environment_block";
    case EnvironmentError::InvalidEntry: return "invalid_environment_entry";
    case EnvironmentError::InvalidUnicode: return "invalid_environment_unicode";
    case EnvironmentError::DuplicateVariable: return "duplicate_environment_variable";
    case EnvironmentError::InvalidPath: return "invalid_environment_path";
    case EnvironmentError::TooLarge: return "environment_too_large";
    case EnvironmentError::AllocationFailure: return "allocation_failed";
    case EnvironmentError::NativeFailure: return "native_failure";
    case EnvironmentError::InvalidHiddenEntry: return "invalid_hidden_environment_entry";
    case EnvironmentError::MissingEntrySeparator: return "environment_entry_without_separator";
    }
    return "native_failure";
}
ChildEnvironment build_child_environment(std::span<const EnvironmentEntry> input,
                                        const LexicalEnvironmentPaths &paths) noexcept {
    return checked([&] { return build(input, paths); });
}
ChildEnvironment build_child_environment(std::span<const wchar_t> input,
                                        const LexicalEnvironmentPaths &paths) noexcept {
    return checked([&] {
        if (input.size() > kEnvironmentMaximumUnits) fail(EnvironmentError::TooLarge);
        if (input.size() < 2 || input[input.size() - 1] || input[input.size() - 2])
            fail(EnvironmentError::InvalidBlock);
        std::vector<EnvironmentEntry> entries;
        size_t position = 0;
        if (input.size() == 2 && !input[0]) return build(entries, paths);
        while (position < input.size() - 1) {
            const size_t begin = position;
            while (position < input.size() && input[position]) ++position;
            if (position == begin || position == input.size()) fail(EnvironmentError::InvalidBlock);
            const std::wstring_view line(input.data() + begin, position - begin);
            if (line.size() > kEnvironmentMaximumEntryUnits) fail(EnvironmentError::TooLarge);
            const auto separator = line.find(L'=', line[0] == L'=' ? 1 : 0);
            if (separator == std::wstring_view::npos) fail(EnvironmentError::MissingEntrySeparator);
            if (entries.size() == kEnvironmentMaximumEntries) fail(EnvironmentError::TooLarge);
            entries.push_back({line.substr(0, separator), line.substr(separator + 1)});
            ++position;
        }
        return build(entries, paths);
    });
}
}
