#pragma once
#include "draft_dispatcher.h"
#include "service_host.h"
#include <QPointer>
#include <QVariantList>
#include <functional>
#include <vector>

namespace valmax::media {
// Supplied by native host integration, never by a search/selection JSON request.
// stillCurrent runs only on the GUI thread and checks the actual native account,
// chat and sender identity. It may reenter/delete the frontend, so it is guarded.
struct HostComposerBinding {
    qint64 accountId = 0;
    quint64 pageId = 0;
    QPointer<QObject> composer, sender, upload, model;
    std::function<bool()> stillCurrent;
};

// GUI adapter. A native host creates and binds it, then exposes this instance to
// its own composer. QML can only search/select/reset; it cannot set account IDs,
// sender pointers or file paths. Rows contain public metadata and in-memory
// image-provider URLs. The retained original URL never leaves native C++.
class MediaComposerFrontend final : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariantList rows READ rows NOTIFY changed)
    Q_PROPERTY(bool busy READ busy NOTIFY changed)
    Q_PROPERTY(bool available READ available NOTIFY changed)
    Q_PROPERTY(QString errorCode READ errorCode NOTIFY changed)
    Q_PROPERTY(int retryAfterSeconds READ retryAfterSeconds NOTIFY changed)
public:
    MediaComposerFrontend(MediaEngineContext* context, MediaServiceHost* service,
                          QObject* parent = nullptr);
    ~MediaComposerFrontend() override;
    bool bind(HostComposerBinding binding); // Host-only; no Q_INVOKABLE.
    void unbind();
    QVariantList rows() const { return rows_; }
    bool busy() const { return busy_; }
    bool available() const;
    QString errorCode() const { return error_; }
    int retryAfterSeconds() const { return retry_; }
    Q_INVOKABLE bool search(const QString& mode, const QString& query);
    Q_INVOKABLE bool select(const QString& opaqueId);
    Q_INVOKABLE void reset();
signals:
    void changed();
    // After native dispatch only. This is public attribution text, not a URL to
    // the staged file or an attachment ACK. The host sets the current draft text.
    void draftDispatched(const QString& caption);
private:
    bool owner() const noexcept;
    bool host_current();
    bool result_current(quint64 session, const EngineMediaLease&, const SearchGeneration&);
    void clear_session(bool detach);
    void fail(ServiceError error);
    void search_done(quint64 session, ServiceResult<SearchRows> result);
    void preview_done(quint64 session, QString id, ServiceResult<QImage> result);
    void selected_done(quint64 session, quint64 ticket, ServiceResult<RetainedMediaFile> result);
    QPointer<MediaEngineContext> context_;
    QPointer<MediaServiceHost> service_;
    HostComposerBinding host_;
    std::vector<QMetaObject::Connection> hostConnections_;
    EngineMediaLease lease_;
    ServicePage page_;
    SearchGeneration generation_;
    quint64 session_ = 1;
    QVariantList rows_;
    bool busy_ = false;
    QString error_;
    int retry_ = 0;
};
} // namespace valmax::media
