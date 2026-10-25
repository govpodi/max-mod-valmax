#include "privacy_host.h"
#include "privacy_api.h"
#include <QCoreApplication>
#include <QDir>
#include <QVariantList>
#include <algorithm>
#include <atomic>

namespace valmax {
namespace {
#if VALMAX_HAS_PRIVACY
std::atomic<bool> verifierOwned{false};
QString code(uint32_t value) {
    switch (value) {
    case VALMAX_PRIVACY_FILE_MISMATCH: return QStringLiteral("file_mismatch");
    case VALMAX_PRIVACY_MODULE_NOT_LOADED: return QStringLiteral("module_not_loaded");
    case VALMAX_PRIVACY_MEMORY_MISMATCH: return QStringLiteral("memory_mismatch");
    case VALMAX_PRIVACY_IO_ERROR: return QStringLiteral("io_error");
    case VALMAX_PRIVACY_NOT_VERIFIED: return QStringLiteral("not_verified");
    case VALMAX_PRIVACY_SERVICE_NOT_RUNNING: return QStringLiteral("service_not_running");
    default: return QStringLiteral("invalid_status");
    }
}
#endif
const char *const groupIds[] = {"tracer_main", "tracer_service", "ui_statistics", "mytracker", "log_masking", "error_logs"};
QVariantList groups(bool verified, bool serviceVerified = false) {
    QVariantList result;
    for (unsigned index = 0; index < 6; ++index) {
        const bool loaded = ((1u << index) & 53u) != 0 || (serviceVerified && (index == 1 || index == 3));
        result.append(QVariantMap{{QStringLiteral("id"), QString::fromLatin1(groupIds[index])},
            {QStringLiteral("state"), !verified ? QStringLiteral("unavailable") : loaded ? QStringLiteral("verified_loaded") : QStringLiteral("verified_package")}});
    }
    return result;
}
#if VALMAX_HAS_PRIVACY
bool valid(const ValmaxPrivacyStatus &value) {
    if (value.size != sizeof(value) || value.version != 1 || value.error != 0 || value.ready != 1
        || value.role != VALMAX_PRIVACY_MAIN || value.disk_rules != 63 || value.loaded_rules != 53
        || value.required_loaded_rules != 53 || value.files_verified != 6 || value.modules_verified != 3
        || value.patches_verified != 31 || value.reserved != 0) return false;
    for (const char c : value.manifest_sha256)
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    return true;
}
#endif
}
#if VALMAX_HAS_PRIVACY
PrivacyHost::~PrivacyHost()
{
    if (owned_) { valmax_privacy_shutdown(); verifierOwned.store(false, std::memory_order_release); }
}
#endif
QVariantMap PrivacyHost::unavailable(const QString &error)
{
    return {{QStringLiteral("schema"), 1}, {QStringLiteral("state"), QStringLiteral("unavailable")},
        {QStringLiteral("error"), error}, {QStringLiteral("buildVerified"), false},
        {QStringLiteral("currentProcessVerified"), false}, {QStringLiteral("serviceProcessVerified"), false},
        {QStringLiteral("serviceError"), QStringLiteral("not_verified")},
        {QStringLiteral("groups"), groups(false)}};
}
#if VALMAX_HAS_PRIVACY
QVariantMap PrivacyHost::refresh()
{
    if (!owned_) {
        if (attempted_) return unavailable(failure_);
        bool expected = false;
        owned_ = verifierOwned.compare_exchange_strong(expected, true, std::memory_order_acq_rel);
        if (!owned_) { attempted_ = true; failure_ = QStringLiteral("verifier_owned"); return unavailable(failure_); }
    }
    if (!verified_) {
        if (attempted_ || std::chrono::steady_clock::now() < retryAt_) return unavailable(failure_);
        ++attempts_;
        const QString directory = QDir::toNativeSeparators(QCoreApplication::applicationDirPath());
        const auto result = valmax_privacy_verify(reinterpret_cast<const wchar_t *>(directory.utf16()), VALMAX_PRIVACY_MAIN);
        if (result != VALMAX_PRIVACY_OK) {
            failure_ = code(result);
            if (result == VALMAX_PRIVACY_MODULE_NOT_LOADED && attempts_ < 8)
                retryAt_ = std::chrono::steady_clock::now() + std::chrono::milliseconds(250u << std::min(attempts_ - 1, 3u));
            else attempted_ = true;
            return unavailable(failure_);
        }
        verified_ = true; attempted_ = true;
    }
    ValmaxPrivacyStatus status{}; status.size = sizeof(status); status.version = 1;
    if (valmax_privacy_status(&status) != VALMAX_PRIVACY_OK || !valid(status)) {
        verified_ = false; failure_ = status.error ? code(status.error) : QStringLiteral("invalid_status");
        return unavailable(failure_);
    }
    const uint32_t serviceResult = valmax_privacy_verify_service();
    const bool serviceVerified = serviceResult == VALMAX_PRIVACY_OK;
    // The remote snapshot is separate from the main-process attestation. The
    // main verifier may have revoked itself while the service was inspected.
    status = {}; status.size = sizeof(status); status.version = 1;
    if (valmax_privacy_status(&status) != VALMAX_PRIVACY_OK || !valid(status)) {
        verified_ = false; failure_ = status.error ? code(status.error) : QStringLiteral("invalid_status");
        return unavailable(failure_);
    }
    return {{QStringLiteral("schema"), 1}, {QStringLiteral("state"), QStringLiteral("verified")},
        {QStringLiteral("error"), QString()}, {QStringLiteral("buildVerified"), true},
        {QStringLiteral("currentProcessVerified"), true}, {QStringLiteral("serviceProcessVerified"), serviceVerified},
        {QStringLiteral("serviceError"), serviceVerified ? QString() : code(serviceResult)},
        {QStringLiteral("groups"), groups(true, serviceVerified)}};
}
#endif
}
