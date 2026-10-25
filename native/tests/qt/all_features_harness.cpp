#include <QGuiApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QEventLoop>
#include <QPluginLoader>
#include <QQmlExtensionPlugin>
#include <QQuickImageProvider>
#include <QPointer>
#include <QFileInfo>
#include <QTextStream>
#include <QTimer>
#include <windows.h>
#include <cstdlib>

namespace {
QList<QObject *> mediaContexts(QQmlEngine &engine) {
    QList<QObject *> contexts;
    for (auto *child : engine.children())
        if (QByteArray(child->metaObject()->className()) == "valmax::media::MediaEngineContext")
            contexts.append(child);
    return contexts;
}
class ForeignProvider final : public QQuickImageProvider {
public:
    ForeignProvider() : QQuickImageProvider(QQuickImageProvider::Image) {}
};
}

int main(int argc,char **argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    qInstallMessageHandler([](QtMsgType type,const QMessageLogContext &,const QString &text){QTextStream(stderr)<<text<<'\n';if(type==QtFatalMsg)std::_Exit(6);});
    if(argc!=3 || qEnvironmentVariable("QT_QPA_PLATFORM")!=QStringLiteral("windows")) return 2;
    QPluginLoader platform(QFileInfo(QString::fromLocal8Bit(argv[0])).absolutePath()+QStringLiteral("/plugins/platforms/qwindows.dll"));
    if(!platform.load()) {QTextStream(stderr)<<platform.errorString();return 6;}
    QGuiApplication app(argc,argv);
    QJsonArray cycles;
    int mediaChecks = 0;
    int composerChecks = 0;
    const auto checkMedia = [&](bool valid, const char *label) {
        ++mediaChecks;
        if (!valid) { QTextStream(stderr)<<"Media engine check failed: "<<label<<'\n'; }
        return valid;
    };
    const auto checkComposer = [&](bool valid, const char *label) {
        ++composerChecks;
        if (!valid) QTextStream(stderr)<<"Media composer check failed: "<<label<<'\n';
        return valid;
    };
    QPluginLoader plugin(QString::fromLocal8Bit(argv[1])+QStringLiteral("/ValMAX/Native/valmaxnativeplugin.dll"));
    for(int i=0;i<3;++i) {
        QPointer<QObject> contextAfterScope;
        {
        QQmlEngine engine;
        engine.addImportPath(QString::fromLocal8Bit(argv[1]));
        QQmlComponent component(&engine,QUrl::fromLocalFile(QString::fromLocal8Bit(argv[2])),QQmlComponent::PreferSynchronous);
        if(component.isError()) {QTextStream(stderr)<<component.errorString();return 3;}
        QScopedPointer<QObject> root(component.create());
        if(!root) {QTextStream(stderr)<<component.errorString();return 4;}
        auto contexts = mediaContexts(engine);
        auto *provider = engine.imageProvider(QStringLiteral("valmaxmedia"));
        if (!checkMedia(contexts.size()==1,"context installed by import")
            || !checkMedia(provider!=nullptr,"provider installed by import")) return 8;
        contextAfterScope = contexts.first();
        if (!checkMedia(contextAfterScope->parent()==&engine,"context owned by engine")
            || !checkMedia(qmlTypeId("ValMAX.Native",1,0,"MediaEngineContext")==-1,"context not registered with QML")) return 8;
        {
            QQmlComponent smoke(&engine);
            smoke.setData("import ValMAX.Native 1.0\nMediaComposer { composer: null }",QUrl(QStringLiteral("qrc:/native-media-smoke.qml")));
            QScopedPointer<QObject> media(smoke.create());
            if (!media) QTextStream(stderr)<<smoke.errorString();
            if (!checkComposer(media!=nullptr,"registered MediaComposer constructs")
                || !checkComposer(qmlTypeId("ValMAX.Native",1,0,"MediaComposer")>=0,"registered public adapter type")
                || !checkComposer(!media->property("available").toBool() && !media->property("busy").toBool(),"null composer unavailable and idle")
                || !checkComposer(media->property("rows").toList().isEmpty(),"null composer has no public results")
                || !checkComposer(media->metaObject()->indexOfProperty("accountId")==-1 && media->metaObject()->indexOfProperty("stagingRoot")==-1,"no identity or path setter exposed")) return 9;
            bool accepted=true;
            if (!checkComposer(QMetaObject::invokeMethod(media.data(),"search",Qt::DirectConnection,Q_RETURN_ARG(bool,accepted),Q_ARG(QString,QStringLiteral("pic")),Q_ARG(QString,QStringLiteral("synthetic")))&&!accepted,"unavailable search finite")
                || !checkComposer(QMetaObject::invokeMethod(media.data(),"select",Qt::DirectConnection,Q_RETURN_ARG(bool,accepted),Q_ARG(QString,QString(32,u'a')))&&!accepted,"unavailable selection finite")
                || !checkComposer(QMetaObject::invokeMethod(media.data(),"reset",Qt::DirectConnection),"null composer reset safe")) return 9;
            const auto fragmentPath=QFileInfo(QString::fromLocal8Bit(argv[0])).absolutePath()+QStringLiteral("/inline_native_fragment.qml");
            QQmlComponent declaration(&engine,QUrl::fromLocalFile(fragmentPath),QQmlComponent::PreferSynchronous);
            QScopedPointer<QObject> inlineRoot(declaration.create());
            if(!inlineRoot)QTextStream(stderr)<<declaration.errorString();
            if(!checkComposer(inlineRoot!=nullptr,"actual generated declaration parses with real plugin")
                || !checkComposer(!inlineRoot->property("modInlineBusy").toBool() && inlineRoot->property("modInlineRows").toList().isEmpty(),"declaration creation does not search")
                || !checkComposer(QMetaObject::invokeMethod(inlineRoot.data(),"modInlineSearch",Qt::DirectConnection),"explicit unavailable declaration search safe")
                || !checkComposer(!inlineRoot->property("modInlineStatus").toString().isEmpty(),"unavailable declaration gives finite Russian status")) return 9;
            auto *input=inlineRoot->findChild<QObject *>(QStringLiteral("synthetic-chat-input"));
            if(!checkComposer(input && input->setProperty("text",QStringLiteral("@vid newer")),"draft text change accepted")
                || !checkComposer(inlineRoot->property("modInlineMode").toString()==QStringLiteral("vid") && inlineRoot->property("modInlineStatus").toString().isEmpty(),"draft change resets native session/status")
                || !checkComposer(QMetaObject::invokeMethod(inlineRoot.data(),"modInlineClose",Qt::DirectConnection),"explicit close safe")
                || !checkComposer(!inlineRoot->property("modInlineVisible").toBool(),"closed draft remains dismissed"))return 9;
            inlineRoot.reset();
            if(!checkComposer(!media->property("available").toBool(),"declaration destruction preserves other unavailable adapter"))return 9;
        }
        auto *extension=qobject_cast<QQmlExtensionPlugin *>(plugin.instance());
        if (!checkMedia(extension!=nullptr,"actual production plugin instance")) return 8;
        extension->initializeEngine(&engine,"ValMAX.Native");
        if (!checkMedia(mediaContexts(engine)==contexts,"repeated initialize does not duplicate context")
            || !checkMedia(engine.imageProvider(QStringLiteral("valmaxmedia"))==provider,"repeated initialize preserves provider")) return 8;
        QEventLoop loop;
        int code=5;
        QObject::connect(&engine,&QQmlEngine::exit,&loop,[&](int value){code=value;loop.quit();});
        QTimer timer; timer.setSingleShot(true);timer.start(8000);
        QObject::connect(&timer,&QTimer::timeout,&loop,&QEventLoop::quit);
        loop.exec();
        cycles.append(QJsonObject{{QStringLiteral("cycle"),i},{QStringLiteral("checks"),root->property("checks").toInt()},
            {QStringLiteral("failure"),root->property("failure").toString()},{QStringLiteral("exitCode"),code}});
        if(code!=0) {QTextStream(stdout)<<QJsonDocument(cycles).toJson(QJsonDocument::Compact)<<'\n';return code;}
        root.reset();
        engine.clearSingletons();
        if (!checkMedia(contextAfterScope && mediaContexts(engine)==contexts,"context survives clearSingletons")
            || !checkMedia(engine.imageProvider(QStringLiteral("valmaxmedia"))==provider,"provider survives clearSingletons")) return 8;
        extension->initializeEngine(&engine,"ValMAX.Native");
        if (!checkMedia(mediaContexts(engine)==contexts,"reinitialize after clearSingletons has no duplicate")
            || !checkMedia(engine.imageProvider(QStringLiteral("valmaxmedia"))==provider,"reinitialize after clearSingletons preserves provider")) return 8;
        engine.removeImageProvider(QStringLiteral("valmaxmedia"));
        extension->initializeEngine(&engine,"ValMAX.Native");
        if (!checkMedia(engine.imageProvider(QStringLiteral("valmaxmedia"))==nullptr,"removed provider stays unavailable")
            || !checkMedia(mediaContexts(engine)==contexts,"removed provider does not replace context")) return 8;
        }
        if (!checkMedia(contextAfterScope.isNull(),"context destroyed with engine")) return 8;
    }
    {
        QQmlEngine collision;
        auto *foreign = new ForeignProvider;
        collision.addImageProvider(QStringLiteral("valmaxmedia"),foreign);
        auto *extension=qobject_cast<QQmlExtensionPlugin *>(plugin.instance());
        if (!extension) return 8;
        extension->initializeEngine(&collision,"ValMAX.Native");
        extension->initializeEngine(&collision,"ValMAX.Native");
        if (!checkMedia(collision.imageProvider(QStringLiteral("valmaxmedia"))==foreign,"foreign provider never replaced")
            || !checkMedia(mediaContexts(collision).isEmpty(),"collision leaves context unavailable")) return 8;
    }
    QJsonObject modules;
    for(const wchar_t *name:{L"valmaxnativeplugin.dll",L"valmax_core.dll",L"valmax_ghost.dll",L"valmax_notifications.dll",L"valmax_privacy.dll"}) {
        wchar_t path[32768];const auto module=GetModuleHandleW(name);
        const DWORD size=module?GetModuleFileNameW(module,path,32768):0;
        if(!size || size>=32768)return 7;
        modules.insert(QString::fromWCharArray(name),QString::fromWCharArray(path,int(size)));
    }
    QTextStream(stdout)<<QJsonDocument(QJsonObject{{QStringLiteral("schema"),1},{QStringLiteral("ok"),true},
        {QStringLiteral("cycles"),cycles},{QStringLiteral("mediaEngineChecks"),mediaChecks},{QStringLiteral("mediaComposerChecks"),composerChecks},
        {QStringLiteral("loadedModules"),modules}}).toJson(QJsonDocument::Compact)<<'\n';
    return 0;
}
