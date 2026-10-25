#pragma once

#include "protocol.h"
#include <QObject>
#include <functional>
#include <memory>

namespace valmax::media {
enum class RequestClass { Api, Preview, Original };
enum class SchedulerOutcome { Completed, Cancelled, StaleGeneration, Rejected };
struct SchedulerCompletion {
    SchedulerOutcome outcome = SchedulerOutcome::Completed;
    Error error;
};
struct MediaScope {
    quint64 lane = 0;
    quint64 generation = 0;
};
struct SchedulerCallbacks {
    std::function<void(quint64)> start;
    std::function<void(quint64)> abort;
    std::function<void(quint64, SchedulerCompletion)> finished;
};
struct SchedulerClock {
    // Optional test clocks. Pure, finite, nonnegative and non-reentrant;
    // monotonicSeconds must never move backwards. Default uses QElapsedTimer.
    std::function<double()> monotonicSeconds;
    std::function<double()> wallSeconds;
    bool automaticDeadlineTimer = true;
};
enum class SchedulerState { Accepting, Draining, Stopping, Drained };
struct SchedulerSnapshot {
    SchedulerState state = SchedulerState::Accepting;
    int queued = 0;
    int active = 0;
    int outstanding = 0;
    int lanes = 0;
    double retryDeadline = 0;
};

// One instance must be shared by API requests, previews and originals.
// Owner-thread-only; wrong-thread calls reject before reading mutable state.
// Callbacks are asynchronous on that thread, may reenter or destroy this object.
// A started ticket owns its slot until complete(), even after abort is requested.
// The transport must call complete after its reply has stopped, using QPointer
// if this scheduler may have been destroyed. This class owns no reply/network IO.
// Keep fetched results private until finished(..., Completed): complete() itself
// is not publication permission. A queued completion can still become stale.
// Each accepted ticket gets one finished callback unless this object is destroyed;
// rejected submit returns an Error and does not schedule any callback.
// Selected-original serialization/download_busy belongs to the higher service.
class ProviderScheduler final : public QObject {
public:
    static constexpr int MaxActive = 2;
    static constexpr int MaxWaiting = 64;
    static constexpr int MaxLanes = 32;
    static constexpr double QueueSeconds = 5.;
    explicit ProviderScheduler(QObject* parent = nullptr, SchedulerClock clock = {},
                               std::function<void()> drained = {});
    ~ProviderScheduler() override;
    Result<MediaScope> open_lane();
    Result<MediaScope> advance(MediaScope current);
    bool close_lane(MediaScope current);
    Result<quint64> submit(MediaScope scope, RequestClass kind, SchedulerCallbacks callbacks);
    bool cancel(quint64 ticket);
    bool complete(quint64 ticket);
    Result<Unit> defer(const QString& retryAfter);
    Result<Unit> provider_admission(); // Same shared clock/cooldown, including full remaining hint.
    bool drain();
    bool shutdown();
    // Schedules an asynchronous pass; useful after advancing an injected clock.
    bool poll();
    Result<SchedulerSnapshot> snapshot() const;

private:
    struct Impl;
    std::unique_ptr<Impl> d_;
    bool on_owner_thread() const noexcept;
    void schedule();
    void pump();
};
} // namespace valmax::media
