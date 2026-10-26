#pragma once

#include <QObject>
#include <QQueue>
#include <QTimer>
#include <QVariantList>
#include <QVariantMap>
#include <QThreadPool>
#include <memory>

namespace valmax {

// Owned by a single QQmlEngine. Never opens a socket. Profile-scoped stores
// require an explicit native-host environment configuration before creation.
// Each event-loop turn publishes at most five identifiers, leaving actual
// delegate creation on the QML GUI thread. This is a migration primitive;
// production list delegates still need to opt into its signals.
class NativeBridge final : public QObject {
    Q_OBJECT
    Q_PROPERTY(int apiVersion READ apiVersion CONSTANT)
    Q_PROPERTY(QString transport READ transport CONSTANT)
    Q_PROPERTY(int pendingRequests READ pendingRequests NOTIFY pendingRequestsChanged)
    Q_PROPERTY(bool servicesAvailable READ servicesAvailable CONSTANT)
    Q_PROPERTY(int pendingOperations READ pendingOperations NOTIFY pendingOperationsChanged)
    Q_PROPERTY(bool runtimeAvailable READ runtimeAvailable NOTIFY runtimeStateChanged)
    Q_PROPERTY(QString runtimeState READ runtimeState NOTIFY runtimeStateChanged)
    Q_PROPERTY(QString ghostRuntimeState READ ghostRuntimeState NOTIFY ghostRuntimeStateChanged)
    Q_PROPERTY(QString notificationRuntimeState READ notificationRuntimeState NOTIFY notificationRuntimeStateChanged)
    Q_PROPERTY(QVariantMap startupTimings READ startupTimings NOTIFY startupTimingsChanged)
    Q_PROPERTY(QVariantMap retentionDiagnostics READ retentionDiagnostics NOTIFY retentionDiagnosticsChanged)
    Q_PROPERTY(QVariantMap privacyReport READ privacyReport NOTIFY privacyReportChanged)
    Q_PROPERTY(QObject* exportDownloader READ exportDownloader CONSTANT)
public:
    explicit NativeBridge(QObject *parent = nullptr);
    ~NativeBridge() override;
    int apiVersion() const { return 1; }
    QString transport() const { return QStringLiteral("in-process"); }
    int pendingRequests() const { return int(queue_.size()); }

    // Result is {accepted, requestId, error}; no implicit numeric conversions.
    Q_INVOKABLE QVariantMap enqueueRows(const QVariantList &rows);
    Q_INVOKABLE bool cancel(const QString &requestId);
    Q_INVOKABLE void cancelAll();
    bool servicesAvailable() const;
    int pendingOperations() const { return pendingOperations_; }
    bool runtimeAvailable() const { return runtimeState_ == QStringLiteral("ready"); }
    QString runtimeState() const { return runtimeState_; }
    QString ghostRuntimeState() const { return ghostRuntimeState_; }
    QString notificationRuntimeState() const { return notificationRuntimeState_; }
    QVariantMap startupTimings() const { return startupTimings_; }
    QVariantMap retentionDiagnostics() const { return retentionDiagnostics_; }
    QVariantMap privacyReport() const { return privacyReport_; }
    QObject* exportDownloader() const { return exportDownloader_; }
    Q_INVOKABLE QVariantMap invoke(const QString &requestJson);
    // Only binds an export-owned native QObject; never accepts EOF assertions.
    Q_INVOKABLE bool beginExportHistory(const QString &jobId, const QString &jobToken);
    Q_INVOKABLE bool bindExportHistory(QObject *view, const QString &jobId, const QString &jobToken);
    Q_INVOKABLE void releaseExportHistory(const QString &jobId, const QString &jobToken);

signals:
    void pendingRequestsChanged();
    void rowsReady(const QString &requestId, const QVariantList &rows);
    void completed(const QString &requestId);
    void cancelled(const QString &requestId);
    void pendingOperationsChanged();
    void operationFinished(const QString &requestId, const QString &responseJson);
    void runtimeStateChanged();
    void ghostRuntimeStateChanged();
    void notificationRuntimeStateChanged();
    void startupTimingsChanged();
    void retentionDiagnosticsChanged();
    void privacyReportChanged();

private:
#ifdef VALMAX_BRIDGE_TESTING
    friend class NativeBridgePrivacyTest;
    friend class NativeBridgeDeliveryTest;
#endif
    struct Request {
        QString id;
        QVariantList rows;
        qsizetype offset = 0;
    };
    void drain();
    void schedule();
    QQueue<Request> queue_;
    QTimer timer_;
    quint64 nextId_ = 0;
    bool publishing_ = false;
    QThreadPool workers_;
    int pendingOperations_ = 0;
    struct RuntimeState;
    std::shared_ptr<RuntimeState> runtime_;
    QObject* exportDownloader_ = nullptr;
    QString runtimeState_ = QStringLiteral("unconfigured");
    QTimer ghostTimer_;
    QString ghostRuntimeState_ = QStringLiteral("unavailable");
    void refreshGhost();
    QTimer notificationTimer_;
    QString notificationRuntimeState_ = QStringLiteral("unavailable");
    QVariantMap startupTimings_;
    QVariantMap retentionDiagnostics_;
    void refreshNotifications();
#if VALMAX_HAS_RETENTION
    QTimer retentionTimer_;
    void refreshRetention();
#endif
    struct PrivacyState;
    std::shared_ptr<PrivacyState> privacy_;
    QTimer privacyTimer_;
    QVariantMap privacyReport_;
    void refreshPrivacy();
};
} // namespace valmax
