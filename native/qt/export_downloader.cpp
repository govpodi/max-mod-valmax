#include "export_downloader.h"
#include "../core/valmax_core.h"
#include <QByteArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QThread>
#include <QTimer>
#include <QUrl>
#include <QUuid>

namespace valmax {
namespace {
constexpr qint64 MaximumBytes = qint64(16) * 1024 * 1024 * 1024;
bool allowedUrl(const QUrl& url, bool loopback) {
    if (!url.isValid() || url.isRelative() || url.host().isEmpty()
        || !url.userInfo().isEmpty() || url.hasFragment() || url.toEncoded().size() > 8192) return false;
    if (loopback && url.scheme() == QStringLiteral("http") && url.host() == QStringLiteral("127.0.0.1")) return true;
    return url.scheme() == QStringLiteral("https") && url.port(443) == 443;
}
QString sinkError(uint32_t code) {
    switch (code) {
    case 3: return QStringLiteral("busy");
    case 4: return QStringLiteral("cancelled");
    case 6: return QStringLiteral("too_large");
    case 7: return QStringLiteral("no_space");
    default: return QStringLiteral("download_failed");
    }
}
uint32_t openCore(void* runtime, const uint8_t* data, size_t size, ValmaxExportDownload** output) {
#if VALMAX_HAS_RUST_CORE
    return valmax_export_download_open(static_cast<ValmaxRuntime*>(runtime), data, size, output);
#else
    Q_UNUSED(runtime); Q_UNUSED(data); Q_UNUSED(size); Q_UNUSED(output); return 2;
#endif
}
uint32_t writeCore(ValmaxExportDownload* sink, const uint8_t* data, size_t size) {
#if VALMAX_HAS_RUST_CORE
    return valmax_export_download_write(sink, data, size);
#else
    Q_UNUSED(sink); Q_UNUSED(data); Q_UNUSED(size); return 2;
#endif
}
uint32_t closeCore(ValmaxExportDownload** sink) {
#if VALMAX_HAS_RUST_CORE
    return valmax_export_download_close(sink);
#else
    *sink = nullptr; return 0;
#endif
}
}

class ExportDownloadWorker final : public QObject {
    Q_OBJECT
public:
    ExportDownloadWorker(QString id, QByteArray job, QUrl url, void* runtime,
                         std::shared_ptr<void> keepAlive, bool loopback)
        : id_(std::move(id)), job_(std::move(job)), url_(std::move(url)), runtime_(runtime),
          keepAlive_(std::move(keepAlive)), loopback_(loopback) {}
    ~ExportDownloadWorker() override { closeSink(); job_.fill(0); }
public slots:
    void begin() {
        manager_ = new QNetworkAccessManager(this);
        timer_ = new QTimer(this); timer_->setSingleShot(true);
        connect(timer_, &QTimer::timeout, this, [this] { finish(false, QStringLiteral("timeout")); });
        timer_->start(120000);
        openSink();
    }
    void cancel(const QString& id) { if (id == id_) finish(false, QStringLiteral("cancelled")); }
signals:
    void done(const QString& id, bool success, const QString& error);
private:
    uint32_t closeSink() { return sink_ ? closeCore(&sink_) : 0; }
    void openSink() {
        if (finished_) return;
        const auto code = openCore(runtime_,
            reinterpret_cast<const uint8_t*>(job_.constData()), static_cast<size_t>(job_.size()), &sink_);
        if (code == 3) {
            QTimer::singleShot(100, this, [this] { openSink(); });
            return;
        }
        job_.fill(0); job_.clear();
        if (code != 0 || !sink_) { finish(false, sinkError(code)); return; }
        issue(url_);
    }
    void issue(const QUrl& url) {
        if (finished_) return;
        if (!allowedUrl(url, loopback_)) { finish(false, QStringLiteral("invalid_url")); return; }
        QNetworkRequest request(url);
        request.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::ManualRedirectPolicy);
        request.setRawHeader(QByteArrayLiteral("Accept-Encoding"), QByteArrayLiteral("identity"));
        reply_ = manager_->get(request);
        reply_->setReadBufferSize(128 * 1024);
        connect(reply_, &QNetworkReply::metaDataChanged, this, [this] { checkHeaders(); });
        connect(reply_, &QIODevice::readyRead, this, [this] { drain(); });
        connect(reply_, &QNetworkReply::finished, this, [this] { replyFinished(); });
    }
    int status() const { return reply_ ? reply_->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt() : 0; }
    void checkHeaders() {
        if (finished_ || !reply_ || status() != 200) return;
        const auto encoding = reply_->rawHeader(QByteArrayLiteral("Content-Encoding")).trimmed().toLower();
        if (!encoding.isEmpty() && encoding != QByteArrayLiteral("identity")) {
            finish(false, QStringLiteral("unsupported_encoding")); return;
        }
        const auto raw = reply_->rawHeader(QByteArrayLiteral("Content-Length")).trimmed();
        if (!raw.isEmpty()) {
            bool valid = false; const auto length = raw.toLongLong(&valid);
            if (!valid || length < 0 || length > MaximumBytes) { finish(false, QStringLiteral("too_large")); return; }
            expected_ = length;
        }
    }
    void drain() {
        if (finished_ || !reply_ || status() != 200) return;
        checkHeaders();
        while (!finished_ && reply_ && reply_->bytesAvailable() > 0) {
            const auto data = reply_->read(64 * 1024);
            if (data.isEmpty()) break;
            if (received_ > MaximumBytes - data.size()) { finish(false, QStringLiteral("too_large")); return; }
            const auto code = writeCore(sink_, reinterpret_cast<const uint8_t*>(data.constData()),
                                                           static_cast<size_t>(data.size()));
            if (code != 0) { finish(false, sinkError(code)); return; }
            received_ += data.size();
        }
    }
    void replyFinished() {
        if (finished_ || !reply_) return;
        const auto target = reply_->attribute(QNetworkRequest::RedirectionTargetAttribute).toUrl();
        if (!target.isEmpty() && status() >= 300 && status() < 400) {
            const auto next = reply_->url().resolved(target);
            if (++redirects_ > 5 || !allowedUrl(next, loopback_)) {
                finish(false, QStringLiteral("invalid_url")); return;
            }
            auto* old = reply_; reply_ = nullptr;
            old->disconnect(this); old->deleteLater();
            issue(next); return;
        }
        if (status() != 200 || reply_->error() != QNetworkReply::NoError) {
            const auto code = status();
            finish(false, code == 403 ? QStringLiteral("permission_denied")
                : code == 404 ? QStringLiteral("not_available") : QStringLiteral("download_failed"));
            return;
        }
        drain();
        if (finished_) return;
        if (received_ == 0 || (expected_ >= 0 && received_ != expected_)) {
            finish(false, QStringLiteral("download_failed")); return;
        }
        finish(true, QString());
    }
    void finish(bool success, const QString& error) {
        if (finished_) return;
        finished_ = true;
        if (timer_) timer_->stop();
        if (reply_) { reply_->disconnect(this); reply_->abort(); reply_->deleteLater(); reply_ = nullptr; }
        job_.fill(0); job_.clear();
        // A terminal signal always follows closure of this task's exact writer.
        // The engine-owned QML owner can then ACK/import/delete that same lease.
        const auto code = closeSink();
        emit done(id_, success && code == 0, success && code != 0 ? sinkError(code) : error);
    }
    QString id_;
    QByteArray job_;
    QUrl url_;
    void* runtime_;
    std::shared_ptr<void> keepAlive_;
    bool loopback_ = false, finished_ = false;
    QNetworkAccessManager* manager_ = nullptr;
    QNetworkReply* reply_ = nullptr;
    QTimer* timer_ = nullptr;
    ValmaxExportDownload* sink_ = nullptr;
    qint64 received_ = 0, expected_ = -1;
    unsigned redirects_ = 0;
};

ExportDownloader::ExportDownloader(QObject* parent) : QObject(parent) {}
ExportDownloader::~ExportDownloader() {
    if (!active_.isEmpty()) emit cancelWorker(active_);
    for (auto* thread : threads_) { thread->quit(); thread->wait(); }
}
void ExportDownloader::setRuntime(void* runtime, std::shared_ptr<void> keepAlive) {
    if (!active_.isEmpty()) cancel(active_);
    runtime_ = runtime; keepAlive_ = std::move(keepAlive);
    emit stateChanged();
}
QVariantMap ExportDownloader::start(const QString& job, const QString& rawUrl) {
    const auto failure = [](const QString& error) { return QVariantMap{{QStringLiteral("accepted"), false}, {QStringLiteral("error"), error}}; };
    if (QThread::currentThread() != thread() || !runtime_) return failure(QStringLiteral("unavailable"));
    if (busy()) return failure(QStringLiteral("busy"));
    const QUrl url(rawUrl, QUrl::StrictMode);
    if (!allowedUrl(url, testLoopback_)) return failure(QStringLiteral("invalid_url"));
    auto body = job.toUtf8();
    if (body.isEmpty() || body.size() > 4096) return failure(QStringLiteral("invalid_request"));
    QJsonParseError parse{};
    const auto document = QJsonDocument::fromJson(body, &parse);
    if (parse.error != QJsonParseError::NoError || !document.isObject()) return failure(QStringLiteral("invalid_request"));
    active_ = QUuid::createUuid().toString(QUuid::Id128);
    const auto id = active_;
    auto* runThread = new QThread(this);
    auto* worker = new ExportDownloadWorker(id, std::move(body), url, runtime_, keepAlive_, testLoopback_);
    threads_.append(runThread);
    worker->moveToThread(runThread);
    connect(this, &ExportDownloader::cancelWorker, worker, &ExportDownloadWorker::cancel, Qt::QueuedConnection);
    connect(runThread, &QThread::started, worker, &ExportDownloadWorker::begin);
    connect(worker, &ExportDownloadWorker::done, runThread, &QThread::quit, Qt::DirectConnection);
    connect(runThread, &QThread::finished, worker, &QObject::deleteLater);
    connect(worker, &ExportDownloadWorker::done, this, [this](const QString& request, bool success, const QString& error) {
        if (active_ != request) return;
        active_.clear();
        const QPointer<ExportDownloader> alive(this); emit stateChanged();
        if (alive) emit completed(request, success, error);
    });
    connect(runThread, &QThread::finished, this, [this, runThread] {
        threads_.removeAll(runThread); runThread->deleteLater();
    });
    runThread->start(); emit stateChanged();
    return {{QStringLiteral("accepted"), true}, {QStringLiteral("requestId"), id}};
}
bool ExportDownloader::cancel(const QString& requestId) {
    if (QThread::currentThread() != thread() || requestId.isEmpty() || active_ != requestId) return false;
    // Receiver lifetime is managed by Qt's connection machinery. Never use a
    // cross-thread QPointer check followed by a raw QObject invocation.
    emit cancelWorker(requestId); return true;
}
}
#include "export_downloader.moc"
