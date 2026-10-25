#include "ghost_host.h"
#include "valmax_core.h"
#include "ghost.h"
#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QThread>
#include <algorithm>
#include <atomic>
#include <cstring>

namespace valmax {
namespace {
// One owner per process. A second QQmlEngine may read settings, but cannot
// replace the live hook policy or fabricate another installation receipt.
std::atomic<bool> owned{false};
bool policyFromRuntime(ValmaxRuntime *runtime, ValmaxGhostPolicy &policy)
{
    constexpr char request[] = "{\"operation\":\"ghost.get\"}";
    ValmaxCoreBuffer result{nullptr, 0, 0};
    const int code = valmax_runtime_dispatch(runtime, reinterpret_cast<const uint8_t *>(request), sizeof(request) - 1, &result);
    QJsonDocument document;
    if (code == 0 && result.data && result.len <= 16384)
        document = QJsonDocument::fromJson(QByteArray(reinterpret_cast<const char *>(result.data), qsizetype(result.len)));
    valmax_core_buffer_free(&result);
    const auto envelope = document.object();
    const auto value = envelope.value(QStringLiteral("result")).toObject();
    const auto settings = value.value(QStringLiteral("settings")).toObject();
    const QByteArray revision = value.value(QStringLiteral("revision")).toString().toLatin1();
    if (envelope.value(QStringLiteral("ok")) != QJsonValue(true) || revision.size() != 64 || settings.size() != 5) return false;
    for (char c : revision) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    policy = {}; policy.size = sizeof(policy); policy.version = 1;
    std::memcpy(policy.revision, revision.constData(), 64);
    const std::pair<const char *, uint8_t *> flags[] = {
        {"enabled", &policy.enabled}, {"hideTyping", &policy.hide_typing},
        {"hideRead", &policy.hide_read}, {"hideOnline", &policy.hide_online}, {"hideStories", &policy.hide_stories}};
    for (const auto &[key, output] : flags) {
        const auto flag = settings.value(QString::fromLatin1(key));
        if (!flag.isBool()) return false;
        *output = flag.toBool() ? 1 : 0;
    }
    return true;
}
}
GhostHost::GhostHost(ValmaxRuntime *runtime) : runtime_(runtime)
{
    static_assert(sizeof(ValmaxGhostPolicy) == 80 && sizeof(ValmaxGhostStatus) == 144);
}
GhostHost::~GhostHost()
{
    valmax_runtime_ghost_ack(runtime_, nullptr);
    if (!lease_) return;
    // Modules and callback memory remain pinned even after successful detach.
    // A finite BUSY result never permits a new owner to overwrite trampolines.
    for (int attempt = 0; attempt < 10; ++attempt) {
        const auto code = valmax_ghost_shutdown();
        if (code == VALMAX_GHOST_OK) { owned.store(false, std::memory_order_release); return; }
        if (code != VALMAX_GHOST_BUSY) return;
        QThread::msleep(10);
    }
}
QString GhostHost::refresh()
{
    if (!installed_ && !attempted_ && std::chrono::steady_clock::now() >= retryAt_) {
        ValmaxGhostPolicy initial{};
        if (!policyFromRuntime(runtime_, initial)) {
            valmax_runtime_ghost_ack(runtime_, nullptr);
            return QStringLiteral("unavailable");
        }
        if (!lease_) {
            bool expected = false;
            lease_ = owned.compare_exchange_strong(expected, true, std::memory_order_acq_rel);
            if (!lease_) attempted_ = true;
        }
        if (lease_) {
            // Prime the verified requested policy before publishing any hook.
            // Otherwise install_targets briefly enables callbacks with the
            // DLL's default/previous flags and the first intercepted event
            // could pass through even when the stored ghost mode is active.
            ValmaxGhostStatus before{}; before.size = sizeof(before); before.version = 1;
            if (valmax_ghost_status(&before) != VALMAX_GHOST_OK || before.ready != 0
                || before.replacements != 0 || before.listeners != 0
                || valmax_ghost_set_policy(&initial) != VALMAX_GHOST_OK) {
                // Nothing was installed by this host. In particular, never
                // detach a pre-existing native owner in our destructor.
                lease_ = false;
                owned.store(false, std::memory_order_release);
                valmax_runtime_ghost_ack(runtime_, nullptr);
                return QStringLiteral("unavailable");
            }
            // Application paths are obtained from the native host, never QML,
            // the runtime profile JSON, or an arbitrary hash/path environment.
            const QString main = QDir::toNativeSeparators(QCoreApplication::applicationFilePath());
            const QString core = QDir::toNativeSeparators(QCoreApplication::applicationDirPath() + QStringLiteral("/core.dll"));
            ++installAttempts_;
            const auto code = valmax_ghost_install(reinterpret_cast<const wchar_t *>(core.utf16()),
                                                  reinterpret_cast<const wchar_t *>(main.utf16()));
            installed_ = code == VALMAX_GHOST_OK;
            if (code == VALMAX_GHOST_NOT_READY && installAttempts_ < 8) {
                const unsigned delay = 250u << std::min(installAttempts_ - 1, 3u);
                retryAt_ = std::chrono::steady_clock::now() + std::chrono::milliseconds(delay);
            } else {
                attempted_ = true; // Success, hard failure, or exhausted retry.
            }
            if (code == VALMAX_GHOST_BUSY) {
                // A pre-existing native owner is not ours to detach later.
                lease_ = false;
                owned.store(false, std::memory_order_release);
            }
        }
    }
    if (!installed_) { valmax_runtime_ghost_ack(runtime_, nullptr); return QStringLiteral("unavailable"); }
    ValmaxGhostPolicy desired{};
    if (!policyFromRuntime(runtime_, desired) || valmax_ghost_set_policy(&desired) != VALMAX_GHOST_OK) {
        valmax_runtime_ghost_ack(runtime_, nullptr); return QStringLiteral("unavailable");
    }
    ValmaxGhostStatus status{}; status.size = sizeof(status); status.version = 1;
    if (valmax_ghost_status(&status) != VALMAX_GHOST_OK || status.ready != 1 || status.error != 0
        || status.replacements != 5 || status.listeners != 7 || std::memcmp(&status.policy, &desired, sizeof(desired)) != 0
        || valmax_runtime_ghost_ack(runtime_, &status) != 0) {
        valmax_runtime_ghost_ack(runtime_, nullptr); return QStringLiteral("unavailable");
    }
    return desired.enabled && (desired.hide_typing || desired.hide_read || desired.hide_online || desired.hide_stories)
        ? QStringLiteral("active") : QStringLiteral("off");
}
}
