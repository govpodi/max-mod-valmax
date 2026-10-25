#include "privacy_host.h"
#include "privacy_api.h"
#include <QCoreApplication>
#include <QDir>
#include <QThread>
#include <QTextStream>
#include <QJsonDocument>
#include <QVariantList>
#include <cstring>

namespace {
uint32_t verifyResult = 0;
uint32_t serviceResult = VALMAX_PRIVACY_SERVICE_NOT_RUNNING;
ValmaxPrivacyStatus native{};
unsigned verifies = 0, statuses = 0, shutdowns = 0, serviceChecks = 0, checks = 0;
bool validPath = false;
void good() { native = {}; native.size = sizeof(native); native.version = 1; native.ready = 1;
    native.role = 1; native.disk_rules = 63; native.loaded_rules = 53; native.files_verified = 6;
    native.modules_verified = 3; native.patches_verified = 31; native.required_loaded_rules = 53;
    std::memset(native.manifest_sha256, 'a', 64); }
bool check(bool value, const char *message) { ++checks; if (!value) QTextStream(stderr) << message << '\n'; return value; }
#define CHECK(value) do { if (!check(value, #value)) return 1; } while(0)
bool verified(const QVariantMap &report) { return report.value(QStringLiteral("state")).toString() == QStringLiteral("verified"); }
bool unavailable(const QVariantMap &report) { return report.value(QStringLiteral("state")).toString() == QStringLiteral("unavailable"); }
}
extern "C" uint32_t valmax_privacy_verify(const wchar_t *path, uint32_t role) {
    ++verifies; validPath = role == VALMAX_PRIVACY_MAIN && QString::fromWCharArray(path) == QDir::toNativeSeparators(QCoreApplication::applicationDirPath());
    return verifyResult;
}
extern "C" uint32_t valmax_privacy_status(ValmaxPrivacyStatus *out) { ++statuses; *out = native; return 0; }
extern "C" uint32_t valmax_privacy_verify_service() { ++serviceChecks; return serviceResult; }
extern "C" uint32_t valmax_privacy_shutdown() { ++shutdowns; return 0; }
int main(int argc, char **argv) {
    QCoreApplication app(argc, argv); good();
    const auto initial = valmax::PrivacyHost::unavailable();
    CHECK(unavailable(initial) && initial.value(QStringLiteral("serviceError")).toString() == QStringLiteral("not_verified"));
    for (uint32_t error : {VALMAX_PRIVACY_FILE_MISMATCH, VALMAX_PRIVACY_MEMORY_MISMATCH, VALMAX_PRIVACY_IO_ERROR}) {
        verifyResult = error; const auto previous = verifies;
        { valmax::PrivacyHost host; CHECK(unavailable(host.refresh())); CHECK(unavailable(host.refresh())); }
        CHECK(verifies == previous + 1);
    }
    CHECK(validPath && statuses == 0);
    verifyResult = VALMAX_PRIVACY_MODULE_NOT_LOADED;
    {
        valmax::PrivacyHost host; CHECK(unavailable(host.refresh())); const auto previous = verifies;
        for (unsigned i = 0; i < 10; ++i) host.refresh();
        CHECK(verifies == previous);
        verifyResult = 0; QThread::msleep(270); CHECK(verified(host.refresh())); CHECK(verifies == previous + 1);
    }
    verifyResult = VALMAX_PRIVACY_MODULE_NOT_LOADED;
    {
        valmax::PrivacyHost host; const auto previous = verifies;
        CHECK(unavailable(host.refresh()));
        for (unsigned i = 0; i < 7; ++i) {
            const unsigned delay = i == 0 ? 270 : i == 1 ? 520 : i == 2 ? 1020 : 2020;
            QThread::msleep(delay); CHECK(unavailable(host.refresh()));
        }
        CHECK(verifies == previous + 8);
        verifyResult = 0; QThread::msleep(2020);
        CHECK(unavailable(host.refresh()) && verifies == previous + 8); // Retry budget stays finite.
    }
    for (unsigned mode = 0; mode < 5; ++mode) {
        good();
        if (mode == 0) native.loaded_rules = 52;
        if (mode == 1) native.modules_verified = 2;
        if (mode == 2) native.role = 2;
        if (mode == 3) native.manifest_sha256[12] = 'z';
        if (mode == 4) native.version = 2;
        valmax::PrivacyHost host; CHECK(unavailable(host.refresh()));
    }
    good();
    {
        valmax::PrivacyHost host; const auto report = host.refresh(); CHECK(verified(report));
        CHECK(report.value(QStringLiteral("buildVerified")).toBool() && report.value(QStringLiteral("currentProcessVerified")).toBool());
        CHECK(!report.value(QStringLiteral("serviceProcessVerified")).toBool());
        CHECK(report.value(QStringLiteral("serviceError")).toString() == QStringLiteral("service_not_running"));
        const auto rows = report.value(QStringLiteral("groups")).toList(); CHECK(rows.size() == 6);
        unsigned loaded = 0, packaged = 0;
        for (const auto &row : rows) {
            const auto state = row.toMap().value(QStringLiteral("state")).toString();
            loaded += state == QStringLiteral("verified_loaded"); packaged += state == QStringLiteral("verified_package");
        }
        CHECK(loaded == 4 && packaged == 2);
        CHECK(serviceChecks > 0);
        serviceResult = VALMAX_PRIVACY_OK;
        const auto service = host.refresh(); CHECK(verified(service));
        CHECK(service.value(QStringLiteral("serviceProcessVerified")).toBool());
        CHECK(service.value(QStringLiteral("serviceError")).toString().isEmpty());
        unsigned serviceLoaded = 0;
        for (const auto &row : service.value(QStringLiteral("groups")).toList())
            serviceLoaded += row.toMap().value(QStringLiteral("state")).toString() == QStringLiteral("verified_loaded");
        CHECK(serviceLoaded == 6);
        serviceResult = VALMAX_PRIVACY_MODULE_NOT_LOADED;
        const auto missingModule = host.refresh();
        CHECK(verified(missingModule) && !missingModule.value(QStringLiteral("serviceProcessVerified")).toBool()
              && missingModule.value(QStringLiteral("serviceError")).toString() == QStringLiteral("module_not_loaded"));
        serviceResult = VALMAX_PRIVACY_MEMORY_MISMATCH;
        const auto mismatch = host.refresh();
        CHECK(verified(mismatch) && !mismatch.value(QStringLiteral("serviceProcessVerified")).toBool()
              && mismatch.value(QStringLiteral("serviceError")).toString() == QStringLiteral("memory_mismatch"));
        serviceResult = VALMAX_PRIVACY_IO_ERROR;
        const auto inaccessible = host.refresh();
        CHECK(verified(inaccessible) && !inaccessible.value(QStringLiteral("serviceProcessVerified")).toBool()
              && inaccessible.value(QStringLiteral("serviceError")).toString() == QStringLiteral("io_error"));
        CHECK(report.size() == 8 && mismatch.size() == 8
              && !mismatch.contains(QStringLiteral("observation"))
              && !mismatch.contains(QStringLiteral("manifest_sha256"))
              && !mismatch.contains(QStringLiteral("path"))
              && !mismatch.contains(QStringLiteral("pid")));
        const auto calls = verifies; CHECK(verified(host.refresh())); CHECK(verifies == calls);
        const auto stopped = shutdowns;
        { valmax::PrivacyHost second; CHECK(unavailable(second.refresh())); }
        CHECK(shutdowns == stopped); // A second engine cannot shut down this one.
        native.ready = 0; native.error = VALMAX_PRIVACY_MEMORY_MISMATCH;
        CHECK(unavailable(host.refresh()));
        CHECK(unavailable(host.refresh()) && verifies == calls); // No endless verify after hard failure.
    }
    good();
    serviceResult = VALMAX_PRIVACY_SERVICE_NOT_RUNNING;
    { valmax::PrivacyHost restarted; CHECK(verified(restarted.refresh())); }
    QTextStream(stdout) << "{\"schema\":1,\"ok\":true,\"checks\":" << checks << ",\"syntheticVerifierABI\":true}\n";
    return 0;
}
