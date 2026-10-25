// Real Rust PIN runtime; hook ABI is simulated only inside this test EXE.
#include "notification_host.h"
#include "notification_api.h"
#include "valmax_core.h"
#include <QCoreApplication>
#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTextStream>
#include <QThread>
#include <array>
#include <atomic>
#include <cstring>
#include <thread>
#include <vector>

namespace {
uint32_t installCode = 0, statusError = 0, replacementCount = 6;
int shutdowns = 0, busyShutdowns = 0, drainingStatuses = 0, checks = 0, installs = 0;
bool validPaths = false, incorrectRevision = false;
ValmaxNotificationPolicy current{};
std::vector<ValmaxNotificationScope> scopes;
bool check(bool condition, const char *message) {
    ++checks; if (!condition) QTextStream(stderr) << message << '\n'; return condition;
}
#define CHECK(value) do { if (!check(value, #value)) return 1; } while (0)
QJsonObject call(ValmaxRuntime *runtime, const QJsonObject &request) {
    auto bytes = QJsonDocument(request).toJson(QJsonDocument::Compact);
    ValmaxCoreBuffer raw{nullptr, 0, 0};
    const auto code = valmax_runtime_dispatch(runtime, reinterpret_cast<const uint8_t *>(bytes.constData()), size_t(bytes.size()), &raw);
    volatile char *data = bytes.data(); for (qsizetype i = 0; i < bytes.size(); ++i) data[i] = 0;
    QJsonObject result;
    if (code == 0 && raw.data) result = QJsonDocument::fromJson(QByteArray(reinterpret_cast<const char *>(raw.data), qsizetype(raw.len))).object();
    valmax_core_buffer_free(&raw); return result;
}
QJsonObject operation(ValmaxRuntime *runtime, const QString &action, const QString &owner = QStringLiteral("101"), const QString &chat = QStringLiteral("202")) {
    QJsonObject value{{QStringLiteral("schema"), 1}, {QStringLiteral("accountId"), owner}, {QStringLiteral("chatId"), chat}};
    if (action == QStringLiteral("set") || action == QStringLiteral("unlock") || action == QStringLiteral("remove")) value.insert(QStringLiteral("pin"), QStringLiteral("426819"));
    if (action == QStringLiteral("set")) value.insert(QStringLiteral("confirmPin"), QStringLiteral("426819"));
    return call(runtime, {{QStringLiteral("operation"), QStringLiteral("chat_lock.execute")}, {QStringLiteral("action"), action}, {QStringLiteral("value"), value}});
}
QJsonArray entries(ValmaxRuntime *runtime) {
    return call(runtime, {{QStringLiteral("operation"), QStringLiteral("chat_lock.catalog")}}).value(QStringLiteral("result")).toObject().value(QStringLiteral("protected")).toArray();
}
bool succeeded(const QJsonObject &value) { return value.value(QStringLiteral("ok")) == QJsonValue(true); }
bool unavailable(const QJsonObject &value) { return value.value(QStringLiteral("ok")) == QJsonValue(false) && value.value(QStringLiteral("error")) == QJsonValue(QStringLiteral("notification_unavailable")); }
}
extern "C" uint32_t valmax_notifications_install(const wchar_t *main, const wchar_t *backend, const wchar_t *asset) {
    ++installs;
    const auto root = QCoreApplication::applicationDirPath();
    validPaths = QString::fromWCharArray(main) == QDir::toNativeSeparators(QCoreApplication::applicationFilePath())
        && QString::fromWCharArray(backend) == QDir::toNativeSeparators(root + QStringLiteral("/notifications.dll"))
        && QString::fromWCharArray(asset) == QDir::toNativeSeparators(root + QStringLiteral("/valmax-private-notification-f2bb5bbaca678eca.png"));
    return installCode;
}
extern "C" uint32_t valmax_notifications_set_policy(const ValmaxNotificationPolicy *policy, const ValmaxNotificationScope *items) {
    if (!policy || !policy->ready) return VALMAX_NOTIFICATION_CATALOG_UNAVAILABLE;
    if (policy->generation <= current.generation || policy->count > 256 || (policy->count && !items)) return VALMAX_NOTIFICATION_INVALID_POLICY;
    current = *policy; scopes.clear();
    for (uint32_t i = 0; i < current.count; ++i) scopes.push_back(items[i]);
    return 0;
}
extern "C" uint32_t valmax_notifications_status(ValmaxNotificationStatus *status) {
    ValmaxNotificationStatus value{}; value.size = sizeof(value); value.version = 1;
    value.ready = 1; value.error = statusError; value.replacements = replacementCount; value.listeners = 2;
    value.generation = current.generation; std::memcpy(value.revision, current.revision, 64);
    if (incorrectRevision) value.revision[0] = value.revision[0] == 'a' ? 'b' : 'a';
    value.applied = drainingStatuses == 0 ? 1 : 0; value.in_flight = drainingStatuses == 0 ? 0 : 1;
    if (drainingStatuses > 0) --drainingStatuses;
    *status = value; return 0;
}
extern "C" uint32_t valmax_notifications_shutdown() {
    ++shutdowns;
    if (busyShutdowns > 0) { --busyShutdowns; return VALMAX_NOTIFICATION_BUSY; }
    return 0;
}
int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    if (argc != 2) return 2;
    const QString root = QString::fromLocal8Bit(argv[1]);
    QDir().mkpath(root + QStringLiteral("/profile")); QDir().mkpath(root + QStringLiteral("/runtime"));
    const auto config = QJsonDocument(QJsonObject{{QStringLiteral("profileDirectory"), root + QStringLiteral("/profile")},
        {QStringLiteral("runtimeDirectory"), root + QStringLiteral("/runtime")}, {QStringLiteral("existingClient"), false}}).toJson(QJsonDocument::Compact);
    ValmaxRuntime *runtime = nullptr; ValmaxCoreBuffer result{nullptr, 0, 0};
    CHECK(valmax_runtime_open(reinterpret_cast<const uint8_t *>(config.constData()), size_t(config.size()), &runtime, &result) == 0 && runtime);
    valmax_core_buffer_free(&result);
    CHECK(unavailable(operation(runtime, QStringLiteral("set")))); CHECK(entries(runtime).isEmpty());
    for (const auto code : {VALMAX_NOTIFICATION_MODULE_MISMATCH, VALMAX_NOTIFICATION_HOOK_FAILED}) {
        installCode = code;
        { valmax::NotificationHost host(runtime); CHECK(host.refresh() == QStringLiteral("unavailable"));
          CHECK(unavailable(operation(runtime, QStringLiteral("set")))); CHECK(entries(runtime).isEmpty()); }
    }
    CHECK(validPaths);
    installCode = VALMAX_NOTIFICATION_BUSY; const int previous = shutdowns;
    { valmax::NotificationHost host(runtime); CHECK(host.refresh() == QStringLiteral("unavailable")); }
    CHECK(shutdowns == previous);
    installCode = VALMAX_NOTIFICATION_NOT_READY;
    {
        valmax::NotificationHost host(runtime);
        CHECK(host.refresh() == QStringLiteral("unavailable"));
        const int initialInstalls = installs;
        for (int i = 0; i < 10; ++i) CHECK(host.refresh() == QStringLiteral("unavailable"));
        CHECK(installs == initialInstalls); // UI cannot retry startup in a loop.
        installCode = 0;
        QThread::msleep(270);
        CHECK(host.refresh() == QStringLiteral("ready"));
        CHECK(installs == initialInstalls + 1); // Same native host lease retained.
    }
    installCode = 0; replacementCount = 5;
    { valmax::NotificationHost host(runtime); CHECK(host.refresh() == QStringLiteral("unavailable"));
      CHECK(unavailable(operation(runtime, QStringLiteral("set")))); CHECK(entries(runtime).isEmpty()); }
    replacementCount = 6;
    {
        valmax::NotificationHost host(runtime);
        CHECK(host.refresh() == QStringLiteral("ready")); CHECK(current.count == 0);
        incorrectRevision = true;
        CHECK(unavailable(operation(runtime, QStringLiteral("set")))); CHECK(entries(runtime).isEmpty());
        incorrectRevision = false; drainingStatuses = 3;
        CHECK(succeeded(operation(runtime, QStringLiteral("set")))); CHECK(drainingStatuses == 0);
        CHECK(entries(runtime).size() == 1); CHECK(current.count == 1 && scopes[0].owner == 101 && scopes[0].chat == 202);
        CHECK(succeeded(operation(runtime, QStringLiteral("set"), QStringLiteral("101"), QStringLiteral("0"))));
        CHECK(current.count == 2 && scopes[0].chat == 0);
        CHECK(succeeded(operation(runtime, QStringLiteral("remove"), QStringLiteral("101"), QStringLiteral("0"))));
        CHECK(host.refresh() == QStringLiteral("ready") && current.count == 1);
        CHECK(succeeded(operation(runtime, QStringLiteral("lock"))));
        CHECK(succeeded(operation(runtime, QStringLiteral("unlock"))));
        CHECK(succeeded(operation(runtime, QStringLiteral("set"), QStringLiteral("9223372036854775807"), QStringLiteral("-9223372036854775808"))));
        CHECK(current.count == 2 && scopes[1].chat == INT64_MIN && scopes[1].owner == INT64_MAX);
        const auto before = current.generation;
        std::array<std::thread, 4> threads;
        std::atomic<int> completed{0};
        for (auto &thread : threads) thread = std::thread([&] { for (int i = 0; i < 4; ++i) if (host.refresh() == QStringLiteral("ready")) ++completed; });
        for (auto &thread : threads) thread.join();
        CHECK(completed == 16 && current.generation == before + 16);
        statusError = VALMAX_NOTIFICATION_LEASE_EXPIRED;
        CHECK(host.refresh() == QStringLiteral("unavailable"));
        CHECK(unavailable(operation(runtime, QStringLiteral("set"), QStringLiteral("101"), QStringLiteral("303"))));
        CHECK(entries(runtime).size() == 2);
        statusError = 0;
        CHECK(succeeded(operation(runtime, QStringLiteral("remove"))));
        CHECK(host.refresh() == QStringLiteral("ready")); CHECK(current.count == 1);
        busyShutdowns = 2;
    }
    CHECK(busyShutdowns == 0);
    CHECK(unavailable(operation(runtime, QStringLiteral("set")))); CHECK(entries(runtime).size() == 1);
    const auto previousGeneration = current.generation;
    {
        valmax::NotificationHost restarted(runtime);
        CHECK(restarted.refresh() == QStringLiteral("ready"));
        CHECK(current.generation > previousGeneration && current.count == 1);
    }
    current.generation = UINT64_MAX;
    { valmax::NotificationHost exhausted(runtime); CHECK(exhausted.refresh() == QStringLiteral("unavailable")); }
    CHECK(current.generation == UINT64_MAX); // Never wrap to a replayable policy.
    valmax_runtime_close(&runtime); CHECK(runtime == nullptr);
    QTextStream(stdout) << "{\"schema\":1,\"ok\":true,\"checks\":" << checks << ",\"syntheticHookABI\":true,\"realRustRuntime\":true}\n";
    return 0;
}
