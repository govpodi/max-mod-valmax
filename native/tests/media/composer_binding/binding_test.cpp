#include "../../../media/composer_binding.h"
#include <QAbstractListModel>
#include <QCoreApplication>
#include <QJSValue>
#include <QQmlEngine>
#include <QQmlContext>
#include <QTextStream>
#include <QThread>
#include <functional>
#include <limits>
#include <memory>
#include <thread>

namespace MeTypes {
struct ContactId { qint64 value = 0; };
struct ChatId { qint64 value = 0; };
struct AccountId { qint64 value = 0; };
}
Q_DECLARE_METATYPE(MeTypes::ContactId)
Q_DECLARE_METATYPE(MeTypes::ChatId)
Q_DECLARE_METATYPE(MeTypes::AccountId)
static std::function<void()> idAction, contactAction, chatAction, commonAction;
static QString contactOverride, chatOverride;
static int helperKind = 0, privateCalls = 0;
static void once(std::function<void()>& stored) { auto call = std::move(stored); stored = {}; if (call) call(); }
namespace SelfInfo {
class Context : public QObject {
    Q_OBJECT
    Q_PROPERTY(MeTypes::ContactId id READ id)
    Q_PROPERTY(MeTypes::AccountId accountId READ internalId)
public:
    using QObject::QObject;
    qint64 current = 9007199254740993LL;
    MeTypes::ContactId id() const { const MeTypes::ContactId value{current}; once(idAction); return value; }
    MeTypes::AccountId internalId() const { return {42}; }
};
}
class WrongIdContext : public SelfInfo::Context {
    Q_OBJECT
    Q_PROPERTY(qint64 id READ badId)
public:
    using SelfInfo::Context::Context;
    qint64 badId() const { return 9007199254740993LL; }
};
namespace Ui::Dependency {
class Common : public QObject {
    Q_OBJECT
    Q_PROPERTY(SelfInfo::Context* selfInfo MEMBER selfInfo CONSTANT)
public:
    using QObject::QObject;
    SelfInfo::Context* selfInfo = nullptr;
};
}
namespace Ui::History::AttachmentUpload {
class UploadModel : public QAbstractListModel {
    Q_OBJECT
public:
    using QAbstractListModel::QAbstractListModel;
    int rowCount(const QModelIndex& = {}) const override { return 0; }
    QVariant data(const QModelIndex&, int) const override { return {}; }
};
class View : public QObject {
    Q_OBJECT
    Q_PROPERTY(Ui::History::AttachmentUpload::UploadModel* uploadModel MEMBER uploadModel CONSTANT)
public:
    using QObject::QObject;
    UploadModel* uploadModel = nullptr;
};
}
namespace Ui::History::MessageSender {
class View : public QObject {
    Q_OBJECT
    Q_PROPERTY(MeTypes::ChatId chatId READ chatId)
    Q_PROPERTY(Ui::History::AttachmentUpload::View* uploadView MEMBER uploadView CONSTANT)
public:
    using QObject::QObject;
    qint64 currentChat = -9007199254740993LL;
    MeTypes::ChatId chatId() const { return {currentChat}; }
    Ui::History::AttachmentUpload::View* uploadView = nullptr;
};
}
namespace Ui::Singletons {
class QmlUtils : public QObject {
    Q_OBJECT
public:
    using QObject::QObject;
    Q_INVOKABLE QString contactIdString(MeTypes::ContactId id) {
        const auto result = contactOverride.isEmpty() ? QString::number(id.value) : contactOverride;
        once(contactAction); return result;
    }
    Q_INVOKABLE QString chatIdString(MeTypes::ChatId id) {
        const auto result = chatOverride.isEmpty() ? QString::number(id.value) : chatOverride;
        once(chatAction); return result;
    }
};
}
class PrivateUtils : public Ui::Singletons::QmlUtils {
    Q_OBJECT
private:
    Q_INVOKABLE QString contactIdString(MeTypes::ContactId id) { ++privateCalls; return QString::number(id.value); }
};
class SignalUtils : public Ui::Singletons::QmlUtils {
    Q_OBJECT
signals:
    QString contactIdString(MeTypes::ContactId id);
};
class Composer : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariant commonDependency READ commonDependency)
    Q_PROPERTY(QObject* messageSenderViewContext MEMBER sender)
    Q_PROPERTY(QObject* attachmentUploadModel MEMBER model)
    Q_PROPERTY(bool visible MEMBER visible)
    Q_PROPERTY(bool enabled MEMBER enabled)
    Q_PROPERTY(bool isCurrentChat MEMBER current)
public:
    using QObject::QObject;
    QVariant common;
    QVariant commonDependency() const { const auto value=common; once(commonAction); return value; }
    QObject* sender = nullptr;
    QObject* model = nullptr;
    bool visible = true, enabled = true, current = true;
};
struct Fixture {
    std::unique_ptr<QQmlEngine> engine = std::make_unique<QQmlEngine>();
    QPointer<Composer> composer = new Composer;
    QPointer<Ui::Dependency::Common> common = new Ui::Dependency::Common(composer);
    QPointer<SelfInfo::Context> self = new SelfInfo::Context(common);
    QPointer<Ui::History::MessageSender::View> sender = new Ui::History::MessageSender::View(composer);
    QPointer<Ui::History::AttachmentUpload::View> upload = new Ui::History::AttachmentUpload::View(sender);
    QPointer<Ui::History::AttachmentUpload::UploadModel> model = new Ui::History::AttachmentUpload::UploadModel(upload);
    Fixture() {
        QQmlEngine::setContextForObject(composer, engine->rootContext());
        common->selfInfo = self; sender->uploadView = upload; upload->uploadModel = model;
        composer->common = QVariant::fromValue<QObject*>(common);
        composer->sender = sender; composer->model = model;
    }
    ~Fixture() { delete composer.data(); idAction={}; contactAction={}; chatAction={}; commonAction={}; contactOverride.clear(); chatOverride.clear(); helperKind=0; }
    auto resolve() { return valmax::media::resolve_composer(engine.get(), composer); }
};

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    qmlRegisterSingletonType<Ui::Singletons::QmlUtils>("QmlSingletonesModule",1,0,"QmlUtils",
        [](QQmlEngine*,QJSEngine*)->QObject* {
            if (helperKind == 1) return new PrivateUtils;
            if (helperKind == 2) return new SignalUtils;
            return new Ui::Singletons::QmlUtils;
        });
    int checks = 0;
    const auto check=[&](bool pass,const char* label) { ++checks; if(!pass) { QTextStream(stderr)<<label<<'\n'; std::exit(1); } };
    {
        Fixture f; auto result=f.resolve(); check(bool(result),"valid typed native chain");
        check(result.value->accountId==9007199254740993LL,"lossless account beyond double precision");
        check(result.value->chatId==QStringLiteral("-9007199254740993"),"lossless signed chat");
        check(result.value->accountId!=42,"use contact id not internal account id");
        check(result.value->composer==f.composer&&result.value->common==f.common&&result.value->selfInfo==f.self,"actual account pointers");
        check(result.value->sender==f.sender&&result.value->upload==f.upload&&result.value->model==f.model,"actual sender pointers");
        check(valmax::media::same_composer(*result.value,*f.resolve().value),"stable capture equal");
        f.self->current=std::numeric_limits<qint64>::max();
        auto changed=f.resolve();check(bool(changed)&&changed.value->accountId==std::numeric_limits<qint64>::max(),"max account lossless");
        check(!valmax::media::same_composer(*result.value,*changed.value),"account change invalidates comparison");
        f.sender->currentChat=0; auto saved=f.resolve();check(bool(saved)&&saved.value->chatId==QStringLiteral("0"),"saved messages zero allowed");
        f.sender->currentChat=std::numeric_limits<qint64>::min(); auto lowest=f.resolve();
        check(bool(lowest)&&lowest.value->chatId==QStringLiteral("-9223372036854775808"),"min signed chat allowed");
        check(!valmax::media::same_composer(*saved.value,*lowest.value),"chat change invalidates comparison");
        f.composer->common=QVariant::fromValue(f.engine->newQObject(f.common));
        check(bool(f.resolve()),"QML var QJSValue QObject wrapper allowed");
    }
    for (qint64 id : {qint64(0),qint64(-1),std::numeric_limits<qint64>::min()}) {
        Fixture f; f.self->current=id;check(!f.resolve(),"nonpositive account rejected");
    }
    for (const auto& text : {QStringLiteral("+12"),QStringLiteral("012"),QStringLiteral(" 12"),QStringLiteral("12.0"),QStringLiteral("9223372036854775808"),QStringLiteral("1e3"),QStringLiteral("-0"),QStringLiteral("abc")}) {
        Fixture f;contactOverride=text;check(!f.resolve(),"noncanonical account rejected");
        contactOverride.clear();chatOverride=text;check(!f.resolve(),"noncanonical chat rejected");
    }
    {
        Fixture f;f.composer->visible=false;check(!f.resolve(),"hidden composer unavailable");
        f.composer->visible=true;f.composer->enabled=false;check(!f.resolve(),"disabled composer unavailable");
        f.composer->enabled=true;f.composer->current=false;check(!f.resolve(),"noncurrent composer unavailable");
    }
    {
        Fixture f;QObject foreign;f.composer->common=QVariant::fromValue(&foreign);check(!f.resolve(),"foreign common rejected");
        f.composer->common=QVariant::fromValue<QObject*>(f.common);f.composer->sender=&foreign;check(!f.resolve(),"foreign sender rejected");
        f.composer->sender=f.sender;f.composer->model=&foreign;check(!f.resolve(),"model relation mismatch rejected");
    }
    {
        Fixture f;QQmlEngine other;check(!valmax::media::resolve_composer(&other,f.composer),"other engine rejected");
        QObject unbound;check(!valmax::media::resolve_composer(f.engine.get(),&unbound),"unbound object rejected");
        check(!valmax::media::resolve_composer(nullptr,f.composer),"missing engine rejected");
        check(!valmax::media::resolve_composer(f.engine.get(),nullptr),"missing composer rejected");
        valmax::media::ComposerError error{};std::thread worker([&]{error=f.resolve().error;});worker.join();
        check(error==valmax::media::ComposerError::WrongThread,"wrong thread rejected before model access");
    }
    {
        Fixture f;idAction=[&]{delete f.composer.data();};check(!f.resolve(),"property getter deletes composer");
    }
    {
        Fixture f;f.common->selfInfo=new WrongIdContext(f.common);check(!f.resolve(),"plain numeric id cannot replace typed ContactId");
    }
    {
        Fixture f;commonAction=[&]{f.engine.reset();};check(!f.resolve(),"object property getter deletes engine");
    }
    {
        Fixture f;idAction=[&]{f.engine.reset();};check(!f.resolve(),"typed property getter deletes engine");
    }
    {
        Fixture f;contactAction=[&]{delete f.composer.data();};check(!f.resolve(),"formatter deletes composer");
    }
    {
        Fixture f;chatAction=[&]{delete f.composer.data();};check(!f.resolve(),"chat formatter deletes composer");
    }
    {
        Fixture f;contactAction=[&]{f.engine.reset();};check(!f.resolve(),"formatter deletes engine with surviving native objects");
    }
    {
        Fixture f;contactAction=[&]{f.self->current=23;};check(!f.resolve(),"reentrant account mutation fails snapshot comparison");
        check(bool(f.resolve()),"stable next call works after account mutation");
    }
    {
        Fixture f;chatAction=[&]{f.sender->currentChat=-23;};check(!f.resolve(),"reentrant chat mutation fails snapshot comparison");
    }
    {
        Fixture f;auto first=f.resolve();delete f.composer.data();check(!valmax::media::same_composer(*first.value,*first.value),"destroyed weak pointers not equal capability");
    }
    {
        Fixture f;helperKind=1;check(!f.resolve(),"private formatter rejected");
        check(privateCalls==0,"private formatter never invoked");
    }
    {
        Fixture f;helperKind=2;
        auto* utils=f.engine->singletonInstance<QObject*>(QStringLiteral("QmlSingletonesModule"),QStringLiteral("QmlUtils"));
        int emissions=0;
        QObject::connect(qobject_cast<SignalUtils*>(utils),&SignalUtils::contactIdString,
            [&emissions](MeTypes::ContactId id){++emissions;return QString::number(id.value);});
        check(!f.resolve(),"signal formatter rejected");check(emissions==0,"signal formatter never emitted");
    }
    QTextStream(stdout)<<"{\"ok\":true,\"checks\":"<<checks<<"}\n";
}
#include "binding_test.moc"
