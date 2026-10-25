#pragma once
#include "protocol.h"
#include <functional>
#include <memory>

namespace valmax::media {
enum class CacheCode { Ok, InvalidInput, StaleScope, ResultExpired, Capacity,
                       IdExhausted, SequenceExhausted, Unavailable };
template<class T> struct CacheResult {
    std::optional<T> value;
    CacheCode error = CacheCode::Ok;
    explicit operator bool() const noexcept { return value.has_value(); }
};
struct CacheScope { quint64 token = 0; };
struct CacheLimits { int entries = 48; qsizetype bytes = 2*1024*1024; int scopes = 32; };
struct CacheSnapshot { int entries = 0; int scopes = 0; qsizetype chargedBytes = 0; };

// Bounded private metadata/URL cache. No media bytes, query, disk, networking,
// timers or UI. Account/page identifiers are trusted native inputs, never JSON.
// A new scope for an existing account/page revokes the old scope and its IDs.
// Tokens never recycle, including clear(); wrap closes admission permanently.
// Methods are mutex serialized. Input monotonic seconds must be finite,
// nonnegative, <=2^40 and nondecreasing. Clock sampling belongs to the caller.
// Exactly at creation+1200s an entry is expired (stricter than legacy '<').
// A returned Candidate copy cannot be recalled: service MUST recheck scope
// before publishing/attaching every asynchronous result. clear() drops cache
// references, not a promise of physical memory erasure of implicitly-shared
// strings or copies held by callers. Public rows expose only public metadata.
// Scope tokens belong to THIS cache instance. Destructor requires that all
// callers have stopped. Result IDs are collision-checked against live entries,
// not an eternal never-reuse ledger; future image providers must also bind their
// own ticket nonce/generation and recheck scope, never trust an ID alone.
class ResultCache final {
public:
    static constexpr double TtlSeconds = 1200.;
    static constexpr int IdAttempts = 8;
    // Optional deterministic entropy source is TEST/host-only. Must return
    // exactly 16 bytes, may throw, must be thread-safe and outlive all concurrent
    // calls (including callbacks which are executing during scope revocation). It is
    // always invoked outside mutex; default uses QRandomGenerator::system().
    explicit ResultCache(CacheLimits limits = {}, std::function<QByteArray()> entropy = {});
    ~ResultCache();
    ResultCache(const ResultCache&) = delete;
    ResultCache& operator=(const ResultCache&) = delete;
    CacheResult<CacheScope> open_scope(qint64 accountId, quint64 pageId);
    bool close_scope(CacheScope scope);
    bool current(CacheScope scope) const;
    void clear();
    // At most six rows, committed atomically. Empty successful batches are
    // allowed. FIFO eviction of older results, never partial new publication.
    CacheResult<QVector<QJsonObject>> publish_batch(CacheScope scope,
        const QVector<Candidate>& candidates, double monotonicSeconds);
    CacheResult<Candidate> resolve(CacheScope scope, const QString& opaqueId,
                                    double monotonicSeconds);
    CacheSnapshot snapshot() const;
private:
    struct Impl;
    std::unique_ptr<Impl> d_;
};
} // namespace valmax::media
