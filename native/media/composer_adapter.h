#pragma once
#include "composer_binding.h"
#include "composer_frontend.h"
#include <QQmlParserStatus>
#include <memory>
#include <vector>

namespace valmax::media {
// GUI-thread, host-only configuration. The engine may have QML adapters before
// this call: its private holder notifies them when the one service becomes ready.
// Same service is idempotent; replacement after a successful configuration is
// rejected, including after destruction. No discovery, filesystem or QML setter.
bool configure_media_composer_engine(QQmlEngine* engine, MediaServiceHost* service);

// Register as MediaComposer in the native QML module. `composer` must be the
// actual pinned host send panel; numeric IDs and local URLs are never QML input.
// The host resets on account/chat/current-panel changes. Independently, every
// publication and final upload re-resolves the native identity without double
// conversion. This adapter does not create a service, sender or presenter.
class MediaComposerAdapter : public QObject, public QQmlParserStatus {
    Q_OBJECT
    Q_INTERFACES(QQmlParserStatus)
    Q_PROPERTY(QObject* composer READ composer WRITE setComposer NOTIFY composerChanged)
    Q_PROPERTY(QVariantList rows READ rows NOTIFY changed)
    Q_PROPERTY(bool busy READ busy NOTIFY changed)
    Q_PROPERTY(bool available READ available NOTIFY changed)
    Q_PROPERTY(QString errorCode READ errorCode NOTIFY changed)
    Q_PROPERTY(int retryAfterSeconds READ retryAfterSeconds NOTIFY changed)
public:
    explicit MediaComposerAdapter(QObject* parent = nullptr);
    ~MediaComposerAdapter() override;
    void classBegin() override {}
    void componentComplete() override;
    QObject* composer() const { return composer_.data(); }
    void setComposer(QObject* composer);
    QVariantList rows() const;
    bool busy() const;
    bool available() const;
    QString errorCode() const;
    int retryAfterSeconds() const;
    Q_INVOKABLE bool search(const QString& mode, const QString& query);
    Q_INVOKABLE bool select(const QString& opaqueId);
    Q_INVOKABLE void reset();
signals:
    void composerChanged();
    void changed();
    void draftDispatched(const QString& caption);
private slots:
    void hostPropertyChanged();
private:
    struct Capability { bool valid = true; };
    bool owner() const noexcept;
    bool refreshBinding();
    bool bindingCurrent() const;
    void invalidateBinding();
    void serviceReady();
    void watch(const ResolvedComposer& binding);
    QPointer<QQmlEngine> engine_;
    QPointer<QObject> composer_, holder_;
    QPointer<MediaComposerFrontend> frontend_;
    std::optional<ResolvedComposer> resolved_;
    std::shared_ptr<Capability> capability_;
    std::vector<QMetaObject::Connection> connections_;
    QMetaObject::Connection composerDestroyed_;
    quint64 revision_ = 1;
    bool complete_ = false, refreshing_ = false, destroying_ = false;
};
} // namespace valmax::media
