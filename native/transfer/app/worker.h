#pragma once
#include "backend.h"
#include <QObject>
#include <QVariantMap>
#include <atomic>
#include <mutex>

namespace valmax::transfer::app {
struct Secret final {
    QByteArray bytes;
    explicit Secret(QByteArray&& value) : bytes(std::move(value)) {}
    ~Secret() { erase(bytes); }
    Secret(const Secret&) = delete;
    Secret& operator=(const Secret&) = delete;
};
struct Control final {
    std::atomic_bool cancelled{false};
    std::mutex mutex;
    NativeTransferHost* host = nullptr; // Access only while mutex held.
    void cancel() noexcept;
};
class Worker final : public QObject {
    Q_OBJECT
public:
    Worker(std::shared_ptr<Backend>, QString local);
    void inspect(quint64 generation, QUrl, std::shared_ptr<Secret>, std::shared_ptr<Control>);
    void importLogin(quint64 generation, std::shared_ptr<Control>);
    void recoveryStatus(quint64 generation, std::shared_ptr<Control>);
    void recover(quint64 generation, std::shared_ptr<Secret>, std::shared_ptr<Control>);
    void reset();
signals:
    void completed(quint64 generation, QVariantMap publicResult);
private:
    std::shared_ptr<Backend> backend_;
    QString local_;
    quint64 generation_ = 0;
    QUrl file_;
    QString fingerprint_;
    std::shared_ptr<Secret> secret_;
};
}
