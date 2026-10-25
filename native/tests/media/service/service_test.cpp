#include "service_host.h"
#include <QBuffer>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QFile>
#include <QDir>
#include <QGuiApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QNetworkAccessManager>
#include <QPluginLoader>
#include <QQmlEngine>
#include <QTextStream>
#include <QThread>
#include <QTimer>
#include <QUrl>
#include <atomic>
#include <thread>
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
using namespace valmax::media;
namespace {
std::atomic<int> checks{0},requests{0},activeReplies{0},maxReplies{0},decodeCount{0},ioStages{0};
std::atomic<bool> holdReply{false},holdAbort{false},holdDecode{false},holdIo{false},holdRetain{false},inIo{false},inRetain{false};
std::atomic<bool> holdFactory{false},inFactory{false};
std::atomic<double> clockSeconds{100.};
QThread* gui=nullptr;
void check(bool value,const char* message){++checks;if(!value){QTextStream(stderr)<<"FAIL: "<<message<<'\n';std::_Exit(1);}}
void events(){QCoreApplication::processEvents(QEventLoop::AllEvents,5);QCoreApplication::sendPostedEvents(nullptr,QEvent::DeferredDelete);}
template<class F>void until(F predicate,const char* message){QElapsedTimer timer;timer.start();while(!predicate()&&timer.elapsed()<7000){events();QThread::msleep(1);}check(predicate(),message);}
void wait_atomic(const std::atomic<bool>& flag){QElapsedTimer timer;timer.start();while(flag&&timer.elapsed()<6000)QThread::msleep(1);check(!flag,"worker test barrier released");}
QByteArray png;
QByteArray response() {
    QJsonObject info{{"mime","image/png"},{"size",png.size()},{"width",48},{"height",32},
        {"url","https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic.png"},
        {"thumburl","https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Synthetic.png/240px-Synthetic.png"},
        {"descriptionurl","https://commons.wikimedia.org/wiki/File:Synthetic.png"},
        {"extmetadata",QJsonObject{{"Artist",QJsonObject{{"value","Fixture author"}}},{"LicenseShortName",QJsonObject{{"value","CC0"}}}}}};
    return QJsonDocument(QJsonObject{{"query",QJsonObject{{"pages",QJsonArray{QJsonObject{{"title","File:Synthetic.png"},{"imageinfo",QJsonArray{info}}}}}}}}).toJson(QJsonDocument::Compact);
}
class Reply final:public QNetworkReply {
    QByteArray bytes_;qint64 offset_=0;bool done_=false,aborted_=false;QTimer timer_;
public:
    Reply(const QNetworkRequest& request,QObject* parent):QNetworkReply(parent) {
        check(QThread::currentThread()!=gui,"network factory uses service thread");++requests;
        const auto active=++activeReplies;maxReplies.store(std::max(maxReplies.load(),active));
        const bool api=request.url().host()==QStringLiteral("commons.wikimedia.org");bytes_=api?response():png;
        setRequest(request);setUrl(request.url());setOperation(QNetworkAccessManager::GetOperation);
        setAttribute(QNetworkRequest::HttpStatusCodeAttribute,200);setHeader(QNetworkRequest::ContentTypeHeader,api?QStringLiteral("application/json"):QStringLiteral("image/png"));
        setHeader(QNetworkRequest::ContentLengthHeader,bytes_.size());open(QIODevice::ReadOnly|QIODevice::Unbuffered);
        timer_.setInterval(1);connect(&timer_,&QTimer::timeout,this,[this]{if(aborted_?!holdAbort.load():!holdReply.load())finish();});timer_.start();
    }
    ~Reply()override{if(!done_)--activeReplies;}
    void abort()override{aborted_=true;if(!holdAbort)finish();}
    qint64 bytesAvailable()const override{return bytes_.size()-offset_+QNetworkReply::bytesAvailable();}
protected:
    qint64 readData(char* data,qint64 length)override{const auto count=std::min(length,qint64(bytes_.size())-offset_);if(count<=0)return -1;memcpy(data,bytes_.constData()+offset_,size_t(count));offset_+=count;return count;}
private:
    void finish(){if(done_)return;done_=true;--activeReplies;timer_.stop();if(aborted_)setError(OperationCanceledError,QStringLiteral("synthetic cancellation"));setFinished(true);emit metaDataChanged();if(!aborted_)emit readyRead();emit finished();}
};
uint32_t stage(ValmaxMediaStagingHandle h,const ValmaxMediaStageRequest* r,const uint8_t* b,size_t n,ValmaxMediaStageResult* o) {
    check(QThread::currentThread()!=gui,"filesystem stage not GUI");++ioStages;inIo=true;wait_atomic(holdIo);inIo=false;
    return valmax_media_staging_stage(h,r,b,n,o);
}
uint32_t retain(ValmaxMediaStagingHandle h) {
    check(QThread::currentThread()!=gui,"filesystem retain not GUI");inRetain=true;wait_atomic(holdRetain);inRetain=false;
    return valmax_media_staging_retain(h);
}
ServiceConfig config(const QString& root) {
    ServiceConfig c;c.explicitStagingRoot=root.toStdWString();
    c.staging={valmax_media_staging_abi_version,valmax_media_staging_begin,stage,valmax_media_staging_signal_cancel,valmax_media_staging_cancel,retain,valmax_media_staging_release,valmax_core_buffer_free};
    c.replyFactory=[](const QNetworkRequest& r,QObject* owner){inFactory=true;wait_atomic(holdFactory);inFactory=false;return new Reply(r,owner);};
    c.controllerClock.monotonicSeconds=[] { return clockSeconds.load(); };
    c.decoder=[](const QByteArray& b,const QString& m,QSize box){check(QThread::currentThread()!=gui,"codec not GUI");++decodeCount;wait_atomic(holdDecode);return decode_thumbnail(b,m,box);};
    return c;
}
QByteArray selection(const QString& id){return QJsonDocument(QJsonObject{{"id",id}}).toJson(QJsonDocument::Compact);}
QString search(MediaServiceHost* host,ServicePage page) {
    clockSeconds.fetch_add(3.);bool done=false;QString id;
    const auto accepted=host->search(page,R"({"mode":"pic","query":"fixture"})",[&](quint64,ServiceResult<SearchRows> result){check(QThread::currentThread()==gui,"typed callback on GUI");check(bool(result),"search callback success");check(result.lease.valid()&&result.generation.valid()&&result.stamp.generation>0,"search provenance");id=result.value->at(0)[QStringLiteral("id")].toString();done=true;});
    check(bool(accepted)&&accepted.lease.valid()&&accepted.generation.valid(),"search admission returns generation");until([&]{return done;},"search completed");return id;
}
void stop(MediaServiceHost*& host) {
    bool drained=false;QObject::connect(host,&MediaServiceHost::drained,[&]{drained=true;});check(host->shutdown(),"shutdown accepted");
    until([&]{return drained;},"asynchronous drain completed");check(host->snapshot().value->drained,"drained snapshot");
    delete host;host=nullptr;until([]{return MediaServiceHost::process_available();},"process slot released only after workers stopped");
}
void test(const QString& root) {
    QImage image(48,32,QImage::Format_ARGB32);image.fill(Qt::green);QBuffer buffer(&png);check(buffer.open(QIODevice::WriteOnly)&&image.save(&buffer,"PNG"),"synthetic PNG created");
    QQmlEngine engine;auto* context=MediaEngineContext::install(&engine);check(context!=nullptr,"actual engine context installed");
    auto lease=context->bind(101,1);auto created=MediaServiceHost::create(config(root));check(bool(created),"process host created");auto* host=*created.value;
    check(!MediaServiceHost::create(config(root)),"duplicate process host refused");
    const auto bound=host->bind(lease);check(bool(bound),"page bound");const auto page=*bound.value;
    bool rejectedCallback=false;check(!host->search(page,"{}",[&](quint64,auto){rejectedCallback=true;}),"invalid payload rejected immediately");events();check(!rejectedCallback,"rejected admission no callback");
    bool wrongThread=false;std::thread wrong([&]{wrongThread=!host->snapshot();});wrong.join();check(wrongThread,"wrong-thread method rejected");
    auto id=search(host,page);
    bool previewDone=false;const auto preview=host->preview(page,id,[&](quint64,ServiceResult<QImage> result){check(bool(result),"preview decoded");check(host->snapshot().value->pendingImages<=2,"GUI images bounded");check(context->current(result.lease)&&result.generation.valid(),"preview GUI guards");const auto published=context->publish(result.lease,*result.value);check(bool(published),"preview published into actual engine store");previewDone=true;});
    check(bool(preview),"preview admitted");until([&]{return previewDone;},"preview complete");until([&]{return host->snapshot().value->pendingImages==0;},"preview credit returned after callback");
    bool selectedDone=false;quint64 selectedTicket=0;QString path;
    const auto selected=host->select(page,selection(id),[&](quint64 ticket,ServiceResult<RetainedMediaFile> result){
        check(bool(result),"selected staged and retained");check(result.generation.valid()&&context->current(result.lease),"selected GUI guards");
        check(result.value->mime==QStringLiteral("image/png")&&result.value->caption.contains(QStringLiteral("Fixture author")),"validated MIME and attribution preserved");
        path=QUrl(QString::fromStdString(result.value->fileUrl)).toLocalFile();QFile file(path);check(file.open(QIODevice::ReadOnly)&&file.readAll()==png,"retained file equals selected original");
        selectedTicket=ticket;selectedDone=true;
    });
    check(bool(selected),"selection admitted");check(host->select(page,selection(id),[](quint64,auto){}).error.code==ServiceCode::DownloadBusy,"second selection rejected globally");
    until([&]{return selectedDone;},"selected callback complete");check(host->snapshot().value->selectedBusy,"gate held through GUI dispatch boundary");
    check(host->complete_selected(selectedTicket)&&!host->complete_selected(selectedTicket),"selected terminal exactly once");until([&]{return !host->snapshot().value->selectedBusy;},"gate returned after GUI terminal");check(QFile::exists(path),"TTL ownership survives terminal");

    bool chainDone=false;
    check(bool(host->select(page,selection(id),[&](quint64 first,auto firstResult){
        check(bool(firstResult)&&host->complete_selected(first),"reentrant chain first terminal");
        const auto next=host->select(page,selection(id),[&](quint64 second,auto secondResult){
            check(bool(secondResult)&&host->snapshot().value->selectedBusy,"old worker ACK did not release new selected gate");
            check(host->complete_selected(second),"reentrant chain second terminal");chainDone=true;
        });
        check(bool(next),"terminal ACK allows immediate reentrant select");
    })),"reentrant chain admitted");
    until([&]{return chainDone;},"reentrant selected chain finishes");

    // Decode remains charged while stale. Advance GUI generation before the
    // service processes the next search; no old pixels may be delivered.
    holdDecode=true;const auto decodedBefore=decodeCount.load();bool staleDone=false;
    check(bool(host->preview(page,id,[&](quint64,ServiceResult<QImage> result){check(!result&&(!result.generation.valid()||result.error.code==ServiceCode::Cancelled),"stale decoded pixels suppressed");staleDone=true;})),"slow preview admitted");
    until([&]{return decodeCount>decodedBefore;},"decoder barrier entered");id=search(host,page);holdDecode=false;until([&]{return staleDone;},"stale preview terminal");

    // Only two codec/delivery credits are allowed even with eight queued rows.
    holdDecode=true;const auto before=decodeCount.load();int previews=0;
    for(int i=0;i<8;++i)check(bool(host->preview(page,id,[&](quint64,auto){++previews;})),"bounded preview admitted");
    check(host->preview(page,id,[](quint64,auto){}).error.code==ServiceCode::Capacity,"ninth preview refused");
    until([&]{return decodeCount>=before+2;},"two codecs active");for(int i=0;i<15;++i){events();QThread::msleep(1);}
    check(decodeCount==before+2&&host->snapshot().value->pendingImages==2,"decoded queue cannot exceed two credits");holdDecode=false;
    until([&]{return previews==8;},"all eight previews finish");until([&]{return host->snapshot().value->pendingImages==0;},"all image credits returned");check(maxReplies<=2,"one shared provider scheduler limits active HTTP to two");

    holdIo=true;bool cancelDone=false;const auto cancelled=host->select(page,selection(id),[&](quint64,ServiceResult<RetainedMediaFile> result){check(!result,"cancelled staging never publishes URL");cancelDone=true;});check(bool(cancelled),"stage cancellation admitted");
    until([]{return inIo.load();},"I/O barrier entered");check(host->cancel(*cancelled.value),"cancel running I/O signalled");
    check(host->snapshot().value->selectedBusy,"cancel retains selected gate until stage stops");holdIo=false;until([&]{return cancelDone;},"cancel terminal after I/O stops");until([&]{return !host->snapshot().value->selectedBusy;},"cancelled gate released");

    holdRetain=true;bool lateDone=false;check(bool(host->select(page,selection(id),[&](quint64,ServiceResult<RetainedMediaFile> result){check(!result&&!result.generation.valid(),"new search suppresses retain race delivery");lateDone=true;})),"retained race admitted");
    until([]{return inRetain.load();},"retain barrier reached");id=search(host,page);holdRetain=false;until([&]{return lateDone;},"retain race terminal");until([&]{return !host->snapshot().value->selectedBusy;},"retain race gate returned");

    holdReply=true;holdAbort=true;bool pendingDone=false;
    const auto pending=host->preview(page,id,[&](quint64,auto){pendingDone=true;});check(bool(pending),"held network preview accepted");until([]{return activeReplies>0;},"held reply started");
    check(host->shutdown(),"shutdown starts without blocking");check(!host->snapshot().value->drained,"drain waits for reply stopped");check(!MediaServiceHost::create(config(root)),"second instance blocked while draining");
    holdReply=false;holdAbort=false;until([&]{return host->snapshot().value->drained;},"held network eventually drains");check(!pendingDone,"shutdown suppresses callbacks");delete host;host=nullptr;until([]{return MediaServiceHost::process_available();},"draining run released");

    // Destructor is also asynchronous; its internal run survives I/O without
    // retaining a GUI pointer or releasing the process reservation early.
    created=MediaServiceHost::create(config(root));host=*created.value;const auto page2=*host->bind(lease).value;id=search(host,page2);
    holdIo=true;bool deletedCallback=false;check(bool(host->select(page2,selection(id),[&](quint64,auto){deletedCallback=true;})),"destructor operation accepted");until([]{return inIo.load();},"destructor I/O running");
    QElapsedTimer elapsed;elapsed.start();delete host;host=nullptr;check(elapsed.elapsed()<1000,"facade destructor does not join I/O");check(!MediaServiceHost::process_available(),"orphan run keeps global reservation");holdIo=false;
    until([]{return MediaServiceHost::process_available();},"orphan run drains by itself");check(!deletedCallback,"dead facade callback suppressed");

    created=MediaServiceHost::create(config(root));host=*created.value;const auto page3=*host->bind(lease).value;id=search(host,page3);
    bool reentrant=false;check(bool(host->select(page3,selection(id),[&](quint64 ticket,auto result){check(bool(result),"reentrant selected success");check(host->complete_selected(ticket),"selected ACK reentrant");delete host;host=nullptr;reentrant=true;})),"reentrant selection admitted");
    until([&]{return reentrant&&MediaServiceHost::process_available();},"callback may ACK and delete facade");
    created=MediaServiceHost::create(config(root));host=*created.value;
    const auto floodPage=*host->bind(lease).value;holdFactory=true;clockSeconds.fetch_add(3.);bool floodDone=false;
    const auto flood=host->search(floodPage,R"({"mode":"pic","query":"fixture"})",[&](quint64,auto){floodDone=true;});check(bool(flood),"command bound search admitted");
    until([]{return inFactory.load();},"service owner temporarily blocked in trusted test factory");
    int admittedBinds=0;
    for(int i=0;i<100;++i){const auto p=host->bind(lease);if(p){++admittedBinds;check(host->close(*p.value),"bound page revoked immediately");}else check(p.error.code==ServiceCode::Capacity,"pending binds fail finite at bound");}
    check(admittedBinds==MediaServiceHost::MaxPages,"bind-close loop cannot enqueue unbounded page commands");
    for(int i=0;i<1000;++i)host->cancel(*flood.value);
    holdFactory=false;until([&]{return floodDone;},"bounded command queue drains after factory returns");stop(host);
}
}
int main(int argc,char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    const auto exe=QFileInfo(QString::fromLocal8Bit(argv[0])).absolutePath();QPluginLoader platform(exe+QStringLiteral("/plugins/platforms/qwindows.dll"));if(!platform.load())return 6;
    QGuiApplication app(argc,argv);gui=app.thread();app.setOrganizationName(QStringLiteral("ValMAXSynthetic"));app.setApplicationName(QStringLiteral("NativeServiceTest"));
    QCoreApplication::setLibraryPaths({exe+QStringLiteral("/plugins")});
    const auto root=qEnvironmentVariable("VALMAX_SYNTHETIC_STAGING_ROOT");check(!root.isEmpty()&&QFileInfo(root).isDir(),"explicit owned synthetic root supplied");
    test(root);check(activeReplies==0,"all fake replies stopped");
    QTextStream(stdout)<<QJsonDocument(QJsonObject{{"passed",true},{"checks",checks.load()},{"fakeHttpReplies",requests.load()},{"maximumActiveReplies",maxReplies.load()},{"realPngDecodes",decodeCount.load()},{"realStagingCalls",ioStages.load()},{"noLiveNetwork",true},{"noRealProfiles",true},{"allThreadsDrained",MediaServiceHost::process_available()}}).toJson(QJsonDocument::Compact)<<'\n';return 0;
}
