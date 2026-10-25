#include "composer_adapter.h"
#include <QAbstractListModel>
#include <QBuffer>
#include <QDir>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QFile>
#include <QGuiApplication>
#include <QHttpHeaders>
#include <QJsonArray>
#include <QJsonDocument>
#include <QNetworkAccessManager>
#include <QPluginLoader>
#include <QQmlComponent>
#include <QQmlContext>
#include <QQmlEngine>
#include <QQmlExpression>
#include <QTextStream>
#include <QThread>
#include <QTimer>
#include <atomic>
#include <cstring>
#include <functional>
#include <memory>
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
using namespace valmax::media;
namespace {
std::atomic<int> checks{0}, requests{0}, active{0};
std::atomic<double> seconds{100};
std::atomic<bool> holdReply{false};
QByteArray png;
QThread* gui=nullptr;
std::function<void()> countAction, formatAction;
void check(bool pass,const char* label) { ++checks; if(!pass){QTextStream(stderr)<<label<<'\n';std::_Exit(1);} }
void events(){QCoreApplication::processEvents(QEventLoop::AllEvents,5);QCoreApplication::sendPostedEvents(nullptr,QEvent::DeferredDelete);}
template<class F>void until(F predicate,const char* label){QElapsedTimer clock;clock.start();while(!predicate()&&clock.elapsed()<7000){events();QThread::msleep(1);}check(predicate(),label);}
void once(std::function<void()>& action){const auto call=std::move(action);action={};if(call)call();}
}
namespace MeTypes { struct ContactId{qint64 value;};struct ChatId{qint64 value;}; }
Q_DECLARE_METATYPE(MeTypes::ContactId)
Q_DECLARE_METATYPE(MeTypes::ChatId)
namespace SelfInfo {
class Context:public QObject {
    Q_OBJECT
    Q_PROPERTY(MeTypes::ContactId id READ id)
public:using QObject::QObject;qint64 current=9007199254740993LL;MeTypes::ContactId id()const{return{current};}
};
}
namespace Ui::Dependency {
class Common:public QObject {
    Q_OBJECT
    Q_PROPERTY(SelfInfo::Context* selfInfo MEMBER selfInfo CONSTANT)
public:using QObject::QObject;SelfInfo::Context* selfInfo=nullptr;
};
}
namespace Ui::History::AttachmentUpload {
class UploadModel:public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(qsizetype attachmentsCount READ count)
public:
    using QAbstractListModel::QAbstractListModel;
    int rowCount(const QModelIndex& ={})const override{return 0;}
    QVariant data(const QModelIndex&,int)const override{return{};}
    qsizetype count()const{once(countAction);return 0;}
};
class View:public QObject {
    Q_OBJECT
    Q_PROPERTY(Ui::History::AttachmentUpload::UploadModel* uploadModel MEMBER uploadModel CONSTANT)
public:
    using QObject::QObject;UploadModel* uploadModel=nullptr;int calls=0;QList<QUrl> files;
    Q_INVOKABLE void uploadPhotoOrVideo(QList<QUrl> urls){++calls;files=std::move(urls);}
};
}
namespace Ui::History::MessageSender {
class View:public QObject {
    Q_OBJECT
    Q_PROPERTY(MeTypes::ChatId chatId READ chatId)
    Q_PROPERTY(Ui::History::AttachmentUpload::View* uploadView MEMBER uploadView CONSTANT)
public:using QObject::QObject;qint64 current=-9007199254740993LL;MeTypes::ChatId chatId()const{return{current};}Ui::History::AttachmentUpload::View* uploadView=nullptr;
};
}
namespace Ui::Singletons {
class QmlUtils:public QObject {
    Q_OBJECT
public:
    Q_INVOKABLE QString contactIdString(MeTypes::ContactId id){const auto result=QString::number(id.value);once(formatAction);return result;}
    Q_INVOKABLE QString chatIdString(MeTypes::ChatId id){return QString::number(id.value);}
};
}
class Composer:public QObject {
    Q_OBJECT
    Q_PROPERTY(QObject* commonDependency MEMBER common)
    Q_PROPERTY(QObject* messageSenderViewContext MEMBER sender)
    Q_PROPERTY(QObject* attachmentUploadModel MEMBER model)
    Q_PROPERTY(bool visible MEMBER visible NOTIFY visibleChanged)
    Q_PROPERTY(bool enabled MEMBER enabled)
    Q_PROPERTY(bool isCurrentChat MEMBER current)
public:using QObject::QObject;QObject* common=nullptr;QObject* sender=nullptr;QObject* model=nullptr;bool visible=true,enabled=true,current=true;
signals:void visibleChanged();
};
struct Host {
    Composer root;
    Ui::Dependency::Common common;
    SelfInfo::Context self;
    Ui::History::MessageSender::View sender;
    Ui::History::AttachmentUpload::View upload;
    Ui::History::AttachmentUpload::UploadModel model;
    Host(QQmlEngine& engine){QQmlEngine::setContextForObject(&root,engine.rootContext());root.common=&common;root.sender=&sender;root.model=&model;common.selfInfo=&self;sender.uploadView=&upload;upload.uploadModel=&model;}
};
namespace {
QByteArray api(){
    QJsonObject info{{"mime","image/png"},{"size",png.size()},{"width",32},{"height",24},
        {"url","https://upload.wikimedia.org/wikipedia/commons/a/ab/Fixture.png"},
        {"thumburl","https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Fixture.png/240px-Fixture.png"},
        {"descriptionurl","https://commons.wikimedia.org/wiki/File:Fixture.png"},
        {"extmetadata",QJsonObject{{"Artist",QJsonObject{{"value","Fixture artist"}}},{"LicenseShortName",QJsonObject{{"value","CC0"}}}}}};
    return QJsonDocument(QJsonObject{{"query",QJsonObject{{"pages",QJsonArray{QJsonObject{{"title","File:Fixture.png"},{"imageinfo",QJsonArray{info}}}}}}}}).toJson(QJsonDocument::Compact);
}
class Reply:public QNetworkReply {
    QByteArray data_;qint64 offset_=0;QTimer timer_;bool ended_=false,aborted_=false;
public:
    Reply(const QNetworkRequest& request,QObject* parent):QNetworkReply(parent){
        check(QThread::currentThread()!=gui,"network on worker only");++requests;++active;
        const bool json=request.url().host()==QStringLiteral("commons.wikimedia.org");data_=json?api():png;
        setRequest(request);setUrl(request.url());setOperation(QNetworkAccessManager::GetOperation);setAttribute(QNetworkRequest::HttpStatusCodeAttribute,200);
        setHeader(QNetworkRequest::ContentTypeHeader,json?QStringLiteral("application/json"):QStringLiteral("image/png"));setHeader(QNetworkRequest::ContentLengthHeader,data_.size());
        auto h=headers();h.append("Set-Cookie","a=1");h.append("Set-Cookie","b=2");setHeaders(h);open(QIODevice::ReadOnly|QIODevice::Unbuffered);
        timer_.setInterval(1);connect(&timer_,&QTimer::timeout,this,[this]{if(!holdReply||aborted_)finish();});timer_.start();
    }
    ~Reply()override{if(!ended_)--active;}
    void abort()override{aborted_=true;}
    bool isSequential()const override{return true;}
    qint64 bytesAvailable()const override{return data_.size()-offset_+QNetworkReply::bytesAvailable();}
protected:qint64 readData(char* to,qint64 length)override{const auto n=std::min(length,qint64(data_.size())-offset_);if(n<=0)return -1;std::memcpy(to,data_.constData()+offset_,size_t(n));offset_+=n;return n;}
private:void finish(){if(ended_)return;ended_=true;--active;timer_.stop();if(aborted_)setError(OperationCanceledError,QStringLiteral("cancelled"));setFinished(true);QPointer<Reply> self(this);emit metaDataChanged();if(!self)return;if(!aborted_)emit readyRead();if(self)emit finished();}
};
ServiceConfig config(const QString& root){
    ServiceConfig c;c.explicitStagingRoot=root.toStdWString();
    c.staging={valmax_media_staging_abi_version,valmax_media_staging_begin,valmax_media_staging_stage,valmax_media_staging_signal_cancel,valmax_media_staging_cancel,valmax_media_staging_retain,valmax_media_staging_release,valmax_core_buffer_free};
    c.replyFactory=[](const QNetworkRequest& request,QObject* parent){return new Reply(request,parent);};
    c.controllerClock.monotonicSeconds=[]{return seconds.load();};return c;
}
void idle(MediaServiceHost* service){until([&]{const auto s=service->snapshot();return s&&s.value->operations==0&&!s.value->selectedBusy&&s.value->pendingImages==0;},"all service operations terminal");}
std::unique_ptr<MediaComposerAdapter> adapter(QQmlEngine& engine,Host& host){
    engine.rootContext()->setContextProperty(QStringLiteral("hostComposer"),&host.root);
    QQmlComponent component(&engine);component.setData("import QtQml\nimport QmlSingletonesModule 1.0\nimport ValMAX.AdapterTest 1.0\nMediaComposer { composer: hostComposer }",QUrl(QStringLiteral("qrc:/adapter-fixture.qml")));
    if(component.isError())QTextStream(stderr)<<component.errorString();
    check(!component.isError(),"actual QML adapter compiles");auto object=std::unique_ptr<MediaComposerAdapter>(qobject_cast<MediaComposerAdapter*>(component.create()));
    check(bool(object),"actual QML adapter creates");return object;
}
QString id(MediaComposerAdapter& a){check(a.rows().size()==1,"one public row");return a.rows().front().toMap().value(QStringLiteral("id")).toString();}
void search(MediaComposerAdapter& a){seconds.fetch_add(3);check(a.search(QStringLiteral("pic"),QStringLiteral("fixture")),"explicit adapter search");until([&]{return !a.busy();},"adapter search terminal");check(a.errorCode().isEmpty(),"no search error");check(a.rows().size()==1,"public result delivered");}
void normal(MediaServiceHost* service,const QString& root){
    QQmlEngine engine;Host host(engine);auto a=adapter(engine,host);check(a->composer()==&host.root,"QML host composer bound");
    const auto before=requests.load();check(!a->available()&&!a->search(QStringLiteral("pic"),QStringLiteral("fixture")),"unconfigured adapter finite unavailable");events();check(requests==before,"no implicit IO or search");
    check(configure_media_composer_engine(&engine,service),"late explicit native service configuration");
    check(a->available(),"late config activates existing adapter");
    check(configure_media_composer_engine(&engine,service),"same service idempotent");
    check(!configure_media_composer_engine(&engine,nullptr),"null service rejected");
    for(const auto name:{"accountId","chatId","localFileUrl","service","filePath"})check(a->metaObject()->indexOfProperty(name)<0,"no QML identity/path/service setter");
    search(*a);until([&]{return !a->rows().front().toMap().value(QStringLiteral("previewSource")).toString().isEmpty();},"decoded preview reaches adapter");idle(service);
    const auto publicJson=QJsonDocument(QJsonArray::fromVariantList(a->rows())).toJson();check(!publicJson.contains("file:")&&!publicJson.contains("upload.wikimedia")&&!publicJson.contains("9007199254740993")&&!publicJson.contains(root.toUtf8()),"no private identities or original URL in public rows");
    int dispatched=0;QString caption;QObject::connect(a.get(),&MediaComposerAdapter::draftDispatched,[&](const QString& text){++dispatched;caption=text;});
    const auto opaque=id(*a);QQmlExpression select(engine.rootContext(),a.get(),QStringLiteral("select(\"")+opaque+QStringLiteral("\")"));check(select.evaluate().toBool()&&!select.hasError(),"actual QML selection invokes adapter");
    until([&]{return dispatched==1;},"draft caption forwarded");idle(service);check(host.upload.calls==1&&host.upload.files.size()==1,"exact existing native mock receives one file");
    QFile file(host.upload.files.front().toLocalFile());check(file.open(QIODevice::ReadOnly)&&file.readAll()==png,"real Rust-retained original readable");file.close();
    check(caption.contains(QStringLiteral("Fixture artist"))&&!caption.contains(QStringLiteral("file:")),"caption excludes native file URL");
    search(*a);idle(service);const auto old=id(*a);host.sender.current-=1;
    check(!a->select(old)&&a->rows().isEmpty()&&!a->busy(),"silent chat switch prevents old selection without rebinding result");check(host.upload.calls==1,"no wrong-chat native invoke");
    search(*a);idle(service);countAction=[&]{host.self.current+=1;};check(a->select(id(*a)),"selection before count getter identity mutation");until([&]{return !a->busy();},"identity-mismatch terminal");idle(service);
    check(host.upload.calls==1&&dispatched==1,"final typed resolver blocks silent account switch after getter");
    a->reset();check(a->available()&&a->rows().isEmpty(),"reset re-resolves actual account");
    search(*a);idle(service);engine.clearSingletons();check(a->available(),"clearSingletons does not remove engine holder");
    check(a->select(id(*a)),"formatter singleton safely recreated");until([&]{return dispatched==2;},"selection after singleton clear");idle(service);
    check(a->setProperty("composer",QVariant::fromValue<QObject*>(nullptr)),"QML composer can detach");check(!a->available()&&a->rows().isEmpty(),"detach unavailable and sources clear");
}
void lifecycle(MediaServiceHost* service){
    QQmlEngine engine;Host host(engine);check(configure_media_composer_engine(&engine,service),"early host config");auto a=adapter(engine,host);check(a->available(),"early config adapter ready");
    search(*a);idle(service);check(host.root.setProperty("visible",false),"host visibility property notify");check(!a->available()&&a->rows().isEmpty(),"hidden composer invalidates public sources");
    check(host.root.setProperty("visible",true),"visible host restored");a->reset();check(a->available(),"visible host resets to ready");
    holdReply=true;seconds.fetch_add(3);check(a->search(QStringLiteral("pic"),QStringLiteral("fixture")),"search pending for disposal");until([]{return active>0;},"owned fake reply held");a.reset();holdReply=false;idle(service);check(host.upload.calls==0,"adapter destruction never dispatches");
    a=adapter(engine,host);QPointer<MediaComposerAdapter> weak=a.get();
    bool entered=false;const auto c=QObject::connect(a.get(),&MediaComposerAdapter::changed,[&]{if(!entered){entered=true;a.reset();}});
    weak->reset();check(!weak,"changed callback can delete adapter");QObject::disconnect(c);idle(service);
    a=adapter(engine,host);formatAction=[&]{a.reset();};weak=a.get();check(!weak->search(QStringLiteral("pic"),QStringLiteral("fixture"))&&!weak,"typed formatter can delete adapter safely");idle(service);
}
QJsonObject modules(){QJsonObject result;for(const auto name:{L"Qt6Core.dll",L"Qt6Gui.dll",L"Qt6Network.dll",L"Qt6Qml.dll",L"Qt6Quick.dll",L"valmax_core.dll"}){const auto module=GetModuleHandleW(name);check(module!=nullptr,"exact native module loaded");wchar_t path[32768];const auto n=GetModuleFileNameW(module,path,DWORD(std::size(path)));check(n>0&&n<std::size(path),"bounded module path");result.insert(QString::fromWCharArray(name),QDir::fromNativeSeparators(QString::fromWCharArray(path,int(n))));}return result;}
}
int main(int argc,char** argv){
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    const auto exe=QFileInfo(QString::fromLocal8Bit(argv[0])).absolutePath();QPluginLoader platform(exe+QStringLiteral("/plugins/platforms/qwindows.dll"));if(!platform.load())return 6;
    QGuiApplication app(argc,argv);gui=app.thread();QCoreApplication::setLibraryPaths({exe+QStringLiteral("/plugins")});
    qmlRegisterType<MediaComposerAdapter>("ValMAX.AdapterTest",1,0,"MediaComposer");
    qmlRegisterSingletonType<Ui::Singletons::QmlUtils>("QmlSingletonesModule",1,0,"QmlUtils",[](QQmlEngine*,QJSEngine*)->QObject*{return new Ui::Singletons::QmlUtils;});
    QImage pixels(32,24,QImage::Format_ARGB32);pixels.fill(Qt::green);QBuffer buffer(&png);check(buffer.open(QIODevice::WriteOnly)&&pixels.save(&buffer,"PNG"),"synthetic PNG fixture");
    const auto root=qEnvironmentVariable("VALMAX_SYNTHETIC_STAGING_ROOT");check(!root.isEmpty()&&QFileInfo(root).isDir(),"explicit own staging root");
    const auto created=MediaServiceHost::create(config(root));check(bool(created),"single real native service");auto* service=*created.value;
    normal(service,root);lifecycle(service);
    // A configured holder cannot adopt a replacement after its service dies.
    QQmlEngine configuredEngine;Host survivor(configuredEngine);check(configure_media_composer_engine(&configuredEngine,service),"persistent holder configured");auto survivorAdapter=adapter(configuredEngine,survivor);
    bool drained=false;QObject::connect(service,&MediaServiceHost::drained,[&]{drained=true;});check(service->shutdown(),"async service shutdown");until([&]{return drained;},"all workers drained");delete service;
    until([]{return MediaServiceHost::process_available();},"process reservation released");check(!survivorAdapter->available(),"service destruction makes adapter unavailable");
    const auto next=MediaServiceHost::create(config(root));check(bool(next),"new standalone service after drain");service=*next.value;
    check(!configure_media_composer_engine(&configuredEngine,service),"old holder refuses silent service replacement");
    drained=false;QObject::connect(service,&MediaServiceHost::drained,[&]{drained=true;});service->shutdown();until([&]{return drained;},"second service drains");delete service;until([]{return MediaServiceHost::process_available();},"second reservation released");
    check(active==0,"all fake replies stopped");
    const auto loaded=modules();
    QTextStream(stdout)<<QJsonDocument(QJsonObject{{"passed",true},{"checks",checks.load()},{"fakeHttpReplies",requests.load()},{"allThreadsDrained",MediaServiceHost::process_available()},{"noLiveNetwork",true},{"noRealMaxPresenter",true},{"draftAcceptanceClaimed",false},{"loadedModules",loaded}}).toJson(QJsonDocument::Compact)<<'\n';
}
#include "adapter_test.moc"
