#include "draft_dispatcher.h"
#include <QFile>
#include <QFileInfo>
#include <QGuiApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPluginLoader>
#include <QQmlEngine>
#include <QTextStream>
#include <QThread>
#include <functional>
#include <limits>
#include <memory>
#include <thread>
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
using namespace valmax::media;
namespace {
int checks=0;
void check(bool ok,const char* label) {
    ++checks;
    if(!ok) {QTextStream(stderr)<<"FAILED: "<<label<<'\n';std::_Exit(1);}
}
struct Observation {int calls=0,reads=0,sends=0;QList<QUrl> urls;Qt::HANDLE thread=nullptr;};
}
class ExactModel final: public QObject {
    Q_OBJECT
    Q_PROPERTY(qsizetype attachmentsCount READ attachmentsCount)
public:
    explicit ExactModel(Observation* seen,QObject* parent=nullptr):QObject(parent),seen_(seen) {}
    qsizetype count=0;
    std::function<void()> onRead;
    qsizetype attachmentsCount() const {
        ++seen_->reads;
        const auto result=count;const auto callback=onRead;
        if(callback)callback(); // may delete this; no later member access
        return result;
    }
private: Observation* seen_;
};
class ExactUpload final: public QObject {
    Q_OBJECT
public:
    explicit ExactUpload(Observation* seen,QObject* parent=nullptr):QObject(parent),seen_(seen) {}
    std::function<void()> onUpload;
    Q_INVOKABLE void uploadPhotoOrVideo(QList<QUrl> urls) {
        ++seen_->calls;seen_->urls=urls;seen_->thread=QThread::currentThreadId();
        const auto callback=onUpload;
        if(callback)callback(); // may delete this; no later member access
    }
    Q_INVOKABLE void sendPressed() {++seen_->sends;}
private: Observation* seen_;
};
class StringModel final: public QObject { Q_OBJECT Q_PROPERTY(QString attachmentsCount READ count)
public: QString count()const{return QStringLiteral("0");} };
class DoubleModel final: public QObject { Q_OBJECT Q_PROPERTY(double attachmentsCount READ count)
public: double count()const{return 0.;} };
class IntModel final: public QObject { Q_OBJECT Q_PROPERTY(int attachmentsCount READ count)
public: int count()const{return 0;} };
class WrongReturn final: public QObject { Q_OBJECT
public: Q_INVOKABLE int uploadPhotoOrVideo(QList<QUrl>) {return 0;} };
class WrongArgument final: public QObject { Q_OBJECT
public: Q_INVOKABLE void uploadPhotoOrVideo(QStringList) {} };
class PrivateUpload final: public QObject { Q_OBJECT
private slots: void uploadPhotoOrVideo(QList<QUrl>) {} };
class ProtectedUpload final: public QObject { Q_OBJECT
protected slots: void uploadPhotoOrVideo(QList<QUrl>) {} };
class SignalUpload final: public QObject { Q_OBJECT
signals: void uploadPhotoOrVideo(QList<QUrl>); };
class SlotUpload final: public QObject { Q_OBJECT
public: int calls=0;
public slots: void uploadPhotoOrVideo(QList<QUrl>) {++calls;} };
namespace {
struct Fixture {
    Observation seen;
    std::unique_ptr<QQmlEngine> engine=std::make_unique<QQmlEngine>();
    QPointer<MediaEngineContext> context=MediaEngineContext::install(engine.get());
    EngineMediaLease lease=context->bind(9007199254740993LL,9007199254740995ULL);
    std::shared_ptr<std::atomic<uint64_t>> generation=std::make_shared<std::atomic<uint64_t>>(1);
    std::unique_ptr<QObject> sender=std::make_unique<QObject>();
    QPointer<ExactUpload> upload=new ExactUpload(&seen,sender.get());
    QPointer<ExactModel> model=new ExactModel(&seen,upload);
    SearchGeneration search()const{return{generation,generation->load()};}
    DraftBinding binding()const {
        auto value=DraftBinding::create(context,lease,sender.get(),upload,model,search(),[]{return true;});
        check(value.has_value(),"trusted native binding");return *value;
    }
    RetainedDraftFile file(const QUrl& url)const{return{url,lease,search()};}
};
template<class Upload> void reject_upload(const QUrl& url) {
    Fixture f;Upload upload;
    auto binding=DraftBinding::create(f.context,f.lease,f.sender.get(),&upload,f.model,f.search(),[]{return true;});
    check(binding.has_value(),"lifetime binding does not claim method ABI");
    check(dispatch_retained(*binding,f.file(url))==DraftDispatchResult::Unavailable,"wrong upload method rejected");
    check(f.seen.calls==0 && f.seen.reads==0,"reject method before model getter");
}
template<class Model> void reject_model(const QUrl& url) {
    Fixture f;Model model;
    auto binding=DraftBinding::create(f.context,f.lease,f.sender.get(),f.upload,&model,f.search(),[]{return true;});
    check(binding.has_value(),"foreign property type binding");
    check(dispatch_retained(*binding,f.file(url))==DraftDispatchResult::Unavailable,"count type cannot coerce");
    check(f.seen.calls==0,"wrong model never invokes upload");
}
void ordinary(const QUrl& url) {
    Fixture f;const auto binding=f.binding();const auto dto=f.file(url);
    check(dispatch_retained(binding,dto)==DraftDispatchResult::Dispatched,"public exact method dispatched");
    check(f.seen.calls==1 && f.seen.urls==QList<QUrl>{url},"one exact owned file URL received");
    check(f.seen.thread==QThread::currentThreadId() && f.seen.sends==0,"direct GUI call and no send");
    check(f.model->count==0,"dispatch does not claim attachment accepted");
    f.model->count=9;
    check(dispatch_retained(binding,dto)==DraftDispatchResult::Dispatched,"nine permits one file");
    for(auto n:{qsizetype(10),qsizetype(11),std::numeric_limits<qsizetype>::max()}) {
        f.model->count=n;const auto count=f.seen.calls;
        check(dispatch_retained(binding,dto)==DraftDispatchResult::Limit && f.seen.calls==count,"limit does not invoke");
    }
    f.model->count=-1;const auto count=f.seen.calls;
    check(dispatch_retained(binding,dto)==DraftDispatchResult::Unavailable && f.seen.calls==count,"negative count invalid");
    f.model->count=0;
    SlotUpload slot;
    auto slotBinding=DraftBinding::create(f.context,f.lease,f.sender.get(),&slot,f.model,f.search(),[]{return true;});
    check(slotBinding && dispatch_retained(*slotBinding,dto)==DraftDispatchResult::Dispatched && slot.calls==1,"public exact slot supported");
}
void invalid_urls(const QUrl& url) {
    Fixture f;auto binding=f.binding();
    const QList<QUrl> invalid={QUrl(),QUrl(QStringLiteral("https://example.test/a.png")),
        QUrl(QStringLiteral("file://server/share/a.png")),QUrl(QStringLiteral("file:relative.png")),
        QUrl(QStringLiteral("file:///tmp/a.png")),QUrl(QStringLiteral("file:///C:/a.png?")),
        QUrl(QStringLiteral("file:///C:/a.png#")),QUrl(QStringLiteral("file:///C:/a/%2E%2E/b.png")),
        QUrl(QStringLiteral("file:///C:/a%00b.png")),QUrl(QStringLiteral("file:///C:/a.png:stream")),
        QUrl(QStringLiteral("file:///C:/a//b.png")),QUrl(QStringLiteral("file:///C:/")+QString(96000,u'a'))};
    for(const auto& bad:invalid) {
        auto dto=f.file(bad);
        check(dispatch_retained(binding,dto)==DraftDispatchResult::Unavailable,"invalid native file URL rejected");
        check(f.seen.calls==0 && f.seen.reads==0,"bad URL no reflection or invocation");
    }
    check(dispatch_retained(binding,f.file(url))==DraftDispatchResult::Dispatched,"valid owned URL still usable");
}
void generations(const QUrl& url) {
    Fixture f;const auto binding=f.binding();const auto dto=f.file(url);
    f.generation->store(2);
    check(dispatch_retained(binding,dto)==DraftDispatchResult::Stale && f.seen.reads==0,"old search rejected before getter");
    check(dispatch_retained(binding,f.file(url))==DraftDispatchResult::Dispatched,"current search on same native binding");
    auto wrongIdentity=f.file(url);wrongIdentity.search.current=std::make_shared<std::atomic<uint64_t>>(2);
    check(dispatch_retained(binding,wrongIdentity)==DraftDispatchResult::Stale,"same number different capability rejected");
    auto zero=f.file(url);zero.search.captured=0;
    check(dispatch_retained(binding,zero)==DraftDispatchResult::Stale,"zero capture invalid");
    auto previous=f.file(url);f.generation->store(0);
    check(dispatch_retained(binding,previous)==DraftDispatchResult::Stale,"closed generation invalid");
    check(!DraftBinding::create(f.context,f.lease,f.sender.get(),f.upload,f.model,f.search(),[]{return true;}),"cannot bind closed generation");
    Fixture next;auto nextBinding=next.binding();auto old=next.file(url);
    next.lease=next.context->advance(next.lease);
    check(dispatch_retained(nextBinding,old)==DraftDispatchResult::Stale,"advanced engine rejects old binding and DTO");
    nextBinding=next.binding();
    check(dispatch_retained(nextBinding,old)==DraftDispatchResult::Stale,"old DTO cannot use new engine binding");
    Fixture other;auto cross=next.file(url);cross.lease=other.lease;
    check(dispatch_retained(nextBinding,cross)==DraftDispatchResult::Stale,"other engine same account/page IDs rejected");
}
void destroyed(const QUrl& url) {
    for(int which=0;which<4;++which) {
        Fixture f;auto binding=f.binding();auto dto=f.file(url);
        if(which==0)f.sender.reset();
        if(which==1)delete f.upload.data();
        if(which==2)delete f.model.data();
        if(which==3)f.engine.reset();
        check(dispatch_retained(binding,dto)==(which==3?DraftDispatchResult::Stale:DraftDispatchResult::Unavailable),"destroyed owner rejects");
        check(f.seen.calls==0,"destroyed objects not invoked");
    }
}
void live_identity(const QUrl& url) {
    {
        Fixture f;bool identity=true;
        auto binding=DraftBinding::create(f.context,f.lease,f.sender.get(),f.upload,f.model,f.search(),[&]{return identity;});
        check(binding.has_value(),"live identity binding");
        f.model->onRead=[&]{identity=false;};
        check(dispatch_retained(*binding,f.file(url))==DraftDispatchResult::Stale,"getter changes native identity without capability revocation");
        check(f.seen.reads==1&&f.seen.calls==0&&f.lease.valid()&&f.search().valid(),"live identity check prevents wrong-chat dispatch");
    }
    for(int which=0;which<5;++which) {
        Fixture f;
        auto binding=DraftBinding::create(f.context,f.lease,f.sender.get(),f.upload,f.model,f.search(),[&] {
            if(which==0)f.sender.reset();
            if(which==1)f.engine.reset();
            if(which==2)f.generation->store(2);
            if(which==3)throw 1;
            return which!=4;
        });
        check(binding.has_value(),"reentrant live identity binding");
        const auto expected=(which==1||which==2||which==4)?DraftDispatchResult::Stale:DraftDispatchResult::Unavailable;
        check(dispatch_retained(*binding,f.file(url))==expected,"host callback liveness and identity rechecked");
        check(f.seen.reads==1&&f.seen.calls==0,"identity callback never invokes revoked target");
    }
    Fixture f;
    check(!DraftBinding::create(f.context,f.lease,f.sender.get(),f.upload,f.model,f.search(),{}),"missing mandatory identity callback rejected");
}
void getter_reentry(const QUrl& url) {
    for(int which=0;which<5;++which) {
        Fixture f;auto binding=f.binding();auto dto=f.file(url);
        f.model->onRead=[&]{
            if(which==0)f.generation->store(2);
            if(which==1)(void)f.context->detach(f.lease);
            if(which==2)f.sender.reset();
            if(which==3)delete f.model.data();
            if(which==4)f.engine.reset();
        };
        const auto expected=(which==0||which==1||which==4)?DraftDispatchResult::Stale:DraftDispatchResult::Unavailable;
        check(dispatch_retained(binding,dto)==expected,"property getter invalidation rechecked");
        check(f.seen.reads==1 && f.seen.calls==0,"invalidated getter never dispatches");
    }
    {
        Fixture f;auto binding=std::make_unique<DraftBinding>(f.binding());
        auto dto=std::make_unique<RetainedDraftFile>(f.file(url));
        f.model->onRead=[&]{binding.reset();dto.reset();};
        check(dispatch_retained(*binding,*dto)==DraftDispatchResult::Dispatched,"reentrant caller argument deletion uses snapshots");
        check(f.seen.calls==1 && !binding && !dto,"deleted caller containers no UAF");
    }
}
void invoked_reentry(const QUrl& url) {
    for(int which=0;which<4;++which) {
        Fixture f;auto binding=std::make_unique<DraftBinding>(f.binding());auto dto=std::make_unique<RetainedDraftFile>(f.file(url));
        f.upload->onUpload=[&]{
            if(which==0)f.sender.reset();
            if(which==1)f.engine.reset();
            if(which==2){f.generation->store(2);(void)f.context->advance(f.lease);}
            if(which==3){binding.reset();dto.reset();}
        };
        check(dispatch_retained(*binding,*dto)==DraftDispatchResult::Dispatched,"already invoked remains dispatched after reentry");
        check(f.seen.calls==1 && f.seen.urls==QList<QUrl>{url} && f.seen.sends==0,"reentrant invoke exact call once no send");
    }
}
void wrong_thread(const QUrl& url) {
    Fixture f;auto binding=f.binding();auto dto=f.file(url);DraftDispatchResult result=DraftDispatchResult::Dispatched;
    std::thread worker([&]{result=dispatch_retained(binding,dto);});worker.join();
    check(result==DraftDispatchResult::Unavailable && f.seen.calls==0 && f.seen.reads==0,"worker rejects before binding reflection");
    check(dispatch_retained(binding,dto)==DraftDispatchResult::Dispatched,"GUI still dispatches after wrong-thread reject");
}
void message(QtMsgType type,const QMessageLogContext&,const QString& text) {
    if(type==QtFatalMsg){QTextStream(stderr)<<"Qt fatal: "<<text<<'\n';std::_Exit(80);}
    if(type==QtCriticalMsg||type==QtWarningMsg)QTextStream(stderr)<<text<<'\n';
}
QJsonObject modules() {
    QJsonObject value;
    for(const wchar_t* name:{L"Qt6Core.dll",L"Qt6Gui.dll",L"Qt6Qml.dll",L"Qt6Quick.dll"}) {
        HMODULE module=GetModuleHandleW(name);wchar_t path[32768]{};
        check(module && GetModuleFileNameW(module,path,32768)>0,"exact Qt module loaded");
        value.insert(QString::fromWCharArray(name),QString::fromWCharArray(path));
    }
    return value;
}
}
int main(int argc,char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    qInstallMessageHandler(message);
    wchar_t executable[32768]{};check(GetModuleFileNameW(nullptr,executable,32768)>0,"own executable path");
    const auto directory=QFileInfo(QString::fromWCharArray(executable)).absolutePath();
    QPluginLoader platform(directory+QStringLiteral("/plugins/platforms/qwindows.dll"));
    check(platform.load(),"pinned QPA preflight");
    QGuiApplication app(argc,argv);
    const auto path=directory+QStringLiteral("/owned-dispatch-fixture.png");
    QFile synthetic(path);check(synthetic.open(QIODevice::WriteOnly|QIODevice::NewOnly),"new owned fixture only");
    const QByteArray bytes=QByteArray::fromHex("89504e470d0a1a0a")+QByteArrayLiteral("SYNTHETIC-DISPATCH-NO-DECODE");
    check(synthetic.write(bytes)==bytes.size() && synthetic.flush(),"synthetic native fixture written");synthetic.close();
    const auto url=QUrl::fromLocalFile(path);
    ordinary(url);invalid_urls(url);generations(url);destroyed(url);getter_reentry(url);live_identity(url);invoked_reentry(url);wrong_thread(url);
    reject_upload<WrongReturn>(url);reject_upload<WrongArgument>(url);reject_upload<PrivateUpload>(url);
    reject_upload<ProtectedUpload>(url);reject_upload<SignalUpload>(url);reject_upload<QObject>(url);
    reject_model<StringModel>(url);reject_model<DoubleModel>(url);reject_model<IntModel>(url);reject_model<QObject>(url);
    {
        Fixture f;QObject model;model.setProperty("attachmentsCount",QVariant::fromValue(qsizetype(0)));
        auto binding=DraftBinding::create(f.context,f.lease,f.sender.get(),f.upload,&model,f.search(),[]{return true;});
        check(binding && dispatch_retained(*binding,f.file(url))==DraftDispatchResult::Unavailable,"dynamic property not native typed model");
    }
    check(synthetic.open(QIODevice::ReadOnly) && synthetic.readAll()==bytes,"all outcomes leave retained file untouched");synthetic.close();
    const auto loaded=modules();
    QJsonObject result{{"passed",true},{"checks",checks},{"qt",QString::fromLatin1(qVersion())},{"loadedModules",loaded},
        {"mockUploadInvoked",true},{"actualMaxInvoked",false},{"attachmentAcceptanceProven",false},
        {"retainedFileUnchanged",true},{"wrongThreadRejected",true},{"reentrantGetterAndInvokeTested",true},
        {"networkUsed",false},{"messagesSent",false},{"sourceFixtureCreatedByTest",true}};
    QTextStream(stdout)<<QJsonDocument(result).toJson(QJsonDocument::Compact)<<'\n';
    return 0;
}
#include "dispatcher_test.moc"

