#include "startup_client.h"
#include <windows.h>
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <iterator>
#include <string>
using namespace valmax::launcher;
namespace {
size_t checks = 0, calls = 0;
ValmaxStartupResult response{64,1,0,0,0,0,0,0,{}};
int32_t returned = 0;
std::string seen;
ValmaxStartupRequest last{};
void check(bool good) { if (!good) { std::cerr << "failed_check=" << checks + 1 << '\n'; std::exit(1); } ++checks; }
extern "C" int32_t fake_status(const uint8_t *p, size_t n, const ValmaxStartupRequest *q, ValmaxStartupResult *r) {
    ++calls; seen.assign(reinterpret_cast<const char *>(p), n); last = *q; *r = response; return returned;
}
ValmaxStartupRequest request() {
    ValmaxStartupRequest q{64,1,1,1,0,0,0,0,{}};
    std::fill(std::begin(q.operation_id), std::end(q.operation_id), static_cast<uint8_t>('a'));
    return q;
}
}
int main() {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    auto q = request();
    const std::wstring helper = L"C:/ValMAXBuild/fixtures/Тест-\U0001f600/.mxmod-helper-temp";
    auto invoke = [&](const ValmaxStartupRequest &r) { return startup_receipt(helper, r, fake_status); };
    check(invoke(q).code == ReceiptCode::Ok);
    check(seen == "C:/ValMAXBuild/fixtures/Тест-\xf0\x9f\x98\x80/.mxmod-helper-temp");
    check(last.action == 1 && std::equal(std::begin(q.operation_id), std::end(q.operation_id), std::begin(last.operation_id)));
    response = {64,1,0,2,2,9,3,27,{}};
    std::copy(std::begin(q.operation_id), std::end(q.operation_id), std::begin(response.operation_id));
    check(invoke(q).value.code == 27);
    for (uint32_t code = 1; code <= 27; ++code) { response.code = code; check(invoke(q).value.code == code); }
    response.operation_id[0] = 'b'; check(invoke(q).code == ReceiptCode::AbiMismatch); response.operation_id[0] = 'a';
    response = {64,1,0,0,0,0,0,0,{}};
    q.action = 2; q.stage = 1; q.state = 1; q.claim = 1;
    response.outcome = 1; check(invoke(q).value.outcome == 1);
    response.outcome = 0; check(invoke(q).code == ReceiptCode::Ok);
    constexpr ReceiptCode errors[] = {ReceiptCode::Ok,ReceiptCode::InvalidInput,ReceiptCode::InvalidReceipt,
        ReceiptCode::UnsafePath,ReceiptCode::StoreBusy,ReceiptCode::IoError,ReceiptCode::InternalError};
    for (uint32_t e = 1; e <= 6; ++e) {
        response.error = e; check(invoke(q).code == errors[e]);
        response.outcome = 1; check(invoke(q).code == ReceiptCode::AbiMismatch); response.outcome = 0;
    }
    response = {64,1,0,0,0,0,0,0,{}};
    const size_t before = calls;
    for (const auto &path : {std::wstring(), std::wstring(31001,L'x'), std::wstring(L"C:/x\0y",7),
            std::wstring(1,static_cast<wchar_t>(0xd800)), std::wstring(16000,L'я')})
        check(startup_receipt(path,q,fake_status).code == ReceiptCode::InvalidInput && calls == before);
    check(startup_receipt(helper,q,nullptr).code == ReceiptCode::CoreUnavailable && calls == before);
    for (uint32_t role = 0; role <= 5; ++role) {
        for (uint32_t stage = 0; stage <= 18; ++stage) {
            for (uint32_t state = 0; state <= 4; ++state) {
                for (uint32_t code = 0; code <= 28; ++code) {
                    for (uint32_t claim = 0; claim <= 2; ++claim) {
                        auto v = q; v.role = role; v.stage = stage; v.state = state; v.code = code; v.claim = claim;
                        const bool valid = role >= 1 && role <= 4 && stage >= 1 && stage <= 17 && state >= 1 && state <= 3
                            && code <= 27 && ((state == 3) == (code != 0)) && claim <= 1 && (claim == 0 || (role != 2 && state == 1));
                        const size_t old = calls;
                        const auto result = invoke(v);
                        check(valid ? result.code == ReceiptCode::Ok && calls == old + 1
                                    : result.code == ReceiptCode::InvalidInput && calls == old);
                    }
                }
            }
        }
    }
    q = request();
    for (auto bad : {ValmaxStartupResult{63,1,0,0,0,0,0,0,{}}, {64,2,0,0,0,0,0,0,{}},
            {64,1,7,0,0,0,0,0,{}}, {64,1,0,3,0,0,0,0,{}}, {64,1,0,1,0,0,0,0,{}},
            {64,1,0,0,1,0,0,0,{}}, {64,1,0,2,0,0,0,0,{}}, {64,1,0,2,1,1,1,1,{}}}) {
        response = bad; check(invoke(q).code == ReceiptCode::AbiMismatch);
    }
    for (const int32_t status : {-1,1,2,INT32_MAX}) { returned = status; check(invoke(q).code == ReceiptCode::AbiMismatch); }
    std::cout << "{\"ok\":true,\"checks\":" << checks << ",\"filesystemTouched\":false,\"realCoreCalled\":false}\n";
}
