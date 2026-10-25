#pragma once
#include "controller.h"
#include "decode_queue.h"
#include "engine_context.h"
#include "service_generation.h"
#include "staging_client.h"
#include <QObject>
#include <functional>
#include <memory>
#include <string>

namespace valmax::media {
enum class ServiceCode { Ok, InvalidInput, Unavailable, Capacity, DownloadBusy,
    Cancelled, Stale, Controller, Decode, Storage };
struct ServiceError {
    ServiceCode code = ServiceCode::Ok;
    ControllerError controller;
    Error media;
    StagingCode storage = StagingCode::Ok;
};
struct ServicePage { quint64 token = 0; };
template<class T> struct ServiceResult {
    std::optional<T> value;
    ServiceError error;
    EngineMediaLease lease;
    ControllerStamp stamp;
    SearchGeneration generation;
    explicit operator bool() const noexcept { return value.has_value(); }
};
struct RetainedMediaFile {
    // Native-only; never register this DTO with QML or serialize/log the URL.
    std::string fileUrl;
    uint64_t bytes = 0;
    QString caption;
    QString mime;
};
using ServiceSearchCallback = std::function<void(quint64, ServiceResult<SearchRows>)>;
using ServicePreviewCallback = std::function<void(quint64, ServiceResult<QImage>)>;
using ServiceSelectedCallback = std::function<void(quint64, ServiceResult<RetainedMediaFile>)>;
struct ServiceConfig {
    StagingApi staging;
    std::wstring explicitStagingRoot;
    // Trusted synthetic seams; production leaves these empty/default. All
    // functions must outlive draining and obey the underlying module contracts.
    ReplyFactory replyFactory;
    ThumbnailDecoder decoder;
    SchedulerClock schedulerClock;
    TransportClock transportClock;
    ControllerClock controllerClock;
    std::function<uint64_t()> unixSeconds;
};
struct ServiceSnapshot {
    int pages = 0, operations = 0, previews = 0, pendingImages = 0;
    bool selectedBusy = false, stopping = false, drained = false;
};

// Process-wide native service, created/called/destructed on the application's
// GUI thread. No QML type, server or path input from UI. There is exactly one
// instance including its asynchronous drain. Each instance creates one service
// QThread and one filesystem QThread; DecodeQueue owns its two codec workers.
// Workers never retain or dereference a GUI QObject. They exchange native DTOs.
// Accepted operations receive at most one asynchronous typed callback; rejected
// admission receives none. Reentrant callbacks may cancel or delete this facade.
// Preview callbacks must publish or discard promptly: two decoded-image credits
// remain charged through GUI callback completion. Copies deliberately retained
// by trusted callback code are outside this queue's ownership bound.
// Selected success holds the global gate until complete_selected(ticket), even
// after download and retain. Frontend must terminal-ACK every selected delivery
// (including stale/suppressed native dispatch). Failure is terminal automatically.
// Shutdown is async. Destruction revokes delivery and leaves a self-owned run
// until replies/codecs/I/O stop; it never joins workers on the GUI thread. Keep
// QApplication/event loops and the verified staging DLL alive until drained,
// or, after facade destruction, until process_available() becomes true.
class MediaServiceHost final : public QObject {
    Q_OBJECT
public:
    static constexpr int MaxPages = 32, MaxOperations = 66;
    static constexpr int MaxPreviews = 8, MaxPendingImages = 2;
    static ServiceResult<MediaServiceHost*> create(ServiceConfig config, QObject* parent = nullptr);
    static bool process_available() noexcept;
    ~MediaServiceHost() override;
    ServiceResult<ServicePage> bind(EngineMediaLease lease);
    ServiceResult<quint64> search(ServicePage page, QByteArray exactJson, ServiceSearchCallback callback);
    ServiceResult<quint64> preview(ServicePage page, QString opaqueId, ServicePreviewCallback callback);
    ServiceResult<quint64> select(ServicePage page, QByteArray exactJson, ServiceSelectedCallback callback);
    bool cancel(quint64 ticket);
    bool complete_selected(quint64 ticket);
    bool close(ServicePage page);
    bool shutdown();
    ServiceResult<ServiceSnapshot> snapshot() const;
signals:
    void drained();
private:
    explicit MediaServiceHost(ServiceConfig config, QObject* parent);
    struct Impl;
    std::unique_ptr<Impl> d_;
    bool owner() const noexcept;
};
} // namespace valmax::media
