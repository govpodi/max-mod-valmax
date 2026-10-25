#include "decode_queue.h"
#include <QCoreApplication>
#include <QEvent>
#include <QMetaObject>
#include <QPointer>
#include <QThread>
#include <QThreadPool>
#include <algorithm>
#include <limits>
#include <map>
#include <utility>

namespace valmax::media {
namespace {
Error invalid() { return {Code::InvalidInput,400,{}}; }
Error unavailable() { return {Code::ProviderUnavailable,503,{}}; }
template<class T> Result<T> rejected(Error error) { return {{},error}; }
}
struct DecodeQueue::Impl {
    enum class Phase { Waiting, Active, Done };
    struct Job {
        quint64 ticket=0, scope=0;
        QByteArray bytes;
        QString mime;
        QSize box;
        std::function<void(quint64,DecodeResult)> callback;
        Phase phase=Phase::Waiting;
        bool cancelled=false;
        Result<Thumbnail> result;
    };
    QThreadPool workers;
    ThumbnailDecoder decoder;
    std::function<void()> drained;
    std::map<quint64,std::shared_ptr<Job>> jobs;
    quint64 next=1;
    bool scheduled=false,stopping=false,notified=false;
    int active()const {
        // Done jobs still own decoder capacity until their result is delivered.
        return int(std::count_if(jobs.begin(),jobs.end(),[](const auto& p){return p.second->phase!=Phase::Waiting;}));
    }
};
DecodeQueue::DecodeQueue(QObject* parent,ThumbnailDecoder decoder,std::function<void()> drained)
    :QObject(parent),d_(std::make_unique<Impl>()) {
    d_->decoder=decoder?std::move(decoder):decode_thumbnail;
    d_->drained=std::move(drained);
    d_->workers.setMaxThreadCount(MaxActive);
    d_->workers.setExpiryTimeout(-1);
}
DecodeQueue::~DecodeQueue() {
    d_->stopping=true;
    // QObject's base and event queue remain alive through this wait. Workers may
    // enqueue completion but never dereference d_ or require the event to run.
    // QObject destruction then removes queued completions before d_ can be used.
    d_->workers.waitForDone();
    // A destroyed() observer may run a nested event loop while QObject's base
    // destructor is still running. Remove our queued lambdas while d_ is alive.
    QCoreApplication::removePostedEvents(this,QEvent::MetaCall);
    d_->jobs.clear();
}
bool DecodeQueue::on_owner_thread()const noexcept { return QThread::currentThread()==thread(); }
void DecodeQueue::schedule() {
    if(d_->scheduled)return;
    d_->scheduled=true;
    QMetaObject::invokeMethod(this,[this]{d_->scheduled=false;pump();},Qt::QueuedConnection);
}
Result<quint64> DecodeQueue::submit(quint64 scope,QByteArray bytes,QString mime,QSize box,
                                  std::function<void(quint64,DecodeResult)> callback) {
    if(!on_owner_thread())return rejected<quint64>(invalid());
    if(scope==0||!callback||box.width()<1||box.height()<1
        ||box.width()>MaxThumbnailDimension||box.height()>MaxThumbnailDimension)
        return rejected<quint64>(invalid());
    const auto valid=validate_preview(bytes,mime);
    if(!valid)return rejected<quint64>(valid.error);
    if(d_->stopping||d_->jobs.size()>=size_t(MaxActive+MaxWaiting)
        ||int(d_->jobs.size())-d_->active()>=MaxWaiting||d_->next==0)
        return rejected<quint64>(unavailable());
    const quint64 ticket=d_->next++;
    auto job=std::make_shared<Impl::Job>();
    // QByteArray/QString fromRawData may borrow caller memory. A small value
    // may also retain a huge reserve; own only the validated bounded payload.
    job->ticket=ticket;job->scope=scope;
    job->bytes=QByteArray(bytes.constData(),bytes.size());
    job->mime=QString(mime.constData(),mime.size());
    job->box=box;job->callback=std::move(callback);
    d_->jobs.emplace(ticket,std::move(job));schedule();
    return {ticket,{}};
}
bool DecodeQueue::cancel(quint64 ticket) {
    if(!on_owner_thread())return false;
    const auto it=d_->jobs.find(ticket);if(it==d_->jobs.end())return false;
    it->second->cancelled=true;
    if(it->second->phase==Impl::Phase::Waiting)it->second->bytes.clear();
    schedule();return true;
}
bool DecodeQueue::cancel_scope(quint64 scope) {
    if(!on_owner_thread()||scope==0)return false;
    bool found=false;
    for(auto& [id,job]:d_->jobs)if(job->scope==scope){(void)id;job->cancelled=true;found=true;
        if(job->phase==Impl::Phase::Waiting)job->bytes.clear();}
    if(found)schedule();return found;
}
bool DecodeQueue::shutdown() {
    if(!on_owner_thread())return false;
    d_->stopping=true;
    for(auto& [id,job]:d_->jobs){(void)id;job->cancelled=true;
        if(job->phase==Impl::Phase::Waiting)job->bytes.clear();}
    schedule();return true;
}
Result<DecodeSnapshot> DecodeQueue::snapshot()const {
    if(!on_owner_thread())return rejected<DecodeSnapshot>(invalid());
    const int active=d_->active();
    return {DecodeSnapshot{int(d_->jobs.size())-active,active,int(d_->jobs.size()),d_->stopping},{}};
}
void DecodeQueue::decoded(quint64 ticket,Result<Thumbnail> result) {
    const auto it=d_->jobs.find(ticket);if(it==d_->jobs.end())return;
    auto& job=*it->second;
    if(job.phase!=Impl::Phase::Active)return;
    job.bytes.clear();job.result=std::move(result);job.phase=Impl::Phase::Done;
    schedule();
}
void DecodeQueue::pump() {
    // One result callback per event-loop turn; callbacks may delete/reenter us.
    for(auto it=d_->jobs.begin();it!=d_->jobs.end();++it) {
        auto job=it->second;
        if(job->phase!=Impl::Phase::Done&&!(job->phase==Impl::Phase::Waiting&&job->cancelled))continue;
        DecodeResult result;
        if(job->cancelled)result.state=DecodeState::Cancelled;
        else if(job->result){result.state=DecodeState::Completed;result.thumbnail=std::move(job->result.value);}
        else {result.state=DecodeState::Failed;result.error=job->result.error;}
        auto callback=std::move(job->callback);d_->jobs.erase(it);
        const QPointer<DecodeQueue> alive(this);
        try{callback(job->ticket,std::move(result));}catch(...){}
        if(alive)schedule();return;
    }
    if(d_->stopping&&d_->jobs.empty()) {
        if(!d_->notified){d_->notified=true;auto callback=std::move(d_->drained);if(callback)try{callback();}catch(...){} }
        return;
    }
    while(!d_->stopping&&d_->active()<MaxActive) {
        const auto it=std::find_if(d_->jobs.begin(),d_->jobs.end(),[](const auto& p){return p.second->phase==Impl::Phase::Waiting&&!p.second->cancelled;});
        if(it==d_->jobs.end())return;
        const auto job=it->second;job->phase=Impl::Phase::Active;
        const auto decoder=d_->decoder;
        d_->workers.start([this,job,decoder]{
            Result<Thumbnail> result;
            try{result=decoder(job->bytes,job->mime,job->box);}catch(...){result.error=unavailable();}
            // Destructor waits until this enqueue finishes; the queued method
            // is removed by QObject destruction if shutdown already began.
            QMetaObject::invokeMethod(this,[this,ticket=job->ticket,result=std::move(result)]()mutable{
                decoded(ticket,std::move(result));
            },Qt::QueuedConnection);
        });
    }
}
} // namespace valmax::media
