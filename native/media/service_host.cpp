#include "service_host.h"
#include <QCoreApplication>
#include <QDateTime>
#include <QEvent>
#include <QPointer>
#include <QThread>
#include <QTimer>
#include <algorithm>
#include <limits>
#include <map>
#include <mutex>
#include <variant>

namespace valmax::media {
namespace {
std::atomic<bool> processReserved{false};
enum class Kind { Search, Preview, Selected };
template<class T> ServiceResult<T> fail(ServiceCode code) { ServiceResult<T> r; r.error.code=code; return r; }
template<class T> ServiceResult<T> ok(T value) { ServiceResult<T> r; r.value=std::move(value); return r; }
struct Shared {
    std::atomic<bool> stopping{false}, ownerAlive{true};
    std::atomic<quint64> selected{0};
    std::atomic<int> images{0};
    std::atomic<int> pendingBinds{0};
};
struct Page {
    ServicePage id;
    EngineMediaLease lease;
    std::shared_ptr<std::atomic<uint64_t>> generation=std::make_shared<std::atomic<uint64_t>>(1);
    ControllerScope scope;
    bool valid() const { return lease.valid()&&generation->load(std::memory_order_acquire)!=0; }
};
struct Op {
    quint64 id=0;
    Kind kind=Kind::Search;
    std::shared_ptr<Page> page;
    SearchGeneration generation;
    ControllerStamp stamp;
    std::atomic<bool> cancelled{false};
    std::atomic<bool> cancellationPosted{false};
    std::mutex signalMutex;
    std::optional<StagingCancellation> stagingCancellation;
    bool valid() const { return !cancelled.load(std::memory_order_acquire)&&page->lease.valid()&&generation.valid(); }
    void signal() {
        cancelled.store(true,std::memory_order_release);
        std::lock_guard lock(signalMutex);
        if(stagingCancellation) stagingCancellation->signal();
    }
};
using Payload=std::variant<std::monostate,SearchRows,QImage,RetainedMediaFile>;
struct Packet { std::shared_ptr<Op> op; ServiceError error; Payload payload; };
using PacketPtr=std::shared_ptr<Packet>;
StagingMime mime_code(const QString& value) {
    if(value==QStringLiteral("image/jpeg"))return StagingMime::Jpeg;
    if(value==QStringLiteral("image/png"))return StagingMime::Png;
    if(value==QStringLiteral("image/gif"))return StagingMime::Gif;
    if(value==QStringLiteral("image/webp"))return StagingMime::Webp;
    if(value==QStringLiteral("video/webm"))return StagingMime::Webm;
    return StagingMime::Mp4;
}
ServiceError controller_error(ControllerError error) { ServiceError e; e.code=ServiceCode::Controller;e.controller=error;return e; }
ServiceError storage_error(StagingCode code) { ServiceError e;e.code=code==StagingCode::Cancelled?ServiceCode::Cancelled:ServiceCode::Storage;e.storage=code;return e; }

// Only this worker creates/destroys StagingJob and PreparedFile. The service
// thread may signal the detached cancellation capability but never their owner.
class IoWorker final : public QObject {
    Q_OBJECT
public:
    explicit IoWorker(ServiceConfig config):config_(std::move(config)){}
    ~IoWorker() override { QCoreApplication::removePostedEvents(this,QEvent::MetaCall); }
    void stage(std::shared_ptr<Op> op,PrivateMedia media) {
        if(active_){emit terminal(op->id,storage_error(StagingCode::Busy),{});return;}
        active_=std::move(op);
        caption_=attribution(media.candidate.metadata);mime_=media.mime;
        if(!active_->valid()){finish(storage_error(StagingCode::Cancelled));return;}
        auto begun=StagingJob::begin(config_.staging,config_.explicitStagingRoot);
        if(!begun){finish(storage_error(begun.code));return;}
        {
            std::lock_guard lock(active_->signalMutex);
            active_->stagingCancellation=begun.value->cancellation();
            if(!active_->valid())active_->stagingCancellation->signal();
        }
        uint64_t now=0;
        try{now=config_.unixSeconds?config_.unixSeconds():uint64_t(QDateTime::currentSecsSinceEpoch());}
        catch(...){finish(storage_error(StagingCode::InternalError));return;}
        const auto* data=reinterpret_cast<const uint8_t*>(media.bytes.constData());
        auto staged=begun.value->stage({data,size_t(media.bytes.size())},mime_code(media.mime),uint64_t(media.bytes.size()),now);
        if(!staged){finish(storage_error(staged.code));return;}
        prepared_=std::move(staged.value);
        emit prepared(active_->id);
    }
    void decide(quint64 id,bool retain) {
        if(!active_||active_->id!=id)return;
        if(!prepared_){finish(storage_error(StagingCode::InvalidState));return;}
        if(!retain||!active_->valid()) {
            const auto result=prepared_->cancel();
            finish(storage_error(result==StagingCode::Ok?StagingCode::Cancelled:result));return;
        }
        RetainedMediaFile file{std::string(prepared_->native_file_url()),prepared_->byte_length(),caption_,mime_};
        const auto result=prepared_->retain_for_handoff();
        if(result!=StagingCode::Ok){finish(storage_error(result));return;}
        finish({},std::move(file));
    }
    void stop() { Q_ASSERT(!active_); deleteLater(); }
signals:
    void prepared(quint64 id);
    void terminal(quint64 id,ServiceError error,RetainedMediaFile file);
private:
    ServiceConfig config_;
    std::shared_ptr<Op> active_;
    std::optional<PreparedFile> prepared_;
    QString caption_,mime_;
    void finish(ServiceError error,RetainedMediaFile file={}) {
        const auto id=active_->id;
        {std::lock_guard lock(active_->signalMutex);active_->stagingCancellation.reset();}
        prepared_.reset();active_.reset();caption_.clear();mime_.clear();
        emit terminal(id,std::move(error),std::move(file));
    }
};

class Worker final : public QObject {
    Q_OBJECT
    struct Work { std::shared_ptr<Op> op; quint64 network=0,decoder=0; bool credit=false,awaitGui=false,io=false; std::optional<PrivateMedia> media; };
public:
    Worker(ServiceConfig config,std::shared_ptr<Shared> shared,IoWorker* io)
        :config_(std::move(config)),shared_(std::move(shared)),io_(io){}
    ~Worker() override { if(timer_)timer_->stop();QCoreApplication::removePostedEvents(this,QEvent::MetaCall); }
    void start() {
        scheduler_=new ProviderScheduler(this,config_.schedulerClock);
        transport_=new MediaTransport(scheduler_,this,config_.replyFactory,config_.transportClock);
        controller_=new MediaController(scheduler_,transport_,this,config_.controllerClock);
        decoder_=new DecodeQueue(this,config_.decoder);
        timer_=new QTimer(this);timer_->setInterval(10);
        connect(timer_,&QTimer::timeout,this,[this]{check_drain();});
    }
    void bind(std::shared_ptr<Page> page) {
        --shared_->pendingBinds;
        if(shared_->stopping||!page->valid())return;
        const auto opened=controller_->open_page(page->lease.account_id(),page->lease.page_id());
        if(opened)page->scope=*opened.value;
        pages_.emplace(page->id.token,std::move(page));
    }
    void close(quint64 token) {
        const auto page=pages_.find(token);
        if(page!=pages_.end()) { controller_->close_page(page->second->scope);pages_.erase(page); }
        std::vector<quint64> ids;for(const auto& [id,work]:work_)if(work.op->page->id.token==token)ids.push_back(id);
        for(const auto id:ids)cancel(id);
    }
    void submit(std::shared_ptr<Op> op,QByteArray json,QString resultId) {
        const auto id=op->id;work_.emplace(id,Work{op});
        if(shared_->stopping||!op->valid()){finish(id,{ServiceCode::Cancelled});return;}
        if(!controller_->current(op->page->scope)){finish(id,{ServiceCode::Unavailable});return;}
        if(op->kind==Kind::Search) {
            std::vector<quint64> old;for(const auto& [other,work]:work_)if(other!=id&&work.op->page==op->page&&!work.op->valid())old.push_back(other);
            for(const auto ticket:old)cancel(ticket);
            auto result=controller_->search(op->page->scope,json,[this,id](quint64,ControllerResult<SearchRows> result){
                auto found=work_.find(id);if(found==work_.end())return;
                found->second.network=0;found->second.op->stamp=result.stamp;
                if(!result){finish(id,controller_error(result.error));return;}
                finish(id,{},std::move(*result.value));
            });
            if(!result){finish(id,controller_error(result.error));return;}
            work_.at(id).network=*result.value;return;
        }
        auto callback=[this,id](quint64,ControllerResult<PrivateMedia> result){media_done(id,std::move(result));};
        const auto result=op->kind==Kind::Preview?controller_->preview(op->page->scope,resultId,std::move(callback)):
            controller_->select(op->page->scope,json,std::move(callback));
        if(!result){finish(id,controller_error(result.error));return;}
        work_.at(id).network=*result.value;
    }
    void cancel(quint64 id) {
        auto found=work_.find(id);if(found==work_.end())return;
        auto& work=found->second;work.op->signal();
        if(work.network){controller_->cancel(work.network);return;}
        if(work.decoder){decoder_->cancel(work.decoder);return;}
        if(work.io)return;
        if(work.awaitGui) { if(shared_->stopping||!shared_->ownerAlive)ack(id);return; }
        finish(id,{ServiceCode::Cancelled});
    }
    void ack(quint64 id) {
        auto found=work_.find(id);if(found==work_.end()||!found->second.awaitGui)return;
        erase(found);pump_decode();check_drain();
    }
    void stop() {
        if(stopping_)return;stopping_=true;shared_->stopping=true;
        controller_->shutdown();transport_->shutdown();scheduler_->shutdown();decoder_->shutdown();
        std::vector<quint64> ids;for(const auto& [id,work]:work_){Q_UNUSED(work);ids.push_back(id);}
        for(const auto id:ids)cancel(id);
        pages_.clear();timer_->start();check_drain();
    }
    void io_prepared(quint64 id) {
        const auto found=work_.find(id);
        const bool keep=found!=work_.end()&&current(found->second.op)&&!shared_->stopping;
        QMetaObject::invokeMethod(io_,[io=io_,id,keep]{io->decide(id,keep);},Qt::QueuedConnection);
    }
    void io_terminal(quint64 id,ServiceError error,RetainedMediaFile file) {
        ioBusy_=false;
        const auto found=work_.find(id);if(found==work_.end()){check_drain();return;}
        found->second.io=false;
        if(error.code!=ServiceCode::Ok)finish(id,std::move(error));
        else finish(id,{},std::move(file));
        check_drain();
    }
signals:
    void packet(PacketPtr packet);
private:
    ServiceConfig config_;std::shared_ptr<Shared> shared_;IoWorker* io_;
    ProviderScheduler* scheduler_=nullptr;MediaTransport* transport_=nullptr;MediaController* controller_=nullptr;
    DecodeQueue* decoder_=nullptr;QTimer* timer_=nullptr;
    std::map<quint64,std::shared_ptr<Page>> pages_;std::map<quint64,Work> work_;
    bool stopping_=false,ioBusy_=false,ending_=false;
    bool current(const std::shared_ptr<Op>& op) const {
        return !shared_->stopping&&op->valid()&&(op->stamp.generation==0||controller_->current(op->stamp));
    }
    void erase(std::map<quint64,Work>::iterator found) {
        if(found->second.credit)--shared_->images;
        if(found->second.op->kind==Kind::Selected){auto expected=found->first;shared_->selected.compare_exchange_strong(expected,0);}
        work_.erase(found);
    }
    void finish(quint64 id,ServiceError error,Payload payload={}) {
        auto found=work_.find(id);if(found==work_.end()||found->second.awaitGui)return;
        auto& work=found->second;work.media.reset();
        if(!current(work.op)){error.code=work.op->cancelled?ServiceCode::Cancelled:ServiceCode::Stale;payload={};}
        if(shared_->stopping||!shared_->ownerAlive){erase(found);check_drain();return;}
        work.awaitGui=true;
        emit packet(std::make_shared<Packet>(Packet{work.op,std::move(error),std::move(payload)}));
    }
    void media_done(quint64 id,ControllerResult<PrivateMedia> result) {
        auto found=work_.find(id);if(found==work_.end())return;
        auto& work=found->second;work.network=0;work.op->stamp=result.stamp;
        if(!result){finish(id,controller_error(result.error));return;}
        if(!current(work.op)){finish(id,{ServiceCode::Stale});return;}
        if(work.op->kind==Kind::Preview){work.media=std::move(result.value);pump_decode();return;}
        if(ioBusy_){finish(id,{ServiceCode::DownloadBusy});return;}
        ioBusy_=true;work.io=true;
        QMetaObject::invokeMethod(io_,[io=io_,op=work.op,media=std::move(*result.value)]()mutable{io->stage(std::move(op),std::move(media));},Qt::QueuedConnection);
    }
    void pump_decode() {
        while(!shared_->stopping&&shared_->images<MediaServiceHost::MaxPendingImages) {
            const auto found=std::find_if(work_.begin(),work_.end(),[](const auto& pair){return pair.second.media.has_value();});
            if(found==work_.end())break;
            const auto id=found->first;auto& work=found->second;
            if(!current(work.op)){finish(id,{ServiceCode::Stale});continue;}
            auto media=std::move(*work.media);work.media.reset();work.credit=true;++shared_->images;
            const auto submitted=decoder_->submit(id,std::move(media.bytes),std::move(media.mime),QSize(240,180),[this,id](quint64,DecodeResult decoded){
                auto it=work_.find(id);if(it==work_.end())return;it->second.decoder=0;
                if(decoded.state!=DecodeState::Completed||!decoded.thumbnail){ServiceError e;e.code=ServiceCode::Decode;e.media=decoded.error;finish(id,e);return;}
                finish(id,{},std::move(decoded.thumbnail->image));
            });
            if(!submitted){ServiceError e;e.code=ServiceCode::Decode;e.media=submitted.error;finish(id,e);}
            else work_.at(id).decoder=*submitted.value;
        }
    }
    void check_drain() {
        if(!stopping_||ending_||ioBusy_||!work_.empty())return;
        const auto scheduler=scheduler_->snapshot();const auto controller=controller_->snapshot();const auto decode=decoder_->snapshot();
        if(!scheduler||scheduler.value->outstanding||!controller||controller.value->operations||!decode||decode.value->pending)return;
        ending_=true;timer_->stop();
        QMetaObject::invokeMethod(io_,[io=io_]{io->stop();},Qt::QueuedConnection);
        deleteLater();
    }
};

// Self-owned GUI-thread run: outlives facade destruction, but never calls its
// dead callbacks. Thread wrappers are deleted only after both finished signals.
class Run final : public QObject {
    Q_OBJECT
public:
    Run(ServiceConfig config,std::shared_ptr<Shared> shared):shared_(std::move(shared)) {
        io_=new IoWorker(config);worker_=new Worker(std::move(config),shared_,io_);
        io_->moveToThread(&ioThread_);worker_->moveToThread(&thread_);
        connect(&thread_,&QThread::started,worker_,&Worker::start);
        connect(this,&Run::command,worker_,[](std::function<void()> callback){callback();},Qt::QueuedConnection);
        connect(worker_,&Worker::packet,this,&Run::packet,Qt::QueuedConnection);
        connect(io_,&IoWorker::prepared,worker_,&Worker::io_prepared,Qt::QueuedConnection);
        connect(io_,&IoWorker::terminal,worker_,&Worker::io_terminal,Qt::QueuedConnection);
        connect(io_,&QObject::destroyed,&ioThread_,&QThread::quit,Qt::DirectConnection);
        connect(worker_,&QObject::destroyed,&thread_,&QThread::quit,Qt::DirectConnection);
        connect(&thread_,&QThread::finished,this,[this]{ended();});
        connect(&ioThread_,&QThread::finished,this,[this]{ended();});
        ioThread_.start();thread_.start();
    }
    ~Run() override { QCoreApplication::removePostedEvents(this,QEvent::MetaCall);processReserved=false; }
    void bind(std::shared_ptr<Page> page){post([page=std::move(page)](Worker* w){w->bind(page);});}
    void close(quint64 id){post([id](Worker* w){w->close(id);});}
    void submit(std::shared_ptr<Op> op,QByteArray json,QString resultId){post([op=std::move(op),json=std::move(json),resultId=std::move(resultId)](Worker* w){w->submit(op,json,resultId);});}
    void cancel(const std::shared_ptr<Op>& op){if(op->cancellationPosted.exchange(true))return;post([id=op->id](Worker* w){w->cancel(id);});}
    void ack(quint64 id){post([id](Worker* w){w->ack(id);});}
    void stop(){if(stopPosted_)return;stopPosted_=true;shared_->stopping=true;post([](Worker* w){w->stop();});}
signals:
    void command(std::function<void()> callback);
    void packet(PacketPtr packet);
    void finished();
private:
    std::shared_ptr<Shared> shared_;QThread thread_,ioThread_;Worker* worker_;IoWorker* io_;
    int ended_=0;bool stopPosted_=false;
    template<class F>void post(F callback) {
        if(ended_)return;
        emit command([worker=worker_,callback=std::move(callback)]{callback(worker);});
    }
    void ended(){if(++ended_==2){emit finished();deleteLater();}}
};
} // anonymous namespace

struct MediaServiceHost::Impl {
    struct Pending {std::shared_ptr<Op> op;std::function<void(PacketPtr)> callback;bool delivered=false;};
    std::shared_ptr<Shared> shared=std::make_shared<Shared>();
    QPointer<Run> run;
    std::map<quint64,std::shared_ptr<Page>> pages;
    std::map<quint64,Pending> pending;
    quint64 nextPage=1,nextTicket=1;bool drained=false;
};
MediaServiceHost::MediaServiceHost(ServiceConfig config,QObject* parent):QObject(parent),d_(std::make_unique<Impl>()) {
    d_->run=new Run(std::move(config),d_->shared);
    connect(d_->run,&Run::packet,this,[this](PacketPtr packet){
        const auto id=packet->op->id;auto found=d_->pending.find(id);if(found==d_->pending.end()){if(d_->run)d_->run->ack(id);return;}
        if(d_->shared->stopping){d_->pending.erase(found);if(d_->run)d_->run->ack(id);return;}
        if(!packet->op->valid()){packet->error.code=packet->op->cancelled?ServiceCode::Cancelled:ServiceCode::Stale;packet->payload={};}
        const bool hold=packet->op->kind==Kind::Selected&&packet->error.code==ServiceCode::Ok&&std::holds_alternative<RetainedMediaFile>(packet->payload);
        auto callback=std::move(found->second.callback);found->second.delivered=hold;
        if(!hold)d_->pending.erase(found);
        QPointer<MediaServiceHost> self(this);
        try{callback(std::move(packet));}catch(...){if(self&&hold)complete_selected(id);}
        if(self&&!hold&&d_->run)d_->run->ack(id);
    });
    connect(d_->run,&Run::finished,this,[this]{d_->drained=true;emit drained();});
}
ServiceResult<MediaServiceHost*> MediaServiceHost::create(ServiceConfig config,QObject* parent) {
    if(!QCoreApplication::instance()||QThread::currentThread()!=QCoreApplication::instance()->thread()||
       (parent&&parent->thread()!=QThread::currentThread())||config.explicitStagingRoot.empty()||config.explicitStagingRoot.size()>31000)
        return fail<MediaServiceHost*>(ServiceCode::InvalidInput);
    bool expected=false;if(!processReserved.compare_exchange_strong(expected,true))return fail<MediaServiceHost*>(ServiceCode::Unavailable);
    try{return ok(new MediaServiceHost(std::move(config),parent));}catch(...){processReserved=false;return fail<MediaServiceHost*>(ServiceCode::Unavailable);}
}
bool MediaServiceHost::process_available()noexcept{return !processReserved.load();}
bool MediaServiceHost::owner()const noexcept{return QThread::currentThread()==thread();}
MediaServiceHost::~MediaServiceHost() {
    Q_ASSERT(owner());d_->shared->ownerAlive=false;
    shutdown();
    if(d_->run)disconnect(d_->run,nullptr,this,nullptr);
    d_->pending.clear();d_->pages.clear();
    // destroyed observers may process MetaCall before QObject removes events.
    QCoreApplication::removePostedEvents(this,QEvent::MetaCall);
}
ServiceResult<ServicePage> MediaServiceHost::bind(EngineMediaLease lease) {
    if(!owner()||!lease.valid())return fail<ServicePage>(ServiceCode::InvalidInput);
    if(d_->shared->stopping||!d_->run)return fail<ServicePage>(ServiceCode::Unavailable);
    if(d_->pages.size()>=MaxPages||d_->shared->pendingBinds>=MaxPages||d_->nextPage==std::numeric_limits<quint64>::max())return fail<ServicePage>(ServiceCode::Capacity);
    std::vector<quint64> old;for(const auto& [id,page]:d_->pages)if(page->lease.account_id()==lease.account_id()&&page->lease.page_id()==lease.page_id())old.push_back(id);
    for(const auto id:old)close({id});
    auto page=std::make_shared<Page>();page->id={d_->nextPage++};page->lease=std::move(lease);
    d_->pages.emplace(page->id.token,page);++d_->shared->pendingBinds;d_->run->bind(page);
    auto result=ok(page->id);result.lease=page->lease;result.generation={page->generation,1};return result;
}
namespace {
template<class T> ServiceResult<T> unpack(const PacketPtr& packet) {
    ServiceResult<T> result;result.error=packet->error;result.lease=packet->op->page->lease;result.stamp=packet->op->stamp;result.generation=packet->op->generation;
    if(auto* value=std::get_if<T>(&packet->payload);value&&result.error.code==ServiceCode::Ok)result.value=std::move(*value);
    return result;
}
}
ServiceResult<quint64> MediaServiceHost::search(ServicePage page,QByteArray json,ServiceSearchCallback callback) {
    if(!owner()||!callback||!parse_search_request(json))return fail<quint64>(ServiceCode::InvalidInput);
    if(d_->shared->stopping||!d_->run)return fail<quint64>(ServiceCode::Unavailable);
    const auto found=d_->pages.find(page.token);if(found==d_->pages.end()||!found->second->valid())return fail<quint64>(ServiceCode::Stale);
    if(d_->pending.size()>=MaxOperations||d_->nextTicket==std::numeric_limits<quint64>::max())return fail<quint64>(ServiceCode::Capacity);
    const auto generation=found->second->generation->load();if(generation==std::numeric_limits<uint64_t>::max()){close(page);return fail<quint64>(ServiceCode::Capacity);}
    found->second->generation->store(generation+1,std::memory_order_release);
    for(const auto& [id,pending]:d_->pending)if(pending.op->page==found->second){Q_UNUSED(id);pending.op->signal();d_->run->cancel(pending.op);}
    auto op=std::make_shared<Op>();op->id=d_->nextTicket++;op->page=found->second;op->generation={op->page->generation,generation+1};
    d_->pending.emplace(op->id,Impl::Pending{op,[callback=std::move(callback)](PacketPtr p){callback(p->op->id,unpack<SearchRows>(p));}});
    d_->run->submit(op,std::move(json),{});
    auto result=ok(op->id);result.lease=op->page->lease;result.generation=op->generation;return result;
}
ServiceResult<quint64> MediaServiceHost::preview(ServicePage page,QString id,ServicePreviewCallback callback) {
    if(!owner()||!callback||id.isEmpty()||id.size()>128)return fail<quint64>(ServiceCode::InvalidInput);
    if(d_->shared->stopping||!d_->run)return fail<quint64>(ServiceCode::Unavailable);
    const auto found=d_->pages.find(page.token);if(found==d_->pages.end()||!found->second->valid())return fail<quint64>(ServiceCode::Stale);
    const auto previews=std::count_if(d_->pending.begin(),d_->pending.end(),[](const auto& pair){return pair.second.op->kind==Kind::Preview;});
    if(previews>=MaxPreviews||d_->pending.size()>=MaxOperations||d_->nextTicket==std::numeric_limits<quint64>::max())return fail<quint64>(ServiceCode::Capacity);
    auto op=std::make_shared<Op>();op->id=d_->nextTicket++;op->kind=Kind::Preview;op->page=found->second;op->generation={op->page->generation,op->page->generation->load()};
    d_->pending.emplace(op->id,Impl::Pending{op,[callback=std::move(callback)](PacketPtr p){callback(p->op->id,unpack<QImage>(p));}});
    d_->run->submit(op,{},std::move(id));
    auto result=ok(op->id);result.lease=op->page->lease;result.generation=op->generation;return result;
}
ServiceResult<quint64> MediaServiceHost::select(ServicePage page,QByteArray json,ServiceSelectedCallback callback) {
    if(!owner()||!callback||!parse_selection_request(json))return fail<quint64>(ServiceCode::InvalidInput);
    if(d_->shared->stopping||!d_->run)return fail<quint64>(ServiceCode::Unavailable);
    const auto found=d_->pages.find(page.token);if(found==d_->pages.end()||!found->second->valid())return fail<quint64>(ServiceCode::Stale);
    if(d_->pending.size()>=MaxOperations||d_->nextTicket==std::numeric_limits<quint64>::max())return fail<quint64>(ServiceCode::Capacity);
    quint64 expected=0;const auto ticket=d_->nextTicket;
    if(!d_->shared->selected.compare_exchange_strong(expected,ticket))return fail<quint64>(ServiceCode::DownloadBusy);
    auto op=std::make_shared<Op>();op->id=d_->nextTicket++;op->kind=Kind::Selected;op->page=found->second;op->generation={op->page->generation,op->page->generation->load()};
    d_->pending.emplace(op->id,Impl::Pending{op,[callback=std::move(callback)](PacketPtr p){callback(p->op->id,unpack<RetainedMediaFile>(p));}});
    d_->run->submit(op,std::move(json),{});
    auto result=ok(op->id);result.lease=op->page->lease;result.generation=op->generation;return result;
}
bool MediaServiceHost::complete_selected(quint64 ticket) {
    if(!owner())return false;
    const auto found=d_->pending.find(ticket);
    if(found==d_->pending.end()||found->second.op->kind!=Kind::Selected||!found->second.delivered)return false;
    d_->pending.erase(found);
    auto expected=ticket;d_->shared->selected.compare_exchange_strong(expected,0);
    if(d_->run)d_->run->ack(ticket);return true;
}
bool MediaServiceHost::cancel(quint64 ticket) {
    if(!owner())return false;
    const auto found=d_->pending.find(ticket);if(found==d_->pending.end())return false;
    found->second.op->signal();if(found->second.delivered)return complete_selected(ticket);
    if(d_->run)d_->run->cancel(found->second.op);return true;
}
bool MediaServiceHost::close(ServicePage page) {
    if(!owner())return false;
    const auto found=d_->pages.find(page.token);if(found==d_->pages.end())return false;
    found->second->generation->store(0,std::memory_order_release);
    std::vector<quint64> ids;for(const auto& [id,pending]:d_->pending)if(pending.op->page==found->second)ids.push_back(id);
    for(const auto id:ids)cancel(id);
    d_->pages.erase(found);if(d_->run)d_->run->close(page.token);return true;
}
bool MediaServiceHost::shutdown() {
    if(!owner()||d_->shared->stopping.exchange(true))return false;
    for(const auto& [id,page]:d_->pages){Q_UNUSED(id);page->generation->store(0,std::memory_order_release);}
    for(const auto& [id,pending]:d_->pending){Q_UNUSED(id);pending.op->signal();}
    d_->pending.clear();d_->pages.clear();if(d_->run)d_->run->stop();return true;
}
ServiceResult<ServiceSnapshot> MediaServiceHost::snapshot()const {
    if(!owner())return fail<ServiceSnapshot>(ServiceCode::InvalidInput);
    const auto previews=std::count_if(d_->pending.begin(),d_->pending.end(),[](const auto& pair){return pair.second.op->kind==Kind::Preview;});
    return ok(ServiceSnapshot{int(d_->pages.size()),int(d_->pending.size()),int(previews),d_->shared->images.load(),d_->shared->selected.load()!=0,d_->shared->stopping,d_->drained});
}
} // namespace valmax::media

#include "service_host.moc"
