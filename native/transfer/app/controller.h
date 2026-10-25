#pragma once
#include "worker.h"
#include <QObject>
#include <QThread>
#include <QVariantList>

namespace valmax::transfer::app {
enum class Mode { Import, Recovery };
class Controller final : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString fileName READ fileName NOTIFY changed)
    Q_PROPERTY(bool available READ available NOTIFY changed)
    Q_PROPERTY(bool busy READ busy NOTIFY changed)
    Q_PROPERTY(bool previewReady READ previewReady NOTIFY changed)
    Q_PROPERTY(bool cancelRequested READ cancelRequested NOTIFY changed)
    Q_PROPERTY(bool succeeded READ succeeded NOTIFY changed)
    Q_PROPERTY(QString status READ status NOTIFY changed)
    Q_PROPERTY(QString errorCode READ errorCode NOTIFY changed)
    Q_PROPERTY(QVariantList accounts READ accounts NOTIFY changed)
    Q_PROPERTY(bool recoveryMode READ recoveryMode CONSTANT)
    Q_PROPERTY(bool recoveryReady READ recoveryReady NOTIFY changed)
public:
    explicit Controller(QObject* parent = nullptr);
    explicit Controller(Mode, QObject* parent = nullptr);
    ~Controller() override;
#ifdef VALMAX_TRANSFER_APP_TESTING
    Controller(std::shared_ptr<Backend>, QString trustedLocal, QObject* parent = nullptr);
    Controller(std::shared_ptr<Backend>, QString trustedLocal, Mode, QObject* parent = nullptr);
    Worker* testingWorker() const { return worker_; } // Native test only, never QML.
#endif
    QString fileName() const;
    bool available() const { return available_; }
    bool busy() const { return busy_; }
    bool previewReady() const { return previewReady_; }
    bool cancelRequested() const { return cancelRequested_; }
    bool succeeded() const { return succeeded_; }
    QString status() const { return status_; }
    QString errorCode() const { return error_; }
    QVariantList accounts() const { return accounts_; }
    bool recoveryMode() const { return mode_ == Mode::Recovery; }
    bool recoveryReady() const { return recoveryReady_; }
    Q_INVOKABLE void chooseFile(const QUrl&);
    Q_INVOKABLE void inputsChanged();
    Q_INVOKABLE void preview(const QString& password);
    Q_INVOKABLE void confirmImport();
    Q_INVOKABLE void refreshRecovery();
    Q_INVOKABLE void confirmRecovery(const QString& password);
    Q_INVOKABLE void cancel();
    Q_INVOKABLE bool requestClose();
signals:
    void changed();
    void clearPassword();
    void readyToClose();
private:
    struct Private {};
    Controller(std::shared_ptr<Backend>, QString, Mode, Private, QObject*);
    void invalidate();
    void publish(bool clearPassword = false);
    void finish(quint64, const QVariantMap&);
    bool nextGeneration();
    QThread thread_;
    Worker* worker_ = nullptr;
    std::shared_ptr<Control> control_;
    QUrl file_;
    const Mode mode_;
    quint64 generation_ = 1, operation_ = 0;
    bool available_ = false, busy_ = false, previewReady_ = false, importing_ = false;
    bool recoveryReady_ = false;
    bool cancelRequested_ = false, succeeded_ = false, closeRequested_ = false, destroying_ = false;
    QString status_, error_;
    QVariantList accounts_;
};
QString errorMessage(const QString& code);
}
