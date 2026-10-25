// Owned synthetic Qt photo lifecycle/cache experiment. No MAX interfaces.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <QGuiApplication>
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QImage>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMap>
#include <QMutex>
#include <QMutexLocker>
#include <QPainter>
#include <QPluginLoader>
#include <QQmlComponent>
#include <QQmlContext>
#include <QQmlEngine>
#include <QQuickImageProvider>
#include <QQuickWindow>
#include <QSGRendererInterface>
#include <QTextStream>
#include <QThread>
#include <QTimer>
#include <QVariant>
#include <algorithm>
#include <functional>
#include <memory>
#include <windows.h>

namespace {
unsigned checks = 0;
bool check(bool condition, const char *description)
{
    ++checks;
    if (!condition) QTextStream(stderr) << description << '\n';
    return condition;
}
#define CHECK(value) do { if (!check(bool(value), #value)) return 1; } while (false)

void runFor(int milliseconds)
{
    QEventLoop loop;
    QTimer::singleShot(milliseconds, Qt::PreciseTimer, &loop, &QEventLoop::quit);
    loop.exec();
}

class Provider final : public QQuickImageProvider {
public:
    Provider() : QQuickImageProvider(QQuickImageProvider::Image), full_(1600, 900, QImage::Format_RGB32),
                 preview_(64, 36, QImage::Format_RGB32)
    {
        full_.fill(QColor(QStringLiteral("#39724c")));
        QPainter painter(&full_);
        for (int y = 0; y < full_.height(); y += 64)
            painter.fillRect(0, y, full_.width(), 12, QColor(QStringLiteral("#b79ad9")));
        painter.end();
        preview_.fill(QColor(QStringLiteral("#78678c")));
    }
    QImage requestImage(const QString &id, QSize *size, const QSize &requested) override
    {
        QElapsedTimer elapsed;
        elapsed.start();
        {
            QMutexLocker lock(&mutex_);
            ++active_;
        }
        const bool full = id.startsWith(QStringLiteral("full-"));
        const QImage &original = full ? full_ : preview_;
        *size = original.size();
        const QImage image = requested.width() > 0 && requested.height() > 0
            ? original.scaled(requested, Qt::KeepAspectRatio, Qt::SmoothTransformation) : original;
        const auto duration = elapsed.nsecsElapsed();
        {
            QMutexLocker lock(&mutex_);
            --active_;
            records_.append(QJsonObject{{QStringLiteral("id"), id},
                {QStringLiteral("width"), requested.width()}, {QStringLiteral("height"), requested.height()},
                {QStringLiteral("full"), full}, {QStringLiteral("workNanoseconds"), double(duration)},
                {QStringLiteral("onGuiThread"), QThread::currentThread() == QCoreApplication::instance()->thread()}});
        }
        return image;
    }
    QJsonObject snapshot() const
    {
        QMutexLocker lock(&mutex_);
        int full = 0, preview = 0, fullGui = 0;
        double work = 0;
        QMap<QString, int> counts;
        for (const auto &entry : records_) {
            const auto record = entry.toObject();
            if (record.value(QStringLiteral("full")).toBool()) {
                ++full;
                if (record.value(QStringLiteral("onGuiThread")).toBool()) ++fullGui;
            } else ++preview;
            work += record.value(QStringLiteral("workNanoseconds")).toDouble();
            const QString key = record.value(QStringLiteral("id")).toString() + QLatin1Char(':')
                + QString::number(record.value(QStringLiteral("width")).toInt()) + QLatin1Char('x')
                + QString::number(record.value(QStringLiteral("height")).toInt());
            ++counts[key];
        }
        QJsonObject duplicates;
        for (auto it = counts.cbegin(); it != counts.cend(); ++it)
            if (it.value() > 1) duplicates.insert(it.key(), it.value());
        return {{QStringLiteral("requests"), int(records_.size())}, {QStringLiteral("fullRequests"), full},
            {QStringLiteral("previewRequests"), preview}, {QStringLiteral("fullRequestsOnGuiThread"), fullGui},
            {QStringLiteral("workNanoseconds"), work}, {QStringLiteral("activeRequests"), active_},
            {QStringLiteral("distinctUrlSizeKeys"), int(counts.size())}, {QStringLiteral("repeatedUrlSizeRequests"), duplicates}};
    }
    QJsonArray records() const { QMutexLocker lock(&mutex_); return records_; }
private:
    QImage full_, preview_;
    mutable QMutex mutex_;
    QJsonArray records_;
    int active_ = 0;
};

class Audit final : public QObject {
    Q_OBJECT
    struct Row { bool alive = false, visible = false, full = false, ready = false, preview = false; };
public:
    Q_INVOKABLE void rowCreated(int id, bool visible) { auto &row = rows_[id]; row.alive = true; row.visible = visible; ++rowCreates_; updatePeaks(); }
    Q_INVOKABLE void rowDestroyed(int id) { rows_[id].alive = false; ++rowDestroys_; updatePeaks(); }
    Q_INVOKABLE void visibility(int id, bool visible) { rows_[id].visible = visible; updatePeaks(); }
    Q_INVOKABLE void fullCreated(int id, bool visible, bool ready)
    { auto &row = rows_[id]; row.full = true; row.ready = ready; row.visible = visible; ++fullCreates_; updatePeaks(); }
    Q_INVOKABLE void fullDestroyed(int id) { rows_[id].full = false; rows_[id].ready = false; ++fullDestroys_; updatePeaks(); }
    Q_INVOKABLE void fullStatus(int id, bool ready) { rows_[id].ready = ready; }
    Q_INVOKABLE void previewStatus(int id, bool ready) { rows_[id].preview = ready; }
    Q_INVOKABLE void targetChanged() { ++targetChanges_; }
    Q_INVOKABLE void sourceChanged() { ++sourceChanges_; }
    Q_INVOKABLE void shaderStatus(int status, const QString &log)
    { if (status == 0) ++compiledShaders_; if (status == 2) { ++shaderErrors_; shaderLog_ = log; } }
    QJsonObject snapshot() const
    {
        int live = 0, visible = 0, active = 0, offscreen = 0, readyVisible = 0, previewVisible = 0;
        for (const auto &row : rows_) {
            if (row.alive) {
                ++live;
                if (row.visible) { ++visible; if (row.ready && row.full) ++readyVisible; if (row.preview) ++previewVisible; }
            }
            if (row.full) { ++active; if (!row.visible) ++offscreen; }
        }
        return {{QStringLiteral("rowCreations"), rowCreates_}, {QStringLiteral("rowDestructions"), rowDestroys_},
            {QStringLiteral("fullImageCreations"), fullCreates_}, {QStringLiteral("fullImageDestructions"), fullDestroys_},
            {QStringLiteral("liveRows"), live}, {QStringLiteral("visibleRows"), visible},
            {QStringLiteral("activeFullImages"), active}, {QStringLiteral("offscreenFullImages"), offscreen},
            {QStringLiteral("readyVisibleFullImages"), readyVisible}, {QStringLiteral("readyVisiblePreviews"), previewVisible},
            {QStringLiteral("peakOffscreenFullImages"), peakOffscreen_}, {QStringLiteral("peakFullImages"), peakFull_},
            {QStringLiteral("decodeTargetChanges"), targetChanges_}, {QStringLiteral("sourceChanges"), sourceChanges_},
            {QStringLiteral("compiledShaders"), compiledShaders_}, {QStringLiteral("shaderErrors"), shaderErrors_},
            {QStringLiteral("shaderLog"), shaderLog_}};
    }
private:
    void updatePeaks()
    {
        int full = 0, offscreen = 0;
        for (const auto &row : rows_) if (row.full) { ++full; if (!row.visible) ++offscreen; }
        peakFull_ = std::max(peakFull_, full); peakOffscreen_ = std::max(peakOffscreen_, offscreen);
    }
    QMap<int, Row> rows_;
    int rowCreates_ = 0, rowDestroys_ = 0, fullCreates_ = 0, fullDestroys_ = 0;
    int peakFull_ = 0, peakOffscreen_ = 0, targetChanges_ = 0, sourceChanges_ = 0, compiledShaders_ = 0, shaderErrors_ = 0;
    QString shaderLog_;
};

// A stable decoded-size role exists before creating delegates. Binding it to
// Item.width during component construction would add a synthetic 1px request
// that the supplied MAX preview-model roles do not establish.
class DecodeGeometry final : public QObject {
    Q_OBJECT
    Q_PROPERTY(int decodeWidth READ decodeWidth NOTIFY decodeWidthChanged)
public:
    int decodeWidth() const { return width_; }
    void setDecodeWidth(int width) { if (width_ != width) { width_ = width; emit decodeWidthChanged(); } }
signals:
    void decodeWidthChanged();
private:
    int width_ = 704;
};

QString modulePath(const wchar_t *name)
{
    wchar_t path[32768];
    const HMODULE module = GetModuleHandleW(name);
    if (!module) return {};
    const DWORD length = GetModuleFileNameW(module, path, 32768);
    return length > 0 && length < 32768 ? QString::fromWCharArray(path, int(length)) : QString();
}
}

int main(int argc, char **argv)
{
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    if (argc != 4 || qEnvironmentVariable("QT_QPA_PLATFORM") != QStringLiteral("windows")) return 2;
    QPluginLoader platform(QString::fromLocal8Bit(argv[2]) + QStringLiteral("/plugins/platforms/qwindows.dll"));
    if (!platform.load()) { QTextStream(stderr) << platform.errorString(); return 3; }
    QGuiApplication app(argc, argv);
    app.setQuitOnLastWindowClosed(false);
    app.setApplicationName(QStringLiteral("ValMAXOwnedPhotoCacheBenchmark"));
    app.setOrganizationName(QStringLiteral("ValMAXSynthetic"));
    const bool candidate = QString::fromLocal8Bit(argv[3]) == QStringLiteral("candidate");
    QQmlEngine engine;
    auto *provider = new Provider;
    engine.addImageProvider(QStringLiteral("cachebench"), provider);
    Audit audit;
    DecodeGeometry geometry;
    engine.rootContext()->setContextProperty(QStringLiteral("audit"), &audit);
    engine.rootContext()->setContextProperty(QStringLiteral("decodeGeometry"), &geometry);
    engine.rootContext()->setContextProperty(QStringLiteral("benchmarkCandidate"), candidate);
    QQmlComponent component(&engine, QUrl::fromLocalFile(QString::fromLocal8Bit(argv[1])));
    if (component.isError()) QTextStream(stderr) << component.errorString();
    CHECK(!component.isError());
    std::unique_ptr<QObject> root(component.create());
    if (!root) { QTextStream(stderr) << component.errorString(); return 4; }
    auto *window = qobject_cast<QQuickWindow *>(root.get());
    CHECK(window);
    engine.setIncubationController(window->incubationController());
    int presentations = 0;
    QObject::connect(window, &QQuickWindow::frameSwapped, window, [&] { ++presentations; });
    QString sceneGraphError;
    QObject::connect(window, &QQuickWindow::sceneGraphError, window, [&](QQuickWindow::SceneGraphError, const QString &message) { sceneGraphError = message; });
    auto ready = [&] {
        const auto state = audit.snapshot();
        return state.value(QStringLiteral("visibleRows")).toInt() >= 2
            && state.value(QStringLiteral("readyVisibleFullImages")) == state.value(QStringLiteral("visibleRows"))
            && state.value(QStringLiteral("readyVisiblePreviews")) == state.value(QStringLiteral("visibleRows"))
            && provider->snapshot().value(QStringLiteral("activeRequests")).toInt() == 0;
    };
    auto settle = [&] {
        runFor(250);
        QElapsedTimer deadline; deadline.start();
        while (!ready() && deadline.elapsed() < 5000) runFor(10);
        runFor(50);
        return ready();
    };
    QJsonArray phases;
    auto snapshot = [&](const QString &name) {
        phases.append(QJsonObject{{QStringLiteral("phase"), name}, {QStringLiteral("provider"), provider->snapshot()},
            {QStringLiteral("lifecycle"), audit.snapshot()}, {QStringLiteral("observedQtPresentations"), presentations},
            {QStringLiteral("width"), window->width()}, {QStringLiteral("height"), window->height()},
            {QStringLiteral("contentY"), root->property("scrollPosition").toDouble()}});
    };
    CHECK(settle()); snapshot(QStringLiteral("coldInitial"));
    root->setProperty("scrollPosition", 30);
    CHECK(settle()); snapshot(QStringLiteral("sameVisibleRows"));
    root->setProperty("scrollPosition", 24 * 180);
    CHECK(settle()); snapshot(QStringLiteral("jumpForward"));
    root->setProperty("scrollPosition", 0);
    CHECK(settle()); snapshot(QStringLiteral("warmReturn"));
    for (int i = 0; i < 60; ++i) { root->setProperty("scrollPosition", (i + 1) * 90); runFor(8); }
    CHECK(settle()); snapshot(QStringLiteral("fastScrollAndSettle"));
    root->setProperty("scrollPosition", 0);
    CHECK(settle()); snapshot(QStringLiteral("returnBeforeResize"));
    for (int i = 0; i < 80; ++i) {
        window->setWidth(680 + i);
        geometry.setDecodeWidth(680 + i - 16);
        runFor(2);
    }
    CHECK(settle()); snapshot(QStringLiteral("resizeBurstSettled"));
    CHECK(sceneGraphError.isEmpty());
    CHECK(audit.snapshot().value(QStringLiteral("shaderErrors")).toInt() == 0);
    CHECK(audit.snapshot().value(QStringLiteral("compiledShaders")).toInt() > 0);
    CHECK(provider->snapshot().value(QStringLiteral("fullRequestsOnGuiThread")).toInt() == 0);
    if (candidate) CHECK(audit.snapshot().value(QStringLiteral("offscreenFullImages")).toInt() == 0);
    const QImage rendered = window->grabWindow();
    CHECK(!rendered.isNull());
    const auto backend = window->rendererInterface()->graphicsApi();
    CHECK(backend != QSGRendererInterface::Software && backend != QSGRendererInterface::Null && backend != QSGRendererInterface::Unknown);
    const auto allRequests = provider->records();
    const auto finalProvider = provider->snapshot();
    window->close(); root.reset(); runFor(50);
    CHECK(audit.snapshot().value(QStringLiteral("rowCreations")) == audit.snapshot().value(QStringLiteral("rowDestructions")));
    CHECK(audit.snapshot().value(QStringLiteral("fullImageCreations")) == audit.snapshot().value(QStringLiteral("fullImageDestructions")));
    const QJsonObject loaded{{QStringLiteral("Qt6Core.dll"), modulePath(L"Qt6Core.dll")},
        {QStringLiteral("Qt6Qml.dll"), modulePath(L"Qt6Qml.dll")}, {QStringLiteral("Qt6Gui.dll"), modulePath(L"Qt6Gui.dll")},
        {QStringLiteral("Qt6Quick.dll"), modulePath(L"Qt6Quick.dll")}, {QStringLiteral("qwindows.dll"), modulePath(L"qwindows.dll")}};
    CHECK(modulePath(L"valmaxnativeplugin.dll").isEmpty());
    CHECK(modulePath(L"valmax_privacy.dll").isEmpty() && modulePath(L"valmax_ghost.dll").isEmpty());
    QTextStream(stdout) << QJsonDocument(QJsonObject{{QStringLiteral("schema"), 1}, {QStringLiteral("ok"), true},
        {QStringLiteral("checks"), int(checks)}, {QStringLiteral("qtVersion"), QString::fromLatin1(qVersion())},
        {QStringLiteral("candidate"), candidate}, {QStringLiteral("cacheEnabled"), true},
        {QStringLiteral("graphicsApi"), int(backend)}, {QStringLiteral("devicePixelRatio"), rendered.devicePixelRatio()},
        {QStringLiteral("renderedSize"), QJsonArray{rendered.width(), rendered.height()}},
        {QStringLiteral("phases"), phases}, {QStringLiteral("requests"), allRequests},
        {QStringLiteral("providerTotal"), finalProvider}, {QStringLiteral("afterDestroy"), audit.snapshot()},
        {QStringLiteral("loadedModules"), loaded}, {QStringLiteral("realClientStarted"), false},
        {QStringLiteral("networkUsed"), false}, {QStringLiteral("gpuTimeMeasured"), false},
        {QStringLiteral("frameRateMeasured"), false}}).toJson(QJsonDocument::Compact) << '\n';
    return 0;
}

#include "photo_cache_benchmark.moc"
