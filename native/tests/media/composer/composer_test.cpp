// End-to-end synthetic frontend only: no MAX presenter, accounts or network.
#include "composer_frontend.h"
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
#include <QQmlEngine>
#include <QTextStream>
#include <QThread>
#include <QTimer>
#include <QUrlQuery>
#include <algorithm>
#include <atomic>
#include <cstring>
#include <functional>
#include <memory>
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
using namespace valmax::media;
namespace {
std::atomic<int> checks{0};
void check(bool value,const char* message) {
    ++checks;
    if(!value){QTextStream(stderr)<<"FAIL: "<<message<<'\n';std::_Exit(1);}
}
void events(){QCoreApplication::processEvents(QEventLoop::AllEvents,5);QCoreApplication::sendPostedEvents(nullptr,QEvent::DeferredDelete);}
template<class F>void until(F predicate,const char* message) {
    QElapsedTimer timer;timer.start();
    while(!predicate()&&timer.elapsed()<7000){events();QThread::msleep(1);}
    check(predicate(),message);
}
struct State {
    QByteArray png;
    QThread* gui=nullptr;
    std::atomic<double> seconds{100.};
    std::atomic<int> requests{0},apis{0},active{0},maximum{0},decodes{0},stages{0},retains{0};
    std::atomic<bool> holdApi{false},holdOriginal{false},holdDecode{false},holdRetain{false},inRetain{false};
    std::atomic<bool> originalFail{false},stageFail{false};
} state;
void barrier(const std::atomic<bool>& held) {
    QElapsedTimer timer;timer.start();
    while(held&&timer.elapsed()<6000)QThread::msleep(1);
    check(!held,"bounded worker barrier released");
}
QByteArray api(const QString& label) {
    QJsonArray pages;
    for(int i=0;i<6;++i) {
        const auto title=label+QString::number(i)+QStringLiteral(".png");
        QJsonObject info{{"mime","image/png"},{"size",state.png.size()},{"width",48},{"height",32},
            {"url",QStringLiteral("https://upload.wikimedia.org/wikipedia/commons/a/ab/")+title},
            {"thumburl",QStringLiteral("https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/")+title+QStringLiteral("/240px-")+title},
            {"descriptionurl",QStringLiteral("https://commons.wikimedia.org/wiki/File:")+title},
            {"extmetadata",QJsonObject{{"Artist",QJsonObject{{"value","<b>Fixture author</b>"}}},{"LicenseShortName",QJsonObject{{"value","CC0"}}}}}};
        pages.append(QJsonObject{{"title",QStringLiteral("File:")+title},{"imageinfo",QJsonArray{info}}});
    }
    return QJsonDocument(QJsonObject{{"query",QJsonObject{{"pages",pages}}}}).toJson(QJsonDocument::Compact);
}
class Reply final:public QNetworkReply {
    QByteArray bytes_;qint64 offset_=0;bool done_=false,aborted_=false,api_=false,original_=false;QTimer timer_;
public:
    Reply(const QNetworkRequest& request,QObject* owner):QNetworkReply(owner) {
        check(QThread::currentThread()!=state.gui,"fake HTTP only on service thread");
        check(request.attribute(QNetworkRequest::CookieLoadControlAttribute).toInt()==QNetworkRequest::Manual
              &&request.attribute(QNetworkRequest::CookieSaveControlAttribute).toInt()==QNetworkRequest::Manual,"cookie persistence disabled");
        check(!request.hasRawHeader("Cookie"),"no response cookie reused");
        ++state.requests;const auto active=++state.active;state.maximum.store(std::max(active,state.maximum.load()));
        api_=request.url().host()==QStringLiteral("commons.wikimedia.org");
        original_=!api_&&!request.url().path().contains(QStringLiteral("/thumb/"));
        if(api_)++state.apis;
        const auto query=QUrlQuery(request.url()).queryItemValue(QStringLiteral("gsrsearch"));
        const auto label=query.contains(QStringLiteral("newer"))?QStringLiteral("Newer"):QStringLiteral("Fixture");
        bytes_=api_?api(label):state.png;
        const bool fail=original_&&state.originalFail.exchange(false);
        setRequest(request);setUrl(request.url());setOperation(QNetworkAccessManager::GetOperation);
        setAttribute(QNetworkRequest::HttpStatusCodeAttribute,fail?404:200);
        setHeader(QNetworkRequest::ContentTypeHeader,api_?QStringLiteral("application/json"):QStringLiteral("image/png"));
        setHeader(QNetworkRequest::ContentLengthHeader,bytes_.size());
        auto headers=QNetworkReply::headers();check(headers.append("Set-Cookie","one=1; Secure")&&headers.append("Set-Cookie","two=2; Secure"),"individual legitimate cookie headers");setHeaders(headers);
        open(QIODevice::ReadOnly|QIODevice::Unbuffered);
        timer_.setInterval(1);connect(&timer_,&QTimer::timeout,this,[this] {
            if(aborted_||(!api_||!state.holdApi)&&(!original_||!state.holdOriginal))finish();
        });timer_.start();
    }
    ~Reply()override{if(!done_)--state.active;}
    void abort()override{aborted_=true;}
    bool isSequential()const override{return true;}
    qint64 bytesAvailable()const override{return bytes_.size()-offset_+QNetworkReply::bytesAvailable();}
protected:
    qint64 readData(char* dest,qint64 size)override {
        const auto n=std::min(size,qint64(bytes_.size())-offset_);if(n<=0)return -1;
        std::memcpy(dest,bytes_.constData()+offset_,size_t(n));offset_+=n;return n;
    }
private:
    void finish() {
        if(done_)return;done_=true;--state.active;timer_.stop();
        if(aborted_)setError(OperationCanceledError,QStringLiteral("synthetic cancellation"));
        setFinished(true);QPointer<Reply> alive(this);emit metaDataChanged();if(!alive)return;
        if(!aborted_)emit readyRead();if(alive)emit finished();
    }
};
uint32_t stage(ValmaxMediaStagingHandle h,const ValmaxMediaStageRequest* r,const uint8_t* b,size_t n,ValmaxMediaStageResult* out) {
    check(QThread::currentThread()!=state.gui,"Rust staging called off GUI");++state.stages;
    if(state.stageFail.exchange(false)){*out={40,1,0,{}};return 8;} // finite injected no-space, no filesystem mutation
    return valmax_media_staging_stage(h,r,b,n,out);
}
uint32_t retain(ValmaxMediaStagingHandle h) {
    check(QThread::currentThread()!=state.gui,"terminal retain called off GUI");++state.retains;state.inRetain=true;
    barrier(state.holdRetain);state.inRetain=false;return valmax_media_staging_retain(h);
}
ServiceConfig config(const QString& root) {
    ServiceConfig result;result.explicitStagingRoot=root.toStdWString();
    result.staging={valmax_media_staging_abi_version,valmax_media_staging_begin,stage,valmax_media_staging_signal_cancel,valmax_media_staging_cancel,retain,valmax_media_staging_release,valmax_core_buffer_free};
    result.replyFactory=[](const QNetworkRequest& request,QObject* owner){return new Reply(request,owner);};
    result.controllerClock.monotonicSeconds=[]{return state.seconds.load();};
    result.decoder=[](const QByteArray& bytes,const QString& mime,QSize box) {
        check(QThread::currentThread()!=state.gui,"real preview decoding off GUI");++state.decodes;
        auto decoded=decode_thumbnail(bytes,mime,box);barrier(state.holdDecode);return decoded;
    };
    return result;
}
class Model final:public QObject {
    Q_OBJECT
    Q_PROPERTY(qsizetype attachmentsCount READ count)
public:
    qsizetype value=0;mutable std::function<void()> reading;
    qsizetype count()const {const auto result=value;const auto callback=reading;if(callback)callback();return result;}
};
class Upload final:public QObject {
    Q_OBJECT
public:
    int calls=0;QList<QUrl> files;std::function<void()> invoking;
    Q_INVOKABLE void uploadPhotoOrVideo(QList<QUrl> urls) {
        check(QThread::currentThread()==state.gui,"draft invoke on GUI");++calls;files=std::move(urls);
        const auto callback=invoking;if(callback)callback();
    }
};
struct Composer {
    QObject composer,sender;Upload upload;Model model;
    HostComposerBinding binding(qint64 account=101,quint64 page=1) {
        return {account,page,&composer,&sender,&upload,&model,[]{return true;}};
    }
};
void idle(MediaServiceHost* service) {
    until([&]{const auto s=service->snapshot();return s&&s.value->operations==0&&s.value->pendingImages==0&&!s.value->selectedBusy;},"service gate and owned operations released");
}
QString first_id(MediaComposerFrontend& front) {check(front.rows().size()==6,"six public rows");return front.rows().front().toMap().value(QStringLiteral("id")).toString();}
QString first_source(MediaComposerFrontend& front) {return front.rows().front().toMap().value(QStringLiteral("previewSource")).toString();}
void search(MediaComposerFrontend& front,const QString& query=QStringLiteral("fixture")) {
    state.seconds.fetch_add(3.);check(front.search(QStringLiteral("pic"),query),"explicit native frontend search admitted");
    until([&]{return !front.busy();},"search terminal GUI state");check(front.errorCode().isEmpty(),"search has no error");
    check(front.rows().size()==6,"bounded six results visible");
}
void preview(MediaComposerFrontend& front) {
    until([&]{if(front.rows().size()!=6)return false;for(const auto& item:front.rows())if(item.toMap().value(QStringLiteral("previewSource")).toString().isEmpty())return false;return true;},"all six real decoded previews published");
}
std::unique_ptr<QObject> image(QQmlEngine& engine,const QString& source) {
    QQmlComponent component(&engine);component.setData("import QtQuick\nImage { width:48; height:32; cache:false; sourceSize:Qt.size(48,32) }",QUrl(QStringLiteral("qrc:/synthetic-composer.qml")));
    check(!component.isError(),"actual QML Image compiles");auto result=std::unique_ptr<QObject>(component.create());check(bool(result),"actual QML Image created");
    check(result->setProperty("source",QUrl(source)),"only private provider capability supplied to QML");
    until([&]{return result->property("status").toInt()==1;},"actual QQmlEngine Image.Ready");return result;
}
void normal_and_errors(MediaServiceHost* service,const QString& root) {
    QQmlEngine engine;auto* context=MediaEngineContext::install(&engine);check(context!=nullptr,"engine context installed");Composer host;
    MediaComposerFrontend front(context,service);check(front.bind(host.binding()),"native composer bound");
    const auto before=state.requests.load();events();check(before==state.requests,"bind does not search automatically");
    check(!front.search(QStringLiteral("pic"),QString(9000,u'a'))&&front.errorCode()==QStringLiteral("invalid_input"),"oversized input rejected before JSON work");
    check(state.requests==before,"invalid search emits no HTTP request");search(front);preview(front);idle(service);
    const auto rows=QJsonDocument(QJsonArray::fromVariantList(front.rows())).toJson(QJsonDocument::Compact);
    check(!rows.contains("upload.wikimedia")&&!rows.contains("file://")&&!rows.contains("accountId")&&!rows.contains(root.toUtf8()),"public rows exclude originals, staging paths and host identities");
    auto picture=image(engine,first_source(front));
    int captions=0;QString caption;QObject::connect(&front,&MediaComposerFrontend::draftDispatched,[&](const QString& value){++captions;caption=value;});
    check(front.select(first_id(front)),"explicit opaque selection admitted");check(front.busy(),"selected busy set");
    check(!front.select(QString(32,u'a')),"busy duplicate selection refused");
    until([&]{return captions==1;},"native draft dispatch and caption signal");idle(service);
    check(host.upload.calls==1&&host.upload.files.size()==1,"mock receives exactly one native local URL");
    const auto path=host.upload.files.front().toLocalFile();check(path.startsWith(root+u'/')||path.startsWith(root+u'\\'),"retained file is inside explicit owned fixture");
    QFile file(path);check(file.open(QIODevice::ReadOnly)&&file.readAll()==state.png,"actual Rust-retained original readable and exact");file.close();
    check(caption.contains(QStringLiteral("Fixture author"))&&caption.contains(QStringLiteral("CC0"))&&!caption.contains(QStringLiteral("file:")),"plain attribution preserved without local path");
    check(front.rows().isEmpty()&&!front.busy()&&front.errorCode().isEmpty(),"successful dispatch clears rows and busy");
    check(host.model.value==0,"void dispatch never claimed attachment acceptance");
    check(picture->setProperty("source",QUrl()),"host clears delivered texture");picture.reset();

    search(front);idle(service);host.model.value=10;check(front.select(first_id(front)),"selection reaches native limit guard");
    until([&]{return !front.busy();},"limit returns finite GUI state");idle(service);
    check(front.errorCode()==QStringLiteral("attachment_limit")&&host.upload.calls==1&&captions==1,"attachment limit prevents invoke and caption");
    host.model.value=0;check(front.select(first_id(front)),"gate reusable immediately after limit");until([&]{return captions==2;},"valid retry dispatches");idle(service);

    search(front);idle(service);state.originalFail=true;check(front.select(first_id(front)),"HTTP failure admitted");
    until([&]{return !front.busy();},"HTTP failure clears GUI busy");idle(service);check(!front.errorCode().isEmpty()&&host.upload.calls==2,"HTTP failure never dispatches");
    state.stageFail=true;check(front.select(first_id(front)),"storage failure admitted after HTTP failure");
    until([&]{return !front.busy();},"storage failure clears GUI busy");idle(service);check(front.errorCode()==QStringLiteral("no_space")&&host.upload.calls==2,"storage finite error never dispatches");
    check(front.select(first_id(front)),"gate usable after storage failure");until([&]{return captions==3;},"selection succeeds after two failures");idle(service);
    front.unbind();idle(service);
}
void bind_reentrancy(MediaServiceHost* service) {
    QQmlEngine engine;auto* context=MediaEngineContext::install(&engine);Composer a,b;MediaComposerFrontend front(context,service);
    bool entered=false;auto connection=QObject::connect(&front,&MediaComposerFrontend::changed,[&] {
        if(!entered){entered=true;check(front.bind(b.binding(202,2)),"changed handler binds newer composer");}
    });
    check(!front.bind(a.binding()),"outer bind aborts after reentrant unbind signal");QObject::disconnect(connection);
    search(front);int captions=0;QObject::connect(&front,&MediaComposerFrontend::draftDispatched,[&]{++captions;});
    check(front.select(first_id(front)),"reentrant binding remains usable");until([&]{return captions==1;},"newer composer dispatch finished");idle(service);
    check(a.upload.calls==0&&b.upload.calls==1,"outer bind never overwrites reentrant new host");front.unbind();

    auto candidate=a.binding();bool called=false;
    candidate.stillCurrent=[&] {if(!called){called=true;check(front.bind(b.binding(202,2)),"stillCurrent rebinds newer host");}return true;};
    check(!front.bind(std::move(candidate))&&front.available(),"old validation does not unbind newer host");
    search(front);check(front.select(first_id(front)),"new host remains selectable after old validation");until([&]{return captions==2;},"second newer composer dispatch");idle(service);
    check(a.upload.calls==0&&b.upload.calls==2,"stillCurrent reentry preserves exact target");front.unbind();idle(service);
}
void search_reentrancy(MediaServiceHost* service) {
    QQmlEngine engine;auto* context=MediaEngineContext::install(&engine);Composer a,b;MediaComposerFrontend front(context,service);
    check(front.bind(a.binding()),"old host for detach reentry");search(front);idle(service);
    bool rebound=false;auto connection=QObject::connect(context,&MediaEngineContext::invalidated,[&] {
        if(!rebound){rebound=true;check(front.bind(b.binding(202,2)),"context invalidation binds new host");}
    });
    const auto before=state.apis.load();state.seconds.fetch_add(3.);
    check(!front.search(QStringLiteral("pic"),QStringLiteral("fixture")),"old query aborts when detach changes host");
    QObject::disconnect(connection);events();check(state.apis==before&&front.available()&&front.rows().isEmpty(),"old query never silently starts on new account");
    search(front,QStringLiteral("newer"));check(front.rows().front().toMap().value(QStringLiteral("title")).toString().contains(QStringLiteral("Newer")),"new explicit query controls visible rows");
    front.unbind();idle(service);
}
void dispatch_reentrancy(MediaServiceHost* service,bool inGetter) {
    QQmlEngine engine;auto* context=MediaEngineContext::install(&engine);Composer a,b;MediaComposerFrontend front(context,service);
    check(front.bind(a.binding()),"old composer for dispatch reentry");search(front);idle(service);
    bool rebound=false;int captions=0;QObject::connect(&front,&MediaComposerFrontend::draftDispatched,[&]{++captions;});
    const auto action=[&] {
        if(rebound)return;rebound=true;state.holdApi=true;
        check(front.bind(b.binding(202,2)),"native dispatch callback binds newer host");
        state.seconds.fetch_add(3.);check(front.search(QStringLiteral("pic"),QStringLiteral("newer")),"reentrant newer search admitted");
    };
    if(inGetter)a.model.reading=action;else a.upload.invoking=action;
    check(front.select(first_id(front)),"old selection starts native reentrant boundary");
    until([&]{return rebound;},"native getter or invoke reentered");
    check(front.busy()&&front.rows().isEmpty()&&front.errorCode().isEmpty(),"old selected completion cannot clobber newer busy/error/rows");
    check(captions==0&&a.upload.calls==(inGetter?0:1),"stale caption suppressed; getter-stale prevents invoke");
    until([&]{return !service->snapshot().value->selectedBusy;},"old selected ticket released after reentrant new session");
    state.holdApi=false;until([&]{return !front.busy();},"newer query finishes normally");
    check(first_id(front).size()==32&&front.rows().front().toMap().value(QStringLiteral("title")).toString().contains(QStringLiteral("Newer")),"newer results survive old completion");
    check(front.select(first_id(front)),"newer selection admitted after reentrant terminal");until([&]{return captions==1;},"newer caption and dispatch delivered");idle(service);
    check(b.upload.calls==1,"only newer host receives subsequent selected file");front.unbind();idle(service);
}
void stale_and_destruction(MediaServiceHost* service) {
    QQmlEngine engine;auto* context=MediaEngineContext::install(&engine);Composer a,b;
    auto front=std::make_unique<MediaComposerFrontend>(context,service);check(front->bind(a.binding()),"stale-test host bound");
    state.holdApi=true;state.seconds.fetch_add(3.);const auto before=state.apis.load();check(front->search(QStringLiteral("pic"),QStringLiteral("fixture")),"old slow search admitted");
    until([&]{return state.apis>before;},"old API reply held");front->reset();state.holdApi=false;search(*front,QStringLiteral("newer"));idle(service);
    check(front->rows().front().toMap().value(QStringLiteral("title")).toString().contains(QStringLiteral("Newer")),"old query completion cannot replace new rows");

    state.holdDecode=true;const auto decoded=state.decodes.load();search(*front);
    until([&]{return state.decodes>decoded;},"real codec held before old preview delivery");
    check(front->bind(b.binding(202,2)),"account switch revokes preview lease");state.holdDecode=false;idle(service);
    check(front->rows().isEmpty()&&context->snapshot().entries==0,"stale decoded pixels never populate new account");

    search(*front);idle(service);state.holdRetain=true;const auto calls=b.upload.calls;check(front->select(first_id(*front)),"selected retain race admitted");
    until([]{return state.inRetain.load();},"actual staged file reaches retain barrier");check(front->bind(a.binding()),"account changes before retain completion");state.holdRetain=false;idle(service);
    check(b.upload.calls==calls&&front->rows().isEmpty()&&!front->busy(),"late retained URL never invokes old or new composer");

    search(*front);idle(service);state.holdOriginal=true;check(front->select(first_id(*front)),"close with original in flight");
    until([]{return state.active>0;},"original reply held");front.reset();state.holdOriginal=false;idle(service);
    check(a.upload.calls==0&&b.upload.calls==calls,"destroyed frontend suppresses native dispatch");

    front=std::make_unique<MediaComposerFrontend>(context,service);auto destroyedHost=std::make_unique<Composer>();check(front->bind(destroyedHost->binding()),"temporary sender bound");
    search(*front);idle(service);state.holdOriginal=true;check(front->select(first_id(*front)),"sender destruction selection admitted");
    until([]{return state.active>0;},"sender destruction original held");destroyedHost.reset();state.holdOriginal=false;idle(service);
    check(!front->available()&&!front->busy()&&front->rows().isEmpty(),"destroyed native sender unbinds and releases gate");front.reset();idle(service);
}
void silent_identity_change(MediaServiceHost* service) {
    QQmlEngine engine;auto* context=MediaEngineContext::install(&engine);Composer host;
    bool sameChat=true;auto binding=host.binding();binding.stillCurrent=[&]{return sameChat;};
    MediaComposerFrontend front(context,service);check(front.bind(std::move(binding)),"live actual identity binding");
    search(front);idle(service);int captions=0;QObject::connect(&front,&MediaComposerFrontend::draftDispatched,[&]{++captions;});
    host.model.reading=[&]{sameChat=false;};
    check(front.select(first_id(front)),"selection begins before silent native identity change");
    until([&]{return !front.busy();},"silent chat change terminates selection");idle(service);
    check(host.upload.calls==0&&captions==0&&front.rows().isEmpty(),"post-getter live identity prevents wrong-chat invoke and caption");
    front.unbind();idle(service);
}
void run(const QString& root) {
    QImage pixels(48,32,QImage::Format_ARGB32);pixels.fill(Qt::green);QBuffer buffer(&state.png);check(buffer.open(QIODevice::WriteOnly)&&pixels.save(&buffer,"PNG"),"real synthetic PNG generated");
    const auto created=MediaServiceHost::create(config(root));check(bool(created),"one actual process service created");auto* service=*created.value;
    normal_and_errors(service,root);bind_reentrancy(service);search_reentrancy(service);
    dispatch_reentrancy(service,false);dispatch_reentrancy(service,true);silent_identity_change(service);stale_and_destruction(service);
    bool drained=false;QObject::connect(service,&MediaServiceHost::drained,[&]{drained=true;});check(service->shutdown(),"service asynchronous shutdown");
    until([&]{return drained;},"network codecs staging and service threads drain");delete service;
    until([]{return MediaServiceHost::process_available();},"process reservation released");
    check(state.active==0&&state.maximum<=2,"all synthetic HTTP completed within shared concurrency two");
}
QJsonObject modules() {
    QJsonObject result;
    for(const auto name:{L"Qt6Core.dll",L"Qt6Gui.dll",L"Qt6Network.dll",L"Qt6Qml.dll",L"Qt6Quick.dll",L"valmax_core.dll"}) {
        const auto module=GetModuleHandleW(name);check(module!=nullptr,"required actual native module loaded");
        wchar_t path[32768];const auto length=GetModuleFileNameW(module,path,DWORD(std::size(path)));
        check(length>0&&length<std::size(path),"loaded module path bounded");
        result.insert(QString::fromWCharArray(name),QDir::fromNativeSeparators(QString::fromWCharArray(path,int(length))));
    }
    return result;
}
}
int main(int argc,char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    const auto exe=QFileInfo(QString::fromLocal8Bit(argv[0])).absolutePath();QPluginLoader platform(exe+QStringLiteral("/plugins/platforms/qwindows.dll"));if(!platform.load())return 6;
    QGuiApplication app(argc,argv);state.gui=app.thread();app.setOrganizationName(QStringLiteral("ValMAXSynthetic"));app.setApplicationName(QStringLiteral("NativeComposerTest"));
    QCoreApplication::setLibraryPaths({exe+QStringLiteral("/plugins")});
    const auto root=qEnvironmentVariable("VALMAX_SYNTHETIC_STAGING_ROOT");check(!root.isEmpty()&&QFileInfo(root).isDir(),"explicit owned staging root");run(QDir::fromNativeSeparators(root));
    const auto loaded=modules();
    QTextStream(stdout)<<QJsonDocument(QJsonObject{{"passed",true},{"checks",checks.load()},{"fakeHttpReplies",state.requests.load()},{"realPngDecodes",state.decodes.load()},{"realStagingCalls",state.stages.load()},{"maximumActiveReplies",state.maximum.load()},{"allThreadsDrained",MediaServiceHost::process_available()},{"noLiveNetwork",true},{"noRealMaxPresenter",true},{"draftAcceptanceClaimed",false},{"loadedModules",loaded}}).toJson(QJsonDocument::Compact)<<'\n';return 0;
}
#include "composer_test.moc"
