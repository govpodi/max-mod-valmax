#include "controller.h"
#include <QElapsedTimer>
#include <QPointer>
#include <QThread>
#include <cmath>
#include <limits>
#include <map>
#include <utility>

namespace valmax::media {
namespace {
template<class T>ControllerResult<T> success(T v){return {std::move(v),{}};}
template<class T>ControllerResult<T> failure(ControllerCode code,Error remote={}){return {{},{code,remote}};}
ControllerCode cache_error(CacheCode code){
    if(code==CacheCode::InvalidInput)return ControllerCode::InvalidInput;
    if(code==CacheCode::StaleScope)return ControllerCode::StaleScope;
    if(code==CacheCode::ResultExpired)return ControllerCode::ResultExpired;
    if(code==CacheCode::Capacity)return ControllerCode::Capacity;
    return ControllerCode::Unavailable;
}
}
struct MediaController::Impl {
    struct Page {qint64 account;quint64 page;MediaScope scheduler;CacheScope cache;};
    struct Operation {quint64 scope=0,generation=0,transport=0;RequestClass kind=RequestClass::Api;Mode mode=Mode::Picture;QString resultId;bool cancelled=false;SearchCallback search;MediaCallback media;};
    QPointer<ProviderScheduler> scheduler;
    QPointer<MediaTransport> transport;
    ControllerClock clock;
    QElapsedTimer elapsed;
    ResultCache cache;
    std::map<quint64,Page> pages;
    std::map<quint64,Operation> operations;
    quint64 nextTicket=1;
    double lastNow=0,lastSearch=-std::numeric_limits<double>::infinity();
    bool accepting=true,available=false,selected=false,fallbackQueued=false;
    Impl(ProviderScheduler* s,MediaTransport* t,ControllerClock c):scheduler(s),transport(t),clock(std::move(c)){elapsed.start();}
    std::optional<double> now(){
        double value=0;
        try{value=clock.monotonicSeconds?clock.monotonicSeconds():double(elapsed.nsecsElapsed())/1e9;}catch(...){return {};}
        if(!std::isfinite(value)||value<lastNow||value>double(quint64(1)<<40))return {};
        lastNow=value;return value;
    }
    bool ready()const{return accepting&&available&&scheduler&&transport&&scheduler->thread()==QThread::currentThread()&&transport->thread()==QThread::currentThread();}
};
MediaController::MediaController(ProviderScheduler* scheduler,MediaTransport* transport,QObject* parent,ControllerClock clock)
    :QObject(parent),d_(std::make_unique<Impl>(scheduler,transport,std::move(clock))){
    d_->available=scheduler&&transport&&scheduler->thread()==thread()&&transport->thread()==thread();
    if(scheduler)connect(scheduler,&QObject::destroyed,this,[this]{dependencies_lost();});
    if(transport)connect(transport,&QObject::destroyed,this,[this]{dependencies_lost();});
}
MediaController::~MediaController(){
    if(d_->scheduler)disconnect(d_->scheduler,nullptr,this,nullptr);
    if(d_->transport)disconnect(d_->transport,nullptr,this,nullptr);
    d_->accepting=false;
    if(d_->transport)for(const auto& [id,op]:d_->operations){Q_UNUSED(id);d_->transport->cancel(op.transport);}
    if(d_->scheduler)for(const auto& [id,page]:d_->pages){Q_UNUSED(id);d_->scheduler->close_lane(page.scheduler);}
    d_->operations.clear();d_->pages.clear();d_->cache.clear();
}
bool MediaController::on_owner_thread()const noexcept{return QThread::currentThread()==thread();}
ControllerResult<ControllerScope> MediaController::open_page(qint64 account,quint64 page){
    if(!on_owner_thread()||account<=0||page==0)return failure<ControllerScope>(ControllerCode::InvalidInput);
    if(!d_->ready())return failure<ControllerScope>(ControllerCode::Unavailable);
    quint64 old=0;for(const auto& [token,p]:d_->pages)if(p.account==account&&p.page==page){old=token;break;}
    if(old)close_page({old});
    if(d_->pages.size()>=MaxScopes)return failure<ControllerScope>(ControllerCode::Capacity);
    const auto lane=d_->scheduler->open_lane();if(!lane)return failure<ControllerScope>(ControllerCode::Unavailable,lane.error);
    const auto scope=d_->cache.open_scope(account,page);
    if(!scope){d_->scheduler->close_lane(*lane.value);return failure<ControllerScope>(cache_error(scope.error));}
    try{d_->pages.emplace(scope.value->token,Impl::Page{account,page,*lane.value,*scope.value});}
    catch(...){d_->cache.close_scope(*scope.value);d_->scheduler->close_lane(*lane.value);return failure<ControllerScope>(ControllerCode::Unavailable);}
    return success(ControllerScope{scope.value->token});
}
bool MediaController::close_page(ControllerScope scope){
    if(!on_owner_thread())return false;
    auto it=d_->pages.find(scope.token);if(it==d_->pages.end())return false;
    const auto page=it->second;d_->pages.erase(it);d_->cache.close_scope(page.cache);
    if(d_->scheduler)d_->scheduler->close_lane(page.scheduler);
    if(d_->transport)for(const auto& [id,op]:d_->operations){Q_UNUSED(id);if(op.scope==scope.token)d_->transport->cancel(op.transport);}
    return true;
}
bool MediaController::current(ControllerScope scope)const{return on_owner_thread()&&d_->ready()&&d_->pages.contains(scope.token)&&d_->cache.current({scope.token});}
bool MediaController::current(ControllerStamp stamp)const{return current(stamp.scope)&&stamp.generation!=0&&d_->pages.at(stamp.scope.token).scheduler.generation==stamp.generation;}
ControllerResult<ControllerStamp> MediaController::capture(ControllerScope scope)const{
    if(!on_owner_thread())return failure<ControllerStamp>(ControllerCode::InvalidInput);
    if(!current(scope))return failure<ControllerStamp>(ControllerCode::StaleScope);
    return success(ControllerStamp{scope,d_->pages.at(scope.token).scheduler.generation});
}
ControllerResult<quint64> MediaController::search(ControllerScope scope,const QByteArray& json,SearchCallback callback){
    if(!on_owner_thread()||!callback)return failure<quint64>(ControllerCode::InvalidInput);
    if(!d_->ready())return failure<quint64>(ControllerCode::Unavailable);
    const auto page=d_->pages.find(scope.token);if(page==d_->pages.end())return failure<quint64>(ControllerCode::StaleScope);
    const auto request=parse_search_request(json);if(!request)return failure<quint64>(ControllerCode::InvalidInput,request.error);
    const auto now=d_->now();if(!now)return failure<quint64>(ControllerCode::Unavailable);
    if(*now-d_->lastSearch<SearchInterval)return failure<quint64>(ControllerCode::SearchTooFast);
    if(d_->operations.size()>=MaxOperations||d_->nextTicket==std::numeric_limits<quint64>::max())return failure<quint64>(ControllerCode::Capacity);
    // Each valid new search revokes prior work for this page, including queued
    // completion callbacks. Previously shown IDs can remain in the TTL cache,
    // but a slow older response must never replace the newer results.
    const auto advanced=d_->scheduler->advance(page->second.scheduler);
    if(!advanced)return failure<quint64>(ControllerCode::Unavailable,advanced.error);
    page->second.scheduler=*advanced.value;
    const auto id=d_->nextTicket++;
    Impl::Operation op;op.scope=scope.token;op.generation=page->second.scheduler.generation;op.kind=RequestClass::Api;op.mode=request.value->mode;op.search=std::move(callback);
    try{d_->operations.emplace(id,std::move(op));}catch(...){return failure<quint64>(ControllerCode::Unavailable);}
    d_->lastSearch=*now;
    QPointer<MediaController> self(this);
    const auto submitted=d_->transport->submit(page->second.scheduler,{RequestClass::Api,request.value->apiUrl,{}},[self,id](quint64,FetchResult result){if(self)self->completed(id,std::move(result));});
    if(!submitted){d_->operations.erase(id);return failure<quint64>(ControllerCode::Remote,submitted.error);}
    d_->operations.at(id).transport=*submitted.value;return success(id);
}
ControllerResult<quint64> MediaController::select(ControllerScope scope,const QByteArray& json,MediaCallback callback){
    if(!on_owner_thread()||!callback)return failure<quint64>(ControllerCode::InvalidInput);
    const auto id=parse_selection_request(json);if(!id)return failure<quint64>(ControllerCode::InvalidInput,id.error);
    return media(scope,*id.value,RequestClass::Original,std::move(callback));
}
ControllerResult<quint64> MediaController::preview(ControllerScope scope,const QString& id,MediaCallback callback){return media(scope,id,RequestClass::Preview,std::move(callback));}
ControllerResult<quint64> MediaController::media(ControllerScope scope,const QString& resultId,RequestClass kind,MediaCallback callback){
    if(!on_owner_thread()||!callback)return failure<quint64>(ControllerCode::InvalidInput);
    if(!d_->ready())return failure<quint64>(ControllerCode::Unavailable);
    const auto page=d_->pages.find(scope.token);if(page==d_->pages.end())return failure<quint64>(ControllerCode::StaleScope);
    const auto now=d_->now();if(!now)return failure<quint64>(ControllerCode::Unavailable);
    const auto candidate=d_->cache.resolve(page->second.cache,resultId,*now);if(!candidate)return failure<quint64>(cache_error(candidate.error));
    if(kind==RequestClass::Preview&&candidate.value->previewUrl.isEmpty())return failure<quint64>(ControllerCode::ResultExpired);
    if(kind==RequestClass::Original&&d_->selected)return failure<quint64>(ControllerCode::DownloadBusy);
    if(d_->operations.size()>=MaxOperations||d_->nextTicket==std::numeric_limits<quint64>::max())return failure<quint64>(ControllerCode::Capacity);
    FetchSpec spec{kind,kind==RequestClass::Original?candidate.value->originalUrl:candidate.value->previewUrl,kind==RequestClass::Original?std::optional(candidate.value->metadata):std::nullopt};
    const auto id=d_->nextTicket++;Impl::Operation op;op.scope=scope.token;op.generation=page->second.scheduler.generation;op.kind=kind;op.resultId=resultId;op.media=std::move(callback);
    try{d_->operations.emplace(id,std::move(op));}catch(...){return failure<quint64>(ControllerCode::Unavailable);}
    if(kind==RequestClass::Original)d_->selected=true;
    QPointer<MediaController> self(this);
    const auto submitted=d_->transport->submit(page->second.scheduler,std::move(spec),[self,id](quint64,FetchResult result){if(self)self->completed(id,std::move(result));});
    if(!submitted){d_->operations.erase(id);if(kind==RequestClass::Original)d_->selected=false;return failure<quint64>(ControllerCode::Remote,submitted.error);}
    d_->operations.at(id).transport=*submitted.value;return success(id);
}
void MediaController::completed(quint64 ticket,FetchResult result){
    if(!on_owner_thread())return;
    auto found=d_->operations.find(ticket);if(found==d_->operations.end())return;
    auto op=std::move(found->second);d_->operations.erase(found);
    if(op.kind==RequestClass::Original)d_->selected=false;
    ControllerError error;
    if(!d_->available||!d_->scheduler||!d_->transport)error.code=ControllerCode::Unavailable;
    else if(!current(ControllerScope{op.scope}))error.code=ControllerCode::StaleScope;
    else if(d_->pages.at(op.scope).scheduler.generation!=op.generation)error.code=ControllerCode::StaleScope;
    else if(op.cancelled||result.outcome==SchedulerOutcome::Cancelled)error.code=ControllerCode::Cancelled;
    else if(result.outcome==SchedulerOutcome::StaleGeneration)error.code=ControllerCode::StaleScope;
    else if(result.outcome!=SchedulerOutcome::Completed||result.error.code!=Code::None)error={ControllerCode::Remote,result.error};
    const auto now=d_->now();if(!now&&error.code==ControllerCode::Ok)error.code=ControllerCode::Unavailable;
    if(op.kind==RequestClass::Api){
        ControllerResult<SearchRows> delivered;
        if(error.code==ControllerCode::Ok){
            auto parsed=parse_search_response(result.bytes,result.mime,op.mode);
            if(!parsed)error={ControllerCode::Remote,parsed.error};
            else{auto batch=d_->cache.publish_batch({op.scope},*parsed.value,*now);if(batch)delivered.value=std::move(*batch.value);else error.code=cache_error(batch.error);}
        }
        delivered.error=error;
        if(delivered.value)delivered.stamp={{op.scope},op.generation};
        // No access to this after a user callback (including callback throws).
        try{op.search(ticket,std::move(delivered));}catch(...){}return;
    }
    ControllerResult<PrivateMedia> delivered;
    if(error.code==ControllerCode::Ok){
        auto candidate=d_->cache.resolve({op.scope},op.resultId,*now);
        if(!candidate)error.code=cache_error(candidate.error);
        else {
            const auto valid=op.kind==RequestClass::Original?validate_download(result.bytes,result.mime,candidate.value->metadata):validate_preview(result.bytes,result.mime);
            if(!valid)error={ControllerCode::Remote,valid.error};
            else delivered.value=PrivateMedia{std::move(*candidate.value),std::move(result.bytes),std::move(result.mime)};
        }
    }
    delivered.error=error;if(delivered.value)delivered.stamp={{op.scope},op.generation};try{op.media(ticket,std::move(delivered));}catch(...){}
}
bool MediaController::cancel(quint64 ticket){
    if(!on_owner_thread())return false;
    auto it=d_->operations.find(ticket);if(it==d_->operations.end())return false;
    it->second.cancelled=true;if(d_->transport)d_->transport->cancel(it->second.transport);return true;
}
bool MediaController::shutdown(){
    if(!on_owner_thread()||!d_->accepting)return false;
    d_->accepting=false;
    while(!d_->pages.empty())close_page({d_->pages.begin()->first});
    if(!d_->transport)dependencies_lost();return true;
}
ControllerResult<ControllerSnapshot> MediaController::snapshot()const{
    if(!on_owner_thread())return failure<ControllerSnapshot>(ControllerCode::InvalidInput);
    return success(ControllerSnapshot{int(d_->pages.size()),int(d_->operations.size()),d_->selected});
}
void MediaController::dependencies_lost(){
    if(!on_owner_thread())return;
    d_->available=false;
    while(!d_->pages.empty())close_page({d_->pages.begin()->first});
    d_->cache.clear();
    if(d_->fallbackQueued)return;d_->fallbackQueued=true;
    QPointer<MediaController> self(this);
    QMetaObject::invokeMethod(this,[self]{
        if(!self)return;
        self->d_->fallbackQueued=false;
        while(!self->d_->operations.empty()){
            const auto ticket=self->d_->operations.begin()->first;
            self->completed(ticket,{SchedulerOutcome::Rejected,{Code::ProviderUnavailable,503,{}},{},{}});
            if(!self)return;
        }
    },Qt::QueuedConnection);
}
} // namespace valmax::media
