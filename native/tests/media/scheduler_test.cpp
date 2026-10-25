#include "scheduler.h"
#include <QCoreApplication>
#include <QEventLoop>
#include <QPointer>
#include <QThread>
#include <QTimer>
#include <QElapsedTimer>
#include <atomic>
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <source_location>
#include <thread>
#include <utility>
#include <vector>
using namespace valmax::media;
namespace {
int checks = 0;
void check(bool value, std::source_location where = std::source_location::current()) {
    if (!value) { std::cerr << "failed_check=" << checks + 1 << " line=" << where.line() << '\n'; std::exit(1); }
    ++checks;
}
void flush() { for (int i = 0; i < 256; ++i) QCoreApplication::processEvents(QEventLoop::AllEvents); }
struct Fixture {
    double monotonic = 10, wall = 1700000000;
    int drains = 0;
    std::vector<quint64> starts, aborts;
    std::vector<std::pair<quint64, SchedulerCompletion>> done;
    std::unique_ptr<ProviderScheduler> scheduler;
    MediaScope scope;
    Fixture() {
        scheduler = std::make_unique<ProviderScheduler>(nullptr,
            SchedulerClock{[this] { return monotonic; }, [this] { return wall; }, false}, [this] { ++drains; });
        const auto lane = scheduler->open_lane(); check(bool(lane)); scope = *lane.value;
    }
    SchedulerCallbacks callbacks() {
        return {[this](quint64 id) {
                    starts.push_back(id);
                    const auto state = scheduler->snapshot(); check(bool(state) && state.value->active <= 2);
                },
                [this](quint64 id) { aborts.push_back(id); },
                [this](quint64 id, SchedulerCompletion result) { done.emplace_back(id, result); }};
    }
    quint64 add(RequestClass kind = RequestClass::Api) {
        const auto request = scheduler->submit(scope, kind, callbacks()); check(bool(request)); return *request.value;
    }
    SchedulerSnapshot state() { const auto s = scheduler->snapshot(); check(bool(s)); return *s.value; }
    SchedulerCompletion result(quint64 id) {
        const auto it = std::find_if(done.begin(), done.end(), [id](const auto& v) { return v.first == id; });
        check(it != done.end()); return it->second;
    }
    void advance_time(double seconds) { monotonic += seconds; check(scheduler->poll()); flush(); }
};

void priority_and_async() {
    Fixture f;
    const auto p1 = f.add(RequestClass::Preview), p2 = f.add(RequestClass::Preview);
    const auto a = f.add(RequestClass::Api), o = f.add(RequestClass::Original);
    check(f.starts.empty()); flush();
    check(f.starts == std::vector<quint64>{a, o});
    check(f.state().active == 2 && f.state().queued == 2);
    check(f.scheduler->complete(a)); check(!f.scheduler->complete(a)); flush();
    check(f.starts == std::vector<quint64>{a, o, p1});
    check(f.scheduler->complete(o)); flush();
    check(f.starts.back() == p2);
}
void queue_limits_deadline() {
    Fixture f;
    for (int i = 0; i < 64; ++i) f.add();
    auto excess = f.scheduler->submit(f.scope, RequestClass::Api, f.callbacks());
    check(!excess && excess.error.code == Code::ProviderUnavailable);
    flush(); check(f.state().active == 2 && f.state().queued == 62);
    f.add(); f.add(); check(f.state().outstanding == 66);
    excess = f.scheduler->submit(f.scope, RequestClass::Preview, f.callbacks()); check(!excess);
    f.advance_time(4.999); check(f.done.empty());
    f.monotonic = 15.; check(f.scheduler->poll()); flush(); check(f.done.size() == 64);
    check(f.state().active == 2 && f.state().queued == 0);
    for (const auto& item : f.done) check(item.second.outcome == SchedulerOutcome::Rejected && item.second.error.code == Code::ProviderUnavailable);
}
void cancellation_and_slot_lifetime() {
    Fixture f;
    const auto a = f.add(), b = f.add(), waiting = f.add(); flush();
    check(f.scheduler->cancel(a)); check(f.scheduler->cancel(a)); flush();
    check(f.aborts == std::vector<quint64>{a}); check(f.state().active == 2); check(f.starts.size() == 2);
    check(f.scheduler->complete(a)); flush(); check(f.starts.back() == waiting);
    check(f.result(a).outcome == SchedulerOutcome::Cancelled);
    check(f.scheduler->complete(b)); check(f.scheduler->cancel(b)); flush();
    check(f.result(b).outcome == SchedulerOutcome::Cancelled);
    check(!f.scheduler->cancel(b) && !f.scheduler->complete(b));
    check(f.scheduler->shutdown()); flush(); check(f.state().active == 1 && f.drains == 0);
    check(f.scheduler->complete(waiting)); flush(); check(f.drains == 1 && f.state().state == SchedulerState::Drained);
    check(f.scheduler->shutdown()); flush(); check(f.drains == 1);
}
void stale_scope_delivery() {
    for (const bool close : {false, true}) {
        Fixture f;
        const auto a = f.add(); flush(); check(f.scheduler->complete(a));
        if (close) check(f.scheduler->close_lane(f.scope));
        else { const auto advanced = f.scheduler->advance(f.scope); check(bool(advanced)); }
        flush(); check(f.result(a).outcome == (close ? SchedulerOutcome::Cancelled : SchedulerOutcome::StaleGeneration));
        check(f.aborts.empty());
        check(!f.scheduler->submit(f.scope, RequestClass::Api, f.callbacks()));
    }
    Fixture f;
    const auto other = f.scheduler->open_lane(); check(bool(other));
    const auto old = f.add();
    const auto separate = f.scheduler->submit(*other.value, RequestClass::Preview, f.callbacks()); check(bool(separate));
    flush();
    const auto changed = f.scheduler->advance(f.scope); check(bool(changed));
    f.scope = *changed.value; const auto fresh = f.add(); flush();
    check(f.aborts == std::vector<quint64>{old}); check(f.starts.size() == 2);
    check(f.scheduler->complete(old)); flush(); check(f.starts.back() == fresh);
    check(f.result(old).outcome == SchedulerOutcome::StaleGeneration);
    check(f.scheduler->complete(*separate.value)); flush(); check(f.result(*separate.value).outcome == SchedulerOutcome::Completed);
}
void cooldown_full_deadline() {
    Fixture f;
    const auto a = f.add(), b = f.add(), pending = f.add(); flush();
    check(bool(f.scheduler->defer(QStringLiteral("999999999999")))); flush();
    check(f.state().retryDeadline == 1000000000009.);
    check(f.result(pending).error.code == Code::ProviderBusy && !f.result(pending).error.retryAfterSeconds);
    auto blocked = f.scheduler->submit(f.scope, RequestClass::Original, f.callbacks());
    check(!blocked && blocked.error.code == Code::ProviderBusy && !blocked.error.retryAfterSeconds);
    check(bool(f.scheduler->defer(QStringLiteral("5")))); check(f.state().retryDeadline == 1000000000009.);
    check(f.scheduler->complete(a)); check(f.scheduler->complete(b)); flush();
    Fixture g;
    check(bool(g.scheduler->defer(QStringLiteral("100000"))));
    g.advance_time(99999.); blocked = g.scheduler->submit(g.scope, RequestClass::Api, g.callbacks());
    check(!blocked && blocked.error.retryAfterSeconds == 1);
    g.advance_time(1.); g.add(); flush(); check(g.starts.size() == 1);
    Fixture malformed; check(bool(malformed.scheduler->defer(QStringLiteral("nonsense"))));
    check(malformed.state().retryDeadline == 15.);
}
void direct_provider_admission() {
    Fixture f;
    check(bool(f.scheduler->provider_admission()));
    check(bool(f.scheduler->defer(QStringLiteral("10"))));
    auto admission = f.scheduler->provider_admission();
    check(!admission && admission.error.retryAfterSeconds == 10);
    f.monotonic = 12.; admission = f.scheduler->provider_admission();
    check(!admission && admission.error.retryAfterSeconds == 8);
    check(bool(f.scheduler->defer(QStringLiteral("999999999999"))));
    admission = f.scheduler->provider_admission();
    check(!admission && admission.error.code == Code::ProviderBusy && !admission.error.retryAfterSeconds);
    std::atomic<bool> rejected = false;
    std::thread other([&] { rejected = !f.scheduler->provider_admission(); }); other.join(); check(rejected);
    f.monotonic = 1000000000011.; check(bool(f.scheduler->provider_admission()));
    f.monotonic = 1.; check(!f.scheduler->provider_admission());
}
void drain_and_reentrancy() {
    Fixture f;
    for (int i = 0; i < 20; ++i) {
        auto cb = f.callbacks(); cb.start = [&f](quint64 id) { f.starts.push_back(id); check(f.scheduler->complete(id)); };
        check(bool(f.scheduler->submit(f.scope, RequestClass::Api, std::move(cb))));
    }
    check(f.scheduler->drain()); check(!f.scheduler->submit(f.scope, RequestClass::Api, f.callbacks())); flush();
    check(f.starts.size() == 20 && f.done.size() == 20 && f.drains == 1);
    Fixture cancel;
    auto cb = cancel.callbacks();
    cb.start = [&cancel](quint64 id) { check(cancel.scheduler->cancel(id)); };
    cb.abort = [&cancel](quint64 id) { cancel.aborts.push_back(id); check(cancel.scheduler->complete(id)); };
    const auto ticket = cancel.scheduler->submit(cancel.scope, RequestClass::Api, std::move(cb)); check(bool(ticket)); flush();
    check(cancel.result(*ticket.value).outcome == SchedulerOutcome::Cancelled);
    Fixture stop;
    cb = stop.callbacks(); cb.start = [&stop](quint64) { check(stop.scheduler->shutdown()); };
    cb.abort = [&stop](quint64 id) { check(stop.scheduler->complete(id)); };
    check(bool(stop.scheduler->submit(stop.scope, RequestClass::Api, std::move(cb)))); flush();
    check(stop.drains == 1);
}
void deletion_from_callbacks() {
    for (int phase = 0; phase < 4; ++phase) {
        QPointer<ProviderScheduler> alive;
        auto* scheduler = new ProviderScheduler(nullptr, {}, [&alive, phase] { if (phase == 3) delete alive.data(); });
        alive = scheduler;
        const auto scope = *scheduler->open_lane().value;
        SchedulerCallbacks cb{
            [&alive, phase](quint64 id) {
                if (phase == 0) delete alive.data();
                else if (phase == 1) alive->cancel(id);
                else alive->complete(id);
            },
            [&alive, phase](quint64) { if (phase == 1) delete alive.data(); },
            [&alive, phase](quint64, SchedulerCompletion) {
                if (phase == 2) delete alive.data();
                else if (phase == 3) alive->drain();
            }};
        check(bool(scheduler->submit(scope, RequestClass::Api, std::move(cb)))); flush(); check(!alive);
    }
}
void lane_bounds_invalid_and_wrong_thread() {
    Fixture f;
    for (int i = 1; i < 32; ++i) check(bool(f.scheduler->open_lane()));
    check(!f.scheduler->open_lane());
    check(!f.scheduler->submit({}, RequestClass::Api, f.callbacks()));
    check(!f.scheduler->submit(f.scope, static_cast<RequestClass>(99), f.callbacks()));
    check(!f.scheduler->submit(f.scope, RequestClass::Api, {}));
    check(!f.scheduler->advance({f.scope.lane, 2}));
    bool rejected = false;
    auto callbacks = f.callbacks();
    std::thread other([&] {
        rejected = !f.scheduler->snapshot() && !f.scheduler->open_lane()
            && !f.scheduler->advance(f.scope) && !f.scheduler->close_lane(f.scope)
            && !f.scheduler->submit(f.scope, RequestClass::Api, callbacks)
            && !f.scheduler->cancel(1) && !f.scheduler->complete(1)
            && !f.scheduler->defer(QStringLiteral("5")) && !f.scheduler->drain()
            && !f.scheduler->shutdown() && !f.scheduler->poll();
    }); other.join(); check(rejected); check(f.state().lanes == 32 && f.state().state == SchedulerState::Accepting);
    f.monotonic = std::numeric_limits<double>::quiet_NaN();
    check(!f.scheduler->submit(f.scope, RequestClass::Api, f.callbacks())); flush(); check(f.drains == 1);
}
void callback_exception_retains_slot() {
    Fixture f;
    auto cb = f.callbacks(); cb.start = [](quint64) { throw std::runtime_error("synthetic"); };
    const auto ticket = f.scheduler->submit(f.scope, RequestClass::Api, std::move(cb)); check(bool(ticket)); flush();
    check(f.state().active == 1 && f.aborts.size() == 1 && f.drains == 0);
    check(f.scheduler->complete(*ticket.value)); flush(); check(f.drains == 1);
}
void completion_before_shutdown_is_cancelled() {
    Fixture f;
    const auto a = f.add(); flush(); check(f.scheduler->complete(a));
    check(f.scheduler->shutdown()); flush();
    check(f.result(a).outcome == SchedulerOutcome::Cancelled && f.drains == 1);
}
std::atomic<int> timerWarnings = 0;
void capture_timer_warning(QtMsgType type, const QMessageLogContext&, const QString& text) {
    if (type == QtWarningMsg && text.contains(QStringLiteral("timer"), Qt::CaseInsensitive)) ++timerWarnings;
}
void real_queue_timer_and_large_cooldown() {
    const auto previous = qInstallMessageHandler(capture_timer_warning);
    {
        ProviderScheduler scheduler;
        const auto scope = *scheduler.open_lane().value;
        check(bool(scheduler.defer(QStringLiteral("999999999999")))); flush();
        SchedulerCallbacks cb{[](quint64) {}, [](quint64) {}, [](quint64, SchedulerCompletion) {}};
        const auto blocked = scheduler.submit(scope, RequestClass::Api, cb);
        check(!blocked && blocked.error.code == Code::ProviderBusy && !blocked.error.retryAfterSeconds);
        check(timerWarnings.load() == 0);
    }
    {
        ProviderScheduler scheduler;
        const auto scope = *scheduler.open_lane().value;
        QEventLoop loop;
        QElapsedTimer elapsed; elapsed.start();
        int started = 0;
        bool expired = false, timedOut = false;
        SchedulerCallbacks cb{[&](quint64) { ++started; }, [](quint64) {},
            [&](quint64, SchedulerCompletion completion) {
                expired = completion.outcome == SchedulerOutcome::Rejected && completion.error.code == Code::ProviderUnavailable;
                loop.quit();
            }};
        check(bool(scheduler.submit(scope, RequestClass::Api, cb)));
        check(bool(scheduler.submit(scope, RequestClass::Original, cb)));
        check(bool(scheduler.submit(scope, RequestClass::Preview, cb)));
        QTimer watchdog; watchdog.setSingleShot(true);
        QObject::connect(&watchdog, &QTimer::timeout, &loop, [&] { timedOut = true; loop.quit(); });
        watchdog.start(10000); loop.exec(); watchdog.stop();
        check(expired && !timedOut && started == 2);
        check(elapsed.elapsed() >= 4900 && elapsed.elapsed() < 10000);
    }
    qInstallMessageHandler(previous);
    check(timerWarnings.load() == 0);
}
}
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    priority_and_async(); queue_limits_deadline(); cancellation_and_slot_lifetime(); stale_scope_delivery();
    cooldown_full_deadline(); drain_and_reentrancy(); deletion_from_callbacks(); lane_bounds_invalid_and_wrong_thread();
    direct_provider_admission();
    callback_exception_retains_slot();
    completion_before_shutdown_is_cancelled(); real_queue_timer_and_large_cooldown();
    std::cout << "{\"ok\":true,\"checks\":" << checks << ",\"syntheticOnly\":true,\"networkUsed\":false,\"filesUsed\":false}\n";
}
