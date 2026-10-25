#pragma once
#include <QObject>
#include <QString>
#include <QThreadPool>
#include <functional>

namespace valmax {
// Explicit GUI handoff only. No profile, credentials, file or password inputs.
// A successful launcher exit acknowledges helper creation, never an import.
class TransferLaunch final : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool available READ available CONSTANT)
    Q_PROPERTY(bool busy READ busy NOTIFY stateChanged)
    Q_PROPERTY(QString state READ state NOTIFY stateChanged)
    Q_PROPERTY(QString error READ error NOTIFY stateChanged)
public:
    explicit TransferLaunch(QObject* parent = nullptr);
    ~TransferLaunch() override;
#ifdef VALMAX_TRANSFER_LAUNCH_TESTING
    TransferLaunch(const QString& trustedApplicationImage, int timeoutMs, std::function<void()> quit, QObject* parent = nullptr);
#endif
    bool available() const { return !launcher_.isEmpty(); }
    bool busy() const { return busy_; }
    QString state() const { return state_; }
    QString error() const { return error_; }
    // Primitive launch does not authorize closing this client.
    Q_INVOKABLE bool startImport();
    // Explicit confirmation owns shutdown intent independently of its page.
    Q_INVOKABLE bool confirmAndStartImport();
    Q_INVOKABLE bool completeHandoff();
signals:
    void stateChanged();
    void handoffFinished(bool helperLaunched, const QString& error);
private:
    static QString launcherForImage(const QString& image);
    bool begin(bool quitAfterSuccess);
    QString launcher_;
    int timeoutMs_ = 25000;
    QThreadPool workers_;
    bool busy_ = false;
    QString state_ = QStringLiteral("idle");
    QString error_;
    bool quitQueued_ = false;
    bool quitAfterSuccess_ = false;
    bool completing_ = false;
    std::function<void()> quit_;
};
}
