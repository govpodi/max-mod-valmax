#include "native_bridge.h"
#if VALMAX_HAS_EXPORT_DOWNLOAD
#include "export_downloader.h"
#endif

#include <QMetaType>
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QEvent>
#include <QPointer>
#include <QSet>
#include <QThread>
#include <QRunnable>
#include <QJsonDocument>
#include <QJsonObject>
#include <memory>
#include <atomic>
#if VALMAX_HAS_EXPORT_HISTORY
#include "export_history_host.h"
#elif VALMAX_HAS_EXPORT_SELECTION
#include "export_selection_host.h"
#endif
#if VALMAX_HAS_GHOST
#include "ghost_host.h"
#endif
#if VALMAX_HAS_NOTIFICATIONS
#include "notification_host.h"
#endif
#if VALMAX_HAS_RETENTION
#include "retention_host_bridge.h"
#endif
#include "privacy_host.h"
#if VALMAX_HAS_RUST_CORE
#include "valmax_core.h"
#endif
#include <cmath>

namespace valmax {
namespace {
constexpr qsizetype MaximumRows = 1024;
constexpr qsizetype MaximumRequests = 8;
constexpr qsizetype BatchSize = 5;
constexpr qsizetype MaximumRequestBytes = 16 * 1024 * 1024;
struct ErasedBytes {
    QByteArray bytes;
    ~ErasedBytes() {
        // Erase the bridge's unique UTF-8 storage. The caller still owns the
        // original QML string; do not claim this erases the JavaScript heap.
        volatile char *data = bytes.data();
        for (qsizetype i = 0; i < bytes.size(); ++i) data[i] = 0;
    }
};
QVariantMap failure(const QString &code)
{
    return {{QStringLiteral("accepted"), false}, {QStringLiteral("requestId"), QString()},
            {QStringLiteral("error"), code}};
}
} // namespace

#if VALMAX_HAS_EXPORT_HISTORY
using ExportBoundHost = ExportHistoryHost;
#elif VALMAX_HAS_EXPORT_SELECTION
using ExportBoundHost = ExportSelectionHost;
#endif

struct NativeBridge::RuntimeState {
#if VALMAX_HAS_RUST_CORE
    ValmaxRuntime *handle = nullptr;
#if VALMAX_HAS_EXPORT_HISTORY || VALMAX_HAS_EXPORT_SELECTION
    std::unique_ptr<ExportBoundHost> history;
#endif
#if VALMAX_HAS_GHOST
    std::unique_ptr<GhostHost> ghost;
    std::atomic<bool> syncing{false};
#endif
#if VALMAX_HAS_NOTIFICATIONS
    std::unique_ptr<NotificationHost> notifications;
    std::atomic<bool> notificationSyncing{false};
#endif
#if VALMAX_HAS_RETENTION
    std::unique_ptr<RetentionHostBridge> retention;
    std::atomic<bool> retentionSyncing{false};
#endif
    ~RuntimeState() {
#if VALMAX_HAS_EXPORT_HISTORY || VALMAX_HAS_EXPORT_SELECTION
        history.reset();
#endif
#if VALMAX_HAS_NOTIFICATIONS
        notifications.reset();
#endif
#if VALMAX_HAS_RETENTION
        retention.reset();
#endif
#if VALMAX_HAS_GHOST
        ghost.reset();
#endif
        valmax_runtime_close(&handle);
    }
#endif
};

struct NativeBridge::PrivacyState {
#if VALMAX_HAS_PRIVACY
    PrivacyHost host;
    std::atomic<bool> syncing{false};
#endif
};

NativeBridge::NativeBridge(QObject *parent) : QObject(parent)
{
#if VALMAX_HAS_EXPORT_DOWNLOAD
    exportDownloader_ = new ExportDownloader(this);
#endif
    workers_.setMaxThreadCount(2);
    timer_.setSingleShot(true);
    timer_.setInterval(1);
    timer_.setTimerType(Qt::PreciseTimer);
    connect(&timer_, &QTimer::timeout, this, &NativeBridge::drain);
    ghostTimer_.setInterval(2000);
    connect(&ghostTimer_, &QTimer::timeout, this, &NativeBridge::refreshGhost);
    notificationTimer_.setInterval(2000);
    connect(&notificationTimer_, &QTimer::timeout, this, &NativeBridge::refreshNotifications);
#if VALMAX_HAS_RETENTION
    retentionTimer_.setInterval(2000);
    connect(&retentionTimer_, &QTimer::timeout, this, &NativeBridge::refreshRetention);
#endif
    privacyReport_ = PrivacyHost::unavailable();
    privacyTimer_.setInterval(2000);
    connect(&privacyTimer_, &QTimer::timeout, this, &NativeBridge::refreshPrivacy);
#if VALMAX_HAS_PRIVACY
    privacy_ = std::make_shared<PrivacyState>();
    refreshPrivacy(); privacyTimer_.start();
#endif
#if VALMAX_HAS_RUST_CORE
    // This value is supplied only by the native launcher or synthetic harness.
    // QML receives no path-changing method and there is no MXMOD discovery.
    const QByteArray config = qgetenv("VALMAX_NATIVE_RUNTIME_CONFIG");
    if (!config.isEmpty()) {
        if (config.size() > 16384 || !servicesAvailable()) {
            runtimeState_ = QStringLiteral("error");
            return;
        }
        runtime_ = std::make_shared<RuntimeState>();
        runtimeState_ = QStringLiteral("opening");
        const auto context = runtime_;
        QElapsedTimer openingClock;
        openingClock.start();
        workers_.start(QRunnable::create([this, config, context, openingClock]() {
            const qint64 workerQueueMs = openingClock.elapsed();
            ValmaxCoreBuffer result{nullptr, 0, 0};
            const int code = valmax_runtime_open(reinterpret_cast<const uint8_t *>(config.constData()),
                                                 size_t(config.size()), &context->handle, &result);
            const bool opened = code == 0 && context->handle != nullptr;
            valmax_core_buffer_free(&result);
            const qint64 rustOpenMs = openingClock.elapsed() - workerQueueMs;
            QString ghostState = QStringLiteral("unavailable");
            QString notificationState = QStringLiteral("unavailable");
            qint64 notificationRefreshMs = -1;
            qint64 ghostRefreshMs = -1;
            qint64 retentionInitMs = -1;
#if VALMAX_HAS_NOTIFICATIONS
            if (opened) {
                const qint64 started = openingClock.elapsed();
                context->notifications = std::make_unique<NotificationHost>(context->handle);
                notificationState = context->notifications->refresh();
                notificationRefreshMs = openingClock.elapsed() - started;
            }
#endif
#if VALMAX_HAS_GHOST
            if (opened) {
                const qint64 started = openingClock.elapsed();
                context->ghost = std::make_unique<GhostHost>(context->handle);
                ghostState = context->ghost->refresh();
                ghostRefreshMs = openingClock.elapsed() - started;
            }
#endif
#if VALMAX_HAS_RETENTION
            if (opened) {
                const qint64 started = openingClock.elapsed();
                const QString profile = QJsonDocument::fromJson(config).object()
                    .value(QStringLiteral("profileDirectory")).toString();
                context->retention = std::make_unique<RetentionHostBridge>(context->handle, profile);
                context->retention->initialize();
                retentionInitMs = openingClock.elapsed() - started;
            }
#endif
            const qint64 workerMs = openingClock.elapsed() - workerQueueMs;
            QMetaObject::invokeMethod(this, [this, opened, ghostState, notificationState,
                                             openingClock, workerQueueMs, rustOpenMs,
                                             notificationRefreshMs, ghostRefreshMs, retentionInitMs,
                                             workerMs]() {
#if VALMAX_HAS_EXPORT_DOWNLOAD
                if (opened) static_cast<ExportDownloader*>(exportDownloader_)->setRuntime(runtime_->handle, runtime_);
#endif
#if VALMAX_HAS_EXPORT_HISTORY || VALMAX_HAS_EXPORT_SELECTION
                if (opened) runtime_->history = std::make_unique<ExportBoundHost>(runtime_->handle);
#endif
                const auto measured = [](qint64 ms) -> QVariant {
                    return ms < 0 ? QVariant() : QVariant::fromValue(ms);
                };
                startupTimings_ = {
                    {QStringLiteral("schema"), 1},
                    {QStringLiteral("workerQueueMs"), workerQueueMs},
                    {QStringLiteral("rustOpenMs"), rustOpenMs},
                    {QStringLiteral("notificationRefreshMs"), measured(notificationRefreshMs)},
                    {QStringLiteral("ghostRefreshMs"), measured(ghostRefreshMs)},
                    {QStringLiteral("retentionInitMs"), measured(retentionInitMs)},
                    {QStringLiteral("workerMs"), workerMs},
                    {QStringLiteral("totalMs"), openingClock.elapsed()},
                };
                runtimeState_ = opened ? QStringLiteral("ready") : QStringLiteral("error");
                ghostRuntimeState_ = ghostState;
                notificationRuntimeState_ = notificationState;
                const QPointer<NativeBridge> alive(this);
                emit runtimeStateChanged();
                if (!alive) return;
                emit ghostRuntimeStateChanged();
                if (!alive) return;
                emit notificationRuntimeStateChanged();
                if (!alive) return;
                emit startupTimingsChanged();
                if (!alive) return;
#if VALMAX_HAS_NOTIFICATIONS
                if (opened) notificationTimer_.start();
#endif
#if VALMAX_HAS_GHOST
                if (opened) ghostTimer_.start();
#endif
#if VALMAX_HAS_RETENTION
                if (opened) retentionTimer_.start();
#endif
            }, Qt::QueuedConnection);
        }));
    }
#endif
}

NativeBridge::~NativeBridge()
{
    // A worker may post a result until waitForDone returns. Remove those posts
    // while members are alive: destroyed() observers may run a nested event
    // loop before QObject's base destructor performs its own event cleanup.
    timer_.stop();
    ghostTimer_.stop();
    notificationTimer_.stop();
#if VALMAX_HAS_RETENTION
    retentionTimer_.stop();
#endif
    privacyTimer_.stop();
    workers_.clear();
#if VALMAX_HAS_RUST_CORE
    // A ready runtime was published onto this GUI thread. During opening its
    // handle is still owned by the worker; no export can have been admitted.
    if (runtimeAvailable() && runtime_ && runtime_->handle)
        valmax_runtime_cancel_pending(runtime_->handle);
#endif
    workers_.waitForDone();
    // Stop/join network tasks while their Rust context and cancel flags live.
    delete exportDownloader_; exportDownloader_ = nullptr;
    QCoreApplication::removePostedEvents(this, QEvent::MetaCall);
    runtime_.reset();
    privacy_.reset();
}

bool NativeBridge::beginExportHistory(const QString &id, const QString &token)
{
#if VALMAX_HAS_EXPORT_HISTORY || VALMAX_HAS_EXPORT_SELECTION
    return runtimeAvailable() && runtime_ && runtime_->history && runtime_->history->begin(id,token);
#else
    Q_UNUSED(id); Q_UNUSED(token); return false;
#endif
}
bool NativeBridge::bindExportHistory(QObject *view, const QString &id, const QString &token)
{
#if VALMAX_HAS_EXPORT_HISTORY || VALMAX_HAS_EXPORT_SELECTION
    return runtimeAvailable() && runtime_ && runtime_->history && runtime_->history->bind(view,id,token);
#else
    Q_UNUSED(view); Q_UNUSED(id); Q_UNUSED(token); return false;
#endif
}
void NativeBridge::releaseExportHistory(const QString &id, const QString &token)
{
#if VALMAX_HAS_EXPORT_HISTORY || VALMAX_HAS_EXPORT_SELECTION
    if (runtime_ && runtime_->history) runtime_->history->release(id,token);
#else
    Q_UNUSED(id); Q_UNUSED(token);
#endif
}

void NativeBridge::refreshGhost()
{
#if VALMAX_HAS_GHOST
    const auto context = runtime_;
    if (!runtimeAvailable() || !context || !context->ghost || context->syncing.exchange(true)) return;
    workers_.start(QRunnable::create([this, context]() {
        const QString state = context->ghost->refresh();
        QMetaObject::invokeMethod(this, [this, context, state]() {
            context->syncing.store(false);
            if (ghostRuntimeState_ == state) return;
            ghostRuntimeState_ = state;
            emit ghostRuntimeStateChanged();
        }, Qt::QueuedConnection);
    }));
#endif
}

void NativeBridge::refreshNotifications()
{
#if VALMAX_HAS_NOTIFICATIONS
    const auto context = runtime_;
    if (!runtimeAvailable() || !context || !context->notifications || context->notificationSyncing.exchange(true)) return;
    workers_.start(QRunnable::create([this, context]() {
        const QString state = context->notifications->refresh();
        QMetaObject::invokeMethod(this, [this, context, state]() {
            context->notificationSyncing.store(false);
            if (notificationRuntimeState_ == state) return;
            notificationRuntimeState_ = state;
            emit notificationRuntimeStateChanged();
        }, Qt::QueuedConnection);
    }));
#endif
}

#if VALMAX_HAS_RETENTION
void NativeBridge::refreshRetention()
{
    const auto context = runtime_;
    if (!runtimeAvailable() || !context || !context->retention
        || context->retentionSyncing.exchange(true)) return;
    workers_.start(QRunnable::create([this, context]() {
        context->retention->refresh();
        QMetaObject::invokeMethod(this, [context]() {
            context->retentionSyncing.store(false);
        }, Qt::QueuedConnection);
    }));
}
#endif

void NativeBridge::refreshPrivacy()
{
#if VALMAX_HAS_PRIVACY
    const auto context = privacy_;
    if (!context || context->syncing.exchange(true)) return;
    workers_.start(QRunnable::create([this, context]() {
        const auto report = context->host.refresh();
        QMetaObject::invokeMethod(this, [this, context, report]() {
            // Do not admit a newer status read until this result reaches the
            // GUI. Otherwise a delayed successful report can overtake revocation.
            context->syncing.store(false);
            if (privacyReport_ == report) return;
            privacyReport_ = report; emit privacyReportChanged();
        }, Qt::QueuedConnection);
    }));
#endif
}

bool NativeBridge::servicesAvailable() const
{
#if VALMAX_HAS_RUST_CORE
    return valmax_core_abi_version() == 1;
#else
    return false;
#endif
}

QVariantMap NativeBridge::invoke(const QString &requestJson)
{
    if (QThread::currentThread() != thread()) return failure(QStringLiteral("wrong_thread"));
    if (!servicesAvailable()) return failure(QStringLiteral("core_unavailable"));
    if (pendingOperations_ >= 8) return failure(QStringLiteral("queue_full"));
    if (requestJson.isEmpty() || requestJson.size() > MaximumRequestBytes) return failure(QStringLiteral("invalid_request"));
#if VALMAX_HAS_RUST_CORE
    auto input = std::make_shared<ErasedBytes>();
    input->bytes = requestJson.toUtf8();
    if (input->bytes.size() > MaximumRequestBytes) return failure(QStringLiteral("invalid_request"));
    const QString id = QStringLiteral("op-") + QString::number(++nextId_);
    const auto context = runtimeAvailable() ? runtime_ : std::shared_ptr<RuntimeState>();
    ++pendingOperations_;
    const QPointer<NativeBridge> alive(this);
    emit pendingOperationsChanged();
    if (!alive) return failure(QStringLiteral("cancelled"));
    workers_.start(QRunnable::create([this, input, id, context]() {
        ValmaxCoreBuffer raw{nullptr, 0, 0};
        const auto *bytes = reinterpret_cast<const uint8_t *>(input->bytes.constData());
#if VALMAX_HAS_RETENTION
        RetentionHostBridge::Change retentionChange;
        if (context && context->retention)
            retentionChange = context->retention->before_dispatch(input->bytes);
        const bool skipRetention = retentionChange.selected && !retentionChange.proceed;
#else
        constexpr bool skipRetention = false;
#endif
        const int code = skipRetention ? 1 : context
            ? valmax_runtime_dispatch(context->handle, bytes, size_t(input->bytes.size()), &raw)
            : valmax_core_dispatch(bytes, size_t(input->bytes.size()), &raw);
        auto output = std::make_shared<ErasedBytes>();
        if (skipRetention)
            output->bytes = QByteArrayLiteral("{\"schema\":1,\"ok\":false,\"error\":\"retention_unavailable\"}");
        else if (code == 0 && raw.data && raw.len <= 24 * 1024 * 1024)
            output->bytes = QByteArray(reinterpret_cast<const char *>(raw.data), qsizetype(raw.len));
        else
            output->bytes = QByteArrayLiteral("{\"schema\":1,\"ok\":false,\"error\":\"internal_error\"}");
        valmax_core_buffer_free(&raw);
#if VALMAX_HAS_RETENTION
        if (context && context->retention) {
            context->retention->after_dispatch(retentionChange, output->bytes);
            if (retentionChange.lock.owns_lock()) retentionChange.lock.unlock();
        }
#endif
        QMetaObject::invokeMethod(this, [this, id, output]() {
            --pendingOperations_;
            const QPointer<NativeBridge> alive(this);
            emit pendingOperationsChanged();
            if (!alive) return;
            emit operationFinished(id, QString::fromUtf8(output->bytes));
        }, Qt::QueuedConnection);
    }));
    return {{QStringLiteral("accepted"), true}, {QStringLiteral("requestId"), id},
            {QStringLiteral("error"), QString()}};
#else
    return failure(QStringLiteral("core_unavailable"));
#endif
}

QVariantMap NativeBridge::enqueueRows(const QVariantList &rows)
{
    if (QThread::currentThread() != thread())
        return failure(QStringLiteral("wrong_thread"));
    if (rows.isEmpty() || rows.size() > MaximumRows)
        return failure(QStringLiteral("invalid_rows"));
    if (queue_.size() >= MaximumRequests)
        return failure(QStringLiteral("queue_full"));
    QVariantList clean;
    QSet<int> seen;
    clean.reserve(rows.size());
    for (const QVariant &value : rows) {
        const int type = value.metaType().id();
        if (type != QMetaType::Int && type != QMetaType::Double && type != QMetaType::UInt)
            return failure(QStringLiteral("invalid_rows"));
        const double number = value.toDouble();
        if (!std::isfinite(number) || number < 0 || number > 1000000000 || std::floor(number) != number)
            return failure(QStringLiteral("invalid_rows"));
        const int row = int(number);
        if (!seen.contains(row)) {
            seen.insert(row);
            clean.append(row);
        }
    }
    const QString id = QString::number(++nextId_);
    queue_.enqueue({id, clean, 0});
    const QPointer<NativeBridge> alive(this);
    emit pendingRequestsChanged();
    if (!alive) return failure(QStringLiteral("cancelled"));
    schedule();
    return {{QStringLiteral("accepted"), true}, {QStringLiteral("requestId"), id},
            {QStringLiteral("error"), QString()}};
}

bool NativeBridge::cancel(const QString &requestId)
{
    if (QThread::currentThread() != thread())
        return false;
    for (qsizetype i = 0; i < queue_.size(); ++i) {
        if (queue_.at(i).id == requestId) {
            queue_.removeAt(i);
            if (queue_.isEmpty())
                timer_.stop();
            const QPointer<NativeBridge> alive(this);
            emit pendingRequestsChanged();
            if (!alive) return true;
            emit cancelled(requestId);
            return true;
        }
    }
    return false;
}

void NativeBridge::cancelAll()
{
    if (QThread::currentThread() != thread() || queue_.isEmpty())
        return;
    const auto previous = queue_;
    queue_.clear();
    timer_.stop();
    const QPointer<NativeBridge> alive(this);
    emit pendingRequestsChanged();
    if (!alive) return;
    for (const auto &request : previous) {
        emit cancelled(request.id);
        if (!alive) return;
    }
}

void NativeBridge::schedule()
{
    if (!queue_.isEmpty() && !timer_.isActive())
        timer_.start();
}

void NativeBridge::drain()
{
    if (queue_.isEmpty() || publishing_)
        return;
    publishing_ = true;
    const QPointer<NativeBridge> alive(this);
    // Copy the batch before emitting: a QML signal handler may cancel or
    // enqueue another request synchronously. Never retain a queue reference.
    const QString id = queue_.head().id;
    const qsizetype offset = queue_.head().offset;
    const QVariantList batch = queue_.head().rows.mid(offset, BatchSize);
    queue_.head().offset += batch.size();
    emit rowsReady(id, batch);
    if (!alive) return;
    // Cancellation inside rowsReady must not subsequently emit completed.
    if (!queue_.isEmpty() && queue_.head().id == id
        && queue_.head().offset == queue_.head().rows.size()) {
        queue_.dequeue();
        emit pendingRequestsChanged();
        if (!alive) return;
        emit completed(id);
        if (!alive) return;
    }
    publishing_ = false;
    schedule();
}
} // namespace valmax
