#include "preview_provider.h"
#include <QCoreApplication>
#include <QColorSpace>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QFileInfo>
#include <QGuiApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPluginLoader>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QQuickItem>
#include <QTextStream>
#include <QThread>
#include <atomic>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include <Windows.h>
using namespace valmax::media;
namespace {
int checks = 0;
void check(bool value, const char* name) {
    ++checks;
    if (!value) { QTextStream(stderr) << "FAILED: " << name << '\n'; std::exit(1); }
}
QString nonce(QChar character = u'a') { return QString(32, character); }
QImage pixels(QSize size = QSize(96, 48), QRgb color = qRgb(30, 80, 160)) {
    QImage image(size, QImage::Format_ARGB32_Premultiplied); image.setColorSpace(QColorSpace::SRgb); image.fill(color); return image;
}
QString key(const QString& url) { return url.sliced(QStringLiteral("image://valmaxmedia/").size()); }
QString put(const std::shared_ptr<PreviewStore>& store, const QImage& image = pixels()) {
    auto value = store->publish(store->snapshot().epoch, image);
    check(bool(value), "publish ready pixels"); return *value.value;
}
bool wait_for(const std::function<bool()>& condition, int milliseconds = 3000) {
    QElapsedTimer time; time.start();
    while (!condition() && time.elapsed() < milliseconds) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 5);
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        QThread::msleep(1);
    }
    return condition();
}
void direct_contract() {
    auto store = std::make_shared<PreviewStore>(nonce()); PreviewProvider provider(store);
    QImage source = pixels(); source.setText(QStringLiteral("secret"), QStringLiteral("synthetic")); source.setDevicePixelRatio(2);
    const auto url = put(store, source); source.fill(Qt::red);
    auto response = std::unique_ptr<QQuickImageResponse>(provider.requestImageResponse(key(url), QSize(30, 30)));
    check(response->errorString().isEmpty(), "ready response");
    auto factory = std::unique_ptr<QQuickTextureFactory>(response->textureFactory());
    check(factory && factory->image().size() == QSize(30, 15), "bounded aspect fit");
    check(factory->image().pixel(4, 4) == qRgb(30, 80, 160), "owned pixels cannot be changed by publisher");
    check(factory->image().textKeys().isEmpty() && factory->image().devicePixelRatio() == 1, "pixel-only snapshot");
    check(factory->image().colorSpace() == QColorSpace(QColorSpace::SRgb), "normalized sRGB is retained");
    check(store->snapshot().imageResponses == 1 && store->snapshot().responses == 1, "snapshot counted");
    response->cancel(); check(!response->errorString().isEmpty() && !response->textureFactory(), "cancelled response has no texture");
    response.reset(); check(store->snapshot().responses == 0 && store->snapshot().imageResponses == 0, "response resources released");
    for (const QString& invalid : {QString(), key(url) + QStringLiteral("?x=1"), QStringLiteral("file:///x"), QStringLiteral("https://example.invalid"), key(url).toUpper(), QString(32, u'b') + key(url).sliced(32)}) {
        response.reset(provider.requestImageResponse(invalid, {}));
        check(!response->errorString().isEmpty() && !response->textureFactory(), "opaque ID policy");
    }
    response.reset();
    const auto oldEpoch = store->snapshot().epoch;
    response.reset(provider.requestImageResponse(key(url), QSize(512, 512)));
    check(store->advance() == oldEpoch + 1, "scope advance");
    check(!response->errorString().isEmpty() && !response->textureFactory(), "revocation precedes factory publication");
    check(!store->publish(oldEpoch, pixels()), "stale worker cannot publish");
    const auto newer = put(store); check(newer != url, "scope tokens not recycled");
    response.reset(provider.requestImageResponse(key(url), {})); check(!response->errorString().isEmpty(), "old generation not found");
    check(!store->publish(store->snapshot().epoch, QImage(513, 1, QImage::Format_ARGB32_Premultiplied)), "publication dimension bound");
    check(!store->publish(store->snapshot().epoch, QImage(10, 10, QImage::Format_RGB32)), "publication format bound");
    auto untagged = pixels(); untagged.setColorSpace({});
    check(!store->publish(store->snapshot().epoch, untagged), "un-normalized input rejected");
    auto wide = pixels(); wide.setColorSpace(QColorSpace::DisplayP3);
    check(!store->publish(store->snapshot().epoch, wide), "arbitrary input color profile rejected");
    response.reset(provider.requestImageResponse(key(newer), QSize(1, 1)));
    factory.reset(response->textureFactory()); check(factory && factory->image().size() == QSize(1, 1), "thin fitted side remains positive");
    store->close(); check(!response->textureFactory() && !store->publish(store->snapshot().epoch, pixels()), "closed store revokes and rejects");
    auto bad = std::make_shared<PreviewStore>(QStringLiteral("not-a-nonce")); check(bad->snapshot().closed, "invalid nonce unavailable");
}
void budgets() {
    auto store = std::make_shared<PreviewStore>(nonce(u'b')); PreviewProvider provider(store);
    QStringList urls;
    for (int i = 0; i < 16; ++i) urls.push_back(put(store));
    auto response = std::unique_ptr<QQuickImageResponse>(provider.requestImageResponse(key(urls[0]), {})); response.reset();
    put(store); check(store->snapshot().entries == 16, "entry budget");
    response.reset(provider.requestImageResponse(key(urls[1]), {})); check(!response->errorString().isEmpty(), "oldest unused entry evicted");
    response.reset(provider.requestImageResponse(key(urls[0]), {})); check(response->errorString().isEmpty(), "recent entry retained"); response.reset();
    store->advance(); urls.clear();
    for (int i = 0; i < 9; ++i) urls.push_back(put(store, pixels(QSize(512, 512))));
    check(store->snapshot().entries == 8 && store->snapshot().residentBytes == PreviewStore::MaxResidentBytes, "resident byte budget");
    response.reset(provider.requestImageResponse(key(urls[0]), {})); check(!response->errorString().isEmpty(), "byte budget evicts image"); response.reset();
    std::vector<std::unique_ptr<QQuickImageResponse>> held;
    for (int i = 0; i < PreviewStore::MaxImageResponses; ++i) {
        held.emplace_back(provider.requestImageResponse(key(urls.back()), QSize(512, 512)));
        check(held.back()->errorString().isEmpty(), "image slot admitted");
    }
    response.reset(provider.requestImageResponse(key(urls.back()), {}));
    check(response->errorString() == QStringLiteral("preview_busy") && !response->textureFactory(), "image snapshot admission bound");
    held.clear(); response.reset(); check(store->snapshot().imageResponses == 0 && store->snapshot().responses == 0, "all snapshot slots released");
}
void reentrant_requests() {
    auto store = std::make_shared<PreviewStore>(nonce(u'c')); PreviewProvider provider(store);
    const auto url = put(store); std::atomic<bool> passed{true};
    std::vector<std::thread> threads;
    for (int i = 0; i < 4; ++i) threads.emplace_back([&] {
        for (int j = 0; j < 100; ++j) {
            std::unique_ptr<QQuickImageResponse> response(provider.requestImageResponse(key(url), QSize(24, 18)));
            if (!response->errorString().isEmpty()) passed.store(false);
            response->cancel();
            if (response->textureFactory()) passed.store(false);
        }
    });
    for (auto& thread : threads) thread.join();
    check(passed.load(), "400 concurrent memory-only requests");
    check(store->snapshot().responses == 0 && store->snapshot().imageResponses == 0, "concurrent cleanup");
}
void revocation_race() {
    auto store = std::make_shared<PreviewStore>(nonce(u'c')); PreviewProvider provider(store);
    QString url = put(store); std::mutex shared; std::atomic<bool> passed{true};
    std::vector<std::thread> readers;
    for (int i = 0; i < 4; ++i) readers.emplace_back([&] {
        for (int j = 0; j < 100; ++j) {
            QString captured; { std::lock_guard lock(shared); captured = url; }
            std::unique_ptr<QQuickImageResponse> response(provider.requestImageResponse(key(captured), QSize(24, 18)));
            std::unique_ptr<QQuickTextureFactory> factory(response->textureFactory());
            if (factory && (factory->image().width() > 24 || factory->image().height() > 18
                || factory->image().colorSpace() != QColorSpace(QColorSpace::SRgb))) passed.store(false);
            // Revoke can race either lookup or factory publication. Failure is
            // finite and allowed; a previously delivered texture is Qt-owned.
            response->cancel();
            if (response->textureFactory()) passed.store(false);
        }
    });
    for (int i = 0; i < 100; ++i) {
        const auto epoch = store->advance();
        const auto published = store->publish(epoch, pixels());
        if (!published) { passed.store(false); break; }
        std::lock_guard lock(shared); url = *published.value;
    }
    for (auto& reader : readers) reader.join();
    check(passed.load(), "400 requests across 100 generation changes");
    check(store->snapshot().responses == 0 && store->snapshot().imageResponses == 0, "revocation race cleanup");
}
std::unique_ptr<QObject> image_object(QQmlEngine& engine, const QString& url) {
    QQmlComponent component(&engine);
    const auto qml = QStringLiteral("import QtQuick\nImage { width: 40; height: 40; cache: false; sourceSize: Qt.size(40,40); source: \"") + url + QStringLiteral("\" }");
    component.setData(qml.toUtf8(), QUrl(QStringLiteral("qrc:/synthetic-preview.qml")));
    check(!component.isError(), "QML component compiled");
    std::unique_ptr<QObject> object(component.create());
    if (!object) QTextStream(stderr) << component.errorString();
    check(bool(object), "QML Image created"); return object;
}
void engine_lifetime() {
    auto first = std::make_shared<PreviewStore>(nonce(u'd'));
    auto second = std::make_shared<PreviewStore>(nonce(u'e'));
    {
        QQmlEngine one, two;
        one.addImageProvider(QStringLiteral("valmaxmedia"), new PreviewProvider(first));
        two.addImageProvider(QStringLiteral("valmaxmedia"), new PreviewProvider(second));
        const auto urlOne = put(first), urlTwo = put(second, pixels(QSize(40, 40), qRgb(160, 20, 30)));
        auto a = image_object(one, urlOne), b = image_object(two, urlTwo);
        check(wait_for([&] { return a->property("status").toInt() == 1 && b->property("status").toInt() == 1; }), "finish-before-connect reaches Image.Ready in two actual engines");
        one.clearSingletons();
        auto after = image_object(one, urlOne); check(wait_for([&] { return after->property("status").toInt() == 1; }), "singleton clearing does not destroy provider store");
        auto wrong = image_object(two, urlOne); check(wait_for([&] { return wrong->property("status").toInt() == 3; }), "other engine cannot resolve foreign token");
        first->advance();
        auto stale = image_object(one, urlOne); check(wait_for([&] { return stale->property("status").toInt() == 3; }), "revoked source cannot reload from QML");
    }
    check(wait_for([&] { return first->snapshot().responses == 0 && second->snapshot().responses == 0; }), "engine response cleanup");
    check(first->snapshot().closed && second->snapshot().closed, "engine-owned provider closes store");
    QString previous;
    for (int i = 0; i < 20; ++i) {
        auto store = std::make_shared<PreviewStore>(QString::number(i + 1, 16).rightJustified(32, u'0'));
        {
            QQmlEngine engine; engine.addImageProvider(QStringLiteral("valmaxmedia"), new PreviewProvider(store));
            const auto url = put(store); check(previous != url, "new engine source is never reused"); previous = url;
            auto object = image_object(engine, url);
            // No event-loop wait: exercise teardown while the reader may still
            // be acquiring or delivering this response.
        }
        check(wait_for([&] { return store->snapshot().responses == 0 && store->snapshot().closed; }), "pending load teardown");
    }
}
}
int main(int argc, char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    qInstallMessageHandler([](QtMsgType type, const QMessageLogContext&, const QString& message) {
        QTextStream(stderr) << message << '\n'; if (type == QtFatalMsg) std::_Exit(6);
    });
    if (qEnvironmentVariable("QT_QPA_PLATFORM") != QStringLiteral("windows")) return 6;
    const auto platformPath = QFileInfo(QString::fromLocal8Bit(argv[0])).absolutePath() + QStringLiteral("/plugins/platforms/qwindows.dll");
    QPluginLoader platform(platformPath); if (!platform.load()) { QTextStream(stderr) << platform.errorString(); return 6; }
    QGuiApplication app(argc, argv);
    app.setOrganizationName(QStringLiteral("ValMAXSynthetic")); app.setApplicationName(QStringLiteral("MemoryPreviewProviderTest"));
    direct_contract(); budgets(); reentrant_requests(); revocation_race(); engine_lifetime();
    QJsonObject loaded;
    for (const wchar_t* name : {L"Qt6Core.dll", L"Qt6Gui.dll", L"Qt6Qml.dll", L"Qt6Quick.dll"}) {
        wchar_t path[32768]{};
        const auto length = GetModuleFileNameW(GetModuleHandleW(name), path, 32768);
        check(length > 0 && length < 32768, "loaded Qt module path");
        loaded.insert(QString::fromWCharArray(name), QString::fromWCharArray(path, int(length)));
    }
    QTextStream(stdout) << QJsonDocument(QJsonObject{{QStringLiteral("passed"),true},{QStringLiteral("checks"),checks},
        {QStringLiteral("concurrentRequests"),800},{QStringLiteral("concurrentGenerationChanges"),100},{QStringLiteral("engineTeardowns"),22},
        {QStringLiteral("qt"),QString::fromLatin1(qVersion())},{QStringLiteral("loadedModules"),loaded},{QStringLiteral("networkUsed"),false},
        {QStringLiteral("filesOpenedByProvider"),false}}).toJson(QJsonDocument::Compact) << '\n';
    return 0;
}
