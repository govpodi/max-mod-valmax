#include "transport.h"
#include <QElapsedTimer>
#include <QCoreApplication>
#include <QHostAddress>
#include <QHttpHeaders>
#include <QNetworkAccessManager>
#include <QNetworkCookieJar>
#include <QNetworkCookie>
#include <QNetworkProxy>
#include <QNetworkProxyFactory>
#include <QPointer>
#include <QStringDecoder>
#include <QThread>
#include <QTimer>
#include <QUrl>
#include <algorithm>
#include <array>
#include <map>
#include <set>
#include <vector>
#include <utility>

namespace valmax::media {
namespace {
Error unavailable() { return {Code::ProviderUnavailable, 503, {}}; }
Error invalid() { return {Code::InvalidInput, 400, {}}; }
Error response_error() { return {Code::InvalidResponse, 502, {}}; }
Error url_error() { return {Code::InvalidRemoteUrl, 502, {}}; }
template<class T> Result<T> reject(Error e) { return {{}, e}; }
qsizetype byte_limit(RequestClass kind) {
    switch (kind) {
    case RequestClass::Api: return MaxApiBytes;
    case RequestClass::Preview: return MaxPreviewBytes;
    case RequestClass::Original: return MaxFileBytes;
    }
    return 0;
}
UrlKind url_kind(RequestClass kind) { return kind == RequestClass::Api ? UrlKind::Api : UrlKind::Media; }
bool valid_expected(const PublicMetadata& e) {
    return e.bytes >= 1 && e.bytes <= MaxFileBytes && e.width >= 1 && e.width <= 16000
        && e.height >= 1 && e.height <= 16000 && qint64(e.width) * e.height <= 80'000'000
        && !media_suffix(e.mime).isEmpty() && e.title.size() <= 1024 && e.author.size() <= 1024
        && e.license.size() <= 1024 && e.source.size() <= 4096;
}
int hex(unsigned char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
std::optional<QString> strict_utf8(const QByteArray& raw) {
    QStringDecoder decoder(QStringDecoder::Utf8, QStringConverter::Flag::Stateless | QStringConverter::Flag::ConvertInitialBom);
    QString text = decoder.decode(raw);
    if (decoder.hasError()) return {};
    return text;
}
// Validate the Location octets BEFORE QUrl can remove dot segments, repair bad
// percent escapes or turn invalid UTF-8 into replacement characters. Decoding
// percent escapes once matches the protocol policy; encoded spaces and literal
// percent characters in legitimate filenames remain supported.
Result<QString> redirect_target(const QString& current, const QByteArray& location, UrlKind kind) {
    if (location.isEmpty() || location.size() > 4096) return reject<QString>(url_error());
    const auto rawText = strict_utf8(location);
    if (!rawText || rawText->startsWith(QChar(0xfeff))) return reject<QString>(url_error());
    for (unsigned char c : location) if (c <= 32 || c == 127 || c == '\\' || c == '#') return reject<QString>(url_error());
    QByteArray decoded, decodedPath;
    bool query = false;
    for (qsizetype i = 0; i < location.size(); ++i) {
        unsigned char c = static_cast<unsigned char>(location[i]);
        if (c == '?') query = true;
        if (c == '%') {
            if (i + 2 >= location.size() || hex(static_cast<unsigned char>(location[i + 1])) < 0
                || hex(static_cast<unsigned char>(location[i + 2])) < 0) return reject<QString>(url_error());
            c = static_cast<unsigned char>((hex(static_cast<unsigned char>(location[i + 1])) << 4) | hex(static_cast<unsigned char>(location[i + 2])));
            i += 2;
        }
        if (c < 32 || c == 127 || c == '\\') return reject<QString>(url_error());
        decoded += char(c);
        if (!query) decodedPath += char(c);
    }
    if (!strict_utf8(decoded)) return reject<QString>(url_error());
    for (const auto& component : decodedPath.split('/')) if (component == "." || component == "..") return reject<QString>(url_error());
    const QUrl relative(*rawText, QUrl::StrictMode);
    if (!relative.isValid()) return reject<QString>(url_error());
    if (!relative.isRelative()) return validate_redirect(current, *rawText, kind);
    // Protocol-relative targets retain their raw authority for the protocol's
    // ASCII-host check; QUrl IDNA normalization must not expand the allowlist.
    if (rawText->startsWith(QStringLiteral("//")))
        return validate_redirect(current, QStringLiteral("https:") + *rawText, kind);
    const QString resolved = QUrl(current, QUrl::StrictMode).resolved(relative).toString(QUrl::FullyEncoded);
    return validate_redirect(current, resolved, kind);
}
bool redirect_status(int status) { return status == 301 || status == 302 || status == 303 || status == 307 || status == 308; }
class EmptyCookieJar final : public QNetworkCookieJar {
public:
    explicit EmptyCookieJar(QObject* parent) : QNetworkCookieJar(parent) {}
    QList<QNetworkCookie> cookiesForUrl(const QUrl&) const override { return {}; }
    bool setCookiesFromUrl(const QList<QNetworkCookie>&, const QUrl&) override { return false; }
};
struct ReplyLease {
    QPointer<ProviderScheduler> scheduler;
    quint64 ticket = 0;
    bool ownsSlot = true;
    void release() {
        if (!ownsSlot) return;
        ownsSlot = false;
        if (scheduler) scheduler->complete(ticket);
    }
};
QNetworkRequest request_for(const QString& url, RequestClass kind) {
    QNetworkRequest request{QUrl(url, QUrl::StrictMode)};
    request.setRawHeader("User-Agent", QByteArray(UserAgent));
    request.setRawHeader("Accept", kind == RequestClass::Api ? QByteArray("application/json") : QByteArray("image/*,video/webm,video/mp4"));
    request.setRawHeader("Accept-Encoding", "identity");
    request.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::ManualRedirectPolicy);
    request.setAttribute(QNetworkRequest::CookieLoadControlAttribute, QNetworkRequest::Manual);
    request.setAttribute(QNetworkRequest::CookieSaveControlAttribute, QNetworkRequest::Manual);
    request.setAttribute(QNetworkRequest::AuthenticationReuseAttribute, QNetworkRequest::Manual);
    request.setAttribute(QNetworkRequest::UseCredentialsAttribute, false);
    request.setAttribute(QNetworkRequest::CacheLoadControlAttribute, QNetworkRequest::AlwaysNetwork);
    request.setAttribute(QNetworkRequest::CacheSaveControlAttribute, false);
    request.setAttribute(QNetworkRequest::Http2AllowedAttribute, false);
    request.setAttribute(QNetworkRequest::Http2DirectAttribute, false);
    request.setAttribute(QNetworkRequest::Http2CleartextAllowedAttribute, false);
    request.setAttribute(QNetworkRequest::HttpPipeliningAllowedAttribute, false);
    request.setAttribute(QNetworkRequest::AutoDeleteReplyOnFinishAttribute, false);
    request.setMaximumRedirectsAllowed(0);
    request.setTransferTimeout(MediaTransport::TotalMilliseconds);
    return request;
}
} // namespace

Result<QNetworkProxy> select_media_proxy(const QList<QNetworkProxy>& configured) {
    if (configured.isEmpty() || configured.size() > 32) return reject<QNetworkProxy>(unavailable());
    const auto& first = configured.front();
    if (first.type() == QNetworkProxy::NoProxy) return {QNetworkProxy(QNetworkProxy::NoProxy), {}};
    if (first.type() != QNetworkProxy::HttpProxy && first.type() != QNetworkProxy::Socks5Proxy)
        return reject<QNetworkProxy>(unavailable());
    const QString host = first.hostName();
    if (host.isEmpty() || host.size() > 253 || first.port() == 0 || host != QString::fromUtf8(host.toUtf8()))
        return reject<QNetworkProxy>(unavailable());
    for (const auto c : host) if (c.unicode() <= 32 || c.unicode() == 127 || QStringLiteral("/@?#%\\").contains(c))
        return reject<QNetworkProxy>(unavailable());
    QHostAddress address;
    if (!address.setAddress(host)) {
        const QByteArray ace = QUrl::toAce(host);
        if (ace.isEmpty() || ace.size() > 253) return reject<QNetworkProxy>(unavailable());
        for (const auto& label : ace.split('.')) {
            if (label.isEmpty() || label.size() > 63 || label.startsWith('-') || label.endsWith('-')) return reject<QNetworkProxy>(unavailable());
            for (const unsigned char c : label) if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-'))
                return reject<QNetworkProxy>(unavailable());
        }
    }
    // Construct anew: user/password, custom Proxy-Authorization/other headers
    // and capability overrides from the inherited object are not carried over.
    return {QNetworkProxy(first.type(), host, first.port()), {}};
}

struct MediaTransport::Impl {
    struct Job {
        quint64 ticket = 0;
        FetchSpec spec;
        std::function<void(quint64, FetchResult)> callback;
        QPointer<QNetworkReply> reply;
        QPointer<QNetworkAccessManager> manager;
        QPointer<QTimer> timer;
        std::shared_ptr<ReplyLease> lease;
        QByteArray body;
        std::array<QByteArray,5> headerValues;
        QString current, next, mime;
        Error error;
        qint64 deadline = 0, declaredLength = -1;
        int redirects = 0;
        int status = 0;
        bool started = false, stopped = false, cancelling = false, headersChecked = false, redirecting = false;
        bool creatingReply = false, orphanDeliveryQueued = false;
    };
    QPointer<ProviderScheduler> scheduler;
    ReplyFactory factory;
    TransportClock clock;
    QElapsedTimer elapsed;
    qint64 lastClock = 0;
    bool stopping = false, destroying = false;
    std::map<quint64, std::shared_ptr<Job>> jobs;
    bool now(qint64& value) {
        try { value = clock.monotonicMilliseconds ? clock.monotonicMilliseconds() : elapsed.elapsed(); }
        catch (...) { return false; }
        if (value < lastClock || value < 0 || value > 1'000'000'000'000'000LL) return false;
        lastClock = value;
        return true;
    }
    std::shared_ptr<Job> job(quint64 ticket) const {
        const auto it = jobs.find(ticket);
        return it == jobs.end() ? nullptr : it->second;
    }
};

MediaTransport::MediaTransport(ProviderScheduler* scheduler, QObject* parent, ReplyFactory factory, TransportClock clock)
    : QObject(parent), d_(std::make_unique<Impl>()) {
    d_->elapsed.start();
    d_->factory = std::move(factory);
    d_->clock = std::move(clock);
    if (scheduler && scheduler->thread() == thread()) d_->scheduler = scheduler;
    if (d_->scheduler) connect(scheduler, &QObject::destroyed, this, [this] {
        d_->scheduler = nullptr;
        shutdown();
    });
}
MediaTransport::~MediaTransport() {
    d_->stopping = true;
    d_->destroying = true;
    if (d_->scheduler) QObject::disconnect(d_->scheduler, nullptr, this, nullptr);
    for (auto* child : children()) {
        QObject::disconnect(child, nullptr, this, nullptr);
        if (auto* timer = qobject_cast<QTimer*>(child)) timer->stop();
    }
    for (auto& [id, job] : d_->jobs) {
        if (d_->scheduler) d_->scheduler->cancel(id);
        job->callback = {};
        if (job->reply) {
            // The independent ReplyLease destruction callback survives this
            // object and completes only after the owned reply actually stops.
            QObject::disconnect(job->reply, nullptr, this, nullptr);
            if (job->manager) QObject::disconnect(job->manager, nullptr, this, nullptr);
            const QPointer<QNetworkReply> reply = job->reply;
            reply->abort();
            if (reply && reply->isFinished() && job->lease) job->lease->release();
        } else if (job->started && job->lease) job->lease->release();
    }
    // QObject::destroyed observers may pump the event loop after Impl has gone
    // but before QObject's own posted-event cleanup. Drop raw-this continuations
    // while Impl is still alive; external scheduler callbacks use QPointer and
    // destroying above to suppress reentrant delivery during this destructor.
    QCoreApplication::removePostedEvents(this, QEvent::MetaCall);
}
bool MediaTransport::on_owner_thread() const noexcept { return QThread::currentThread() == thread(); }
Result<quint64> MediaTransport::submit(MediaScope scope, FetchSpec spec, std::function<void(quint64, FetchResult)> finished) {
    if (!on_owner_thread()) return reject<quint64>(invalid());
    if (d_->stopping || !d_->scheduler) return reject<quint64>(unavailable());
    if (!d_->factory && (!QCoreApplication::instance() || QCoreApplication::instance()->thread() == thread()))
        return reject<quint64>(unavailable());
    if (!finished || byte_limit(spec.kind) == 0 || spec.url.size() > 4096
        || (spec.kind == RequestClass::Original ? (!spec.expected || !valid_expected(*spec.expected)) : spec.expected.has_value()))
        return reject<quint64>(invalid());
    const auto url = validate_url(spec.url, url_kind(spec.kind));
    if (!url) return reject<quint64>(url.error);
    const QPointer<MediaTransport> self(this);
    const auto accepted = d_->scheduler->submit(scope, spec.kind, {
        [self](quint64 id) { if (self && !self->d_->destroying) self->start(id); },
        [self](quint64 id) { if (self && !self->d_->destroying) self->abort(id); },
        [self](quint64 id, SchedulerCompletion r) { if (self && !self->d_->destroying) self->finish(id, r); }});
    if (!accepted) return accepted;
    auto job = std::make_shared<Impl::Job>();
    job->ticket = *accepted.value;
    job->current = *url.value;
    job->spec = std::move(spec);
    job->callback = std::move(finished);
    d_->jobs.emplace(job->ticket, std::move(job));
    return accepted;
}
bool MediaTransport::cancel(quint64 ticket) {
    if (!on_owner_thread()) return false;
    return d_->jobs.contains(ticket) && d_->scheduler && d_->scheduler->cancel(ticket);
}
bool MediaTransport::shutdown() {
    if (!on_owner_thread()) return false;
    d_->stopping = true;
    std::vector<quint64> ids;
    for (const auto& [id, job] : d_->jobs) { (void)job; ids.push_back(id); }
    for (const auto id : ids) {
        if (d_->scheduler) d_->scheduler->cancel(id);
        else abort(id);
    }
    return true;
}
bool MediaTransport::poll() {
    if (!on_owner_thread()) return false;
    QTimer::singleShot(0, this, [this] { deadlines(); });
    return true;
}
void MediaTransport::start(quint64 ticket) {
    const auto job = d_->job(ticket);
    if (!job || job->started) return;
    job->started = true;
    qint64 now = 0;
    if (!d_->now(now)) { fail(ticket, unavailable()); return; }
    job->deadline = now + TotalMilliseconds;
    if (d_->clock.automaticDeadlineTimer) {
        job->timer = new QTimer(this);
        job->timer->setSingleShot(true);
        job->timer->setTimerType(Qt::PreciseTimer);
        connect(job->timer, &QTimer::timeout, this, [this, ticket] { fail(ticket, unavailable()); });
        job->timer->start(TotalMilliseconds);
    }
    create_reply(ticket);
}
void MediaTransport::create_reply(quint64 ticket) {
    const auto job = d_->job(ticket);
    if (!job || job->stopped || job->cancelling || job->error.code != Code::None) return;
    qint64 now = 0;
    if (!d_->now(now) || now >= job->deadline) { fail(ticket, unavailable()); return; }
    const auto safe = validate_url(job->current, url_kind(job->spec.kind));
    if (!safe) { fail(ticket, safe.error); return; }
    const auto request = request_for(job->current, job->spec.kind);
    QNetworkReply* reply = nullptr;
    if (d_->factory) {
        job->creatingReply = true;
        reply = d_->factory(request, this);
    }
    else {
        if (!QCoreApplication::instance() || QCoreApplication::instance()->thread() == thread()) { fail(ticket, unavailable()); return; }
        const QNetworkProxyQuery query(request.url());
        auto configured = QNetworkProxyFactory::proxyForQuery(query);
        if (!configured.isEmpty() && configured.front().type() == QNetworkProxy::DefaultProxy)
            configured = QNetworkProxyFactory::systemProxyForQuery(query);
        const auto route = select_media_proxy(configured);
        // PAC/OS lookup is synchronous on this dedicated worker. Never issue a
        // GET after it consumed the total budget, nor choose a direct fallback.
        if (!route || !d_->now(now) || now >= job->deadline) { fail(ticket, unavailable()); return; }
        auto manager = new QNetworkAccessManager(this);
        job->manager = manager;
        manager->setProxy(*route.value);
        manager->setCookieJar(new EmptyCookieJar(manager));
        manager->setCache(nullptr);
        manager->setRedirectPolicy(QNetworkRequest::ManualRedirectPolicy);
        manager->setAutoDeleteReplies(false);
        connect(manager, &QNetworkAccessManager::authenticationRequired, this,
            [this, ticket](QNetworkReply*, QAuthenticator*) { fail(ticket, unavailable()); });
        connect(manager, &QNetworkAccessManager::proxyAuthenticationRequired, this,
            [this, ticket](const QNetworkProxy&, QAuthenticator*) { fail(ticket, unavailable()); });
        job->creatingReply = true;
        reply = manager->get(request);
        if (reply) reply->setParent(this);
    }
    job->creatingReply = false;
    if (!reply) { fail(ticket, unavailable()); return; }
    if (reply->thread() != thread() || reply->parent() != this) {
        // Trusted factory contract violation; never touch a foreign-thread reply.
        fail(ticket, unavailable()); return;
    }
    job->reply = reply;
    job->lease = std::make_shared<ReplyLease>(ReplyLease{d_->scheduler, ticket, true});
    const auto lease = job->lease;
    reply->setReadBufferSize(ReadBufferBytes);
    connect(reply, &QObject::destroyed, this, [this, ticket] { reply_destroyed(ticket); });
    if (d_->scheduler) connect(reply, &QObject::destroyed, d_->scheduler, [lease] { lease->release(); });
    connect(reply, &QNetworkReply::metaDataChanged, this, [this, ticket] { metadata(ticket); });
    connect(reply, &QIODevice::readyRead, this, [this, ticket] { read(ticket); });
    connect(reply, &QNetworkReply::finished, this, [this, ticket] { reply_finished(ticket); });
    connect(reply, &QNetworkReply::sslErrors, this, [this, ticket](const QList<QSslError>&) { fail(ticket, unavailable()); });
    if (job->error.code != Code::None) { fail(ticket, job->error); return; }
    // A synthetic or cached-immediate reply may have finished before connections.
    // Defer handling to avoid callback-before-ticket-registration publication.
    QTimer::singleShot(0, this, [this, ticket] {
        const auto j = d_->job(ticket);
        if (!j || !j->reply || j->stopped) return;
        if (j->reply->isFinished()) reply_finished(ticket);
        else if (j->reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).isValid()) metadata(ticket);
    });
}
void MediaTransport::metadata(quint64 ticket) {
    const auto job = d_->job(ticket);
    if (!job || !job->reply || job->stopped || job->cancelling || job->error.code != Code::None) return;
    const QPointer<QNetworkReply> reply = job->reply;
    if (reply->url() != QUrl(job->current, QUrl::StrictMode)) { fail(ticket, url_error()); return; }
    qsizetype size = 0;
    std::set<QByteArray> unique;
    // rawHeaderPairs() merges repeated names. In Qt 6.11.2, Set-Cookie is
    // joined with LF and other headers with comma-space. Inspect the individual
    // parsed entries instead: legitimate cookies must not look like injected
    // line breaks, and repeated critical headers must not disappear in a merge.
    const auto headers = reply->headers();
    if (headers.size() > 128) { fail(ticket, response_error()); return; }
    for (qsizetype index = 0; index < headers.size(); ++index) {
        const auto rawName = headers.nameAt(index);
        const QByteArrayView nameView(rawName.data(), rawName.size());
        const auto value = headers.valueAt(index);
        if (nameView.size() > 256 || value.size() > 8192
            || nameView.contains('\r') || nameView.contains('\n') || nameView.contains('\0')
            || value.contains('\r') || value.contains('\n') || value.contains('\0')) {
            fail(ticket, response_error()); return;
        }
        size += nameView.size() + value.size();
        if (size > 64 * 1024) { fail(ticket, response_error()); return; }
        const auto name = nameView.toByteArray().toLower();
        if ((name == "content-length" || name == "content-type" || name == "content-encoding" || name == "location" || name == "retry-after")
            && !unique.insert(name).second) { fail(ticket, response_error()); return; }
    }
    const auto statusValue = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute);
    if (statusValue.metaType().id() != QMetaType::Int) { fail(ticket, response_error()); return; }
    const int status = statusValue.toInt();
    if (reply->attribute(QNetworkRequest::SourceIsFromCacheAttribute).toBool()
        || reply->attribute(QNetworkRequest::OriginalContentLengthAttribute).isValid()) { fail(ticket, response_error()); return; }
    const std::array<QByteArray,5> values{reply->rawHeader("Content-Type"),reply->rawHeader("Content-Encoding"),
        reply->rawHeader("Content-Length"),reply->rawHeader("Location"),reply->rawHeader("Retry-After")};
    if (job->headersChecked) {
        if (job->status != status || job->headerValues != values) fail(ticket, response_error());
        return;
    }
    job->headersChecked = true;
    job->status = status;
    job->headerValues = values;
    if (status == 429 || status == 503) {
        if (d_->scheduler) {
            const auto busy = d_->scheduler->defer(QString::fromLatin1(reply->rawHeader("Retry-After")));
            const auto admission = busy ? d_->scheduler->provider_admission() : busy;
            fail(ticket, admission ? Error{Code::ProviderBusy,503,{}} : admission.error);
        } else fail(ticket, unavailable());
        return;
    }
    if (redirect_status(status)) {
        if (job->redirects >= MaxRedirects) { fail(ticket, url_error()); return; }
        const auto next = redirect_target(job->current, reply->rawHeader("Location"), url_kind(job->spec.kind));
        if (!next) { fail(ticket, next.error); return; }
        job->redirecting = true;
        job->next = *next.value;
        // No redirect body is needed. Preserve the slot until abort finishes.
        if (reply->isFinished()) return;
        reply->abort();
        return;
    }
    const auto validated = validate_response_headers(status, QString::fromLatin1(reply->rawHeader("Content-Type")),
        QString::fromLatin1(reply->rawHeader("Content-Encoding")), QString::fromLatin1(reply->rawHeader("Content-Length")), byte_limit(job->spec.kind));
    if (!validated) { fail(ticket, validated.error); return; }
    job->mime = *validated.value;
    if (job->spec.kind == RequestClass::Api && job->mime != QStringLiteral("application/json")) { fail(ticket, response_error()); return; }
    if (!reply->rawHeader("Content-Length").isEmpty()) job->declaredLength = reply->rawHeader("Content-Length").toLongLong();
}
void MediaTransport::read(quint64 ticket) {
    const QPointer<MediaTransport> alive(this);
    qint64 now = 0;
    const auto before = d_->job(ticket);
    if (before && !before->stopped && (!d_->now(now) || now >= before->deadline)) { fail(ticket, unavailable()); return; }
    metadata(ticket);
    if (!alive) return;
    const auto job = d_->job(ticket);
    if (!job || !job->reply || job->stopped || job->redirecting || job->cancelling || job->error.code != Code::None) return;
    const QPointer<QNetworkReply> reply = job->reply;
    std::array<char, 64 * 1024> buffer{};
    while (reply && reply->bytesAvailable() > 0) {
        const qint64 remaining = byte_limit(job->spec.kind) - job->body.size();
        const qint64 requested = std::min<qint64>(qint64(buffer.size()), remaining + 1);
        const qint64 count = reply->read(buffer.data(), requested);
        if (!alive) return;
        if (count < 0) { fail(ticket, unavailable()); return; }
        if (count == 0) break;
        if (count > remaining) { fail(ticket, {Code::FileTooLarge,413,{}}); return; }
        job->body.append(buffer.data(), count);
    }
}
void MediaTransport::reply_finished(quint64 ticket) {
    const auto job = d_->job(ticket);
    if (!job || !job->reply || job->stopped) return;
    const QPointer<MediaTransport> alive(this);
    const QPointer<QNetworkReply> reply = job->reply;
    if (!reply->isFinished()) return;
    metadata(ticket);
    if (!alive || !reply || job->stopped) return;
    if (job->redirecting && !job->cancelling && job->error.code == Code::None) {
        // Old finished reply must not release the new reply's chain slot when
        // its deferred deletion runs. Every chain has exactly one live lease.
        job->lease->ownsSlot = false;
        job->lease.reset();
        QObject::disconnect(reply, nullptr, this, nullptr);
        reply->deleteLater();
        if (job->manager) {
            QObject::disconnect(job->manager, nullptr, this, nullptr);
            job->manager->deleteLater();
            job->manager = nullptr;
        }
        job->reply = nullptr;
        job->current = std::move(job->next);
        job->redirecting = false;
        job->headersChecked = false;
        ++job->redirects;
        create_reply(ticket);
        return;
    }
    if (job->error.code == Code::None && !job->cancelling) {
        if (reply->error() != QNetworkReply::NoError) job->error = unavailable();
        else {
            read(ticket);
            if (!alive || !reply || job->stopped) return;
            if (job->error.code == Code::None) {
                if (job->declaredLength >= 0 && job->body.size() != job->declaredLength) job->error = response_error();
                else if (job->spec.kind == RequestClass::Preview) job->error = validate_preview(job->body, job->mime).error;
                else if (job->spec.kind == RequestClass::Original) job->error = validate_download(job->body, job->mime, *job->spec.expected).error;
            }
        }
    }
    job->stopped = true;
    if (job->timer) job->timer->stop();
    if (job->cancelling || job->error.code != Code::None) job->body.clear();
    retire(ticket);
    reply->deleteLater();
    if (job->manager) job->manager->deleteLater();
}
void MediaTransport::reply_destroyed(quint64 ticket) {
    const auto job = d_->job(ticket);
    if (!job || job->stopped) return;
    job->reply = nullptr;
    job->stopped = true;
    job->body.clear();
    if (job->error.code == Code::None) job->error = unavailable();
    if (job->timer) job->timer->stop();
    if (job->manager) job->manager->deleteLater();
    retire(ticket);
}
void MediaTransport::fail(quint64 ticket, Error error) {
    const auto job = d_->job(ticket);
    if (!job || job->stopped) return;
    if (job->error.code == Code::None) job->error = error;
    job->body.clear();
    job->redirecting = false;
    if (job->timer) job->timer->stop();
    if (!job->reply) {
        if (job->creatingReply) return; // get() may emit authentication synchronously.
        job->stopped = true;
        if (job->manager) job->manager->deleteLater();
        retire(ticket);
        return;
    }
    const QPointer<QNetworkReply> reply = job->reply;
    if (reply->isFinished()) {
        job->stopped = true;
        retire(ticket);
        reply->deleteLater();
        if (job->manager) job->manager->deleteLater();
        return;
    }
    reply->abort(); // May synchronously emit finished; do not touch this after.
}
void MediaTransport::abort(quint64 ticket) {
    const auto job = d_->job(ticket);
    if (!job) return;
    job->cancelling = true;
    job->body.clear();
    job->redirecting = false;
    if (job->timer) job->timer->stop();
    if (job->stopped) { retire(ticket); return; }
    if (!job->reply) {
        job->stopped = true;
        retire(ticket);
        return;
    }
    const QPointer<QNetworkReply> reply = job->reply;
    if (reply->isFinished()) { reply_finished(ticket); return; }
    reply->abort();
}
void MediaTransport::retire(quint64 ticket) {
    const auto job = d_->job(ticket);
    if (!job || !job->stopped) return;
    if (d_->scheduler) {
        if (job->lease) job->lease->release();
        else if (job->started) d_->scheduler->complete(ticket);
    } else if (!job->orphanDeliveryQueued) {
        job->orphanDeliveryQueued = true;
        // Scheduler destruction suppresses its callbacks. The transport still
        // delivers one finite failure, but only once the owned reply has stopped.
        QTimer::singleShot(0, this, [this, ticket] {
            finish(ticket, {SchedulerOutcome::Rejected, unavailable()});
        });
    }
}
void MediaTransport::finish(quint64 ticket, SchedulerCompletion result) {
    const auto job = d_->job(ticket);
    if (!job) return;
    if (job->timer) job->timer->deleteLater();
    FetchResult output{result.outcome, result.error, {}, {}};
    if (result.outcome == SchedulerOutcome::Completed) {
        output.error = job->error;
        if (output.error.code == Code::None && job->stopped && !job->cancelling) {
            output.bytes = std::move(job->body);
            output.mime = std::move(job->mime);
        }
    }
    auto callback = std::move(job->callback);
    d_->jobs.erase(ticket);
    if (callback) callback(ticket, std::move(output)); // May delete/reenter this.
}
void MediaTransport::deadlines() {
    qint64 now = 0;
    const bool valid = d_->now(now);
    std::vector<quint64> expired;
    for (const auto& [id, job] : d_->jobs)
        if (job->started && !job->stopped && (!valid || now >= job->deadline)) expired.push_back(id);
    const QPointer<MediaTransport> alive(this);
    for (const auto id : expired) { fail(id, unavailable()); if (!alive) return; }
}
} // namespace valmax::media
