#include "composer_frontend.h"
#include <QCoreApplication>
#include <QJsonDocument>
#include <QThread>
#include <limits>
#include <utility>

namespace valmax::media {
namespace {
quint64 next_session(quint64 value) {
    return !value || value==std::numeric_limits<quint64>::max()?0:value+1;
}
QString error_code(const ServiceError& e) {
    switch(e.code) {
    case ServiceCode::InvalidInput: return QStringLiteral("invalid_input");
    case ServiceCode::DownloadBusy: return QStringLiteral("download_busy");
    case ServiceCode::Capacity: return QStringLiteral("busy");
    case ServiceCode::Cancelled: return QStringLiteral("cancelled");
    case ServiceCode::Stale: return QStringLiteral("result_expired");
    case ServiceCode::Storage: return QString::fromLatin1(staging_code(e.storage));
    case ServiceCode::Decode: return code_name(e.media.code);
    case ServiceCode::Controller:
        switch(e.controller.code) {
        case ControllerCode::InvalidInput: return QStringLiteral("invalid_input");
        case ControllerCode::SearchTooFast: return QStringLiteral("search_too_fast");
        case ControllerCode::ResultExpired:
        case ControllerCode::StaleScope: return QStringLiteral("result_expired");
        case ControllerCode::DownloadBusy: return QStringLiteral("download_busy");
        case ControllerCode::Remote: return code_name(e.controller.remote.code);
        case ControllerCode::Cancelled: return QStringLiteral("cancelled");
        default: return QStringLiteral("unavailable");
        }
    default: return QStringLiteral("unavailable");
    }
}
}

MediaComposerFrontend::MediaComposerFrontend(MediaEngineContext* context, MediaServiceHost* service, QObject* parent)
    : QObject(parent), context_(context), service_(service) {
    if(!owner() || (context && context->thread()!=thread()) || (service && service->thread()!=thread())) {
        context_.clear(); service_.clear(); return;
    }
    if(context) {
        connect(context,&MediaEngineContext::invalidated,this,[this] {
            if(!page_.token && !lease_.serial()) return;
            clear_session(false);
            emit changed();
        });
        connect(context,&QObject::destroyed,this,[this] { unbind(); });
    }
    if(service) connect(service,&QObject::destroyed,this,[this] { unbind(); });
}
MediaComposerFrontend::~MediaComposerFrontend() {
    for(const auto& connection:hostConnections_) disconnect(connection);
    if(context_) disconnect(context_,nullptr,this,nullptr);
    if(service_) disconnect(service_,nullptr,this,nullptr);
    const auto context=context_; const auto lease=lease_;
    clear_session(false);
    // Do not emit context.invalidated synchronously from a derived destructor:
    // an external handler could attempt to delete this frontend a second time.
    if(context && lease.serial()) QMetaObject::invokeMethod(context,[context,lease] {
        if(context) context->detach(lease);
    },Qt::QueuedConnection);
}
bool MediaComposerFrontend::owner() const noexcept {
    const auto app=QCoreApplication::instance();
    return app && QThread::currentThread()==app->thread() && thread()==app->thread();
}
bool MediaComposerFrontend::available() const {
    return owner() && context_ && service_ && host_.accountId>0 && host_.pageId
        && host_.composer && host_.sender && host_.upload && host_.model && bool(host_.stillCurrent);
}
bool MediaComposerFrontend::host_current() {
    if(!available()) return false;
    // Copy callback first: it may release its own containing binding.
    const auto callback=host_.stillCurrent;
    const auto session=session_;
    QPointer<MediaComposerFrontend> alive(this);
    bool current=false;
    try { current=callback(); } catch(...) { return false; }
    return alive && current && alive->session_==session && alive->available();
}
bool MediaComposerFrontend::bind(HostComposerBinding binding) {
    if(!owner() || binding.accountId<=0 || !binding.pageId || !binding.stillCurrent
        || !binding.composer || !binding.sender || !binding.upload || !binding.model) return false;
    for(const auto object:{binding.composer,binding.sender,binding.upload,binding.model})
        if(object->thread()!=thread()) return false;
    QPointer<MediaComposerFrontend> alive(this);
    const auto expected=next_session(session_);
    unbind();
    if(!alive || session_!=expected || !expected || !context_ || !service_ || !binding.composer || !binding.sender
        || !binding.upload || !binding.model) return false;
    host_=std::move(binding);
    for(const auto object:{host_.composer,host_.sender,host_.upload,host_.model})
        hostConnections_.push_back(connect(object,&QObject::destroyed,this,[this] { unbind(); }));
    const auto current=host_current();
    if(!alive || session_!=expected) return false;
    if(!current) { unbind(); return false; }
    emit changed();
    return alive && alive->session_==expected && alive->available();
}
void MediaComposerFrontend::clear_session(bool detach) {
    // Clear own identity before calling either dependency: context invalidation
    // and host callbacks may synchronously reenter this object.
    const auto page=page_; const auto lease=lease_;
    page_={}; lease_={}; generation_={};
    session_=next_session(session_);
    rows_.clear(); busy_=false; error_.clear(); retry_=0;
    const auto service=service_; const auto context=context_;
    if(service && page.token) service->close(page);
    // Last operation: signal delivery may delete this frontend.
    if(detach && context && lease.serial()) context->detach(lease);
}
void MediaComposerFrontend::unbind() {
    if(!owner()) return;
    for(const auto& connection:hostConnections_) disconnect(connection);
    hostConnections_.clear(); host_={};
    QPointer<MediaComposerFrontend> alive(this);
    clear_session(true);
    if(alive) emit changed();
}
void MediaComposerFrontend::reset() {
    if(!owner()) return;
    QPointer<MediaComposerFrontend> alive(this);
    clear_session(true);
    if(alive) emit changed();
}
void MediaComposerFrontend::fail(ServiceError error) {
    busy_=false; error_=error_code(error);
    retry_=error.code==ServiceCode::Controller ? error.controller.remote.retryAfterSeconds.value_or(0)
                                               : error.media.retryAfterSeconds.value_or(0);
    emit changed();
}
bool MediaComposerFrontend::result_current(quint64 session,const EngineMediaLease& lease,const SearchGeneration& generation) {
    if(session_!=session || !session || !context_ || !context_->current(lease)
        || !context_->current(lease_) || !generation.valid() || generation.current!=generation_.current
        || generation.captured!=generation_.captured) return false;
    return host_current();
}
bool MediaComposerFrontend::search(const QString& mode,const QString& query) {
    if(!owner() || busy_) return false;
    // Bound work before constructing JSON; the protocol applies the tighter
    // 160-codepoint trimmed-query and encoded-byte limits afterwards.
    if(mode.size()!=3 || query.size()>8192) { fail({ServiceCode::InvalidInput}); return false; }
    const auto payload=QJsonDocument(QJsonObject{{QStringLiteral("mode"),mode},{QStringLiteral("query"),query}}).toJson(QJsonDocument::Compact);
    if(!parse_search_request(payload)) { fail({ServiceCode::InvalidInput}); return false; }
    QPointer<MediaComposerFrontend> alive(this);
    if(!host_current() || !alive) return false;
    const auto session=next_session(session_);
    clear_session(true);
    if(!alive || !session || session_!=session || !available()) return false;
    const auto bound=context_->bind(host_.accountId,host_.pageId);
    if(!alive || session_!=session || !bound.valid() || !available()) return false;
    lease_=bound;
    const auto page=service_->bind(lease_);
    if(!page) { fail(page.error); return false; }
    page_=*page.value;
    const auto admission=service_->search(page_,payload,[alive,session](quint64,ServiceResult<SearchRows> result) {
        if(alive) alive->search_done(session,std::move(result));
    });
    if(!admission) { fail(admission.error); return false; }
    generation_=admission.generation; busy_=true; error_.clear(); retry_=0;
    emit changed();
    return true;
}
void MediaComposerFrontend::search_done(quint64 session,ServiceResult<SearchRows> result) {
    QPointer<MediaComposerFrontend> alive(this);
    if(!result_current(session,result.lease,result.generation) || !alive) {
        if(alive && session_==session) reset();
        return;
    }
    if(!result) { fail(result.error); return; }
    if(result.value->size()>ResultLimit) { fail({ServiceCode::Unavailable}); return; }
    rows_.clear(); busy_=false; error_.clear(); retry_=0;
    QVector<QString> previews;
    for(const auto& row:*result.value) {
        auto visible=row.toVariantMap();
        visible.insert(QStringLiteral("previewSource"),QString());
        rows_.append(visible);
        if(row.value(QStringLiteral("preview")).toBool()) previews.append(row.value(QStringLiteral("id")).toString());
    }
    emit changed();
    if(!alive || session_!=session) return;
    for(const auto& id:previews) {
        if(!service_ || !context_ || !context_->current(lease_) || !generation_.valid()) break;
        service_->preview(page_,id,[alive,session,id](quint64,ServiceResult<QImage> image) {
            if(alive) alive->preview_done(session,id,std::move(image));
        });
    }
}
void MediaComposerFrontend::preview_done(quint64 session,QString id,ServiceResult<QImage> result) {
    if(!result) return;
    QPointer<MediaComposerFrontend> alive(this);
    if(!result_current(session,result.lease,result.generation) || !alive) return;
    for(auto& item:rows_) {
        auto row=item.toMap();
        if(row.value(QStringLiteral("id")).toString()!=id) continue;
        const auto url=context_->publish(result.lease,*result.value);
        if(!url) return;
        row.insert(QStringLiteral("previewSource"),*url.value); item=row;
        emit changed(); return;
    }
}
bool MediaComposerFrontend::select(const QString& id) {
    if(!owner() || busy_ || !page_.token || !generation_.valid()) return false;
    bool listed=false;
    for(const auto& row:rows_) if(row.toMap().value(QStringLiteral("id")).toString()==id) listed=true;
    if(!listed) return false;
    QPointer<MediaComposerFrontend> alive(this);
    if(!host_current() || !alive) return false;
    const auto session=session_; const auto service=service_;
    const auto payload=QJsonDocument(QJsonObject{{QStringLiteral("id"),id}}).toJson(QJsonDocument::Compact);
    const auto admission=service_->select(page_,payload,[alive,service,session](quint64 ticket,ServiceResult<RetainedMediaFile> result) {
        if(alive) alive->selected_done(session,ticket,std::move(result));
        else if(service) service->complete_selected(ticket);
    });
    if(!admission) { fail(admission.error); return false; }
    busy_=true; error_.clear(); retry_=0; emit changed(); return true;
}
void MediaComposerFrontend::selected_done(quint64 session,quint64 ticket,ServiceResult<RetainedMediaFile> result) {
    // The gate is always released, including reentrant frontend destruction.
    const auto service=service_;
    struct Terminal { QPointer<MediaServiceHost> service; quint64 ticket;
        ~Terminal(){if(service)service->complete_selected(ticket);} } terminal{service,ticket};
    QPointer<MediaComposerFrontend> alive(this);
    if(!result_current(session,result.lease,result.generation) || !alive) {
        if(alive && session_==session) reset();
        return;
    }
    if(!result) { fail(result.error); return; }
    const auto binding=DraftBinding::create(context_,result.lease,host_.sender,host_.upload,host_.model,
                                           result.generation,host_.stillCurrent);
    if(!binding) { fail({ServiceCode::Unavailable}); return; }
    const auto& original=*result.value;
    const auto url=QUrl::fromEncoded(QByteArray::fromStdString(original.fileUrl),QUrl::StrictMode);
    const auto dispatched=dispatch_retained(*binding,{url,result.lease,result.generation});
    if(!alive) return;
    // An upload/count getter can synchronously switch the chat or even start a
    // newer search. Finish this ticket without touching that newer UI state.
    if(service) service->complete_selected(ticket);
    terminal.service.clear();
    if(!result_current(session,result.lease,result.generation) || !alive) {
        if(alive && session_==session) reset();
        return;
    }
    if(dispatched!=DraftDispatchResult::Dispatched) {
        busy_=false;
        error_=dispatched==DraftDispatchResult::Limit?QStringLiteral("attachment_limit"):
               dispatched==DraftDispatchResult::Stale?QStringLiteral("result_expired"):QStringLiteral("unavailable");
        emit changed(); return;
    }
    // Clear terminal gate before any user signal can start another selection.
    busy_=false; rows_.clear(); error_.clear(); retry_=0;
    emit changed();
    if(!alive || !result_current(session,result.lease,result.generation) || !alive) return;
    emit draftDispatched(original.caption);
}
} // namespace valmax::media
