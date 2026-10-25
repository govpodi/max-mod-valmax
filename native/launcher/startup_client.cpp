#include "startup_client.h"
#include <windows.h>
#include <algorithm>
#include <cstddef>
#include <iterator>
#include <string>

namespace valmax::launcher {
static_assert(sizeof(ValmaxStartupRequest) == 64 && sizeof(ValmaxStartupResult) == 64);
static_assert(offsetof(ValmaxStartupRequest, operation_id) == 32);
static_assert(offsetof(ValmaxStartupResult, operation_id) == 32);
namespace {
bool finite_fields(uint32_t role, uint32_t stage, uint32_t state, uint32_t code) {
    return role >= 1 && role <= 4 && stage >= 1 && stage <= 17 && state >= 1 && state <= 3
        && code <= 27 && ((state == 3) == (code != 0));
}
bool zero_receipt(const ValmaxStartupResult &r) {
    return r.role == 0 && r.stage == 0 && r.state == 0 && r.code == 0
        && std::all_of(std::begin(r.operation_id), std::end(r.operation_id), [](uint8_t c) { return c == 0; });
}
bool valid_request(const ValmaxStartupRequest &r) {
    if (r.size != 64 || r.version != 1 || r.role < 1 || r.role > 4
        || !std::all_of(std::begin(r.operation_id), std::end(r.operation_id), [](uint8_t c) {
            return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
        })) return false;
    if (r.action == 1) return r.stage == 0 && r.state == 0 && r.code == 0 && r.claim == 0;
    return r.action == 2 && finite_fields(r.role, r.stage, r.state, r.code) && r.claim <= 1
        && (r.claim == 0 || (r.role != 2 && r.state == 1));
}
bool valid_result(const ValmaxStartupRequest &q, const ValmaxStartupResult &r) {
    if (r.size != 64 || r.version != 1 || r.error > 6 || r.outcome > 2) return false;
    if (r.error != 0) return r.outcome == 0 && zero_receipt(r);
    if (r.outcome == 0) return zero_receipt(r);
    if (q.action == 2) return r.outcome == 1 && zero_receipt(r);
    return r.outcome == 2 && finite_fields(r.role, r.stage, r.state, r.code)
        && std::equal(std::begin(r.operation_id), std::end(r.operation_id), std::begin(q.operation_id));
}
}
const char *receipt_code(ReceiptCode code) noexcept {
    switch (code) {
    case ReceiptCode::Ok: return "ok";
    case ReceiptCode::InvalidInput: return "invalid_request";
    case ReceiptCode::CoreUnavailable: return "core_unavailable";
    case ReceiptCode::AbiMismatch: return "core_abi_mismatch";
    case ReceiptCode::InvalidReceipt: return "invalid_receipt";
    case ReceiptCode::UnsafePath: return "unsafe_path";
    case ReceiptCode::StoreBusy: return "store_busy";
    case ReceiptCode::IoError: return "io_error";
    default: return "internal_error";
    }
}
ReceiptResult startup_receipt(std::wstring_view helper, const ValmaxStartupRequest &request,
    StartupStatusFn status) noexcept {
    const auto fail = [](ReceiptCode code) { return ReceiptResult{code, {}}; };
    if (!valid_request(request) || helper.empty() || helper.size() > 31000
        || helper.find(L'\0') != helper.npos) return fail(ReceiptCode::InvalidInput);
    if (!status) return fail(ReceiptCode::CoreUnavailable);
    try {
        const int length = static_cast<int>(helper.size());
        const int bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, helper.data(), length, nullptr, 0, nullptr, nullptr);
        if (bytes <= 0 || bytes > 31000) return fail(ReceiptCode::InvalidInput);
        std::string utf8(static_cast<size_t>(bytes), '\0');
        if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, helper.data(), length, utf8.data(), bytes, nullptr, nullptr) != bytes)
            return fail(ReceiptCode::InvalidInput);
        ValmaxStartupResult result{};
        if (status(reinterpret_cast<const uint8_t *>(utf8.data()), utf8.size(), &request, &result) != 0
            || !valid_result(request, result)) return fail(ReceiptCode::AbiMismatch);
        constexpr ReceiptCode errors[] = {ReceiptCode::Ok, ReceiptCode::InvalidInput, ReceiptCode::InvalidReceipt,
            ReceiptCode::UnsafePath, ReceiptCode::StoreBusy, ReceiptCode::IoError, ReceiptCode::InternalError};
        if (result.error != 0) return fail(errors[result.error]);
        return {ReceiptCode::Ok, result};
    } catch (...) { return fail(ReceiptCode::InternalError); }
}
}
