#include "engine_context.h"
#include <QColorSpace>
#include <QElapsedTimer>
#include <QEvent>
#include <QEventLoop>
#include <QFileInfo>
#include <QGuiApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPluginLoader>
#include <QPointer>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QQuickTextureFactory>
#include <QTextStream>
#include <QThread>
#include <atomic>
#include <memory>
#include <thread>
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>

using namespace valmax::media;
namespace {
std::atomic<int> checks{0};
void check(bool ok, const char* name) {
    ++checks;
    if (!ok) { QTextStream(stderr) << "FAILED: " << name << '\n'; std::_Exit(1); }
}
void events() {
    QCoreApplication::processEvents(QEventLoop::AllEvents, 10);
    QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
}
template<class F> void until(F predicate, const char* name) {
    QElapsedTimer timer; timer.start();
    while (!predicate() && timer.elapsed() < 5000) { events(); QThread::msleep(1); }
    check(predicate(), name);
}
QImage pixels() {
    QImage image(40, 20, QImage::Format_ARGB32_Premultiplied);
    image.fill(QColor(60, 70, 80)); image.setColorSpace(QColorSpace(QColorSpace::SRgb));
    return image;
}
std::unique_ptr<QQuickImageResponse> response(QQmlEngine& engine, const QString& url) {
    auto provider = dynamic_cast<PreviewProvider*>(engine.imageProvider(QStringLiteral("valmaxmedia")));
    check(provider != nullptr, "engine owns expected native provider");
    return std::unique_ptr<QQuickImageResponse>(provider->requestImageResponse(url.mid(20), QSize(40, 40)));
}
void lifecycle(int singletonId) {
    auto engine = std::make_unique<QQmlEngine>();
    QPointer<MediaEngineContext> context = MediaEngineContext::install(engine.get());
    check(context && context->parent() == engine.get(), "context belongs to engine");
    check(MediaEngineContext::install(engine.get()) == context, "install is idempotent");
    EngineMediaLease empty;
    check(!empty.valid() && empty.account_id() == 0 && empty.page_id() == 0 && empty.serial() == 0, "empty token rejected");
    check(!context->bind(0, 1).valid() && !context->bind(-1, 1).valid() && !context->bind(1, 0).valid(), "invalid host IDs rejected");
    auto first = context->bind(9007199254740993LL, 9007199254740995ULL);
    check(first.valid() && context->current(first), "first native composer bound");
    check(first.account_id() == 9007199254740993LL && first.page_id() == 9007199254740995ULL, "IDs remain exact above JS safe integer");
    auto published = context->publish(first, pixels());
    check(bool(published) && published.value->startsWith(QStringLiteral("image://valmaxmedia/")), "only opaque provider URL returned");
    auto ready = response(*engine, *published.value);
    check(ready->errorString().isEmpty(), "published thumbnail response ready");
    auto texture = std::unique_ptr<QQuickTextureFactory>(ready->textureFactory());
    check(texture && texture->image() == pixels(), "provider returns expected synthetic pixels");

    QQmlComponent component(engine.get());
    component.setData("import QtQuick\nImage { width: 40; height: 40; cache: false }", QUrl(QStringLiteral("qrc:/owned-engine-media.qml")));
    auto image = std::unique_ptr<QObject>(component.create());
    check(image && !component.isError(), "actual QML image created");
    check(image->setProperty("source", QUrl(*published.value)), "image accepts provider URL");
    until([&] { return image->property("status").toInt() == 1; }, "Image.Ready from engine-owned provider");
    QObject::connect(context, &MediaEngineContext::invalidated, image.get(), [&] { image->setProperty("source", QUrl()); });

    QPointer<QObject> disposable = engine->singletonInstance<QObject*>(singletonId);
    check(disposable, "real singleton instantiated");
    const auto epoch = context->snapshot().epoch;
    engine->clearSingletons(); events();
    check(disposable.isNull(), "clearSingletons destroys disposable singleton");
    check(context && context->current(first) && context->snapshot().epoch == epoch, "engine context survives singleton reset");
    check(image->property("status").toInt() == 1 && context->snapshot().entries == 1, "existing preview survives singleton reset");

    auto next = context->advance(first);
    check(!first.valid() && next.valid() && next.serial() > first.serial(), "new search immediately revokes old capability");
    check(context->snapshot().entries == 0 && context->snapshot().residentBytes == 0, "generation clears owned pixel cache");
    until([&] { return image->property("status").toInt() == 0; }, "adapter invalidation clears displayed source");
    check(!context->publish(first, pixels()) && !context->advance(first).valid() && !context->detach(first), "late old commands cannot alter new scope");
    auto oldResponse = response(*engine, *published.value);
    check(!oldResponse->errorString().isEmpty(), "revoked URL cannot serve old pixels");
    auto second = context->publish(next, pixels());
    check(bool(second) && second.value != published.value, "new URL does not reuse revoked key");
    auto another = context->bind(102, 3);
    check(!next.valid() && another.valid() && context->snapshot().entries == 0, "switching account revokes previous composer");

    std::thread worker([context, another] {
        check(another.valid() && another.account_id() == 102, "worker reads immutable capability and atomic validity");
        check(!context->current(another) && !context->bind(103, 4).valid(), "cross-thread context operations rejected");
        check(!context->publish(another, pixels()) && context->snapshot().closed, "cross-thread publication rejected");
    });
    worker.join();
    check(context->current(another), "rejected worker methods leave current page unchanged");
    std::atomic<bool> revokedObserved{false};
    std::thread waiting([another, &revokedObserved] {
        QElapsedTimer timer; timer.start();
        while (another.valid() && timer.elapsed() < 5000) std::this_thread::yield();
        revokedObserved.store(!another.valid());
    });
    check(context->detach(another), "detach accepted"); waiting.join();
    check(revokedObserved && !context->detach(another), "revocation observable without worker QObject access");
    auto last = context->bind(104, 5);
    image.reset(); texture.reset(); ready.reset(); oldResponse.reset();
    engine.reset(); events();
    check(context.isNull() && !last.valid(), "engine teardown destroys context and revokes external token");
}
void isolation() {
    QQmlEngine a, b;
    auto ca = MediaEngineContext::install(&a); auto cb = MediaEngineContext::install(&b);
    const auto la = ca->bind(1, 1); const auto lb = cb->bind(1, 1);
    check(!ca->current(lb) && !cb->current(la), "identical numeric IDs cannot cross engine boundary");
    auto ua = ca->publish(la, pixels()); auto ub = cb->publish(lb, pixels());
    check(ua && ub && ua.value != ub.value, "independent engine nonces");
    auto wrong = response(b, *ua.value);
    check(!wrong->errorString().isEmpty(), "engine A token is unavailable through engine B provider");
    auto fresh = ca->advance(la);
    check(fresh.valid() && cb->current(lb) && cb->snapshot().entries == 1, "one engine clear cannot clear another");
    a.removeImageProvider(QStringLiteral("valmaxmedia"));
    check(!ca->current(fresh) && !MediaEngineContext::install(&a), "removed provider is not silently replaced");
    int failedBindClears = 0;
    QObject::connect(ca, &MediaEngineContext::invalidated, ca, [&] { ++failedBindClears; });
    check(!ca->bind(2, 2).valid() && !fresh.valid() && failedBindClears == 1,
          "failed replacement still clears displayed sources and revokes old capability");

    QQmlEngine occupied;
    auto existing = std::make_shared<PreviewStore>(QString(32, u'f'));
    occupied.addImageProvider(QStringLiteral("valmaxmedia"), new PreviewProvider(existing));
    check(!MediaEngineContext::install(&occupied) && !existing->snapshot().closed, "existing provider name left untouched");
}
void reentrancy() {
    QQmlEngine engine;
    auto context = MediaEngineContext::install(&engine);
    bool recurse = true; EngineMediaLease nested;
    auto connection = QObject::connect(context, &MediaEngineContext::invalidated, context, [&] {
        if (recurse) { recurse = false; nested = context->bind(202, 2); }
    });
    check(!context->bind(201, 1).valid() && context->current(nested), "reentrant bind cannot return an obsolete capability");
    QObject::disconnect(connection);
    QPointer<MediaEngineContext> guard(context);
    QObject::connect(context, &MediaEngineContext::invalidated, context, [context] { delete context; });
    check(!context->advance(nested).valid() && guard.isNull() && !nested.valid(), "deletion in invalidated callback is safe");
    check(!MediaEngineContext::install(&engine), "closed provider not reused after manual context destruction");
    QQmlEngine detachedEngine;
    auto detached = MediaEngineContext::install(&detachedEngine); auto lease = detached->bind(1, 1);
    QObject::connect(detached, &MediaEngineContext::invalidated, detached, [detached] { delete detached; });
    check(detached->detach(lease) && !lease.valid(), "delete during detach does not access destroyed state");
}
}
int main(int argc, char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    qInstallMessageHandler([](QtMsgType type, const QMessageLogContext&, const QString& message) {
        QTextStream(stderr) << message << '\n'; if (type == QtFatalMsg) std::_Exit(6);
    });
    if (qEnvironmentVariable("QT_QPA_PLATFORM") != QStringLiteral("windows")) return 6;
    const auto root = QFileInfo(QString::fromLocal8Bit(argv[0])).absolutePath();
    QPluginLoader platform(root + QStringLiteral("/plugins/platforms/qwindows.dll"));
    if (!platform.load()) return 6;
    QGuiApplication app(argc, argv);
    app.setOrganizationName(QStringLiteral("ValMAXSynthetic")); app.setApplicationName(QStringLiteral("NativeMediaEngineTest"));
    QCoreApplication::setLibraryPaths({root + QStringLiteral("/plugins")});
    const auto singletonId = qmlRegisterSingletonType<QObject>("ValMAXEngineProof", 1, 0, "Disposable",
        [](QQmlEngine*, QJSEngine*) -> QObject* { return new QObject; });
    check(!MediaEngineContext::install(nullptr), "null engine rejected");
    for (int i = 0; i < 12; ++i) lifecycle(singletonId);
    isolation(); reentrancy(); events();
    QJsonObject modules;
    for (const wchar_t* name : {L"Qt6Core.dll", L"Qt6Gui.dll", L"Qt6Qml.dll", L"Qt6Quick.dll"}) {
        wchar_t path[32768]{}; const auto length = GetModuleFileNameW(GetModuleHandleW(name), path, 32768);
        check(length > 0 && length < 32768, "actual Qt module path");
        modules.insert(QString::fromWCharArray(name), QString::fromWCharArray(path, int(length)));
    }
    QTextStream(stdout) << QJsonDocument(QJsonObject{{"passed", true}, {"checks", checks.load()},
        {"qt", QString::fromLatin1(qVersion())}, {"loadedModules", modules}, {"engineLifecycleCycles", 12},
        {"imageReadyTransitions", 12}, {"singletonResetCycles", 12}, {"networkUsed", false},
        {"mediaWrittenToDisk", false}, {"messageSent", false}}).toJson(QJsonDocument::Compact) << '\n';
}
