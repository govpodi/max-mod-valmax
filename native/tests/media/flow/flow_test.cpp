// Synthetic integration only. No production plugin registration or live HTTP.
#include "controller.h"
#include "decode_queue.h"
#include "preview_provider.h"
#include <QBuffer>
#include <QColorSpace>
#include <QElapsedTimer>
#include <QEvent>
#include <QEventLoop>
#include <QFileInfo>
#include <QGuiApplication>
#include <QHttpHeaders>
#include <QJsonArray>
#include <QJsonDocument>
#include <QPluginLoader>
#include <QPointer>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QSemaphore>
#include <QTextStream>
#include <QThread>
#include <QTimer>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <deque>
#include <future>
#include <memory>
#include <type_traits>
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
using namespace valmax::media;
namespace {
std::atomic<int> checks{0};
void check(bool ok,const char* name) {
    ++checks;
    if(!ok){QTextStream(stderr)<<"FAILED: "<<name<<'\n';std::_Exit(1);}
}
void events(){QCoreApplication::processEvents(QEventLoop::AllEvents,10);QCoreApplication::sendPostedEvents(nullptr,QEvent::DeferredDelete);}
template<class F>void until(F predicate,const char* name) {
    QElapsedTimer timer;timer.start();
    while(!predicate()&&timer.elapsed()<5000){events();QThread::msleep(1);}
    check(predicate(),name);
}
template<class T>T await(std::future<T>& result,const char* name) {
    until([&]{return result.wait_for(std::chrono::seconds(0))==std::future_status::ready;},name);
    return result.get();
}
QByteArray make_png() {
    QImage image(96,48,QImage::Format_ARGB32);image.fill(QColor(70,120,180,255));
    image.setText(QStringLiteral("Comment"),QStringLiteral("synthetic metadata must not reach preview pixels"));
    QByteArray bytes;QBuffer buffer(&bytes);
    check(buffer.open(QIODevice::WriteOnly)&&image.save(&buffer,"PNG"),"encode synthetic PNG in memory");return bytes;
}
QByteArray api(const QByteArray& bytes) {
    QJsonObject info{{"mime","image/png"},{"size",bytes.size()},{"width",96},{"height",48},
        {"url","https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic.png"},
        {"thumburl","https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Synthetic.png/240px-Synthetic.png"},
        {"descriptionurl","https://commons.wikimedia.org/wiki/File:Synthetic.png"},
        {"extmetadata",QJsonObject{{"Artist",QJsonObject{{"value","<b>Fixture author</b>"}}},{"LicenseShortName",QJsonObject{{"value","CC0"}}}}}};
    return QJsonDocument(QJsonObject{{"query",QJsonObject{{"pages",QJsonArray{QJsonObject{{"title","File:Synthetic.png"},{"imageinfo",QJsonArray{info}}}}}}}}).toJson(QJsonDocument::Compact);
}
QByteArray selection(const QString& id){return QJsonDocument(QJsonObject{{"id",id}}).toJson(QJsonDocument::Compact);}
struct Plan{QByteArray bytes;QString mime;bool hold=false;};
class FakeReply final:public QNetworkReply {
    QByteArray bytes_;qint64 offset_=0;bool done_=false;
public:
    FakeReply(const QNetworkRequest& request,QObject* parent,Plan plan):QNetworkReply(parent),bytes_(std::move(plan.bytes)) {
        setRequest(request);setUrl(request.url());setOperation(QNetworkAccessManager::GetOperation);
        setAttribute(QNetworkRequest::HttpStatusCodeAttribute,200);
        setHeader(QNetworkRequest::ContentTypeHeader,plan.mime);setHeader(QNetworkRequest::ContentLengthHeader,bytes_.size());
        auto individual=QNetworkReply::headers();
        check(individual.append("Set-Cookie","flow_fixture_one=1; Secure; HttpOnly"),"first synthetic response cookie");
        check(individual.append("Set-Cookie","flow_fixture_two=2; Secure; HttpOnly"),"second synthetic response cookie");
        setHeaders(individual);
        check(rawHeader("Set-Cookie").contains('\n'),"Qt aggregates cookie entries in raw representation");
        open(QIODevice::ReadOnly|QIODevice::Unbuffered);
        if(!plan.hold)QTimer::singleShot(0,this,[this]{finish();});
    }
    void finish(){if(done_)return;done_=true;setFinished(true);QPointer<FakeReply> alive(this);emit metaDataChanged();if(!alive)return;emit readyRead();if(alive)emit finished();}
    void abort()override{setError(QNetworkReply::OperationCanceledError,QString());QTimer::singleShot(0,this,[this]{finish();});}
    bool isSequential()const override{return true;}
    qint64 bytesAvailable()const override{return bytes_.size()-offset_+QNetworkReply::bytesAvailable();}
protected:
    qint64 readData(char* dest,qint64 limit)override{
        const auto n=std::min(limit,qint64(bytes_.size())-offset_);if(n<=0)return -1;
        std::memcpy(dest,bytes_.constData()+offset_,size_t(n));offset_+=n;return n;
    }
};
struct DecodeBarrier {
    std::atomic<bool> pause{false};QSemaphore entered,release;std::atomic<int> decoded{0};
};
struct PreviewOutcome{QString url;bool stale=false;};
struct Service {
    QObject* context;QThread* owner;double now=0;QByteArray png;std::deque<Plan> plans;
    QVector<QPointer<FakeReply>> replies;int requested=0,staleSuppressed=0;
    std::shared_ptr<PreviewStore> store;std::shared_ptr<DecodeBarrier> barrier;
    std::unique_ptr<ProviderScheduler> scheduler;std::unique_ptr<MediaTransport> transport;
    std::unique_ptr<MediaController> controller;std::unique_ptr<DecodeQueue> decode;
    ControllerScope page;
    Service(QObject* ctx,std::shared_ptr<PreviewStore> s,std::shared_ptr<DecodeBarrier> b,QByteArray p)
        :context(ctx),owner(QThread::currentThread()),png(std::move(p)),store(std::move(s)),barrier(std::move(b)) {
        check(owner!=QCoreApplication::instance()->thread(),"controller constructed on dedicated service thread");
        scheduler=std::make_unique<ProviderScheduler>(nullptr,SchedulerClock{[this]{return now;},[this]{return 1700000000.+now;},false});
        transport=std::make_unique<MediaTransport>(scheduler.get(),nullptr,[this](const QNetworkRequest& request,QObject* parent)->QNetworkReply*{
            check(QThread::currentThread()==owner,"reply factory service affinity");check(!plans.empty(),"only planned fake requests");
            auto plan=std::move(plans.front());plans.pop_front();++requested;
            auto reply=new FakeReply(request,parent,std::move(plan));replies.push_back(reply);return reply;
        },TransportClock{[this]{return qint64(now*1000);},false});
        controller=std::make_unique<MediaController>(scheduler.get(),transport.get(),nullptr,ControllerClock{[this]{return now;}});
        decode=std::make_unique<DecodeQueue>(nullptr,[gate=barrier,thread=owner](const QByteArray& bytes,const QString& mime,QSize box){
            check(QThread::currentThread()!=thread&&QThread::currentThread()!=QCoreApplication::instance()->thread(),"real decoder off service and GUI threads");
            auto result=decode_thumbnail(bytes,mime,box);++gate->decoded;
            if(gate->pause.exchange(false)){gate->entered.release();check(gate->release.tryAcquire(1,5000),"bounded synthetic decoder barrier");}
            return result;
        });
        auto opened=controller->open_page(9007199254740993LL,1);check(bool(opened),"host opens synthetic account/page");page=*opened.value;
    }
    void search(std::shared_ptr<std::promise<SearchRows>> done) {
        now+=2;plans.push_back({api(png),"application/json"});
        check(bool(controller->search(page,R"({"mode":"pic","query":"synthetic"})",[this,done](quint64,ControllerResult<SearchRows> result){
            check(QThread::currentThread()==owner,"search completion service affinity");check(bool(result)&&controller->current(result.stamp),"current successful search");
            check(result.value->size()==1,"one fixture row");
            const auto wire=QJsonDocument(result.value->at(0)).toJson(QJsonDocument::Compact);
            check(!wire.contains("upload.wikimedia")&&!wire.contains("synthetic\"}")&&!wire.contains("ownerId"),"public row excludes private URL/query/scope");
            done->set_value(std::move(*result.value));
        })),"search accepted");
    }
    void preview(const QString& id,std::shared_ptr<std::promise<PreviewOutcome>> done) {
        plans.push_back({png,"image/png"});
        check(bool(controller->preview(page,id,[this,done](quint64,ControllerResult<PrivateMedia> result){
            check(bool(result)&&controller->current(result.stamp),"preview bytes current before decode");
            const auto stamp=result.stamp;const auto epoch=store->snapshot().epoch;
            check(bool(decode->submit(page.token,std::move(result.value->bytes),result.value->mime,QSize(40,40),
                [this,done,stamp,epoch](quint64,DecodeResult decoded){
                    check(QThread::currentThread()==owner,"decode result delivered on service thread");
                    check(decoded.state==DecodeState::Completed&&decoded.thumbnail.has_value(),"real PNG successfully decoded");
                    check(decoded.thumbnail->image.size()==QSize(40,20),"thumbnail aspect ratio");
                    check(decoded.thumbnail->image.textKeys().isEmpty(),"PNG metadata stripped from published pixels");
                    if(!controller->current(stamp)){++staleSuppressed;done->set_value({{},true});return;}
                    check(decoded.thumbnail->image.colorSpace()==QColorSpace(QColorSpace::SRgb),"normalized sRGB pixels");
                    auto published=store->publish(epoch,decoded.thumbnail->image);check(bool(published),"current decoded pixels published");
                    done->set_value({std::move(*published.value),false});
                })),"decode queue accepted");
        })),"preview request accepted");
    }
    void select(const QString& id,std::shared_ptr<std::promise<bool>> done) {
        plans.push_back({png,"image/png"});
        check(bool(controller->select(page,selection(id),[this,done](quint64,ControllerResult<PrivateMedia> result){
            check(QThread::currentThread()==owner,"selected original native callback affinity");
            check(bool(result)&&controller->current(result.stamp),"current selected original");
            check(result.value->bytes==png&&result.value->mime==QStringLiteral("image/png"),"exact original bytes only in native callback");
            check(result.value->candidate.metadata.author==QStringLiteral("Fixture author"),"plain author metadata retained");
            check(!controller->snapshot().value->selectedBusy,"selected gate released on terminal completion");
            done->set_value(true); // Only a synthetic boolean crosses to GUI; no media/URL.
        })),"selected request accepted");
        check(controller->snapshot().value->selectedBusy,"selected gate acquired");
    }
    void stop() {
        check(controller->shutdown()&&transport->shutdown()&&decode->shutdown()&&scheduler->shutdown(),"all owned components accept shutdown");
    }
    bool drained()const {
        return controller->snapshot().value->operations==0&&decode->snapshot().value->pending==0&&scheduler->snapshot().value->outstanding==0;
    }
    ~Service(){controller.reset();transport.reset();decode.reset();scheduler.reset();}
};
struct Fixture {
    QThread thread;QObject* context=new QObject;Service* service=nullptr;
    std::shared_ptr<PreviewStore> store=std::make_shared<PreviewStore>(QString(32,u'f'));
    std::shared_ptr<DecodeBarrier> barrier=std::make_shared<DecodeBarrier>();
    Fixture(){
        const auto png=make_png();context->moveToThread(&thread);
        QObject::connect(&thread,&QThread::finished,context,&QObject::deleteLater);thread.start();
        call([&]{service=new Service(context,store,barrier,png);});
    }
    template<class F>void call(F&& fn){check(QMetaObject::invokeMethod(context,std::forward<F>(fn),Qt::BlockingQueuedConnection),"service command dispatched");}
    SearchRows search(){auto p=std::make_shared<std::promise<SearchRows>>();auto future=p->get_future();call([&]{service->search(p);});return await(future,"search terminal callback");}
    std::future<PreviewOutcome> preview(const QString& id){auto p=std::make_shared<std::promise<PreviewOutcome>>();auto f=p->get_future();call([&]{service->preview(id,p);});return f;}
    void stop(){call([&]{service->stop();});until([&]{bool done=false;call([&]{done=service->drained();});return done;},"all queues drained");}
    ~Fixture(){
        call([&]{delete service;service=nullptr;});thread.quit();check(thread.wait(5000),"service thread stopped");
    }
};
std::unique_ptr<QObject> image_object(QQmlEngine& engine,const QString& source) {
    QQmlComponent component(&engine);
    component.setData("import QtQuick\nImage { width: 40; height: 40; cache: false; sourceSize: Qt.size(40,40) }",QUrl(QStringLiteral("qrc:/owned-flow.qml")));
    check(!component.isError(),"actual QtQuick Image compiled");auto object=std::unique_ptr<QObject>(component.create());
    if(!object)QTextStream(stderr)<<component.errorString();check(bool(object),"actual QtQuick Image created");
    check(object->property("cache").toBool()==false,"QML image cache disabled");check(object->setProperty("source",QUrl(source)),"provider token supplied to QML");return object;
}
void flow() {
    Fixture f;auto rows=f.search();QString id=rows.at(0)["id"].toString();check(id.size()==32,"opaque result ID");
    auto preview=f.preview(id);auto first=await(preview,"preview decode and publish complete");
    check(!first.stale&&first.url.startsWith(QStringLiteral("image://valmaxmedia/")),"QML receives only provider token");
    {
        QQmlEngine engine;engine.addImageProvider(QStringLiteral("valmaxmedia"),new PreviewProvider(f.store));
        auto image=image_object(engine,first.url);until([&]{return image->property("status").toInt()==1;},"actual Image.Ready from full native pipeline");
        check(f.store->snapshot().entries==1,"one decoded entry resident");
        // Hold a genuine decoded PNG on its worker; advance only controller
        // generation to independently exercise the required post-decode stamp.
        f.barrier->pause=true;auto late=f.preview(id);
        until([&]{return f.barrier->entered.available()>0;},"second real decode reaches barrier");
        check(f.barrier->entered.tryAcquire(),"acquire decoder barrier signal");
        const auto epoch=f.store->snapshot().epoch;rows=f.search();id=rows.at(0)["id"].toString();
        check(f.store->snapshot().epoch==epoch,"provider epoch unchanged for independent controller guard test");
        f.barrier->release.release();const auto stale=await(late,"late decode returns finite stale result");
        check(stale.stale&&stale.url.isEmpty()&&f.store->snapshot().entries==1,"old decoded image never published after newer search");
        // A delivered texture is Qt-owned: host must explicitly clear it when
        // its page changes, independently of native cache revocation.
        check(image->setProperty("source",QUrl()),"clear previously displayed source");
        until([&]{return image->property("status").toInt()==0;},"old QML image is Null after explicit clear");
        check(f.store->advance()!=0&&f.store->snapshot().entries==0,"host revokes old preview epoch");
        auto refreshed=f.preview(id);const auto next=await(refreshed,"new generation preview");
        check(next.url!=first.url&&!next.stale,"new preview token differs");
        check(image->setProperty("source",QUrl(next.url)),"new provider source supplied");
        until([&]{return image->property("status").toInt()==1;},"new image Ready after stale suppression");
        auto done=std::make_shared<std::promise<bool>>();auto selected=done->get_future();
        f.call([&]{f.service->select(id,done);});check(await(selected,"selected native callback complete"),"selected original validated without files or send");
        check(image->setProperty("source",QUrl()),"clear QML source before shutdown");image.reset();
        f.stop();
        f.call([&]{check(f.service->staleSuppressed==1,"one late result suppressed");check(f.service->requested==6,"six fake HTTP replies only");check(f.service->plans.empty(),"all planned replies consumed");
            check(f.service->controller->snapshot().value->scopes==0,"controller scopes released");});
    }
    until([&]{return f.store->snapshot().responses==0;},"QQmlEngine responses released");
    check(f.store->snapshot().closed&&f.store->snapshot().residentBytes==0,"engine teardown closes and empties provider store");
    check(f.barrier->decoded==3,"three genuine PNG decodes");
}
}
int main(int argc,char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    qInstallMessageHandler([](QtMsgType type,const QMessageLogContext&,const QString& message){QTextStream(stderr)<<message<<'\n';if(type==QtFatalMsg)std::_Exit(6);});
    if(qEnvironmentVariable("QT_QPA_PLATFORM")!=QStringLiteral("windows"))return 6;
    const auto root=QFileInfo(QString::fromLocal8Bit(argv[0])).absolutePath();QPluginLoader platform(root+QStringLiteral("/plugins/platforms/qwindows.dll"));
    if(!platform.load()){QTextStream(stderr)<<platform.errorString();return 6;}
    QGuiApplication app(argc,argv);app.setOrganizationName(QStringLiteral("ValMAXSynthetic"));app.setApplicationName(QStringLiteral("NativeMediaFlowTest"));
    QCoreApplication::setLibraryPaths({root+QStringLiteral("/plugins")});flow();events();
    QJsonObject modules;for(const wchar_t* name:{L"Qt6Core.dll",L"Qt6Gui.dll",L"Qt6Network.dll",L"Qt6Qml.dll",L"Qt6Quick.dll"}){
        wchar_t path[32768]{};const auto length=GetModuleFileNameW(GetModuleHandleW(name),path,32768);
        check(length>0&&length<32768,"actual Qt module path");modules.insert(QString::fromWCharArray(name),QString::fromWCharArray(path,int(length)));
    }
    QTextStream(stdout)<<QJsonDocument(QJsonObject{{"passed",true},{"checks",checks.load()},{"qt",QString::fromLatin1(qVersion())},
        {"loadedModules",modules},{"dedicatedServiceThread",true},{"realPngDecodes",3},{"fakeHttpReplies",6},
        {"imageReadyTransitions",2},{"staleDecodeSuppressed",1},{"selectedOriginalNativeCallback",true},
        {"networkUsed",false},{"mediaWrittenToDisk",false},{"messageSent",false}}).toJson(QJsonDocument::Compact)<<'\n';return 0;
}
