#pragma once

#include "scheduler.h"
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QNetworkProxy>
#include <functional>
#include <memory>

namespace valmax::media {
struct FetchSpec {
    RequestClass kind = RequestClass::Api;
    QString url; // Private native data. Never expose URLs/search text in diagnostics.
    std::optional<PublicMetadata> expected; // Required only for Original.
};
struct FetchResult {
    SchedulerOutcome outcome = SchedulerOutcome::Completed;
    Error error;
    QByteArray bytes; // Private native result; only present after Completed success.
    QString mime;
};
struct TransportClock {
    // Trusted pure, non-reentrant test clock; finite nondecreasing milliseconds.
    std::function<qint64()> monotonicMilliseconds;
    bool automaticDeadlineTimer = true;
};
using ReplyFactory = std::function<QNetworkReply*(const QNetworkRequest&, QObject*)>;
// Pure route policy, useful to test without querying this computer's settings.
// Uses only the first configured route and drops credentials/custom headers.
Result<QNetworkProxy> select_media_proxy(const QList<QNetworkProxy>& configured);

// Owner-thread-only, no files/QML. Share ONE ProviderScheduler across every
// API/preview/original request. Does not serialize selected original downloads:
// the service still needs download_busy. At most 5 manually validated redirects
// and 20 seconds TOTAL after admission; fixed class-specific body limits.
// Production owns one no-cookie/no-cache/no-credentials manager per request.
// It honors the first configured app/system proxy and never falls back to direct.
// Production requires a dedicated non-GUI owner thread. OS proxy lookup may
// block that thread; elapsed deadline is rechecked BEFORE GET, so hard 20-second
// interruption of OS/PAC lookup is NOT promised. No global proxy is modified.
// ReplyFactory is a trusted test seam: create a fresh same-thread reply parented
// to the supplied owner, do not mutate/reenter/delete the transport while creating.
// Aborted slots remain occupied until finished/destroyed. Results remain private
// until the scheduler accepts publication for the current scope. Destruction
// suppresses callbacks and stops owned replies; it does not destroy the scheduler.
class MediaTransport final : public QObject {
public:
    static constexpr int TotalMilliseconds = 20'000;
    static constexpr int MaxRedirects = 5;
    static constexpr qint64 ReadBufferBytes = 64 * 1024;
    explicit MediaTransport(ProviderScheduler* scheduler, QObject* parent = nullptr,
                            ReplyFactory factory = {}, TransportClock clock = {});
    ~MediaTransport() override;
    Result<quint64> submit(MediaScope scope, FetchSpec spec,
                          std::function<void(quint64, FetchResult)> finished);
    bool cancel(quint64 ticket);
    bool shutdown(); // Stops only this transport's jobs, not other scheduler users.
    bool poll(); // Advance injected deadlines asynchronously; no real network in tests.

private:
    struct Impl;
    std::unique_ptr<Impl> d_;
    bool on_owner_thread() const noexcept;
    void start(quint64 ticket);
    void abort(quint64 ticket);
    void finish(quint64 ticket, SchedulerCompletion result);
    void create_reply(quint64 ticket);
    void metadata(quint64 ticket);
    void read(quint64 ticket);
    void reply_finished(quint64 ticket);
    void reply_destroyed(quint64 ticket);
    void fail(quint64 ticket, Error error);
    void retire(quint64 ticket);
    void deadlines();
};
} // namespace valmax::media
