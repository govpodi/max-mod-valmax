// Host lifecycle test: real Rust Runtime, synthetic in-EXE hook ABI responses.
// No test DLL, arbitrary hash input, MAX code, profile, credential or network.
#include "ghost_host.h"
#include "valmax_core.h"
#include "ghost.h"
#include <QCoreApplication>
#include <QDir>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTextStream>
#include <QThread>
#include <cstring>

namespace {
uint32_t installResult = 0, statusError = 0, listenerCount = 7, policyResult = 0;
int shutdowns = 0, busyShutdowns = 0, checks = 0, installs = 0, policyCalls = 0;
int sequence = 0, lastAppliedPolicy = 0;
bool pathsValid = false, installed = false, everyInstallPrimed = true, activeAtInstall = false;
ValmaxGhostPolicy applied{};
bool check(bool condition, const char *message) {
    ++checks; if (!condition) QTextStream(stderr) << message << '\n'; return condition;
}
#define CHECK(value) do { if (!check(value, #value)) return 1; } while (0)
QJsonObject call(ValmaxRuntime *runtime, const QJsonObject &request) {
    const auto bytes = QJsonDocument(request).toJson(QJsonDocument::Compact);
    ValmaxCoreBuffer raw{nullptr, 0, 0};
    const auto code = valmax_runtime_dispatch(runtime, reinterpret_cast<const uint8_t *>(bytes.constData()), size_t(bytes.size()), &raw);
    QJsonObject result;
    if (code == 0 && raw.data) result = QJsonDocument::fromJson(QByteArray(reinterpret_cast<const char *>(raw.data), qsizetype(raw.len))).object();
    valmax_core_buffer_free(&raw); return result;
}
QJsonObject ghost(ValmaxRuntime *runtime) { return call(runtime, {{QStringLiteral("operation"), QStringLiteral("ghost.get")}}).value(QStringLiteral("result")).toObject(); }
QString state(ValmaxRuntime *runtime) { return ghost(runtime).value(QStringLiteral("runtime")).toObject().value(QStringLiteral("state")).toString(); }
}
extern "C" uint32_t valmax_ghost_install(const wchar_t *core, const wchar_t *main) {
    ++sequence;
    everyInstallPrimed = everyInstallPrimed && lastAppliedPolicy == sequence - 1
        && applied.size == sizeof(applied) && applied.version == 1;
    activeAtInstall = applied.enabled == 1 && applied.hide_typing == 1 && applied.hide_read == 1;
    ++installs;
    pathsValid = QString::fromWCharArray(main) == QDir::toNativeSeparators(QCoreApplication::applicationFilePath())
        && QString::fromWCharArray(core) == QDir::toNativeSeparators(QCoreApplication::applicationDirPath() + QStringLiteral("/core.dll"));
    if (installResult == VALMAX_GHOST_OK) installed = true;
    return installResult;
}
extern "C" uint32_t valmax_ghost_set_policy(const ValmaxGhostPolicy *policy) {
    ++sequence; ++policyCalls;
    if (policyResult != VALMAX_GHOST_OK) return policyResult;
    applied = *policy; lastAppliedPolicy = sequence; return VALMAX_GHOST_OK;
}
extern "C" uint32_t valmax_ghost_status(ValmaxGhostStatus *status) {
    ValmaxGhostStatus value{}; value.size = sizeof(value); value.version = 1;
    value.ready = installed ? 1 : 0; value.policy = applied; value.error = statusError;
    value.replacements = installed ? 5 : 0; value.listeners = installed ? listenerCount : 0; *status = value; return 0;
}
extern "C" uint32_t valmax_ghost_shutdown() {
    ++shutdowns;
    if (busyShutdowns > 0) { --busyShutdowns; return VALMAX_GHOST_BUSY; }
    installed = false; return VALMAX_GHOST_OK;
}
extern "C" void valmax_ghost_invalidate_unread() {}

int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    if (argc != 2) return 2;
    const QString fixture = QString::fromLocal8Bit(argv[1]);
    QDir().mkpath(fixture + QStringLiteral("/profile")); QDir().mkpath(fixture + QStringLiteral("/runtime"));
    const auto config = QJsonDocument(QJsonObject{{QStringLiteral("profileDirectory"), fixture + QStringLiteral("/profile")},
        {QStringLiteral("runtimeDirectory"), fixture + QStringLiteral("/runtime")}, {QStringLiteral("existingClient"), false}}).toJson(QJsonDocument::Compact);
    ValmaxRuntime *runtime = nullptr; ValmaxCoreBuffer opened{nullptr, 0, 0};
    CHECK(valmax_runtime_open(reinterpret_cast<const uint8_t *>(config.constData()), size_t(config.size()), &runtime, &opened) == 0 && runtime);
    valmax_core_buffer_free(&opened);
    CHECK(state(runtime) == QStringLiteral("unavailable"));
    const int beforeInvalid = installs, beforeInvalidPolicy = policyCalls;
    { valmax::GhostHost invalidRuntime(nullptr); CHECK(invalidRuntime.refresh() == QStringLiteral("unavailable")); }
    CHECK(installs == beforeInvalid && policyCalls == beforeInvalidPolicy); // No policy, no hooks.
    policyResult = VALMAX_GHOST_POLICY_CONFLICT;
    {
        valmax::GhostHost host(runtime);
        CHECK(host.refresh() == QStringLiteral("unavailable"));
        CHECK(installs == beforeInvalid && state(runtime) == QStringLiteral("unavailable"));
        policyResult = VALMAX_GHOST_OK;
        CHECK(host.refresh() == QStringLiteral("off"));
        CHECK(installs == beforeInvalid + 1);
    }
    installed = true; // Simulate another native owner with live hooks.
    const int beforeForeignInstall = installs, beforeForeignPolicy = policyCalls, beforeForeignShutdown = shutdowns;
    { valmax::GhostHost foreign(runtime); CHECK(foreign.refresh() == QStringLiteral("unavailable")); }
    CHECK(installs == beforeForeignInstall && policyCalls == beforeForeignPolicy
        && shutdowns == beforeForeignShutdown); // Never overwrite or detach it.
    installed = false;
    for (const auto failure : {VALMAX_GHOST_MODULE_MISMATCH, VALMAX_GHOST_HOOK_FAILED}) {
        installResult = failure;
        { valmax::GhostHost host(runtime); CHECK(host.refresh() == QStringLiteral("unavailable")); CHECK(state(runtime) == QStringLiteral("unavailable")); }
    }
    CHECK(everyInstallPrimed);
    CHECK(pathsValid);
    installResult = VALMAX_GHOST_BUSY;
    const int before = shutdowns;
    { valmax::GhostHost host(runtime); CHECK(host.refresh() == QStringLiteral("unavailable")); }
    CHECK(shutdowns == before); // Do not detach another owner's hooks.
    installResult = VALMAX_GHOST_NOT_READY;
    {
        valmax::GhostHost host(runtime);
        CHECK(host.refresh() == QStringLiteral("unavailable"));
        const int firstAttempt = installs;
        for (int i = 0; i < 10; ++i) CHECK(host.refresh() == QStringLiteral("unavailable"));
        CHECK(installs == firstAttempt); // A cold start cannot spin on the worker.
        installResult = VALMAX_GHOST_OK;
        QThread::msleep(270);
        CHECK(host.refresh() == QStringLiteral("off"));
        CHECK(installs == firstAttempt + 1); // Same lease retries after core loads.
    }
    installResult = VALMAX_GHOST_MODULE_MISMATCH;
    {
        valmax::GhostHost host(runtime);
        CHECK(host.refresh() == QStringLiteral("unavailable"));
        const int hardFailure = installs;
        QThread::msleep(270);
        CHECK(host.refresh() == QStringLiteral("unavailable"));
        CHECK(installs == hardFailure); // Wrong loaded module is never retried.
    }
    installResult = VALMAX_GHOST_NOT_READY;
    {
        valmax::GhostHost host(runtime);
        const int beforeRetry = installs;
        const unsigned delays[] = {250, 500, 1000, 2000, 2000, 2000, 2000};
        for (int attempt = 0; attempt < 8; ++attempt) {
            CHECK(host.refresh() == QStringLiteral("unavailable"));
            CHECK(installs == beforeRetry + attempt + 1);
            if (attempt < 7) QThread::msleep(delays[attempt] + 30);
        }
        installResult = VALMAX_GHOST_OK;
        QThread::msleep(270);
        CHECK(host.refresh() == QStringLiteral("unavailable"));
        CHECK(installs == beforeRetry + 8); // Exhausted cold start stays closed.
    }
    installResult = VALMAX_GHOST_OK; listenerCount = 6;
    { valmax::GhostHost host(runtime); CHECK(host.refresh() == QStringLiteral("unavailable")); CHECK(state(runtime) == QStringLiteral("unavailable")); }
    listenerCount = 7;
    {
        valmax::GhostHost host(runtime);
        CHECK(host.refresh() == QStringLiteral("off")); CHECK(state(runtime) == QStringLiteral("off"));
        auto snapshot = ghost(runtime); auto settings = snapshot.value(QStringLiteral("settings")).toObject();
        settings.insert(QStringLiteral("enabled"), true);
        const auto saved = call(runtime, {{QStringLiteral("operation"), QStringLiteral("ghost.set")},
            {QStringLiteral("revision"), snapshot.value(QStringLiteral("revision"))}, {QStringLiteral("settings"), settings}});
        CHECK(saved.value(QStringLiteral("ok")) == QJsonValue(true));
        CHECK(state(runtime) == QStringLiteral("pending")); // Save is not an ACK.
        CHECK(host.refresh() == QStringLiteral("active")); CHECK(state(runtime) == QStringLiteral("active"));
        statusError = VALMAX_GHOST_HOOK_FAILED;
        CHECK(host.refresh() == QStringLiteral("unavailable")); CHECK(state(runtime) == QStringLiteral("unavailable"));
        statusError = 0; CHECK(host.refresh() == QStringLiteral("active"));
        busyShutdowns = 2;
    }
    CHECK(busyShutdowns == 0); CHECK(state(runtime) == QStringLiteral("unavailable"));
    const int beforeActiveInstall = installs;
    {
        valmax::GhostHost restarted(runtime);
        CHECK(restarted.refresh() == QStringLiteral("active"));
        CHECK(installs == beforeActiveInstall + 1 && activeAtInstall && everyInstallPrimed);
    }
    CHECK(state(runtime) == QStringLiteral("unavailable"));
    valmax_runtime_close(&runtime); CHECK(runtime == nullptr);
    QTextStream(stdout) << "{\"schema\":1,\"ok\":true,\"checks\":" << checks << ",\"syntheticHookABI\":true,\"realRustRuntime\":true}\n";
    return 0;
}
