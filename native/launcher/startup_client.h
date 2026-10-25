#pragma once
#include "../core/valmax_core.h"
#include <string_view>

namespace valmax::launcher {
using StartupStatusFn = decltype(&valmax_startup_status);
enum class ReceiptCode : uint32_t {
    Ok, InvalidInput, CoreUnavailable, AbiMismatch, InvalidReceipt,
    UnsafePath, StoreBusy, IoError, InternalError
};
struct ReceiptResult {
    ReceiptCode code = ReceiptCode::InternalError;
    ValmaxStartupResult value{};
};
const char *receipt_code(ReceiptCode) noexcept;
// Explicit host-only call. The caller supplies and retains a trusted loaded
// core function; this function loads no module and discovers no directories.
// Persistence is optional diagnostics, never readiness/permission to launch.
// Keep the primary startup outcome independently when this returns a failure.
ReceiptResult startup_receipt(std::wstring_view helper_directory,
    const ValmaxStartupRequest &, StartupStatusFn trusted_status) noexcept;
}
