#pragma once
#include "result_cache.h"
#include "transport.h"
#include <QObject>
#include <functional>
#include <memory>

namespace valmax::media {
enum class ControllerCode { Ok, InvalidInput, Unavailable, SearchTooFast,
    ResultExpired, DownloadBusy, StaleScope, Cancelled, Capacity, Remote };
struct ControllerError { ControllerCode code = ControllerCode::Ok; Error remote; };
struct ControllerScope { quint64 token = 0; };
struct ControllerStamp { ControllerScope scope; quint64 generation = 0; };
template<class T> struct ControllerResult {
    std::optional<T> value;
    ControllerError error;
    // Populated on successful asynchronous search/media completion. Native
    // adapters MUST carry this stamp through decode/staging/UI delivery.
    ControllerStamp stamp;
    explicit operator bool()const noexcept{return value.has_value();}
};
struct PrivateMedia { Candidate candidate; QByteArray bytes; QString mime; };
using SearchRows = QVector<QJsonObject>;
using SearchCallback = std::function<void(quint64,ControllerResult<SearchRows>)>;
using MediaCallback = std::function<void(quint64,ControllerResult<PrivateMedia>)>;
struct ControllerClock {
    // Host/test only, thread-safe if shared elsewhere, finite nondecreasing
    // seconds <=2^40. Pure: must not reenter or destroy this object.
    std::function<double()> monotonicSeconds;
};
struct ControllerSnapshot { int scopes=0,operations=0; bool selectedBusy=false; };

// Native service only. No HTTP server, QML invokables, filesystem, decoding or
// publication of private bytes/URLs. All methods and callbacks run on the owner
// thread, a dedicated service QThread in production. Caller owns ONE shared
// scheduler/transport pair on that thread and drains this controller first.
// Callbacks are asynchronous and may reenter/delete the controller. Destruction
// cancels owned requests and suppresses callbacks. Dependency destruction yields
// asynchronous Unavailable once per accepted operation, never inline callbacks.
// Account/page IDs are host-only, never accepted in JSON supplied by the UI.
// An admitted new search advances the page's scheduler generation, cancelling
// older search/preview/original work. Old visible result IDs remain usable until
// TTL/eviction, but old asynchronous callbacks cannot publish over new results.
// decode/staging/UI adapters MUST recheck the completion STAMP after their own
// asynchronous work (page-current alone is insufficient after a newer search);
// returned copies cannot be physically recalled. Move-to-thread while active
// and concurrent destruction are unsupported.
class MediaController final : public QObject {
public:
    static constexpr int MaxOperations=66;
    static constexpr int MaxScopes=32;
    static constexpr double SearchInterval=2.;
    explicit MediaController(ProviderScheduler* scheduler,MediaTransport* transport,
                             QObject* parent=nullptr,ControllerClock clock={});
    ~MediaController()override;
    ControllerResult<ControllerScope> open_page(qint64 accountId,quint64 pageId);
    bool close_page(ControllerScope scope);
    bool current(ControllerScope scope)const;
    bool current(ControllerStamp stamp)const;
    ControllerResult<ControllerStamp> capture(ControllerScope scope)const;
    ControllerResult<quint64> search(ControllerScope scope,const QByteArray& exactJson,SearchCallback callback);
    ControllerResult<quint64> select(ControllerScope scope,const QByteArray& exactJson,MediaCallback callback);
    // Native decoder/provider pipeline only. No raw result is exposed to QML.
    ControllerResult<quint64> preview(ControllerScope scope,const QString& opaqueId,MediaCallback callback);
    bool cancel(quint64 ticket);
    bool shutdown();
    ControllerResult<ControllerSnapshot> snapshot()const;
private:
    struct Impl;
    std::unique_ptr<Impl> d_;
    bool on_owner_thread()const noexcept;
    void completed(quint64 ticket,FetchResult result);
    void dependencies_lost();
    ControllerResult<quint64> media(ControllerScope,const QString&,RequestClass,MediaCallback);
};
} // namespace valmax::media
