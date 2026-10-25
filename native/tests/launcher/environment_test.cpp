#include "environment.h"
#include <windows.h>
#include <algorithm>
#include <array>
#include <cstdlib>
#include <iostream>
#include <map>
#include <random>
#include <string>
using namespace valmax::launcher;
namespace {
size_t checks = 0;
void check(bool value, const char *name) {
    if (!value) { std::cerr << "FAIL " << name << '\n'; std::exit(1); }
    ++checks;
}
constexpr LexicalEnvironmentPaths paths{L"C:\\Synthetic\\Bundle\\webview2\\155.0.4283.60",
                                         L"C:\\Synthetic\\Local\\MXMOD"};
std::map<std::wstring, std::wstring> unpack(const ChildEnvironment &result) {
    check(result.ok() && result.block.size() >= 2, "successful nonempty result");
    check(result.block.back() == 0 && result.block[result.block.size() - 2] == 0, "two terminators");
    size_t begin = 0;
    std::map<std::wstring, std::wstring> entries;
    std::wstring previous;
    while (begin < result.block.size() - 1) {
        size_t end = begin;
        while (end < result.block.size() && result.block[end]) ++end;
        check(end > begin && end < result.block.size(), "bounded output framing");
        const std::wstring line(result.block.data() + begin, end - begin);
        const size_t split = line.find(L'=', line[0] == L'=' ? 1 : 0);
        check(split != std::wstring::npos && split != 0, "output name value delimiter");
        const auto name = line.substr(0, split);
        if (!previous.empty()) {
            check(CompareStringOrdinal(previous.data(), static_cast<int>(previous.size()), name.data(),
                static_cast<int>(name.size()), TRUE) == CSTR_LESS_THAN, "Windows ordinal sorted unique");
        }
        entries.emplace(name, line.substr(split + 1));
        previous = name;
        begin = end + 1;
    }
    check(begin == result.block.size() - 1, "exact output framing");
    return entries;
}
std::vector<wchar_t> block(std::span<const EnvironmentEntry> entries) {
    std::vector<wchar_t> result;
    for (const auto &e : entries) {
        result.insert(result.end(), e.name.begin(), e.name.end());
        result.push_back(L'=');
        result.insert(result.end(), e.value.begin(), e.value.end());
        result.push_back(0);
    }
    if (entries.empty()) result.push_back(0);
    result.push_back(0);
    return result;
}
void failure(const ChildEnvironment &result, EnvironmentError expected, const char *name) {
    check(!result.ok() && result.error == expected && result.block.empty(), name);
}
void defaults_and_copy() {
    const auto empty = build_child_environment(std::span<const EnvironmentEntry>{}, paths);
    const auto values = unpack(empty);
    check(values.size() == 4, "only four fixed variables in empty input");
    check(values.at(L"WEBVIEW2_BROWSER_EXECUTABLE_FOLDER") == paths.fixed_webview_runtime, "fixed runtime");
    check(values.at(L"WEBVIEW2_USER_DATA_FOLDER") == L"C:\\Synthetic\\Local\\MXMOD\\business-webview2", "own data");
    check(values.at(L"QT_WEBVIEW_PLUGIN") == L"webview2", "fixed native plugin");
    check(values.at(L"QML_XHR_ALLOW_FILE_READ") == L"1", "legacy optimization contract retained");
    constexpr std::array input{
        EnvironmentEntry{L"Path", L";relative;\"C:\\Synthetic Bundle\";C:\\Other;"},
        EnvironmentEntry{L"TEMP", L"C:\\Synthetic Temp"},
        EnvironmentEntry{L"LOCALAPPDATA", L"opaque, not used to choose profile"},
        EnvironmentEntry{L"=C:", L"C:\\Синтетика\\😀"},
        EnvironmentEntry{L"Юникод", L"Значение😀=one\nline\t"},
        EnvironmentEntry{L"EMPTY", L""},
        EnvironmentEntry{L"QSG_RHI_BACKEND", L"d3d11"},
        EnvironmentEntry{L"QT_QUICK_BACKEND", L"software"},
        EnvironmentEntry{L"QT_SCALE_FACTOR", L"1.5"},
        EnvironmentEntry{L"QML_DISABLE_DISK_CACHE", L"1"},
        EnvironmentEntry{L"WEBVIEW20_USER_DATA_FOLDER", L"unrelated exact prefix"},
        EnvironmentEntry{L"OTHER", L"synthetic opaque value"}
    };
    auto input_block = block(input);
    const auto before = input_block;
    const auto result = build_child_environment(input, paths);
    const auto parsed = build_child_environment(input_block, paths);
    const auto output = unpack(result);
    for (const auto &e : input) check(output.at(std::wstring(e.name)) == e.value, "ordinary value preserved");
    check(parsed.block == result.block, "entry and block APIs equivalent");
    check(input_block == before, "borrowed block unchanged");
    const std::array<wchar_t, 2> zero{0, 0};
    check(build_child_environment(zero, paths).block == empty.block, "empty Windows block");
}
void stripped_overrides() {
    constexpr std::array keys{
        L"WEBVIEW2_USER_DATA_FOLDER", L"webview2_user_data_folder", L"WebView2_Browser_Executable_Folder",
        L"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", L"WEBVIEW2_RELEASE_CHANNEL_PREFERENCE",
        L"WEBVIEW2_PIPE_FOR_SCRIPT_DEBUGGER", L"WEBVIEW2_WAIT_FOR_SCRIPT_DEBUGGER",
        L"WEBVIEW2_FUTURE_UNKNOWN", L"WEBVIEW2_",
        L"QT_PLUGIN_PATH", L"QT_QPA_PLATFORM_PLUGIN_PATH", L"QML_IMPORT_PATH", L"QML2_IMPORT_PATH",
        L"QT_QPA_PLATFORM", L"QT_QPA_PLATFORMTHEME", L"QT_QPA_GENERIC_PLUGINS", L"QT_IM_MODULE",
        L"QT_IM_MODULES", L"QT_STYLE_OVERRIDE", L"QT_QUICK_CONTROLS_STYLE",
        L"QT_QUICK_CONTROLS_FALLBACK_STYLE", L"QT_QUICK_CONTROLS_CONF", L"QML_DISK_CACHE_PATH",
        L"QT_QPA_FONTDIR", L"QT_WEBVIEW_PLUGIN", L"QML_XHR_ALLOW_FILE_READ",
        L"VALMAX_NATIVE_RUNTIME_CONFIG", L"MAX_MOD_STARTUP_OPERATION"
    };
    const auto baseline = build_child_environment(std::span<const EnvironmentEntry>{}, paths);
    for (const auto *key : keys) {
        std::wstring mixed(key);
        for (size_t i = 0; i < mixed.size(); i += 2)
            if (mixed[i] >= L'A' && mixed[i] <= L'Z') mixed[i] += L'a' - L'A';
        const std::array input{EnvironmentEntry{key, L"untrusted"}, EnvironmentEntry{mixed, L"other"}};
        check(build_child_environment(input, paths).block == baseline.block, "override variants removed completely");
    }
}
void duplicate_guards() {
    const std::array pairs{
        std::pair{L"PATH", L"Path"}, std::pair{L"=C:", L"=c:"},
        std::pair{L"Имя", L"ИМЯ"}, std::pair{L"A", L"A"}
    };
    for (const auto &[a, b] : pairs) {
        const std::array entries{EnvironmentEntry{a, L"one"}, EnvironmentEntry{b, L"two"}};
        failure(build_child_environment(entries, paths), EnvironmentError::DuplicateVariable, "case insensitive duplicate");
        failure(build_child_environment(block(entries), paths), EnvironmentError::DuplicateVariable, "block duplicate");
    }
    const std::array different{EnvironmentEntry{L"SS", L"one"}, EnvironmentEntry{L"ß", L"two"}};
    check(build_child_environment(different, paths).ok(), "Windows ordinal differs from linguistic casefold");
    const std::array supplementary{EnvironmentEntry{L"𐐀", L"one"}, EnvironmentEntry{L"𐐨", L"two"}};
    const bool ordinal_equal = CompareStringOrdinal(supplementary[0].name.data(), 2,
        supplementary[1].name.data(), 2, TRUE) == CSTR_EQUAL;
    const auto supplementary_result = build_child_environment(supplementary, paths);
    check(ordinal_equal ? supplementary_result.error == EnvironmentError::DuplicateVariable
                        : supplementary_result.ok(), "supplementary uses actual Windows ordinal table");
}
void shell_bookkeeping() {
    const auto baseline = build_child_environment(std::span<const EnvironmentEntry>{}, paths);
    constexpr std::array keys{L"=::", L"=ExitCode", L"=ExitCodeAscii", L"=eXiTcOdE"};
    for (const auto *key : keys) {
        for (const auto *value : {L"", L"::\\", L"0", L"synthetic private value"}) {
            const std::array input{EnvironmentEntry{key, value}};
            check(build_child_environment(input, paths).block == baseline.block, "shell bookkeeping omitted");
            check(build_child_environment(block(input), paths).block == baseline.block, "raw shell bookkeeping omitted");
        }
        const std::wstring invalid(1, wchar_t(0xd800));
        const std::array bad{EnvironmentEntry{key, invalid}};
        failure(build_child_environment(bad, paths), EnvironmentError::InvalidUnicode, "private values still validated");
        const std::wstring large(kEnvironmentMaximumEntryUnits, L'x');
        const std::array oversized{EnvironmentEntry{key, large}};
        failure(build_child_environment(oversized, paths), EnvironmentError::TooLarge, "private entry capacity still enforced");
    }
    const std::array missing{L'A', L'\0', L'\0'};
    failure(build_child_environment(missing, paths), EnvironmentError::MissingEntrySeparator, "missing separator classified");
}
void malformed() {
    for (const auto *name : {L"", L"A=B", L"=", L"=1:", L"=AB:", L"=A", L"=A::", L"=other"}) {
        const std::array entries{EnvironmentEntry{name, L"value"}};
        failure(build_child_environment(entries, paths), name[0] == L'=' ? EnvironmentError::InvalidHiddenEntry
                : EnvironmentError::InvalidEntry, "invalid variable name");
    }
    const std::array empty_drive{EnvironmentEntry{L"=C:", L""}};
    failure(build_child_environment(empty_drive, paths), EnvironmentError::InvalidHiddenEntry, "empty drive entry");
    const std::array bad_strings{
        std::wstring(L"a\0b", 3), std::wstring(1, wchar_t(0xd800)), std::wstring(1, wchar_t(0xdc00)),
        std::wstring{wchar_t(0xd800), L'x'}, std::wstring{wchar_t(0xd800), wchar_t(0xd800)}
    };
    for (const auto &bad : bad_strings) {
        const std::array bad_name{EnvironmentEntry{bad, L"x"}};
        const std::array bad_value{EnvironmentEntry{L"A", bad}};
        const std::array bad_removed{EnvironmentEntry{L"WEBVIEW2_INJECTION", bad}};
        failure(build_child_environment(bad_name, paths), EnvironmentError::InvalidUnicode, "invalid Unicode name");
        failure(build_child_environment(bad_value, paths), EnvironmentError::InvalidUnicode, "invalid Unicode value");
        failure(build_child_environment(bad_removed, paths), EnvironmentError::InvalidUnicode, "removed values still validated");
    }
    const std::array<std::vector<wchar_t>, 9> bad_blocks{
        std::vector<wchar_t>{}, {0}, {0, 0, 0}, {L'A', L'=', L'B', 0},
        {L'A', L'=', L'B', 0, 0, L'X', 0, 0}, {0, L'A', 0, 0},
        {L'A', L'=', L'B', 0, L'X', 0, 0}, {L'=', L'A', 0, 0}, {L'A', 0, 0}
    };
    for (const auto &data : bad_blocks) {
        const auto result = build_child_environment(data, paths);
        check(!result.ok() && result.block.empty(), "malformed framing fails without partial output");
    }
    const std::array value_equals{EnvironmentEntry{L"a", L"=x=y="}};
    check(unpack(build_child_environment(block(value_equals), paths)).at(L"a") == L"=x=y=", "value equal signs preserved");
}
void path_guards() {
    constexpr std::array bad{
        L"", L"MXMOD", L"C:MXMOD", L"1:\\a\\MXMOD", L"C:\\", L"\\\\server\\share\\MXMOD",
        L"\\\\?\\C:\\a\\MXMOD", L"\\\\.\\C:\\a\\MXMOD", L"C:/a/../MXMOD", L"C:/a/./MXMOD",
        L"C:/a//MXMOD", L"C:/a/MXMOD/", L"C:/a /MXMOD", L"C:/a./MXMOD", L"C:/a:b/MXMOD",
        L"C:/a\n/MXMOD", L"C:/a*/MXMOD", L"C:/a?/MXMOD", L"C:/a\"/MXMOD", L"C:/a</MXMOD",
        L"C:/a>/MXMOD", L"C:/a|/MXMOD", L"C:/ONEME/MXMOD", L"C:/OnEmE/MXMOD", L"C:/NUL/MXMOD",
        L"C:/con.txt/MXMOD", L"C:/aux/MXMOD", L"C:/COM1/MXMOD", L"C:/LPT9/MXMOD",
        L"C:/COM¹/MXMOD", L"C:/LPT²/MXMOD", L"C:/CONIN$/MXMOD", L"C:/CONOUT$/MXMOD"
    };
    const std::span<const EnvironmentEntry> empty;
    for (const auto *bad_path : bad) {
        failure(build_child_environment(empty, {bad_path, paths.prepared_mxmod_root}), EnvironmentError::InvalidPath, "unsafe runtime path");
        failure(build_child_environment(empty, {paths.fixed_webview_runtime, bad_path}), EnvironmentError::InvalidPath, "unsafe profile path");
    }
    failure(build_child_environment(empty, {paths.fixed_webview_runtime, L"C:/a/Other"}), EnvironmentError::InvalidPath, "MXMOD basename required");
    for (const auto *overlap : {L"C:/Synthetic/Local/MXMOD", L"C:/Synthetic/Local/MXMOD/business-webview2/runtime", L"C:/Synthetic/Local"})
        failure(build_child_environment(empty, {overlap, paths.prepared_mxmod_root}), EnvironmentError::InvalidPath, "runtime and profile may not overlap");
    const auto alternate = unpack(build_child_environment(empty,
        {L"D:/Сборка 😀/webview2/155.0", L"C:/Локально 😀/mxmod"}));
    check(alternate.at(L"WEBVIEW2_BROWSER_EXECUTABLE_FOLDER") == L"D:\\Сборка 😀\\webview2\\155.0", "Unicode path and slashes");
    check(alternate.at(L"WEBVIEW2_USER_DATA_FOLDER") == L"C:\\Локально 😀\\mxmod\\business-webview2", "drive and MXMOD case");
    const std::wstring zero(L"C:/a/MXMOD\0x", 12);
    failure(build_child_environment(empty, {paths.fixed_webview_runtime, zero}), EnvironmentError::InvalidPath, "embedded NUL path");
    const std::wstring long_path = L"C:/" + std::wstring(kEnvironmentMaximumPathUnits, L'a');
    failure(build_child_environment(empty, {long_path, paths.prepared_mxmod_root}), EnvironmentError::InvalidPath, "path size limit");
}
void capacities() {
    const std::wstring exact_value(kEnvironmentMaximumEntryUnits - 2, L'x');
    const std::array exact{EnvironmentEntry{L"A", exact_value}};
    check(build_child_environment(exact, paths).ok(), "exact entry size accepted");
    const std::wstring too_long(kEnvironmentMaximumEntryUnits - 1, L'x');
    const std::array over{EnvironmentEntry{L"A", too_long}};
    failure(build_child_environment(over, paths), EnvironmentError::TooLarge, "entry size enforced");
    failure(build_child_environment(block(over), paths), EnvironmentError::TooLarge, "raw entry size enforced");
    std::vector<EnvironmentEntry> repeated(kEnvironmentMaximumEntries, {L"WEBVIEW2_X", L"x"});
    check(build_child_environment(repeated, paths).ok(), "max input entries with removed duplicates");
    repeated.push_back({L"WEBVIEW2_X", L"x"});
    failure(build_child_environment(repeated, paths), EnvironmentError::TooLarge, "input count enforced");
    failure(build_child_environment(block(repeated), paths), EnvironmentError::TooLarge, "raw input count enforced");
    std::vector<std::wstring> names;
    names.reserve(kEnvironmentMaximumEntries);
    for (size_t i = 0; i < kEnvironmentMaximumEntries; ++i) names.push_back(L"N" + std::to_wstring(i));
    std::vector<EnvironmentEntry> unique;
    for (size_t i = 0; i < kEnvironmentMaximumEntries - 4; ++i) unique.push_back({names[i], L"x"});
    check(build_child_environment(unique, paths).ok(), "exact output entry count accepted");
    unique.push_back({names.back(), L"x"});
    failure(build_child_environment(unique, paths), EnvironmentError::TooLarge, "output count includes fixed entries");
    const std::wstring payload(30000, L'x');
    std::vector<EnvironmentEntry> big;
    for (size_t i = 0; i < 36; ++i) big.push_back({names[i], payload});
    failure(build_child_environment(big, paths), EnvironmentError::TooLarge, "aggregate entry API bound");
    failure(build_child_environment(block(big), paths), EnvironmentError::TooLarge, "aggregate block API bound");

    // Fill to precisely the output block limit, including all canonical entries.
    const auto baseline = build_child_environment(std::span<const EnvironmentEntry>{}, paths).block.size();
    std::vector<std::wstring> values;
    std::vector<EnvironmentEntry> boundary;
    values.reserve(64);
    size_t remaining = kEnvironmentMaximumUnits - baseline;
    size_t index = 0;
    while (remaining) {
        const size_t overhead = names[index].size() + 2;
        const size_t units = std::min(remaining, size_t(30000));
        check(units >= overhead, "boundary fixture valid");
        values.emplace_back(units - overhead, L'x');
        boundary.push_back({names[index], values.back()});
        remaining -= units;
        ++index;
    }
    const auto accepted = build_child_environment(boundary, paths);
    check(accepted.ok() && accepted.block.size() == kEnvironmentMaximumUnits, "exact output block capacity accepted");
    boundary.push_back({L"LAST", L"x"});
    failure(build_child_environment(boundary, paths), EnvironmentError::TooLarge, "output aggregate enforced including fixed entries");
}
void permutation_and_roundtrip() {
    std::mt19937 random(0x56414cU);
    constexpr std::array original{
        EnvironmentEntry{L"A", L"1"}, EnvironmentEntry{L"B", L"2"}, EnvironmentEntry{L"Юникод", L"😀"},
        EnvironmentEntry{L"=D:", L"D:\\Synthetic"}, EnvironmentEntry{L"Path", L"C:/Synthetic;"},
        EnvironmentEntry{L"webview2_future", L"remove"}, EnvironmentEntry{L"QT_PLUGIN_PATH", L"remove"}
    };
    const auto canonical = build_child_environment(original, paths).block;
    for (size_t round = 0; round < 200; ++round) {
        auto shuffled = original;
        std::shuffle(shuffled.begin(), shuffled.end(), random);
        const auto built = build_child_environment(shuffled, paths);
        check(built.ok() && built.block == canonical, "permutation stable");
        check(build_child_environment(built.block, paths).block == canonical, "canonical idempotent roundtrip");
    }
    for (const auto code : {EnvironmentError::Ok, EnvironmentError::InvalidBlock, EnvironmentError::InvalidEntry,
        EnvironmentError::InvalidUnicode, EnvironmentError::DuplicateVariable, EnvironmentError::InvalidPath,
        EnvironmentError::TooLarge, EnvironmentError::AllocationFailure, EnvironmentError::NativeFailure,
        EnvironmentError::InvalidHiddenEntry, EnvironmentError::MissingEntrySeparator})
        check(std::string_view(environment_code(code)).size() < 48, "finite error code");
    check(std::string_view(environment_code(static_cast<EnvironmentError>(999))) == "native_failure", "unknown enum finite");
}
}
int main() {
    defaults_and_copy(); stripped_overrides(); shell_bookkeeping(); duplicate_guards(); malformed();
    path_guards(); capacities(); permutation_and_roundtrip();
    std::cout << "{\"schema\":1,\"syntheticOnly\":true,\"checks\":" << checks
              << ",\"processesStarted\":0,\"filesystemOperations\":0,\"parentEnvironmentAccess\":false}\n";
}
