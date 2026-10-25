// Build-only executable. Linked only to the inspected Rust core and its C++
// adapter; all receipt IO is confined to its caller-created synthetic fixture.
#include "startup_client.h"
#include <windows.h>
#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <iterator>
#include <string>
using namespace valmax::launcher;
namespace fs = std::filesystem;
namespace {
size_t checks = 0;
void check(bool good) { if (!good) { std::cerr << "failed_check=" << checks+1 << '\n'; std::exit(1); } ++checks; }
ValmaxStartupRequest request(char operation) {
    ValmaxStartupRequest q{64,1,1,1,0,0,0,0,{}};
    std::fill(std::begin(q.operation_id), std::end(q.operation_id), static_cast<uint8_t>(operation));
    return q;
}
}
int wmain(int argc, wchar_t **argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    check(argc == 3);
    const fs::path root(argv[1]);
    const fs::path base(L"C:/ValMAXBuild/fixtures/startup-client-interop");
    check(root.is_absolute() && root.lexically_normal() == root);
    check(fs::canonical(root.parent_path()) == fs::canonical(base));
    const auto name = root.filename().wstring();
    check(name.size() == 32 && std::all_of(name.begin(), name.end(), [](wchar_t c) {
        return (c >= L'0' && c <= L'9') || (c >= L'a' && c <= L'f');
    }));
    check(fs::is_directory(root) && fs::canonical(root) == fs::canonical(base) / name);
    const std::wstring helper = (root / L".mxmod-helper-temp").wstring();
    auto invoke = [&](const ValmaxStartupRequest &q) { return startup_receipt(helper, q, valmax_startup_status); };
    const std::wstring mode(argv[2]);
    if (mode == L"native-first") {
        auto q = request('a');
        const auto absent = invoke(q); check(absent.code == ReceiptCode::Ok && absent.value.outcome == 0);
        check(!fs::exists(fs::path(helper)));
        q.action = 2; q.stage = 1; q.state = 1; q.claim = 1;
        check(invoke(q).value.outcome == 1);
        q.role = 2; q.stage = 9; q.state = 3; q.code = 27; q.claim = 0;
        check(invoke(q).value.outcome == 1);
        const auto precise = invoke(request('a'));
        check(precise.code == ReceiptCode::Ok && precise.value.outcome == 2 && precise.value.code == 27);
        q.code = 1; check(invoke(q).value.outcome == 0);
        check(invoke(request('a')).value.code == 27);
        check(invoke(request('b')).value.outcome == 0);
    } else if (mode == L"python-first") {
        const auto current = invoke(request('b'));
        check(current.code == ReceiptCode::Ok && current.value.outcome == 2 && current.value.state == 1 && current.value.code == 0);
        auto old = request('a'); old.action = 2; old.role = 2; old.stage = 9; old.state = 3; old.code = 25;
        check(invoke(old).value.outcome == 0);
        auto ready = request('b'); ready.action = 2; ready.stage = 11; ready.state = 2;
        check(invoke(ready).value.outcome == 1);
        const auto completed = invoke(request('b'));
        check(completed.code == ReceiptCode::Ok && completed.value.outcome == 2 && completed.value.stage == 11 && completed.value.state == 2);
        check(invoke(request('a')).value.outcome == 0);
    } else { check(false); }
    std::cout << "{\"ok\":true,\"checks\":" << checks << ",\"realCoreCalled\":true,\"realProfilesTouched\":false}\n";
}
