// Test-only entrypoint. Production executable has no fixture/backend switches.
#include "../controller.h"
#include <QCoreApplication>
#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QGuiApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPointer>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQmlError>
#include <QQuickStyle>
#include <QQuickItem>
#include <QQuickWindow>
#include <QScreen>
#include <QStyleHints>
#include <QThread>
#include <QWheelEvent>
#include <windows.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <exception>
#include <functional>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <source_location>
#include <thread>

using namespace valmax::transfer;
using namespace valmax::transfer::app;
namespace {
int checks = 0;
void require(bool value, const std::source_location where = std::source_location::current()) {
    ++checks;
    if (!value) {
        std::cerr << "{\"failedCheck\":" << checks << ",\"sourceLine\":" << where.line() << "}" << std::endl;
        throw std::runtime_error("check_failed");
    }
}
void until(const std::function<bool()>& predicate, int timeout = 3000) {
    QElapsedTimer timer; timer.start();
    while (!predicate() && timer.elapsed() < timeout) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 5);
        QThread::msleep(1);
    }
    require(predicate());
}
void pump(int ms = 20) {
    QElapsedTimer timer; timer.start();
    while (timer.elapsed() < ms) { QCoreApplication::processEvents(QEventLoop::AllEvents, 5); QThread::msleep(1); }
}
const QString password = QStringLiteral("synthetic-transfer-helper-password");
class Fake final : public Backend {
public:
    const std::thread::id gui = std::this_thread::get_id();
    std::atomic_int previews{0}, imports{0}, recoveryChecks{0}, recoveries{0};
    std::atomic_bool holdPreview{false}, holdImport{false}, released{false};
    std::atomic_bool commitAfterCancel{false}, failImport{false}, ordinaryFailure{false};
    std::atomic_bool sawCancel{false}, wrongThread{false};
    std::mutex mutex;
    std::condition_variable condition;
    QString expectedLocal;
    QString recoveryError;
    std::atomic_bool noRecovery{false};
    RecoveryStatusReply recoveryStatus(const QString& local) override {
        if (std::this_thread::get_id() == gui) wrongThread = true;
        ++recoveryChecks;
        if (local != expectedLocal) return {false, false, QStringLiteral("internal_error"), {}};
        return {true, !noRecovery, {}, noRecovery ? QString() : QString(64, u'b')};
    }
    RecoveryReply recover(const QString& local, const QByteArray& secret,
        const QString& fingerprint, NativeTransferHost& host) override {
        if (std::this_thread::get_id() == gui) wrongThread = true;
        ++recoveries;
        if (local != expectedLocal || secret != password.toUtf8() || fingerprint != QString(64, u'b'))
            return {false, QStringLiteral("authentication_failed"), 0};
        const auto callbacks = host.callbacks();
        while (holdImport.load() && !released.load()) {
            if (callbacks.cancelled(callbacks.context)) { sawCancel = true; break; }
            std::unique_lock lock(mutex); condition.wait_for(lock, std::chrono::milliseconds(2));
        }
        if (sawCancel && !commitAfterCancel) return {false, QStringLiteral("cancelled"), 0};
        if (!recoveryError.isEmpty()) return {false, recoveryError, 0};
        return {true, {}, 2};
    }
    PreviewReply inspect(const QUrl&, const QByteArray& secret) override {
        if (std::this_thread::get_id() == gui) wrongThread = true;
        ++previews;
        if (holdPreview.load()) {
            std::unique_lock lock(mutex);
            condition.wait_for(lock, std::chrono::seconds(5), [&] { return released.load(); });
        }
        if (secret != password.toUtf8()) return {false, QStringLiteral("authentication_failed"), {}, {}};
        return {true, {}, QString(64, u'a'), {
            {QStringLiteral("42"), QStringLiteral("Alice Example"), QStringLiteral("AE")},
            {QStringLiteral("9223372036854775807"), QStringLiteral("Борис Тестовый"), QStringLiteral("БТ")}}};
    }
    ImportReply importLogin(const QString& local, const QUrl&, const QByteArray& secret,
        const QString& fingerprint, NativeTransferHost& host) override {
        if (std::this_thread::get_id() == gui) wrongThread = true;
        ++imports;
        if (local != expectedLocal || secret != password.toUtf8() || fingerprint != QString(64, u'a'))
            return {false, QStringLiteral("internal_error"), {}, 0};
        const auto callbacks = host.callbacks();
        while (holdImport.load() && !released.load()) {
            if (callbacks.cancelled(callbacks.context)) { sawCancel = true; break; }
            std::unique_lock lock(mutex); condition.wait_for(lock, std::chrono::milliseconds(2));
        }
        if (sawCancel && !commitAfterCancel) return {false, QStringLiteral("cancelled"), {}, 0};
        if (failImport) return {false, QStringLiteral("rollback_failed"), {}, 0};
        if (ordinaryFailure) return {false, QStringLiteral("credential_io"), {}, 0};
        return {true, {}, QStringLiteral("added"), 2};
    }
    void release() { released = true; condition.notify_all(); }
};
std::shared_ptr<Fake> fake(const QString& local) {
    auto backend = std::make_shared<Fake>(); backend->expectedLocal = local; return backend;
}
QUrl source(const QString& fixture) { return QUrl::fromLocalFile(QDir(fixture).filePath(QStringLiteral("synthetic-selected.mdata"))); }
void previewReady(Controller& controller, const QString& fixture) {
    controller.chooseFile(source(fixture)); controller.preview(password);
    until([&] { return !controller.busy(); });
    require(controller.previewReady()); require(controller.accounts().size() == 2);
    require(controller.accounts()[1].toMap().value(QStringLiteral("accountId")).toString() == QStringLiteral("9223372036854775807"));
}
void controllerTests(const QString& fixture) {
    const auto local = QDir(fixture).filePath(QStringLiteral("synthetic-local"));
    {
        auto backend = fake(local); Controller controller(backend, local);
        require(controller.available()); require(!controller.busy()); require(!controller.previewReady());
        int clears = 0; QObject::connect(&controller, &Controller::clearPassword, &controller, [&] { ++clears; });
        controller.preview(QStringLiteral("short"));
        require(!controller.busy()); require(backend->previews == 0);
        previewReady(controller, fixture);
        require(clears >= 2); require(!backend->wrongThread);
        controller.inputsChanged(); require(!controller.previewReady()); require(controller.accounts().isEmpty());
        controller.confirmImport(); pump(); require(backend->imports == 0);
        previewReady(controller, fixture); controller.confirmImport(); controller.confirmImport();
        until([&] { return !controller.busy(); });
        require(backend->imports == 1); require(controller.succeeded()); require(!controller.previewReady());
        require(controller.accounts().isEmpty()); require(controller.requestClose());
        controller.confirmImport(); pump(); require(backend->imports == 1);
    }
    {
        auto backend = fake(local); backend->holdPreview = true;
        Controller controller(backend, local); controller.chooseFile(source(fixture)); controller.preview(password);
        until([&] { return backend->previews == 1; }); require(controller.busy());
        controller.preview(password); controller.inputsChanged(); backend->release();
        until([&] { return !controller.busy(); });
        require(backend->previews == 1); require(!controller.previewReady()); require(controller.accounts().isEmpty());
        controller.confirmImport(); pump(); require(backend->imports == 0);
        backend->holdPreview = false; previewReady(controller, fixture);
    }
    {
        auto backend = fake(local); Controller controller(backend, local);
        std::atomic_bool completed{false};
        QObject::connect(controller.testingWorker(), &Worker::completed, controller.testingWorker(),
            [&](quint64, const QVariantMap&) { completed = true; }, Qt::DirectConnection);
        controller.chooseFile(source(fixture)); controller.preview(password);
        // Deliberately do not pump the GUI: its completed event is queued.
        for (int i = 0; i < 1000 && !completed; ++i) QThread::msleep(1);
        require(completed); require(controller.busy()); controller.cancel();
        until([&] { return !controller.busy(); });
        require(!controller.previewReady()); require(controller.accounts().isEmpty());
        require(controller.errorCode() == QStringLiteral("cancelled"));
        controller.confirmImport(); pump(); require(backend->imports == 0);
    }
    {
        auto backend = fake(local); backend->holdImport = true; backend->commitAfterCancel = true;
        Controller controller(backend, local); previewReady(controller, fixture);
        controller.confirmImport(); until([&] { return backend->imports == 1; });
        const auto chosen = controller.fileName();
        controller.chooseFile(QUrl::fromLocalFile(QStringLiteral("C:/never/other.mdata")));
        controller.inputsChanged(); require(controller.fileName() == chosen);
        controller.cancel(); until([&] { return !controller.busy(); });
        require(backend->sawCancel); require(controller.succeeded()); require(controller.errorCode().isEmpty());
        require(controller.status().contains(QStringLiteral("Аккаунты добавлены")));
    }
    {
        auto backend = fake(local); backend->failImport = true;
        Controller controller(backend, local); previewReady(controller, fixture); controller.confirmImport();
        until([&] { return !controller.busy(); });
        require(controller.errorCode() == QStringLiteral("rollback_failed")); require(!controller.previewReady());
        require(controller.status().contains(QStringLiteral("Не запускайте")));
        controller.confirmImport(); pump(); require(backend->imports == 1);
        require(!controller.available()); backend->failImport = false;
        controller.preview(password); controller.inputsChanged(); controller.chooseFile(QUrl()); pump();
        require(backend->previews == 1); require(controller.errorCode() == QStringLiteral("rollback_failed"));
    }
    {
        auto backend = fake(local); backend->ordinaryFailure = true;
        Controller controller(backend, local); previewReady(controller, fixture); controller.confirmImport();
        until([&] { return !controller.busy(); });
        require(controller.errorCode() == QStringLiteral("credential_io")); require(controller.available());
        controller.confirmImport(); pump(); require(backend->imports == 1);
        backend->ordinaryFailure = false; previewReady(controller, fixture); require(controller.errorCode().isEmpty());
    }
    {
        auto backend = fake(local); backend->holdImport = true;
        Controller controller(backend, local); previewReady(controller, fixture);
        int closeSignals = 0; QObject::connect(&controller, &Controller::readyToClose, &controller, [&] { ++closeSignals; });
        controller.confirmImport(); until([&] { return backend->imports == 1; });
        require(!controller.requestClose()); until([&] { return closeSignals == 1; });
        require(!controller.busy()); require(backend->sawCancel); require(controller.requestClose());
        controller.preview(password); pump(); require(backend->previews == 1);
    }
    {
        auto backend = fake(local); backend->holdImport = true;
        backend->commitAfterCancel = true; backend->failImport = true;
        Controller controller(backend, local); previewReady(controller, fixture);
        int closeSignals = 0; QObject::connect(&controller, &Controller::readyToClose, &controller, [&] { ++closeSignals; });
        controller.confirmImport(); until([&] { return backend->imports == 1; });
        require(!controller.requestClose()); until([&] { return !controller.busy(); });
        require(closeSignals == 0); require(controller.errorCode() == QStringLiteral("rollback_failed"));
        require(controller.status().contains(QStringLiteral("Не запускайте")));
    }
    {
        auto backend = fake(local); backend->holdPreview = true;
        auto controller = std::make_unique<Controller>(backend, local);
        controller->chooseFile(source(fixture)); controller->preview(password);
        until([&] { return backend->previews == 1; });
        std::thread release([backend] { std::this_thread::sleep_for(std::chrono::milliseconds(30)); backend->release(); });
        controller.reset(); release.join(); require(!backend->wrongThread);
        pump(); // Completion queued during derived destruction must not execute.
    }
    {
        auto backend = fake(local);
        auto* controller = new Controller(backend, local);
        QPointer<Controller> alive(controller);
        controller->chooseFile(source(fixture));
        QObject::connect(controller, &Controller::changed, controller, [controller] {
            if (controller->busy()) delete controller;
        }, Qt::DirectConnection);
        controller->preview(password); require(alive.isNull()); pump();
    }
    {
        auto backend = fake(local); auto* controller = new Controller(backend, local);
        std::atomic_bool completed{false}; int delivered = 0;
        QObject::connect(controller->testingWorker(), &Worker::completed, controller->testingWorker(),
            [&](quint64, const QVariantMap&) { completed = true; }, Qt::DirectConnection);
        QObject::connect(controller, &Controller::changed, qApp, [&] { ++delivered; });
        controller->chooseFile(source(fixture)); controller->preview(password);
        for (int i = 0; i < 1000 && !completed; ++i) QThread::msleep(1);
        require(completed); const auto before = delivered;
        QObject::connect(controller, &QObject::destroyed, qApp, [controller] {
            QCoreApplication::sendPostedEvents(controller, QEvent::MetaCall);
            QCoreApplication::processEvents();
        }, Qt::DirectConnection);
        delete controller; require(delivered == before); pump();
    }
}
void snapshots(const QString& fixture) {
    const auto local = QDir(fixture).filePath(QStringLiteral("synthetic-local"));
    auto backend = fake(local); Controller controller(backend, local);
    QQmlApplicationEngine engine;
    QObject::connect(&engine, &QQmlEngine::warnings, &engine, [](const QList<QQmlError>& warnings) {
        // Test-only source diagnostics remain visible when Qt logging is disabled.
        for (const auto& warning : warnings) {
            const QJsonObject diagnostic{{QStringLiteral("qmlSource"), warning.url().fileName()},
                {QStringLiteral("line"), warning.line()}, {QStringLiteral("column"), warning.column()},
                {QStringLiteral("description"), warning.description().left(512)}};
            std::cerr << QJsonDocument(diagnostic).toJson(QJsonDocument::Compact).constData() << std::endl;
        }
    });
    engine.setInitialProperties({{QStringLiteral("visible"), false},
        {QStringLiteral("x"), -32000}, {QStringLiteral("y"), -32000}});
    engine.rootContext()->setContextProperty(QStringLiteral("transferController"), &controller);
    engine.load(QUrl(QStringLiteral("qrc:/valmax-transfer/Main.qml")));
    require(engine.rootObjects().size() == 1);
    auto* window = qobject_cast<QQuickWindow*>(engine.rootObjects().first()); require(window != nullptr);
    window->setFlag(Qt::WindowDoesNotAcceptFocus, true);
    window->setFlag(Qt::Tool, true);
    window->setPosition(-32000, -32000);
    window->setVisible(true);
    const auto directory = QDir(fixture).filePath(QStringLiteral("screenshots"));
    require(QDir().mkpath(directory));
    auto shot = [&](const QString& name) {
        window->requestUpdate(); pump(100);
        const auto image = window->grabWindow(); require(!image.isNull());
        require(image.save(QDir(directory).filePath(name + QStringLiteral(".png"))));
    };
    shot(QStringLiteral("idle")); previewReady(controller, fixture); shot(QStringLiteral("preview"));
    window->resize(390, 450); shot(QStringLiteral("preview-small"));
    auto* scroll = window->findChild<QObject*>(QStringLiteral("transferScrollView"));
    require(scroll != nullptr);
    auto* flickable = scroll->property("contentItem").value<QObject*>();
    require(flickable != nullptr && flickable->property("contentY").isValid());
    auto* primary = window->findChild<QQuickItem*>(QStringLiteral("primaryButton"));
    require(primary != nullptr);
    const auto startY = flickable->property("contentY").toReal();
    require(flickable->property("contentHeight").toReal() > flickable->property("height").toReal());
    const auto buttonVisible = [&] {
        const auto origin = primary->mapToScene(QPointF());
        return origin.y() >= 0 && origin.y() + primary->height() <= window->height();
    };
    require(!buttonVisible());
    // Deliver only to this owned window; never inject desktop or user input.
    QElapsedTimer wheelClock; wheelClock.start();
    for (int i = 0; i < 16 && !buttonVisible(); ++i) {
        const QPointF localPoint(window->width() / 2.0, window->height() / 2.0);
        QWheelEvent wheel(localPoint, window->mapToGlobal(localPoint), QPoint(), QPoint(0, -120),
            Qt::NoButton, Qt::NoModifier, Qt::NoScrollPhase, false, Qt::MouseEventSynthesizedByApplication);
        wheel.setTimestamp(static_cast<quint64>(wheelClock.elapsed()) + 1);
        QCoreApplication::sendEvent(window, &wheel);
        pump(50);
    }
    const auto primaryOrigin = primary->mapToScene(QPointF());
    const QJsonObject scrollMetrics{{QStringLiteral("scrollCheck"), true},
        {QStringLiteral("contentY"), flickable->property("contentY").toReal()},
        {QStringLiteral("contentHeight"), flickable->property("contentHeight").toReal()},
        {QStringLiteral("viewHeight"), flickable->property("height").toReal()},
        {QStringLiteral("primaryY"), primaryOrigin.y()}, {QStringLiteral("primaryHeight"), primary->height()},
        {QStringLiteral("windowHeight"), window->height()}, {QStringLiteral("wheelReachedButton"), buttonVisible()}};
    std::cerr << QJsonDocument(scrollMetrics).toJson(QJsonDocument::Compact).constData() << std::endl;
    if (!buttonVisible()) shot(QStringLiteral("preview-small-wheel-failure"));
    require(flickable->property("contentY").toReal() > startY);
    require(buttonVisible());
    require(primary->property("enabled").toBool());
    shot(QStringLiteral("preview-small-bottom"));
    window->resize(620, 690);
    require(flickable->setProperty("contentY", 0.0));
    QGuiApplication::styleHints()->setColorScheme(Qt::ColorScheme::Dark);
    pump(); require(window->property("dark").toBool());
    shot(QStringLiteral("preview-dark"));
    QGuiApplication::styleHints()->setColorScheme(Qt::ColorScheme::Light);
    pump(); require(!window->property("dark").toBool());
    backend->holdImport = true; controller.confirmImport();
    until([&] { return backend->imports == 1; }); shot(QStringLiteral("busy"));
    controller.cancel(); until([&] { return !controller.busy(); });
    require(controller.errorCode() == QStringLiteral("cancelled"));
    window->close(); pump();
}
void realPreview(const QString& fixture) {
    auto backend = makeProductionBackend();
    auto secret = password.toUtf8();
    const auto result = backend->inspect(source(fixture), secret);
    require(result.ok); require(result.accounts.size() == 2); require(result.fingerprint.size() == 64);
    require(result.accounts[0].id == QStringLiteral("42"));
    require(result.accounts[1].id == QStringLiteral("9223372036854775807"));
    require(result.accounts[0].name == QStringLiteral("Alice Example"));
    require(result.accounts[1].name == QStringLiteral("Борис Тестовый"));
    auto wrong = QByteArray("synthetic-wrong-password");
    const auto rejected = backend->inspect(source(fixture), wrong);
    require(!rejected.ok); require(rejected.error == QStringLiteral("authentication_failed"));
    for (const auto& name : {QStringLiteral("synthetic-tampered.mdata"), QStringLiteral("synthetic-invalid.mdata")}) {
        const auto bad = backend->inspect(QUrl::fromLocalFile(QDir(fixture).filePath(name)), secret);
        require(!bad.ok); require(bad.accounts.isEmpty()); require(bad.fingerprint.isEmpty());
    }
    erase(secret); erase(wrong); require(secret.isEmpty() && wrong.isEmpty());
}
void recoveryTests(const QString& fixture) {
    const auto local = QDir(fixture).filePath(QStringLiteral("synthetic-local"));
    {
        auto backend = fake(local); Controller controller(backend, local, Mode::Recovery);
        until([&] { return backend->recoveryChecks == 1 && !controller.busy(); });
        require(controller.recoveryMode()); require(controller.recoveryReady()); require(!controller.previewReady());
        require(backend->recoveries == 0); require(controller.accounts().isEmpty());
        controller.chooseFile(source(fixture)); controller.preview(password); controller.confirmImport(); pump();
        require(controller.fileName().isEmpty()); require(backend->previews == 0 && backend->imports == 0);
        controller.confirmRecovery(QStringLiteral("short")); require(backend->recoveries == 0);
        require(controller.errorCode() == QStringLiteral("invalid_password"));
        controller.confirmRecovery(password); controller.confirmRecovery(password);
        until([&] { return !controller.busy(); });
        require(backend->recoveries == 1); require(controller.succeeded()); require(!controller.available());
        require(!controller.recoveryReady()); require(controller.status().contains(QStringLiteral("Профиль восстановлен")));
        require(!backend->wrongThread); require(controller.requestClose());
    }
    {
        auto backend = fake(local); backend->recoveryError = QStringLiteral("authentication_failed");
        Controller controller(backend, local, Mode::Recovery);
        until([&] { return controller.recoveryReady(); }); controller.confirmRecovery(password);
        until([&] { return !controller.busy(); }); require(!controller.recoveryReady()); require(controller.available());
        controller.confirmRecovery(password); pump(); require(backend->recoveries == 1);
        backend->recoveryError.clear(); controller.refreshRecovery(); until([&] { return controller.recoveryReady(); });
        controller.confirmRecovery(password); until([&] { return !controller.busy(); }); require(controller.succeeded());
    }
    for (const auto& code : {QStringLiteral("rollback_failed"), QStringLiteral("recovery_required"), QString()}) {
        auto backend = fake(local); backend->holdImport = true; backend->commitAfterCancel = true; backend->recoveryError = code;
        Controller controller(backend, local, Mode::Recovery); int closed = 0;
        QObject::connect(&controller, &Controller::readyToClose, &controller, [&] { ++closed; });
        until([&] { return controller.recoveryReady(); }); controller.confirmRecovery(password);
        until([&] { return backend->recoveries == 1; }); require(!controller.requestClose());
        controller.inputsChanged(); controller.refreshRecovery();
        until([&] { return !controller.busy(); }); require(backend->sawCancel); require(backend->recoveryChecks == 1);
        if (code.isEmpty()) { require(controller.succeeded()); require(closed == 1); }
        else {
            require(!controller.succeeded()); require(controller.errorCode() == code); require(closed == 0);
            require(!controller.available()); require(controller.status().contains(QStringLiteral("Не запускайте")));
            controller.refreshRecovery(); pump(); require(backend->recoveryChecks == 1);
        }
        require(controller.requestClose());
    }
    {
        auto backend = fake(local); Controller controller(backend, local, Mode::Recovery);
        std::atomic_bool completed{false};
        QObject::connect(controller.testingWorker(), &Worker::completed, controller.testingWorker(),
            [&](quint64, const QVariantMap&) { completed = true; }, Qt::DirectConnection);
        controller.refreshRecovery(); // Starts before the queued automatic status check.
        for (int i = 0; i < 1000 && !completed; ++i) QThread::msleep(1);
        require(completed); controller.cancel(); until([&] { return !controller.busy(); });
        require(!controller.recoveryReady()); require(controller.errorCode() == QStringLiteral("cancelled"));
        controller.confirmRecovery(password); pump(); require(backend->recoveries == 0);
    }
    {
        auto backend = fake(local); backend->noRecovery = true; Controller controller(backend, local, Mode::Recovery);
        until([&] { return backend->recoveryChecks == 1 && !controller.busy(); });
        require(!controller.recoveryReady()); require(controller.errorCode().isEmpty());
        controller.confirmRecovery(password); pump(); require(backend->recoveries == 0);
    }
    {
        auto backend = fake(local); backend->holdImport = true;
        auto controller = std::make_unique<Controller>(backend, local, Mode::Recovery);
        until([&] { return controller->recoveryReady(); }); controller->confirmRecovery(password);
        until([&] { return backend->recoveries == 1; }); controller.reset();
        require(backend->sawCancel); require(!backend->wrongThread); pump();
    }
}
void recoverySnapshots(const QString& fixture) {
    const auto local = QDir(fixture).filePath(QStringLiteral("synthetic-local"));
    auto backend = fake(local); Controller controller(backend, local, Mode::Recovery);
    QQmlApplicationEngine engine;
    engine.setInitialProperties({{QStringLiteral("visible"), false}, {QStringLiteral("opacity"), 0.0}});
    engine.rootContext()->setContextProperty(QStringLiteral("transferController"), &controller);
    engine.load(QUrl(QStringLiteral("qrc:/valmax-transfer/Main.qml")));
    require(engine.rootObjects().size() == 1);
    auto* window = qobject_cast<QQuickWindow*>(engine.rootObjects().first()); require(window != nullptr);
    window->setOpacity(0.0);
    window->setFlag(Qt::WindowDoesNotAcceptFocus, true); window->setFlag(Qt::Tool, true);
    window->setFlag(Qt::WindowTransparentForInput, true);
    auto* screen = QGuiApplication::primaryScreen(); require(screen != nullptr);
    const auto screenBounds = screen->availableGeometry();
    // Popup positioning uses screen geometry. Keep this owned window inside it,
    // but transparent, unfocusable and input-transparent to avoid desktop impact.
    window->setPosition(screenBounds.topLeft() + QPoint(20, 20)); window->setVisible(true);
    until([&] { return controller.recoveryReady(); });
    require(!window->findChild<QObject*>(QStringLiteral("chooseFileButton"))->property("visible").toBool());
    auto* input = window->findChild<QObject*>(QStringLiteral("passwordField")); require(input != nullptr);
    require(input->setProperty("text", password));
    auto* primary = window->findChild<QObject*>(QStringLiteral("primaryButton")); require(primary != nullptr);
    auto* dialog = window->findChild<QObject*>(QStringLiteral("recoveryConfirmation")); require(dialog != nullptr);
    const auto directory = QDir(fixture).filePath(QStringLiteral("screenshots")); require(QDir().mkpath(directory));
    auto shot = [&](const QString& name) {
        window->requestUpdate(); pump(100); const auto image = window->grabWindow(); require(!image.isNull());
        require(image.save(QDir(directory).filePath(name + QStringLiteral(".png"))));
    };
    shot(QStringLiteral("recovery-ready"));
    require(QMetaObject::invokeMethod(primary, "clicked", Qt::DirectConnection)); pump(200);
    require(dialog->property("visible").toBool()); require(backend->recoveries == 0);
    auto checkDialogBounds = [&] {
        const auto x = dialog->property("x").toReal(), y = dialog->property("y").toReal();
        const auto width = dialog->property("width").toReal(), height = dialog->property("height").toReal();
        const QJsonObject geometry{{QStringLiteral("dialogX"), x}, {QStringLiteral("dialogY"), y},
            {QStringLiteral("dialogWidth"), width}, {QStringLiteral("dialogHeight"), height},
            {QStringLiteral("windowWidth"), window->width()}, {QStringLiteral("windowHeight"), window->height()},
            {QStringLiteral("windowX"), window->x()}, {QStringLiteral("windowY"), window->y()}};
        std::cerr << QJsonDocument(geometry).toJson(QJsonDocument::Compact).constData() << std::endl;
        require(width > 250 && height > 150);
        require(x >= 0 && y >= 0 && x + width <= window->width() && y + height <= window->height());
        for (const auto& name : {QStringLiteral("recoveryConfirmationTitle"), QStringLiteral("recoveryConfirmationBody"),
             QStringLiteral("recoveryRejectButton"), QStringLiteral("recoveryAcceptButton")}) {
            auto* item = window->findChild<QQuickItem*>(name); require(item != nullptr && item->isVisible());
            const auto bounds = item->mapRectToScene(item->boundingRect());
            const QJsonObject itemGeometry{{QStringLiteral("item"), name},
                {QStringLiteral("sceneX"), bounds.x()}, {QStringLiteral("sceneY"), bounds.y()},
                {QStringLiteral("sceneWidth"), bounds.width()}, {QStringLiteral("sceneHeight"), bounds.height()},
                {QStringLiteral("implicitHeight"), item->implicitHeight()}};
            std::cerr << QJsonDocument(itemGeometry).toJson(QJsonDocument::Compact).constData() << std::endl;
            require(bounds.width() > 0 && bounds.height() > 0);
            require(bounds.left() >= x && bounds.top() >= y && bounds.right() <= x + width && bounds.bottom() <= y + height);
            require(bounds.left() >= 0 && bounds.top() >= 0 && bounds.right() <= window->width() && bounds.bottom() <= window->height());
            if (name == QStringLiteral("recoveryConfirmationBody"))
                require(item->height() >= item->property("implicitHeight").toReal() && item->height() > 40);
        }
    };
    shot(QStringLiteral("recovery-confirmation"));
    checkDialogBounds();
    window->resize(390, 450); pump(200); checkDialogBounds();
    shot(QStringLiteral("recovery-confirmation-small"));
    require(QMetaObject::invokeMethod(dialog, "reject", Qt::DirectConnection)); pump(); require(backend->recoveries == 0);
    window->resize(620, 690); pump(100);
    require(QMetaObject::invokeMethod(primary, "clicked", Qt::DirectConnection)); pump();
    backend->holdImport = true;
    require(QMetaObject::invokeMethod(dialog, "accept", Qt::DirectConnection));
    until([&] { return backend->recoveries == 1; }); require(input->property("text").toString().isEmpty());
    shot(QStringLiteral("recovery-busy")); controller.cancel(); until([&] { return !controller.busy(); });
    require(controller.errorCode() == QStringLiteral("cancelled")); window->close(); pump();
}
void realRecoveryStatus(const QString& fixture) {
    const auto local = QDir(fixture).filePath(QStringLiteral("synthetic-recovery-local"));
    require(QDir().mkpath(local)); auto backend = makeProductionBackend();
    const auto result = backend->recoveryStatus(local);
    require(result.ok && !result.required && result.fingerprint.isEmpty());
    QFile marker(QDir(local).filePath(QStringLiteral("MXMOD-MData-Recovery.json")));
    require(!marker.exists()); require(marker.open(QIODevice::WriteOnly | QIODevice::NewOnly));
    require(marker.write("{") == 1); marker.close();
    const auto bad = backend->recoveryStatus(local);
    require(!bad.ok && bad.error == QStringLiteral("recovery_required"));
    require(marker.remove());
}
QJsonObject modules() {
    QJsonObject result;
    for (const auto* name : {L"valmax_core.dll", L"Qt6Core.dll", L"Qt6Gui.dll", L"Qt6Qml.dll", L"Qt6Quick.dll", L"qwindows.dll"}) {
        wchar_t path[32768]{};
        const auto module = GetModuleHandleW(name);
        const auto count = module ? GetModuleFileNameW(module, path, 32768) : 0;
        require(count != 0 && count < 32768);
        result.insert(QString::fromWCharArray(name), QString::fromWCharArray(path, static_cast<qsizetype>(count)));
    }
    return result;
}
}
int main(int argc, char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    QGuiApplication application(argc, argv);
    application.setQuitOnLastWindowClosed(false);
    QQuickStyle::setStyle(QStringLiteral("Fusion"));
    try {
        require(argc == 3);
        const auto suite = QString::fromLocal8Bit(argv[1]);
        const auto fixture = QDir::cleanPath(QString::fromLocal8Bit(argv[2]));
        require(fixture.startsWith(QStringLiteral("C:/ValMAXBuild/"), Qt::CaseInsensitive)
            || fixture.startsWith(QStringLiteral("C:\\ValMAXBuild\\"), Qt::CaseInsensitive)
            || fixture.startsWith(QStringLiteral("D:/ValMAXBuild/"), Qt::CaseInsensitive)
            || fixture.startsWith(QStringLiteral("D:\\ValMAXBuild\\"), Qt::CaseInsensitive));
        require(QDir(fixture).exists());
        if (suite == QStringLiteral("preview")) {
            std::exception_ptr failure;
            std::thread worker([&] { try { realPreview(fixture); } catch (...) { failure = std::current_exception(); } });
            worker.join(); if (failure) std::rethrow_exception(failure);
        }
        else if (suite == QStringLiteral("controller")) { controllerTests(fixture); snapshots(fixture); }
        else if (suite == QStringLiteral("recovery")) {
            std::exception_ptr failure;
            std::thread worker([&] { try { realRecoveryStatus(fixture); } catch (...) { failure = std::current_exception(); } });
            worker.join(); if (failure) std::rethrow_exception(failure);
            recoveryTests(fixture); recoverySnapshots(fixture);
        }
        else require(false);
        const auto loaded = modules();
        const QJsonObject result{{QStringLiteral("ok"), true}, {QStringLiteral("checks"), checks},
            {QStringLiteral("suite"), suite}, {QStringLiteral("loadedModules"), loaded},
            {QStringLiteral("realCredentialCalls"), false}, {QStringLiteral("successfulProductionImportTested"), false},
            {QStringLiteral("successfulProductionRecoveryTested"), false}};
        std::cout << QJsonDocument(result).toJson(QJsonDocument::Compact).constData() << std::endl;
        return 0;
    } catch (...) {
        std::cout << "{\"ok\":false,\"error\":\"transfer_app_test_failed\",\"checks\":" << checks << "}" << std::endl;
        return 1;
    }
}
