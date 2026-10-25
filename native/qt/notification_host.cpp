#include "notification_host.h"
#include "notification_api.h"
#include "valmax_core.h"
#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QThread>
#include <atomic>
#include <algorithm>
#include <cstring>
#include <limits>
#include <vector>

namespace valmax {
namespace {
std::atomic<bool> hostOwned{false};
bool canonicalId(const QString &value, bool owner, int64_t &out)
{
    if (value.isEmpty() || value.size() > 20) return false;
    bool ok = false;
    const qint64 number = value.toLongLong(&ok, 10);
    if (!ok || (owner && number <= 0) || QString::number(number) != value) return false;
    out = number; return true;
}
bool catalog(const uint8_t *input, size_t size, ValmaxNotificationPolicy &policy,
             std::vector<ValmaxNotificationScope> &scopes)
{
    if (!input || size == 0 || size > 65536) return false;
    const auto document = QJsonDocument::fromJson(QByteArray(reinterpret_cast<const char *>(input), qsizetype(size)));
    if (!document.isObject()) return false;
    const auto value = document.object();
    if (value.size() != 5 || value.value(QStringLiteral("schema")) != QJsonValue(1)
        || value.value(QStringLiteral("ready")) != QJsonValue(true)
        || !value.value(QStringLiteral("protected")).isArray()
        || value.value(QStringLiteral("leaseMs")) != QJsonValue(5000)) return false;
    const QByteArray revision = value.value(QStringLiteral("revision")).toString().toLatin1();
    if (revision.size() != 64) return false;
    for (char c : revision) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    const auto entries = value.value(QStringLiteral("protected")).toArray();
    if (entries.size() > 256) return false;
    QString previousOwner, previousChat;
    // IDs have only canonical ASCII digits and an optional leading minus, so
    // exact Python/Rust canonical JSON can be emitted without escaping rules.
    QByteArray canonical = QByteArrayLiteral("{\"protected\":[");
    for (const auto entry : entries) {
        if (!entry.isObject()) return false;
        const auto item = entry.toObject();
        if (item.size() != 2 || !item.value(QStringLiteral("ownerId")).isString()
            || !item.value(QStringLiteral("chatId")).isString()) return false;
        const QString owner = item.value(QStringLiteral("ownerId")).toString();
        const QString chat = item.value(QStringLiteral("chatId")).toString();
        ValmaxNotificationScope scope{};
        if (!canonicalId(owner, true, scope.owner) || !canonicalId(chat, false, scope.chat)
            || (!scopes.empty() && (owner < previousOwner || (owner == previousOwner && chat <= previousChat)))) return false;
        if (!scopes.empty()) canonical += ',';
        canonical += QByteArrayLiteral("{\"chatId\":\"") + chat.toLatin1()
            + QByteArrayLiteral("\",\"ownerId\":\"") + owner.toLatin1() + QByteArrayLiteral("\"}");
        scopes.push_back(scope); previousOwner = owner; previousChat = chat;
    }
    canonical += QByteArrayLiteral("],\"ready\":true,\"schema\":1}");
    if (QCryptographicHash::hash(canonical, QCryptographicHash::Sha256).toHex() != revision) return false;
    policy = {}; policy.size = sizeof(policy); policy.version = 1;
    policy.ready = 1; policy.lease_ms = 5000; policy.count = uint32_t(scopes.size());
    std::memcpy(policy.revision, revision.constData(), 64); return true;
}
}

NotificationHost::NotificationHost(ValmaxRuntime *runtime) : runtime_(runtime)
{
    const ValmaxNotificationCallbacks callbacks{sizeof(ValmaxNotificationCallbacks), 1, this, &prepare, &release};
    attached_ = valmax_runtime_notifications_attach(runtime_, &callbacks) == 0;
}
NotificationHost::~NotificationHost()
{
    // The bridge has drained every worker. Detach still waits for a Rust lease,
    // so callbacks can never outlive this user_data or the native context.
    if (attached_) valmax_runtime_notifications_attach(runtime_, nullptr);
    if (!owned_) return;
    for (int attempt = 0; attempt < 10; ++attempt) {
        const auto code = valmax_notifications_shutdown();
        if (code == VALMAX_NOTIFICATION_OK) { hostOwned.store(false, std::memory_order_release); return; }
        if (code != VALMAX_NOTIFICATION_BUSY) return;
        QThread::msleep(10);
    }
    // A still-running callback keeps the process-lifetime native owner pinned.
}
bool NotificationHost::install()
{
    if (installed_) return true;
    if (attempted_ || std::chrono::steady_clock::now() < retryAt_) return false;
    if (!owned_) {
        bool expected = false;
        owned_ = hostOwned.compare_exchange_strong(expected, true, std::memory_order_acq_rel);
        if (!owned_) { attempted_ = true; return false; }
    }
    ++installAttempts_;
    const QString main = QDir::toNativeSeparators(QCoreApplication::applicationFilePath());
    const QString backend = QDir::toNativeSeparators(QCoreApplication::applicationDirPath() + QStringLiteral("/notifications.dll"));
    const QString asset = QDir::toNativeSeparators(QCoreApplication::applicationDirPath() + QStringLiteral("/valmax-private-notification-f2bb5bbaca678eca.png"));
    const auto code = valmax_notifications_install(reinterpret_cast<const wchar_t *>(main.utf16()),
        reinterpret_cast<const wchar_t *>(backend.utf16()), reinterpret_cast<const wchar_t *>(asset.utf16()));
    installed_ = code == VALMAX_NOTIFICATION_OK;
    if (installed_) {
        // The DLL preserves known scopes/generation across shutdown. A new
        // engine must continue its counter, never replay generation one.
        ValmaxNotificationStatus status{}; status.size = sizeof(status); status.version = 1;
        if (valmax_notifications_status(&status) != VALMAX_NOTIFICATION_OK || status.size != sizeof(status)
            || status.version != 1 || status.ready != 1 || status.replacements != 6 || status.listeners != 2
            || (status.error != VALMAX_NOTIFICATION_OK && status.error != VALMAX_NOTIFICATION_CATALOG_UNAVAILABLE
                && status.error != VALMAX_NOTIFICATION_LEASE_EXPIRED)) {
            installed_ = false;
        } else generation_ = status.generation;
    }
    if (code == VALMAX_NOTIFICATION_NOT_READY && installAttempts_ < 8) {
        const unsigned delay = 250u << std::min(installAttempts_ - 1, 3u);
        retryAt_ = std::chrono::steady_clock::now() + std::chrono::milliseconds(delay);
        return false;
    }
    attempted_ = true; // Hard failure or exhausted bounded startup retry.
    if (code == VALMAX_NOTIFICATION_BUSY) {
        owned_ = false; hostOwned.store(false, std::memory_order_release);
    }
    return installed_;
}
void NotificationHost::revokeFreshness()
{
    if (!installed_) return;
    ValmaxNotificationPolicy policy{}; policy.size = sizeof(policy); policy.version = 1;
    // An unready update preserves the DLL's last-known protected scopes.
    valmax_notifications_set_policy(&policy, nullptr);
}
int32_t NotificationHost::prepare(void *self, const uint8_t *input, size_t size, uint64_t *lease)
{
    if (!self || !lease) return 1;
    *lease = 0;
    try { return static_cast<NotificationHost *>(self)->prepareCatalog(input, size, lease); }
    catch (...) { return 1; } // C ABI must not propagate a C++ exception.
}
int32_t NotificationHost::prepareCatalog(const uint8_t *input, size_t size, uint64_t *lease)
{
    std::unique_lock guard(transaction_);
    ValmaxNotificationPolicy policy{};
    std::vector<ValmaxNotificationScope> scopes;
    if (!catalog(input, size, policy, scopes)) { revokeFreshness(); return 1; }
    if (!install() || generation_ == std::numeric_limits<uint64_t>::max()) return 1;
    policy.generation = ++generation_;
    if (valmax_notifications_set_policy(&policy, scopes.empty() ? nullptr : scopes.data()) != VALMAX_NOTIFICATION_OK) return 1;
    // applied requires old notification callbacks to drain. Bound this wait;
    // a busy/mismatched filter must not permit the verifier to be persisted.
    for (int attempt = 0; attempt < 20; ++attempt) {
        ValmaxNotificationStatus status{}; status.size = sizeof(status); status.version = 1;
        if (valmax_notifications_status(&status) != VALMAX_NOTIFICATION_OK || status.ready != 1
            || status.size != sizeof(status) || status.version != 1
            || status.error != 0 || status.replacements != 6 || status.listeners != 2
            || status.generation != policy.generation || std::memcmp(status.revision, policy.revision, 64) != 0) return 1;
        if (status.applied == 1 && status.in_flight == 0) {
            lease_ = policy.generation; leaseThread_ = std::this_thread::get_id(); *lease = lease_;
            guard.release(); // paired release callback on this same OS thread
            return 0;
        }
        QThread::msleep(5);
    }
    return 1;
}
void NotificationHost::release(void *self, uint64_t lease)
{
    if (!self) return;
    auto *host = static_cast<NotificationHost *>(self);
    if (lease == 0 || host->lease_ != lease || host->leaseThread_ != std::this_thread::get_id()) return;
    host->lease_ = 0; host->leaseThread_ = {}; host->transaction_.unlock();
}
QString NotificationHost::refresh()
{
    // Never hold transaction_ while calling Rust: prepare acquires it after
    // the Rust store/grant locks, including the durable set transaction.
    return attached_ && valmax_runtime_notifications_refresh(runtime_) == 0
        ? QStringLiteral("ready") : QStringLiteral("unavailable");
}
}
