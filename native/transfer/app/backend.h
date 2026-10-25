#pragma once
#include "../host_callbacks.h"
#include <QByteArray>
#include <QList>
#include <QString>
#include <QUrl>
#include <memory>

namespace valmax::transfer::app {
struct Account { QString id, name, shortName; };
struct PreviewReply {
    bool ok = false;
    QString error, fingerprint;
    QList<Account> accounts;
};
struct ImportReply {
    bool ok = false;
    QString error, result;
    int profiles = 0;
};
struct RecoveryStatusReply {
    bool ok = false, required = false;
    QString error, fingerprint;
};
struct RecoveryReply {
    bool ok = false;
    QString error;
    int files = 0;
};
// Worker-only host interface. No QObject/QML callbacks, logging or discovery.
// A production instance calls only the linked, pinned Rust transfer C ABI.
class Backend {
public:
    virtual ~Backend() = default;
    virtual PreviewReply inspect(const QUrl&, const QByteArray& password) = 0;
    virtual ImportReply importLogin(const QString& explicitLocal, const QUrl&,
        const QByteArray& password, const QString& fingerprint, NativeTransferHost&) = 0;
    virtual RecoveryStatusReply recoveryStatus(const QString& explicitLocal) = 0;
    virtual RecoveryReply recover(const QString& explicitLocal, const QByteArray& password,
        const QString& fingerprint, NativeTransferHost&) = 0;
};
std::shared_ptr<Backend> makeProductionBackend();
void erase(QByteArray&) noexcept;
// Production helper uses this synchronously during Controller construction,
// after the main entrypoint's context guard and before worker/GUI callbacks.
// Not an independent desktop-context authorization API; tests supply own roots.
QString nativeLocalDirectory(); // SHGetKnownFolderPath, never QML/argv/env input.
}
