#include "controller.h"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QEvent>
#include <QEventLoop>
#include <QJsonArray>
#include <QJsonDocument>
#include <QPointer>
#include <QTimer>
#include <algorithm>
#include <cstring>
#include <deque>
#include <iostream>
#include <thread>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
#endif
using namespace valmax::media;
namespace {
int checks=0;
void check(bool b){++checks;if(!b){std::cerr<<"failed check "<<checks<<'\n';std::exit(1);}}
QByteArray png(){return QByteArray::fromHex("89504e470d0a1a0a")+"synthetic";}
QByteArray api(){return R"({"query":{"pages":[{"title":"File:Synthetic.png","imageinfo":[{"mime":"image/png","size":17,"width":320,"height":240,"url":"https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic.png","thumburl":"https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Synthetic.png/240px-Synthetic.png","descriptionurl":"https://commons.wikimedia.org/wiki/File:Synthetic.png","extmetadata":{"Artist":{"value":"<b>Test author</b>"},"LicenseShortName":{"value":"CC0"}}}]}]}})";}
QByteArray request(){return R"({"mode":"pic","query":"synthetic"})";}
QByteArray selection(const QString& id){return QJsonDocument(QJsonObject{{"id",id}}).toJson(QJsonDocument::Compact);}
void events(){for(int i=0;i<20;++i){QCoreApplication::processEvents(QEventLoop::AllEvents);QCoreApplication::sendPostedEvents(nullptr,QEvent::DeferredDelete);}}
template<class F>void until(F predicate){QElapsedTimer timer;timer.start();while(!predicate()&&timer.elapsed()<1500)events();check(predicate());}
struct Plan{QByteArray body;QString mime;bool hold=false;bool holdAbort=false;int status=200;};
class FakeReply final:public QNetworkReply {
    QByteArray data_;qint64 position_=0;bool holdAbort_=false,done_=false;
public:
    int abortCount=0;
    FakeReply(const QNetworkRequest& req,QObject* owner,Plan p):QNetworkReply(owner),data_(std::move(p.body)),holdAbort_(p.holdAbort){
        setRequest(req);setUrl(req.url());setOperation(QNetworkAccessManager::GetOperation);
        setAttribute(QNetworkRequest::HttpStatusCodeAttribute,p.status);
        setHeader(QNetworkRequest::ContentTypeHeader,p.mime);
        setHeader(QNetworkRequest::ContentLengthHeader,data_.size());
        open(QIODevice::ReadOnly|QIODevice::Unbuffered);
        if(!p.hold)QTimer::singleShot(0,this,[this]{finish();});
    }
    void finish(){if(done_)return;done_=true;setFinished(true);QPointer<FakeReply> self(this);emit metaDataChanged();if(!self)return;emit readyRead();if(!self)return;emit finished();}
    void abort()override{++abortCount;setError(QNetworkReply::OperationCanceledError,QString());if(!holdAbort_)QTimer::singleShot(0,this,[this]{finish();});}
    bool isSequential()const override{return true;}
    qint64 bytesAvailable()const override{return data_.size()-position_+QNetworkReply::bytesAvailable();}
protected:
    qint64 readData(char* out,qint64 max)override{const auto n=std::min(max,qint64(data_.size())-position_);if(n<=0)return -1;std::memcpy(out,data_.constData()+position_,size_t(n));position_+=n;return n;}
};
struct Harness {
    double now=0;std::deque<Plan> plans;QVector<QPointer<FakeReply>> replies;int created=0;
    std::unique_ptr<ProviderScheduler> scheduler;
    std::unique_ptr<MediaTransport> transport;
    std::unique_ptr<MediaController> controller;
    Harness(){
        scheduler=std::make_unique<ProviderScheduler>(nullptr,SchedulerClock{[this]{return now;},[this]{return 1700000000.+now;},false});
        ReplyFactory factory=[this](const QNetworkRequest& request,QObject* owner)->QNetworkReply*{
            check(!plans.empty());auto p=std::move(plans.front());plans.pop_front();++created;
            auto reply=new FakeReply(request,owner,std::move(p));replies.push_back(reply);return reply;
        };
        transport=std::make_unique<MediaTransport>(scheduler.get(),nullptr,std::move(factory),TransportClock{[this]{return qint64(now*1000);},false});
        controller=std::make_unique<MediaController>(scheduler.get(),transport.get(),nullptr,ControllerClock{[this]{return now;}});
    }
    ~Harness(){controller.reset();transport.reset();scheduler.reset();events();}
    ControllerScope page(qint64 account=1,quint64 pageId=1){auto p=controller->open_page(account,pageId);check(bool(p));return *p.value;}
    QString search(ControllerScope page){
        plans.push_back({api(),"application/json"});QString id;int calls=0;
        auto ticket=controller->search(page,request(),[&](quint64,ControllerResult<SearchRows> r){++calls;check(bool(r));check(controller->current(r.stamp));check(r.value->size()==1);const auto wire=QJsonDocument(r.value->at(0)).toJson();check(!wire.contains("upload.wikimedia"));check(!wire.contains("accountId"));id=r.value->at(0)["id"].toString();});
        check(bool(ticket));check(calls==0);until([&]{return calls==1;});check(id.size()==32);return id;
    }
};
void success_and_debounce(){
    Harness h;const auto p=h.page();
    check(!h.controller->search(p,request(),{}));
    check(!h.controller->search(p,"{}",[](quint64,auto){}));
    check(h.created==0);const auto id=h.search(p);check(h.created==1);
    check(h.controller->search(p,request(),[](quint64,auto){}).error.code==ControllerCode::SearchTooFast);
    const auto other=h.page(2,1);check(h.controller->search(other,request(),[](quint64,auto){}).error.code==ControllerCode::SearchTooFast);
    h.now=2;h.search(other);check(h.created==2);
    check(h.controller->select(other,selection(id),[](quint64,auto){}).error.code==ControllerCode::ResultExpired);
    h.plans.push_back({png(),"image/png"});int calls=0;
    auto selected=h.controller->select(p,selection(id),[&](quint64,ControllerResult<PrivateMedia> r){++calls;check(bool(r));check(r.value->bytes==png());check(r.value->mime=="image/png");check(r.value->candidate.metadata.author=="Test author");check(!h.controller->snapshot().value->selectedBusy);});
    check(bool(selected));check(h.controller->snapshot().value->selectedBusy);check(calls==0);
    check(h.controller->select(p,selection(id),[](quint64,auto){}).error.code==ControllerCode::DownloadBusy);
    until([&]{return calls==1;});check(h.controller->snapshot().value->operations==0);
    h.plans.push_back({png(),"image/png"});calls=0;ControllerStamp previewStamp;
    check(bool(h.controller->preview(p,id,[&](quint64,ControllerResult<PrivateMedia> r){++calls;check(bool(r));check(r.value->bytes==png());previewStamp=r.stamp;check(h.controller->current(previewStamp));})));
    until([&]{return calls==1;});
    const auto captured=*h.controller->capture(p).value;check(captured.generation==previewStamp.generation);
    h.now=4;h.search(p);check(h.controller->current(p));check(!h.controller->current(previewStamp));check(!h.controller->current(captured));
    h.now=1200;const int before=h.created;
    check(h.controller->select(p,selection(id),[](quint64,auto){}).error.code==ControllerCode::ResultExpired);check(h.created==before);
}
void cancelled_busy_and_stale(){
    Harness h;auto p=h.page();auto id=h.search(p);
    h.plans.push_back({png(),"image/png",true,true});int calls=0;ControllerCode code=ControllerCode::Ok;
    auto ticket=h.controller->select(p,selection(id),[&](quint64,ControllerResult<PrivateMedia> r){++calls;check(!r.value);code=r.error.code;});check(bool(ticket));
    until([&]{return h.created==2;});QPointer<FakeReply> reply=h.replies.last();
    check(h.controller->cancel(*ticket.value));events();check(reply&&reply->abortCount>0);check(calls==0);check(h.controller->snapshot().value->selectedBusy);
    check(h.controller->select(p,selection(id),[](quint64,auto){}).error.code==ControllerCode::DownloadBusy);
    reply->finish();until([&]{return calls==1;});check(code==ControllerCode::Cancelled);check(!h.controller->snapshot().value->selectedBusy);
    h.now=2;h.plans.push_back({api(),"application/json",true,true});calls=0;
    check(bool(h.controller->search(p,request(),[&](quint64,ControllerResult<SearchRows> r){++calls;check(!r.value);code=r.error.code;})));
    until([&]{return h.created==3;});reply=h.replies.last();
    const auto fresh=h.page();check(fresh.token!=p.token);check(!h.controller->current(p));check(h.controller->current(fresh));
    events();check(calls==0);reply->finish();until([&]{return calls==1;});check(code==ControllerCode::StaleScope);
    check(h.controller->select(fresh,selection(id),[](quint64,auto){}).error.code==ControllerCode::ResultExpired);
}
void errors_and_lifetime(){
    Harness h;const auto p=h.page();int calls=0;ControllerCode code=ControllerCode::Ok;
    h.plans.push_back({"not json","application/json"});
    check(bool(h.controller->search(p,request(),[&](quint64,ControllerResult<SearchRows> r){++calls;check(!r.value);code=r.error.code;})));
    until([&]{return calls==1;});check(code==ControllerCode::Remote);
    h.now=2;h.plans.push_back({api(),"application/json",true,true});calls=0;
    check(bool(h.controller->search(p,request(),[&](quint64,ControllerResult<SearchRows> r){++calls;check(!r.value);code=r.error.code;})));
    until([&]{return h.created==2;});h.transport.reset();check(calls==0);until([&]{return calls==1;});check(code==ControllerCode::Unavailable);
    check(h.controller->snapshot().value->operations==0);check(h.controller->snapshot().value->scopes==0);check(!h.controller->search(p,request(),[](quint64,auto){}));
    Harness schedulerLost;const auto lostPage=schedulerLost.page();schedulerLost.plans.push_back({api(),"application/json",true,true});int lostCalls=0;
    check(bool(schedulerLost.controller->search(lostPage,request(),[&](quint64,ControllerResult<SearchRows> r){++lostCalls;check(!r.value);check(r.error.code==ControllerCode::Unavailable);})));
    until([&]{return schedulerLost.created==1;});schedulerLost.scheduler.reset();check(lostCalls==0);until([&]{return lostCalls==1;});check(schedulerLost.controller->snapshot().value->scopes==0);
    Harness deleting;auto page=deleting.page();deleting.plans.push_back({api(),"application/json"});bool delivered=false;
    check(bool(deleting.controller->search(page,request(),[&](quint64,ControllerResult<SearchRows> r){check(bool(r));delivered=true;deleting.controller.reset();})));
    until([&]{return delivered;});events();check(!deleting.controller);
    Harness throwing;page=throwing.page();throwing.plans.push_back({api(),"application/json"});calls=0;
    check(bool(throwing.controller->search(page,request(),[&](quint64,auto){++calls;throw 1;})));until([&]{return calls==1;});check(throwing.controller->snapshot().value->operations==0);
    Harness destroyed;page=destroyed.page();destroyed.plans.push_back({api(),"application/json",true,false});calls=0;
    check(bool(destroyed.controller->search(page,request(),[&](quint64,auto){++calls;})));until([&]{return destroyed.created==1;});destroyed.controller.reset();events();check(calls==0);
}
void newest_search_wins(){
    Harness h;const auto p=h.page();int oldCalls=0,newCalls=0;ControllerCode oldCode=ControllerCode::Ok;
    h.plans.push_back({api(),"application/json",true,true});
    check(bool(h.controller->search(p,request(),[&](quint64,ControllerResult<SearchRows> r){++oldCalls;check(!r.value);oldCode=r.error.code;})));
    until([&]{return h.created==1;});auto old=h.replies.last();h.now=2;
    h.plans.push_back({api(),"application/json"});
    check(bool(h.controller->search(p,request(),[&](quint64,ControllerResult<SearchRows> r){++newCalls;check(bool(r));})));
    until([&]{return newCalls==1;});check(oldCalls==0);check(old&&old->abortCount>0);
    old->finish();until([&]{return oldCalls==1;});check(oldCode==ControllerCode::StaleScope);check(newCalls==1);
    h.now=4;const auto id=h.search(p);int mediaCalls=0;
    h.plans.push_back({png(),"image/png",true,true});
    check(bool(h.controller->select(p,selection(id),[&](quint64,ControllerResult<PrivateMedia> r){++mediaCalls;check(!r.value);check(r.error.code==ControllerCode::StaleScope);})));
    until([&]{return h.created==4;});auto selected=h.replies.last();h.now=6;h.search(p);
    check(mediaCalls==0);check(h.controller->snapshot().value->selectedBusy);
    check(h.controller->select(p,selection(id),[](quint64,auto){}).error.code==ControllerCode::DownloadBusy);
    selected->finish();until([&]{return mediaCalls==1;});check(!h.controller->snapshot().value->selectedBusy);
}
void bounds_and_thread(){
    Harness h;for(int i=0;i<32;++i)h.page(i+1,1);check(h.controller->open_page(100,1).error.code==ControllerCode::Capacity);
    auto replaced=h.page(1,1);check(h.controller->snapshot().value->scopes==32);
    ControllerCode other=ControllerCode::Ok;std::thread foreign([&]{other=h.controller->snapshot().error.code;});foreign.join();check(other==ControllerCode::InvalidInput);
    check(h.controller->shutdown());check(!h.controller->shutdown());check(!h.controller->current(replaced));check(h.controller->snapshot().value->scopes==0);
}
void operation_bound(){
    Harness h;const auto page=h.page();const auto id=h.search(page);int finished=0;
    auto callback=[&](quint64,ControllerResult<PrivateMedia> r){++finished;check(!r.value);check(r.error.code==ControllerCode::StaleScope);};
    for(int i=0;i<2;++i){h.plans.push_back({png(),"image/png",true,false});check(bool(h.controller->preview(page,id,callback)));}
    until([&]{return h.created==3;});
    for(int i=0;i<64;++i)check(bool(h.controller->preview(page,id,callback)));
    check(h.controller->snapshot().value->operations==66);
    check(h.controller->preview(page,id,callback).error.code==ControllerCode::Capacity);
    check(h.controller->shutdown());check(finished==0);until([&]{return finished==66;});
    check(h.controller->snapshot().value->operations==0);check(h.created==3);
}
}
int main(int argc,char** argv){
#ifdef _WIN32
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
#endif
    QCoreApplication app(argc,argv);success_and_debounce();cancelled_busy_and_stale();errors_and_lifetime();newest_search_wins();bounds_and_thread();operation_bound();
    std::cout<<"{\"passed\":true,\"checks\":"<<checks<<"}\n";return 0;
}
