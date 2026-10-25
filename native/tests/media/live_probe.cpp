// Explicit, opt-in Wikimedia smoke check. No MAX process, account or local server.
#include "controller.h"
#include "thumbnail.h"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QJsonDocument>
#include <QJsonArray>
#include <QChildEvent>
#include <QNetworkReply>
#include <QSet>
#include <QSslSocket>
#include <QThread>
#include <QTimer>
#include <atomic>
#include <cstdio>

using namespace valmax::media;
namespace {
std::atomic<int> warnings{0};
void quiet_log(QtMsgType type, const QMessageLogContext&, const QString&) {
    if(type == QtWarningMsg || type == QtCriticalMsg || type == QtFatalMsg) ++warnings;
}
class Probe final : public QObject {
public:
    explicit Probe(QJsonObject* output) : output_(output) {}
    QJsonObject report;
    void start() {
        elapsed.start();
        report.insert(QStringLiteral("qt"), QString::fromLatin1(qVersion()));
        report.insert(QStringLiteral("tlsAvailable"), QSslSocket::supportsSsl());
        scheduler = new ProviderScheduler(this);
        transport = new MediaTransport(scheduler, this);
        transport->installEventFilter(this);
        controller = new MediaController(scheduler, transport, this);
        const auto page = controller->open_page(101, 1); // Synthetic identifiers only.
        if(!page) { stop(page.error); return; }
        scope = *page.value;
        const auto accepted = controller->search(scope, QByteArrayLiteral("{\"mode\":\"pic\",\"query\":\"Example.png\"}"),
            [this](quint64, ControllerResult<SearchRows> result) {
                if(!result) { stop(result.error); return; }
                report.insert(QStringLiteral("rows"), result.value->size());
                for(const auto& row : *result.value) {
                    if(row.value(QStringLiteral("preview")).toBool() &&
                       row.value(QStringLiteral("bytes")).toInteger() <= 4 * 1024 * 1024) {
                        selected = row.value(QStringLiteral("id")).toString();
                        break;
                    }
                }
                if(selected.isEmpty()) { stop({ControllerCode::ResultExpired,{}}); return; }
                const auto begun = controller->preview(scope, selected,
                    [this](quint64, ControllerResult<PrivateMedia> preview) {
                        if(!preview) { stop(preview.error); return; }
                        report.insert(QStringLiteral("previewBytes"), preview.value->bytes.size());
                        const auto decoded = decode_thumbnail(preview.value->bytes, preview.value->mime);
                        if(!decoded) { stop({ControllerCode::Remote,decoded.error}); return; }
                        report.insert(QStringLiteral("decoded"), !decoded.value->image.isNull());
                        const auto json = QJsonDocument(QJsonObject{{QStringLiteral("id"),selected}}).toJson(QJsonDocument::Compact);
                        const auto download = controller->select(scope, json,
                            [this](quint64, ControllerResult<PrivateMedia> file) {
                                if(!file) { stop(file.error); return; }
                                report.insert(QStringLiteral("originalBytes"), file.value->bytes.size());
                                stop({}); // Bytes leave scope; no file or draft is created.
                            });
                        if(!download) stop(download.error);
                    });
                if(!begun) stop(begun.error);
            });
        report.insert(QStringLiteral("searchAccepted"), bool(accepted));
        if(!accepted) stop(accepted.error);
    }
private:
    bool eventFilter(QObject*, QEvent* event) override {
        if(event->type() != QEvent::ChildAdded) return false;
        auto* reply = qobject_cast<QNetworkReply*>(static_cast<QChildEvent*>(event)->child());
        if(!reply || network.size() >= 8) return false;
        const qsizetype index = network.size();
        network.append(QJsonObject{});
        const auto record = [this, reply, index] {
            const auto status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute);
            const auto rawType = reply->rawHeader("Content-Type").toLower();
            const QString mime = rawType.startsWith("application/json") ? QStringLiteral("json") :
                rawType.startsWith("image/") ? QStringLiteral("image") :
                rawType.startsWith("text/html") ? QStringLiteral("html") : QStringLiteral("other");
            qsizetype headerBytes = 0;
            bool invalidHeader = false, duplicateCritical = false;
            QJsonArray invalidHeaders;
            QSet<QByteArray> names;
            for(const auto& pair : reply->rawHeaderPairs()) {
                headerBytes += pair.first.size() + pair.second.size();
                invalidHeader |= pair.first.size()>256 || pair.second.size()>8192 ||
                    pair.first.contains('\r') || pair.first.contains('\n') || pair.first.contains('\0') ||
                    pair.second.contains('\r') || pair.second.contains('\n') || pair.second.contains('\0');
                if(pair.first.size()>256 || pair.second.size()>8192 || pair.first.contains('\r') || pair.first.contains('\n') || pair.first.contains('\0') || pair.second.contains('\r') || pair.second.contains('\n') || pair.second.contains('\0'))
                    invalidHeaders.append(QJsonObject{{QStringLiteral("name"),QString::fromLatin1(pair.first.left(64))},
                        {QStringLiteral("cr"),pair.second.contains('\r')},{QStringLiteral("lf"),pair.second.contains('\n')},
                        {QStringLiteral("nul"),pair.second.contains('\0')},{QStringLiteral("bytes"),pair.second.size()}});
                const auto key = pair.first.toLower();
                if(key == "content-length" || key == "content-type" || key == "content-encoding" ||
                   key == "location" || key == "retry-after") {
                    duplicateCritical |= names.contains(key);
                    names.insert(key);
                }
            }
            network[index] = QJsonObject{
                {QStringLiteral("status"),status.toInt()},
                {QStringLiteral("statusType"),status.metaType().id()},
                {QStringLiteral("networkError"),int(reply->error())},
                {QStringLiteral("mimeClass"),mime},
                {QStringLiteral("headerCount"),reply->rawHeaderPairs().size()},
                {QStringLiteral("headerBytes"),headerBytes},
                {QStringLiteral("invalidHeader"),invalidHeader},
                {QStringLiteral("invalidHeaders"),invalidHeaders},
                {QStringLiteral("duplicateCritical"),duplicateCritical},
                {QStringLiteral("declaredBytes"),reply->rawHeader("Content-Length").toLongLong()},
                {QStringLiteral("encodingPresent"),!reply->rawHeader("Content-Encoding").isEmpty()},
                {QStringLiteral("cached"),reply->attribute(QNetworkRequest::SourceIsFromCacheAttribute).toBool()},
                {QStringLiteral("originalLengthPresent"),reply->attribute(QNetworkRequest::OriginalContentLengthAttribute).isValid()}
            };
        };
        connect(reply, &QNetworkReply::metaDataChanged, this, record);
        connect(reply, &QNetworkReply::finished, this, record);
        connect(reply, &QNetworkReply::errorOccurred, this, [record](QNetworkReply::NetworkError) { record(); });
        return false;
    }
    ProviderScheduler* scheduler = nullptr;
    MediaTransport* transport = nullptr;
    MediaController* controller = nullptr;
    ControllerScope scope;
    QString selected;
    QElapsedTimer elapsed;
    bool stopped = false;
    QJsonObject* output_;
    QJsonArray network;
    void stop(ControllerError error) {
        if(stopped) return;
        stopped = true;
        report.insert(QStringLiteral("ok"), error.code == ControllerCode::Ok);
        report.insert(QStringLiteral("controllerCode"), int(error.code));
        report.insert(QStringLiteral("providerCode"), code_name(error.remote.code));
        report.insert(QStringLiteral("providerStatus"), error.remote.status);
        report.insert(QStringLiteral("elapsedMs"), elapsed.elapsed());
        report.insert(QStringLiteral("warnings"), warnings.load());
        if(error.remote.retryAfterSeconds)
            report.insert(QStringLiteral("retryAfterSeconds"), *error.remote.retryAfterSeconds);
        // Defer destruction out of controller callback. Ownership stays entirely
        // on this service thread; no blocking callback to the GUI/main thread.
        QTimer::singleShot(0, this, [this] {
            delete controller; controller = nullptr;
            delete transport; transport = nullptr;
            delete scheduler; scheduler = nullptr;
            report.insert(QStringLiteral("network"), network);
            *output_ = report;
            QThread::currentThread()->quit();
        });
    }
};
}
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    qInstallMessageHandler(quiet_log);
    QCoreApplication::setLibraryPaths({QCoreApplication::applicationDirPath() + QStringLiteral("/plugins")});
    if(app.arguments().size() != 2 || app.arguments().at(1) != QStringLiteral("--live-public-wikimedia")) return 64;
    QThread worker;
    QJsonObject result;
    auto* probe = new Probe(&result);
    probe->moveToThread(&worker);
    QObject::connect(&worker, &QThread::started, probe, [probe] { probe->start(); });
    QObject::connect(&worker, &QThread::finished, &app, &QCoreApplication::quit);
    QObject::connect(&worker, &QThread::finished, probe, &QObject::deleteLater);
    worker.start();
    app.exec();
    worker.wait();
    // The worker has joined; its finite report is now immutable. No private
    // URLs, query text, provider body, file bytes or credentials are reported.
    const QByteArray report = QJsonDocument(result).toJson(QJsonDocument::Compact);
    const bool ok = result.value(QStringLiteral("ok")).toBool();
    std::fwrite(report.constData(), 1, size_t(report.size()), stdout);
    std::fputc('\n', stdout);
    return ok ? 0 : 2;
}
