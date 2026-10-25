#include "scheduler.h"
#include <QDateTime>
#include <QElapsedTimer>
#include <QPointer>
#include <QThread>
#include <QTimer>
#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <utility>

namespace valmax::media {
namespace {
Error invalid() { return {Code::InvalidInput, 400, {}}; }
Error unavailable() { return {Code::ProviderUnavailable, 503, {}}; }
template<class T> Result<T> reject(Error error) { return {{}, error}; }
bool valid_class(RequestClass kind) {
    return kind == RequestClass::Api || kind == RequestClass::Preview || kind == RequestClass::Original;
}
}
struct ProviderScheduler::Impl {
    enum class Phase { Waiting, Active, Done };
    struct Job {
        quint64 ticket;
        MediaScope scope;
        RequestClass kind;
        double deadline;
        SchedulerCallbacks callbacks;
        Phase phase = Phase::Waiting;
        bool cancellation = false;
        bool abortDelivered = false;
        SchedulerCompletion completion;
    };
    SchedulerClock clock;
    QElapsedTimer elapsed;
    QTimer* timer;
    std::function<void()> drained;
    std::map<quint64, quint64> lanes;
    std::map<quint64, Job> jobs;
    RetryDeadline retry;
    SchedulerState state = SchedulerState::Accepting;
    quint64 nextLane = 1, nextTicket = 1;
    double lastClock = 0;
    bool clockFailed = false;
    bool drainedDelivered = false;

    int count(Phase phase) const {
        return static_cast<int>(std::count_if(jobs.begin(), jobs.end(), [phase](const auto& x) { return x.second.phase == phase; }));
    }
    bool valid_scope(MediaScope scope) const {
        const auto it = lanes.find(scope.lane);
        return scope.lane != 0 && scope.generation != 0 && it != lanes.end() && it->second == scope.generation;
    }
    bool now(double& value) {
        if (clockFailed) return false;
        try {
            value = clock.monotonicSeconds ? clock.monotonicSeconds() : static_cast<double>(elapsed.nsecsElapsed()) / 1e9;
        } catch (...) { clockFailed = true; return false; }
        if (!std::isfinite(value) || value < lastClock || value < 0 || value > 1e15) {
            clockFailed = true;
            return false;
        }
        lastClock = value;
        return true;
    }
    void terminal(Job& job, SchedulerOutcome why, Error error = {}) {
        job.phase = Phase::Done;
        job.completion = {why, error};
    }
    void cancel_job(Job& job, SchedulerOutcome why) {
        if (job.phase == Phase::Waiting) terminal(job, why);
        else if (job.phase == Phase::Done && (job.completion.outcome == SchedulerOutcome::Completed
                                            || job.completion.outcome == SchedulerOutcome::Rejected)) {
            // Completion is not publication: a generation can become stale
            // between transport completion and queued UI delivery.
            job.completion = {why, {}};
        }
        else if (job.phase == Phase::Active && !job.cancellation) {
            job.cancellation = true;
            job.completion = {why, {}};
        }
    }
    void stop() {
        if (state == SchedulerState::Drained) return;
        state = SchedulerState::Stopping;
        for (auto& [id, job] : jobs) {
            (void)id;
            cancel_job(job, SchedulerOutcome::Cancelled);
        }
    }
};

ProviderScheduler::ProviderScheduler(QObject* parent, SchedulerClock clock, std::function<void()> drained)
    : QObject(parent), d_(std::make_unique<Impl>()) {
    d_->clock = std::move(clock);
    d_->drained = std::move(drained);
    d_->elapsed.start();
    d_->timer = new QTimer(this);
    d_->timer->setSingleShot(true);
    d_->timer->setTimerType(Qt::PreciseTimer);
    connect(d_->timer, &QTimer::timeout, this, [this] { pump(); });
}
ProviderScheduler::~ProviderScheduler() {
    // Destruction silently detaches callbacks. Shutdown+complete is required
    // for a real drain; external transport owns abort/destruction of its replies.
    d_->timer->stop();
}
bool ProviderScheduler::on_owner_thread() const noexcept { return QThread::currentThread() == thread(); }
void ProviderScheduler::schedule() { d_->timer->start(0); }

Result<MediaScope> ProviderScheduler::open_lane() {
    if (!on_owner_thread()) return reject<MediaScope>(invalid());
    if (d_->state != SchedulerState::Accepting || d_->clockFailed) return reject<MediaScope>(unavailable());
    if (d_->lanes.size() >= static_cast<size_t>(MaxLanes) || d_->nextLane == 0) return reject<MediaScope>(unavailable());
    const quint64 lane = d_->nextLane++;
    d_->lanes.emplace(lane, 1);
    return {MediaScope{lane, 1}, {}};
}
Result<MediaScope> ProviderScheduler::advance(MediaScope scope) {
    if (!on_owner_thread()) return reject<MediaScope>(invalid());
    if (!d_->valid_scope(scope) || scope.generation == std::numeric_limits<quint64>::max()) return reject<MediaScope>(invalid());
    if (d_->state != SchedulerState::Accepting) return reject<MediaScope>(unavailable());
    ++d_->lanes[scope.lane];
    for (auto& [id, job] : d_->jobs) {
        (void)id;
        if (job.scope.lane == scope.lane) d_->cancel_job(job, SchedulerOutcome::StaleGeneration);
    }
    schedule();
    return {MediaScope{scope.lane, scope.generation + 1}, {}};
}
bool ProviderScheduler::close_lane(MediaScope scope) {
    if (!on_owner_thread()) return false;
    if (!d_->valid_scope(scope)) return false;
    d_->lanes.erase(scope.lane);
    for (auto& [id, job] : d_->jobs) {
        (void)id;
        if (job.scope.lane == scope.lane) d_->cancel_job(job, SchedulerOutcome::Cancelled);
    }
    schedule();
    return true;
}
Result<quint64> ProviderScheduler::submit(MediaScope scope, RequestClass kind, SchedulerCallbacks callbacks) {
    if (!on_owner_thread()) return reject<quint64>(invalid());
    if (!d_->valid_scope(scope) || !valid_class(kind) || !callbacks.start || !callbacks.abort || !callbacks.finished)
        return reject<quint64>(invalid());
    if (d_->state != SchedulerState::Accepting || d_->clockFailed) return reject<quint64>(unavailable());
    double now = 0;
    if (!d_->now(now)) { d_->stop(); schedule(); return reject<quint64>(unavailable()); }
    const auto admission = d_->retry.admission(now);
    if (!admission) return reject<quint64>(admission.error);
    if (d_->jobs.size() >= static_cast<size_t>(MaxWaiting + MaxActive) || d_->count(Impl::Phase::Waiting) >= MaxWaiting || d_->nextTicket == 0)
        return reject<quint64>(unavailable());
    const quint64 ticket = d_->nextTicket++;
    d_->jobs.emplace(ticket, Impl::Job{ticket, scope, kind, now + QueueSeconds, std::move(callbacks)});
    schedule();
    return {ticket, {}};
}
bool ProviderScheduler::cancel(quint64 ticket) {
    if (!on_owner_thread()) return false;
    const auto it = d_->jobs.find(ticket);
    if (it == d_->jobs.end() || (it->second.phase == Impl::Phase::Done
        && it->second.completion.outcome != SchedulerOutcome::Completed
        && it->second.completion.outcome != SchedulerOutcome::Rejected)) return false;
    d_->cancel_job(it->second, SchedulerOutcome::Cancelled);
    schedule();
    return true;
}
bool ProviderScheduler::complete(quint64 ticket) {
    if (!on_owner_thread()) return false;
    const auto it = d_->jobs.find(ticket);
    if (it == d_->jobs.end() || it->second.phase != Impl::Phase::Active) return false;
    auto& job = it->second;
    d_->terminal(job, job.cancellation ? job.completion.outcome : SchedulerOutcome::Completed);
    schedule();
    return true;
}
Result<Unit> ProviderScheduler::defer(const QString& retryAfter) {
    if (!on_owner_thread()) return reject<Unit>(invalid());
    if (d_->state == SchedulerState::Drained || d_->clockFailed) return reject<Unit>(unavailable());
    double now = 0, wall = 0;
    if (!d_->now(now)) { d_->stop(); schedule(); return reject<Unit>(unavailable()); }
    try {
        wall = d_->clock.wallSeconds ? d_->clock.wallSeconds() : static_cast<double>(QDateTime::currentMSecsSinceEpoch()) / 1000.;
    } catch (...) { return reject<Unit>(invalid()); }
    auto result = d_->retry.defer(retryAfter, wall, now);
    if (result) schedule();
    return result;
}
Result<Unit> ProviderScheduler::provider_admission() {
    if (!on_owner_thread()) return reject<Unit>(invalid());
    if (d_->state == SchedulerState::Drained || d_->clockFailed) return reject<Unit>(unavailable());
    double now = 0;
    if (!d_->now(now)) { d_->stop(); schedule(); return reject<Unit>(unavailable()); }
    return d_->retry.admission(now);
}
bool ProviderScheduler::drain() {
    if (!on_owner_thread()) return false;
    if (d_->state == SchedulerState::Accepting) d_->state = SchedulerState::Draining;
    schedule();
    return true;
}
bool ProviderScheduler::shutdown() {
    if (!on_owner_thread()) return false;
    d_->stop();
    schedule();
    return true;
}
bool ProviderScheduler::poll() {
    if (!on_owner_thread()) return false;
    schedule();
    return true;
}
Result<SchedulerSnapshot> ProviderScheduler::snapshot() const {
    if (!on_owner_thread()) return reject<SchedulerSnapshot>(invalid());
    return {SchedulerSnapshot{d_->state, d_->count(Impl::Phase::Waiting), d_->count(Impl::Phase::Active),
                              static_cast<int>(d_->jobs.size()), static_cast<int>(d_->lanes.size()), d_->retry.deadline()}, {}};
}

void ProviderScheduler::pump() {
    if (!on_owner_thread()) return;
    double now = 0;
    if (!d_->now(now)) d_->stop();
    const auto admission = d_->clockFailed ? reject<Unit>(unavailable()) : d_->retry.admission(now);
    for (auto& [id, job] : d_->jobs) {
        (void)id;
        if (job.phase != Impl::Phase::Waiting) continue;
        if (!admission) d_->terminal(job, SchedulerOutcome::Rejected, admission.error);
        else if (now >= job.deadline) d_->terminal(job, SchedulerOutcome::Rejected, unavailable());
    }
    // Deliver one event per turn. All state transitions precede callbacks, and
    // no iterator/reference to a job survives a callback/reentrant mutation.
    std::function<void()> delivery;
    quint64 started = 0;
    const auto done = std::find_if(d_->jobs.begin(), d_->jobs.end(), [](const auto& x) { return x.second.phase == Impl::Phase::Done; });
    if (done != d_->jobs.end()) {
        const auto id = done->first;
        const auto result = done->second.completion;
        auto callback = std::move(done->second.callbacks.finished);
        d_->jobs.erase(done);
        delivery = [callback = std::move(callback), id, result] { callback(id, result); };
    } else {
        const auto abort = std::find_if(d_->jobs.begin(), d_->jobs.end(), [](const auto& x) {
            return x.second.phase == Impl::Phase::Active && x.second.cancellation && !x.second.abortDelivered;
        });
        if (abort != d_->jobs.end()) {
            abort->second.abortDelivered = true;
            auto callback = std::move(abort->second.callbacks.abort);
            const auto id = abort->first;
            delivery = [callback = std::move(callback), id] { callback(id); };
        } else if (d_->state != SchedulerState::Stopping && d_->state != SchedulerState::Drained && d_->count(Impl::Phase::Active) < MaxActive) {
            auto next = d_->jobs.end();
            for (auto it = d_->jobs.begin(); it != d_->jobs.end(); ++it) {
                if (it->second.phase != Impl::Phase::Waiting) continue;
                if (next == d_->jobs.end()) next = it;
                if (it->second.kind != RequestClass::Preview) { next = it; break; }
            }
            if (next != d_->jobs.end()) {
                next->second.phase = Impl::Phase::Active;
                started = next->first;
                auto callback = std::move(next->second.callbacks.start);
                delivery = [callback = std::move(callback), started] { callback(started); };
            }
        }
    }
    if (!delivery && d_->state != SchedulerState::Accepting && d_->jobs.empty() && !d_->drainedDelivered) {
        d_->drainedDelivered = true;
        d_->state = SchedulerState::Drained;
        delivery = std::move(d_->drained);
    }
    if (delivery) {
        QPointer<ProviderScheduler> alive(this);
        bool failed = false;
        try { delivery(); } catch (...) { failed = true; }
        if (!alive) return;
        if (failed) {
            // A callback may have started external work before throwing. Never
            // release its slot speculatively; shutdown requests abort instead.
            d_->stop();
        }
        schedule();
        return;
    }
    if (d_->clock.automaticDeadlineTimer && !d_->clockFailed) {
        double nearest = std::numeric_limits<double>::infinity();
        for (const auto& [id, job] : d_->jobs) {
            (void)id;
            if (job.phase == Impl::Phase::Waiting) nearest = std::min(nearest, job.deadline);
        }
        if (std::isfinite(nearest)) {
            // Only queue deadlines arm timers. Provider cooldown may exceed a
            // day or INT_MAX milliseconds and never enters a QTimer interval.
            const double milliseconds = std::clamp(std::ceil((nearest - now) * 1000.), 1., 5000.);
            d_->timer->start(static_cast<int>(milliseconds));
        }
    }
}
} // namespace valmax::media
