#pragma once
#include <QObject>
#include <QPointer>
#include <QVariantMap>
#include <QList>
#include <memory>
class QThread;
namespace valmax {
// Owns only a network task. File creation/writes stay in an authenticated Rust
// export sink; QML never grants this object an arbitrary filesystem pathname.
class ExportDownloader final : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool available READ available NOTIFY stateChanged)
    Q_PROPERTY(bool busy READ busy NOTIFY stateChanged)
public:
    explicit ExportDownloader(QObject* parent = nullptr);
    ~ExportDownloader() override;
    void setRuntime(void* runtime, std::shared_ptr<void> keepAlive);
    bool available() const { return runtime_ != nullptr; }
    bool busy() const { return !active_.isEmpty(); }
    Q_INVOKABLE QVariantMap start(const QString& job, const QString& url);
    Q_INVOKABLE bool cancel(const QString& requestId);
#ifdef VALMAX_EXPORT_DOWNLOAD_TESTING
    void allowLoopbackForTest() { testLoopback_ = true; }
#endif
signals:
    void completed(const QString& requestId, bool success, const QString& error);
    void stateChanged();
    void cancelWorker(const QString& requestId);
private:
    void* runtime_ = nullptr;
    std::shared_ptr<void> keepAlive_;
    QString active_;
    QList<QThread*> threads_;
    bool testLoopback_ = false;
};
}
