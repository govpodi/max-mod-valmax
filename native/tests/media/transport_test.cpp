#include "transport.h"
#include <QCoreApplication>
#include <QEventLoop>
#include <QElapsedTimer>
#include <QHttpHeaders>
#include <QNetworkAccessManager>
#include <QPointer>
#include <QSslError>
#include <QTimer>
#include <atomic>
#include <cstring>
#include <iostream>
#include <map>
#include <memory>
#include <source_location>
#include <thread>
#include <vector>
using namespace valmax::media;
namespace {
int checks = 0;
void check(bool value, std::source_location where = std::source_location::current()) {
    ++checks;
    if (!value) { std::cerr << "check=" << checks << " line=" << where.line() << '\n'; std::exit(1); }
}
void flush() { for (int i = 0; i < 128; ++i) QCoreApplication::processEvents(QEventLoop::AllEvents); }
void deletions() { QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete); flush(); }
QString media_url() { return QStringLiteral("https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic.png"); }
QByteArray png(qsizetype size = 12) { QByteArray b(size,'x'); b.replace(0,8,QByteArray::fromHex("89504e470d0a1a0a")); return b; }
FetchSpec api() { return {RequestClass::Api, QString::fromLatin1(Api), {}}; }
FetchSpec preview() { return {RequestClass::Preview, media_url(), {}}; }
FetchSpec original(qsizetype size = 12) {
    PublicMetadata m; m.mime=QStringLiteral("image/png"); m.bytes=size; m.width=10; m.height=10;
    return {RequestClass::Original, media_url(), m};
}
class FakeReply final : public QNetworkReply {
public:
    QByteArray pending;
    bool delayAbort = false, aborted = false;
    qint64 largestRead = 0;
    int abortCalls = 0;
    explicit FakeReply(const QNetworkRequest& request, QObject* owner) : QNetworkReply(owner) {
        setRequest(request); setUrl(request.url()); setOperation(QNetworkAccessManager::GetOperation);
        open(QIODevice::ReadOnly | QIODevice::Unbuffered);
    }
    void headers(int status=200, QByteArray mime="application/json", QByteArray length={}, QByteArray encoding={}) {
        setAttribute(QNetworkRequest::HttpStatusCodeAttribute,status);
        setRawHeader("Content-Type",mime);
        if (!length.isEmpty()) setRawHeader("Content-Length",length);
        if (!encoding.isEmpty()) setRawHeader("Content-Encoding",encoding);
    }
    void header(const QByteArray& name, const QByteArray& value) { setRawHeader(name,value); }
    void attr(QNetworkRequest::Attribute key, QVariant value) { setAttribute(key,value); }
    void url(QString value) { setUrl(QUrl(value)); }
    void notify_headers() { emit metaDataChanged(); }
    void raw_headers(const QHttpHeaders& value) { setHeaders(value); }
    void push(QByteArray data) { pending += data; emit readyRead(); }
    void stop(NetworkError error=NoError) {
        if (isFinished()) return;
        if (error != NoError) setError(error,QStringLiteral("synthetic"));
        setFinished(true); emit finished();
    }
    void abort() override {
        ++abortCalls; aborted=true;
        if (!delayAbort) stop(OperationCanceledError);
    }
    void tls_error() { emit sslErrors({QSslError(QSslError::SelfSignedCertificate)}); }
    qint64 bytesAvailable() const override { return pending.size() + QNetworkReply::bytesAvailable(); }
    bool isSequential() const override { return true; }
protected:
    qint64 readData(char* target, qint64 maximum) override {
        largestRead = std::max(largestRead,maximum);
        const qint64 count=std::min(maximum,qint64(pending.size()));
        if (count) { std::memcpy(target,pending.constData(),size_t(count)); pending.remove(0,count); }
        return count;
    }
};
struct Fixture {
    qint64 milliseconds=1000;
    double schedulerSeconds=1, wall=1700000000;
    std::function<double()> schedulerTime;
    std::unique_ptr<ProviderScheduler> scheduler;
    std::unique_ptr<MediaTransport> transport;
    MediaScope scope;
    std::vector<QPointer<FakeReply>> replies;
    std::vector<QNetworkRequest> requests;
    std::map<quint64,FetchResult> done;
    bool delayedAbort=false, returnNull=false, immediatelyFinished=false;
    Fixture() {
        scheduler=std::make_unique<ProviderScheduler>(nullptr,SchedulerClock{[this]{return schedulerTime ? schedulerTime() : schedulerSeconds;},[this]{return wall;},false});
        scope=*scheduler->open_lane().value;
        transport=std::make_unique<MediaTransport>(scheduler.get(),nullptr,
            [this](const QNetworkRequest& request,QObject* owner)->QNetworkReply* {
                requests.push_back(request);
                check(request.rawHeader("Accept-Encoding")=="identity");
                check(request.attribute(QNetworkRequest::RedirectPolicyAttribute).toInt()==QNetworkRequest::ManualRedirectPolicy);
                check(request.attribute(QNetworkRequest::CookieLoadControlAttribute).toInt()==QNetworkRequest::Manual);
                check(request.attribute(QNetworkRequest::CookieSaveControlAttribute).toInt()==QNetworkRequest::Manual);
                check(request.attribute(QNetworkRequest::AuthenticationReuseAttribute).toInt()==QNetworkRequest::Manual);
                check(!request.attribute(QNetworkRequest::UseCredentialsAttribute).toBool());
                check(!request.attribute(QNetworkRequest::Http2AllowedAttribute).toBool());
                check(!request.attribute(QNetworkRequest::Http2DirectAttribute).toBool());
                check(!request.attribute(QNetworkRequest::Http2CleartextAllowedAttribute).toBool());
                check(!request.attribute(QNetworkRequest::HttpPipeliningAllowedAttribute).toBool());
                check(!request.attribute(QNetworkRequest::CacheSaveControlAttribute).toBool());
                check(request.attribute(QNetworkRequest::CacheLoadControlAttribute).toInt()==QNetworkRequest::AlwaysNetwork);
                check(!request.hasRawHeader("Cookie") && !request.hasRawHeader("Authorization") && !request.hasRawHeader("Proxy-Authorization"));
                check(request.rawHeader("User-Agent")==QByteArray(UserAgent));
                if (returnNull) return nullptr;
                auto reply=new FakeReply(request,owner); reply->delayAbort=delayedAbort;
                replies.push_back(reply);
                if (immediatelyFinished) { reply->headers(); reply->pending="{}"; reply->stop(); }
                return reply;
            },TransportClock{[this]{return milliseconds;},false});
    }
    quint64 add(FetchSpec spec=api()) {
        auto r=transport->submit(scope,std::move(spec),[this](quint64 id,FetchResult value){ check(!done.contains(id)); done.emplace(id,std::move(value)); });
        check(bool(r)); return *r.value;
    }
    FakeReply* last() { check(!replies.empty() && replies.back()); return replies.back(); }
    FetchResult result(quint64 id) { check(done.contains(id)); return done.at(id); }
    int active() { auto s=scheduler->snapshot(); check(bool(s)); return s.value->active; }
    void ok_json() { auto r=last(); r->headers(); r->push("{}"); r->stop(); flush(); }
};
void success_and_policy() {
    Fixture f; auto id=f.add(); check(f.requests.empty()); flush();
    auto reply=f.last(); check(reply->readBufferSize()==65536);
    reply->headers(200,"application/json; charset=utf-8","4"); reply->notify_headers(); reply->push("{"); reply->push("  }");
    check(f.done.empty()); reply->stop(); check(f.done.empty()); flush();
    auto r=f.result(id); check(r.outcome==SchedulerOutcome::Completed && r.error.code==Code::None && r.bytes=="{  }" && r.mime==QStringLiteral("application/json"));
    check(f.active()==0);
    auto p=f.add(preview()); flush(); reply=f.last(); reply->headers(200,"image/png"); reply->push(png()); reply->stop(); flush();
    check(f.result(p).bytes==png());
    auto o=f.add(original()); flush(); reply=f.last(); reply->headers(200,"image/png","12"); reply->push(png()); reply->stop(); flush(); check(f.result(o).bytes==png());
    f.immediatelyFinished=true; auto immediate=f.add(); flush(); check(f.result(immediate).bytes=="{}");
    f.returnNull=true; auto null=f.add(); flush(); check(f.result(null).error.code==Code::ProviderUnavailable);
}
void class_limits() {
    for (const auto kind : {RequestClass::Api,RequestClass::Preview,RequestClass::Original}) {
        const qsizetype limit=kind==RequestClass::Api ? MaxApiBytes : kind==RequestClass::Preview ? MaxPreviewBytes : MaxFileBytes;
        for (bool excess : {false,true}) {
            Fixture f; auto spec=kind==RequestClass::Api ? api() : kind==RequestClass::Preview ? preview() : original(limit);
            auto id=f.add(spec); flush(); auto r=f.last(); r->headers(200,kind==RequestClass::Api ? QByteArray("application/json") : QByteArray("image/png"));
            r->push(kind==RequestClass::Api ? QByteArray(limit+(excess?1:0),'x') : png(limit+(excess?1:0)));
            if (!r->isFinished()) r->stop(); flush(); auto result=f.result(id);
            check(r->largestRead<=65536);
            check(excess ? (result.error.code==Code::FileTooLarge && result.bytes.isEmpty()) : (result.error.code==Code::None && result.bytes.size()==limit));
        }
    }
}
void invalid_headers_and_media() {
    struct Case {int status; QByteArray mime,length,encoding,body; Code error;};
    const std::vector<Case> cases={
        {200,"application/json","1000001",{},"{}",Code::FileTooLarge},
        {200,"application/json","-1",{},"{}",Code::FileTooLarge},
        {200,"application/json","4",{},"{}",Code::InvalidResponse},
        {200,"application/json",{},"gzip","{}",Code::UnsupportedMedia},
        {200,"text/html",{}, {},"{}",Code::InvalidResponse},
        {404,"application/json",{}, {},"{}",Code::ProviderUnavailable},
        {401,"application/json",{}, {},"{}",Code::ProviderUnavailable},
        {200,"application/json\r\nx",{}, {},"{}",Code::InvalidResponse}};
    for (const auto& c:cases) {
        Fixture f; auto id=f.add(); flush(); auto r=f.last(); r->headers(c.status,c.mime,c.length,c.encoding); r->push(c.body); if(!r->isFinished())r->stop(); flush();
        check(f.result(id).error.code==c.error && f.result(id).bytes.isEmpty());
    }
    for (bool originalKind : {false,true}) {
        Fixture f; auto id=f.add(originalKind ? original() : preview()); flush(); auto r=f.last(); r->headers(200,"image/png"); r->push("not png"); r->stop(); flush();
        check(f.result(id).error.code==Code::UnsupportedMedia);
    }
    for (int mode=0;mode<4;++mode) {
        Fixture f; auto id=f.add(); flush(); auto r=f.last(); r->headers();
        if(mode==0)r->attr(QNetworkRequest::SourceIsFromCacheAttribute,true);
        if(mode==1)r->attr(QNetworkRequest::OriginalContentLengthAttribute,99);
        if(mode==2)r->header("X-Big",QByteArray(8193,'x'));
        if(mode==3)r->url(QStringLiteral("https://evil.example/"));
        r->push("{}"); if(!r->isFinished())r->stop(); flush(); check(f.result(id).error.code!=Code::None);
    }
    Fixture changed; auto id=changed.add();flush();auto r=changed.last();r->headers();r->push("{}");r->header("Content-Type","text/html");r->stop();flush();
    check(changed.result(id).error.code==Code::InvalidResponse && changed.result(id).bytes.isEmpty());
}
void individual_header_entries() {
    // Qt's real QHttpHeaders preserves duplicate entries while rawHeaderPairs
    // joins Set-Cookie with LF. None of these synthetic values is a real cookie.
    Fixture f;auto id=f.add();flush();auto r=f.last();r->headers();
    QHttpHeaders headers;
    check(headers.append("Content-Type","application/json"));
    check(headers.append("Content-Length","2"));
    check(headers.append("Set-Cookie","fixture_one=1; Path=/; Secure; HttpOnly"));
    check(headers.append("Set-Cookie","fixture_two=2; Expires=Wed, 21 Oct 2037 07:28:00 GMT; Secure"));
    r->raw_headers(headers);
    check(r->QNetworkReply::headers().size()==4 && r->rawHeaderPairs().size()==3);
    check(r->rawHeader("Set-Cookie").contains('\n'));
    r->notify_headers();r->push("{}");r->stop();flush();
    check(f.result(id).error.code==Code::None && f.result(id).bytes=="{}");
    const auto again=f.add();flush();
    check(f.requests.size()==2 && !f.requests.back().hasRawHeader("Cookie"));
    f.ok_json();check(f.result(again).error.code==Code::None);

    // Redirect chains use newly constructed controlled requests, never cookies
    // returned by a previous hop. Manual load/save attributes are also asserted
    // on every request by Fixture's factory; production EmptyCookieJar is kept.
    const auto redirected=f.add();flush();r=f.last();r->headers(302);
    check(headers.append("Location","/w/api.php"));r->raw_headers(headers);r->notify_headers();flush();
    check(f.requests.size()==4 && !f.requests.back().hasRawHeader("Cookie"));
    f.ok_json();check(f.result(redirected).error.code==Code::None);

    const std::vector<std::pair<QByteArray,QByteArray>> critical={
        {"Content-Type","application/json"},{"Content-Length","2"},
        {"Content-Encoding","identity"},{"Location","/w/api.php"},{"Retry-After","5"}};
    for(const auto& [name,value]:critical) {
        Fixture duplicate;const auto ticket=duplicate.add();flush();auto reply=duplicate.last();reply->headers();
        QHttpHeaders repeated;
        if(name!="Content-Type")check(repeated.append("Content-Type","application/json"));
        check(repeated.append(name,value));check(repeated.append(name.toLower(),value));
        reply->raw_headers(repeated);reply->push("{}");if(!reply->isFinished())reply->stop();flush();
        check(duplicate.result(ticket).error.code==Code::InvalidResponse && duplicate.result(ticket).bytes.isEmpty());
    }
    for(int count:{128,129}) {
        Fixture capped;const auto ticket=capped.add();flush();auto reply=capped.last();reply->headers();QHttpHeaders repeated;
        check(repeated.append("Content-Type","application/json"));
        for(int i=1;i<count;++i)check(repeated.append("X-Count","x"));
        reply->raw_headers(repeated);check(reply->QNetworkReply::headers().size()==count);
        check(reply->rawHeaderPairs().size()==2); // old audit could not count entries
        reply->push("{}");if(!reply->isFinished())reply->stop();flush();
        check(capped.result(ticket).error.code==(count==128?Code::None:Code::InvalidResponse));
    }
    for(int count:{2,8}) {
        Fixture capped;const auto ticket=capped.add();flush();auto reply=capped.last();reply->headers();QHttpHeaders repeated;
        check(repeated.append("Content-Type","application/json"));
        const QByteArray value(count==2?5000:8192,'x');
        for(int i=0;i<count;++i)check(repeated.append("X-Bytes",value));
        reply->raw_headers(repeated);reply->push("{}");if(!reply->isFinished())reply->stop();flush();
        check(capped.result(ticket).error.code==(count==2?Code::None:Code::InvalidResponse));
    }
    Fixture changed;id=changed.add();flush();r=changed.last();r->headers();r->push("{");
    QHttpHeaders mutated=r->QNetworkReply::headers();check(mutated.append("content-type","application/json"));r->raw_headers(mutated);r->stop();flush();
    check(changed.result(id).error.code==Code::InvalidResponse && changed.result(id).bytes.isEmpty());
}
void redirects() {
    const std::vector<QByteArray> good={"Next.png","/wikipedia/commons/a/ab/Next.png","https://thumb.wikimedia.org/wikipedia/commons/a/ab/Next.png",
        "//upload.wikimedia.org/wikipedia/commons/a/ab/Next.png","Space%20name.png","Hash%23name.png","Percent%25name.png"};
    for(const auto& target:good) {
        Fixture f; auto id=f.add(preview()); flush(); auto first=f.last(); first->headers(302); first->header("Location",target); first->notify_headers(); flush();
        check(f.requests.size()==2 && f.active()==1); deletions(); check(f.active()==1);
        auto second=f.last(); second->headers(200,"image/png"); second->push(png()); second->stop(); flush(); check(f.result(id).bytes==png());
    }
    const std::vector<QByteArray> bad={"../Next.png","./Next.png","%2e%2e/Next.png","/wikipedia/commons/a/../Next.png","%FF.png","%xx.png","Bad%.png",
        QByteArray("Bad\xff.png"),"https://evil.example/Next.png","http://upload.wikimedia.org/wikipedia/commons/a.png",
        "https://user@upload.wikimedia.org/wikipedia/commons/a.png","//upload.wikimedia.org.evil.example/wikipedia/commons/a.png",
        "Next.png#","Next.png?x=1","Next%00.png","Next%5c.png","Next.png\r\nX:bad","Bad Name.png",
        "//\xef\xbd\x95pload.wikimedia.org/wikipedia/commons/a.png"};
    for(const auto& target:bad) {
        Fixture f; auto id=f.add(preview()); flush(); auto r=f.last(); r->headers(302); r->header("Location",target); r->notify_headers(); flush();
        check(f.requests.size()==1 && (f.result(id).error.code==Code::InvalidRemoteUrl || f.result(id).error.code==Code::InvalidResponse) && f.result(id).bytes.isEmpty());
    }
    Fixture f; auto id=f.add(); flush();
    for(int i=0;i<6;++i) {auto r=f.last();r->headers(302);r->header("Location","/w/api.php");r->notify_headers();flush();}
    check(f.requests.size()==6 && f.result(id).error.code==Code::InvalidRemoteUrl);
    Fixture null;id=null.add(preview());flush();null.returnNull=true;
    auto old=null.last();old->headers(302);old->header("Location","Next.png");old->notify_headers();flush();
    check(null.result(id).error.code==Code::ProviderUnavailable && null.active()==0);
    Fixture expired;id=expired.add(preview());flush();expired.milliseconds=21000;
    old=expired.last();old->headers(302);old->header("Location","Next.png");old->notify_headers();flush();
    check(expired.result(id).error.code==Code::ProviderUnavailable && expired.active()==0 && expired.requests.size()==1);
}
void cooldown_and_deadline() {
    for (const QByteArray retry : {QByteArray("12"),QByteArray("999999999999")}) {
        Fixture f; auto id=f.add(); flush(); auto r=f.last();r->headers(429);r->header("Retry-After",retry);r->notify_headers();flush();
        auto result=f.result(id);check(result.error.code==Code::ProviderBusy);
        check(retry=="12" ? result.error.retryAfterSeconds==12 : !result.error.retryAfterSeconds);
        auto later=f.transport->submit(f.scope,api(),[](quint64,FetchResult){});check(!later && later.error.code==Code::ProviderBusy);
        check(f.scheduler->snapshot().value->retryDeadline==(retry=="12" ? 13.0 : 1000000000000.0));
    }
    Fixture jumped;auto jumpId=jumped.add();flush();int reads=0;
    jumped.schedulerTime=[&reads]{return ++reads==1 ? 1. : 10.;};
    auto jumpReply=jumped.last();jumpReply->headers(503);jumpReply->header("Retry-After","5");jumpReply->notify_headers();flush();
    check(jumped.result(jumpId).error.code==Code::ProviderBusy && jumped.result(jumpId).bytes.isEmpty());
    Fixture f; f.delayedAbort=true; auto id=f.add();flush();auto r=f.last();r->headers();r->push("{");
    f.milliseconds=20999;check(f.transport->poll());flush();check(!r->aborted);
    f.milliseconds=21000;check(f.transport->poll());flush();check(r->aborted && f.active()==1 && f.done.empty());
    r->stop(QNetworkReply::OperationCanceledError);flush();check(f.result(id).error.code==Code::ProviderUnavailable && f.active()==0);
    Fixture chain;auto ticket=chain.add(preview());flush();chain.milliseconds=20999;
    auto old=chain.last();old->headers(302);old->header("Location","Next.png");old->notify_headers();flush();check(chain.requests.size()==2);
    chain.milliseconds=21000;auto next=chain.last();next->headers(200,"image/png");next->push(png());flush();
    check(chain.result(ticket).error.code==Code::ProviderUnavailable);
}
void actual_total_timer() {
    ProviderScheduler scheduler;
    QPointer<FakeReply> reply;
    MediaTransport transport(&scheduler,nullptr,[&reply](const QNetworkRequest& request,QObject* owner){reply=new FakeReply(request,owner);return reply.data();});
    QEventLoop loop;bool failed=false,timedOut=false;QElapsedTimer elapsed;elapsed.start();
    auto accepted=transport.submit(*scheduler.open_lane().value,api(),[&](quint64,FetchResult result){failed=result.error.code==Code::ProviderUnavailable && result.bytes.isEmpty();loop.quit();});
    check(bool(accepted));
    QTimer watchdog;watchdog.setSingleShot(true);QObject::connect(&watchdog,&QTimer::timeout,&loop,[&]{timedOut=true;loop.quit();});
    watchdog.start(25000);loop.exec();watchdog.stop();
    check(failed && !timedOut && elapsed.elapsed()>=19500 && elapsed.elapsed()<25000);
    check(scheduler.snapshot().value->active==0);
}
void cancellation_and_stale() {
    Fixture f; f.delayedAbort=true;auto a=f.add(),b=f.add(),c=f.add();flush();check(f.requests.size()==2);
    check(f.transport->cancel(a));flush();check(f.replies[0]->aborted && f.active()==2 && f.requests.size()==2 && !f.done.contains(a));
    f.replies[0]->stop(QNetworkReply::OperationCanceledError);flush();check(f.requests.size()==3 && f.result(a).outcome==SchedulerOutcome::Cancelled);
    check(f.transport->cancel(c));flush();delete f.replies[2].data();flush();check(f.result(c).outcome==SchedulerOutcome::Cancelled);
    auto r=f.replies[1];r->headers();r->push("{}");r->stop();check(bool(f.scheduler->advance(f.scope)));flush();
    check(f.result(b).outcome==SchedulerOutcome::StaleGeneration && f.result(b).bytes.isEmpty());
    Fixture done; auto ticket=done.add();flush();r=done.last();r->headers();r->push("{}");r->stop();check(done.transport->cancel(ticket));flush();
    check(done.result(ticket).outcome==SchedulerOutcome::Cancelled && done.result(ticket).bytes.isEmpty());
}
void lifecycle_and_errors() {
    Fixture f;auto id=f.add();flush();delete f.last();flush();check(f.result(id).error.code==Code::ProviderUnavailable && f.active()==0);
    Fixture tls;id=tls.add();flush();tls.last()->tls_error();flush();check(tls.result(id).error.code==Code::ProviderUnavailable);
    Fixture auth;id=auth.add();flush();auth.last()->headers(401);auth.last()->stop(QNetworkReply::AuthenticationRequiredError);flush();check(auth.result(id).error.code==Code::ProviderUnavailable);
    Fixture dying;dying.delayedAbort=true;auto a=dying.add(),b=dying.add(),c=dying.add();flush();dying.scheduler.reset();flush();
    check(dying.result(c).error.code==Code::ProviderUnavailable && dying.done.size()==1);
    dying.replies[0]->stop(QNetworkReply::OperationCanceledError);delete dying.replies[1].data();flush();
    check(dying.result(a).error.code==Code::ProviderUnavailable && dying.result(b).error.code==Code::ProviderUnavailable && dying.done.size()==3);
    Fixture destructor;destructor.delayedAbort=true;destructor.add();flush();destructor.transport.reset();flush();check(destructor.active()==0 && destructor.done.empty());
    Fixture callback;QPointer<MediaTransport> weak=callback.transport.get();
    auto accepted=callback.transport->submit(callback.scope,api(),[&callback](quint64,FetchResult r){check(r.bytes=="{}");callback.transport.reset();});
    check(bool(accepted));flush();callback.ok_json();check(!weak);
    Fixture pending;id=pending.add();flush();auto finished=pending.last();finished->headers();finished->push("{}");finished->stop();pending.scheduler.reset();flush();
    check(pending.result(id).error.code==Code::ProviderUnavailable && pending.result(id).bytes.isEmpty());
    for(int mode=0;mode<3;++mode) {
        Fixture destruction;auto* raw=destruction.transport.get();bool observed=false;
        QObject::connect(raw,&QObject::destroyed,[raw,&observed]{QCoreApplication::sendPostedEvents(raw,QEvent::MetaCall);observed=true;});
        if(mode==0)check(raw->poll());
        else if(mode==1) {destruction.add();destruction.scheduler.reset();}
        else {
            destruction.immediatelyFinished=true;destruction.add();
            check(QMetaObject::invokeMethod(destruction.scheduler->findChild<QTimer*>(),"timeout",Qt::DirectConnection));
        }
        destruction.transport.reset();flush();check(observed && destruction.done.empty());
    }
}
void shared_scheduler_and_shutdown() {
    Fixture f; std::vector<QPointer<FakeReply>> replies;int secondDone=0;
    MediaTransport second(f.scheduler.get(),nullptr,[&](const QNetworkRequest& request,QObject* owner){auto r=new FakeReply(request,owner);replies.push_back(r);return r;},TransportClock{[&]{return f.milliseconds;},false});
    const auto a=f.add(), b=f.add();
    auto c=second.submit(f.scope,preview(),[&](quint64,FetchResult r){++secondDone;check(r.bytes==png());});check(bool(c));flush();
    check(f.active()==2 && replies.empty());check(f.transport->shutdown());flush();
    check(f.result(a).outcome==SchedulerOutcome::Cancelled && f.result(b).outcome==SchedulerOutcome::Cancelled);
    check(replies.size()==1 && f.active()==1);
    replies[0]->headers(200,"image/png");replies[0]->push(png());replies[0]->stop();flush();check(secondDone==1 && f.active()==0);
    check(!f.transport->submit(f.scope,api(),[](quint64,FetchResult){}));
}
void invalid_input_and_thread() {
    Fixture f; for(auto spec : {original(),preview(),api()}) {
        if(spec.kind==RequestClass::Original)spec.expected.reset();else spec.expected=PublicMetadata{};
        check(!f.transport->submit(f.scope,spec,[](quint64,FetchResult){}));
    }
    auto spec=original();spec.expected->bytes=MaxFileBytes+1;check(!f.transport->submit(f.scope,spec,[](quint64,FetchResult){}));
    check(!f.transport->submit(f.scope,api(),{}));
    std::atomic<bool> rejected=false;
    std::thread other([&] {rejected=!f.transport->submit(f.scope,api(),[](quint64,FetchResult){}) && !f.transport->cancel(1) && !f.transport->poll() && !f.transport->shutdown();});
    other.join();check(rejected && f.requests.empty());
    MediaTransport production(f.scheduler.get());check(!production.submit(f.scope,api(),[](quint64,FetchResult){}));
}
void proxy_policy() {
    check(!select_media_proxy({}));
    QNetworkProxy proxy(QNetworkProxy::HttpProxy,QStringLiteral("proxy.example"),8080,QStringLiteral("synthetic-user"),QStringLiteral("synthetic-password"));
    proxy.setRawHeader("Proxy-Authorization","synthetic-value");
    auto r=select_media_proxy({proxy,QNetworkProxy(QNetworkProxy::NoProxy)});check(bool(r));
    check(r.value->hostName()==QStringLiteral("proxy.example") && r.value->port()==8080 && r.value->user().isEmpty() && r.value->password().isEmpty() && r.value->rawHeaderList().isEmpty());
    for(const auto type:{QNetworkProxy::DefaultProxy,QNetworkProxy::HttpCachingProxy,QNetworkProxy::FtpCachingProxy})check(!select_media_proxy({QNetworkProxy(type),QNetworkProxy(QNetworkProxy::NoProxy)}));
    check(bool(select_media_proxy({QNetworkProxy(QNetworkProxy::NoProxy)})));
    for(const auto host:{QStringLiteral(""),QStringLiteral("bad@host"),QStringLiteral("http://proxy"),QStringLiteral("proxy/path"),QStringLiteral("bad host"),QStringLiteral("a..b")})
        check(!select_media_proxy({QNetworkProxy(QNetworkProxy::Socks5Proxy,host,1080),QNetworkProxy(QNetworkProxy::NoProxy)}));
    check(bool(select_media_proxy({QNetworkProxy(QNetworkProxy::Socks5Proxy,QStringLiteral("::1"),1080)})));
    check(bool(select_media_proxy({QNetworkProxy(QNetworkProxy::Socks5Proxy,QStringLiteral("прокси.example"),1080)})));
}
}
int main(int argc,char**argv) {
    QCoreApplication app(argc,argv);
    success_and_policy();class_limits();invalid_headers_and_media();individual_header_entries();redirects();cooldown_and_deadline();cancellation_and_stale();lifecycle_and_errors();shared_scheduler_and_shutdown();invalid_input_and_thread();proxy_policy();
    actual_total_timer();
    deletions();
    std::cout << "{\"ok\":true,\"checks\":" << checks << ",\"realHttp\":false,\"syntheticReplies\":true,\"userProfiles\":false,\"individualHeaders\":true,\"criticalDuplicateCases\":5,\"cookiesReused\":false}\n";
    return 0;
}
