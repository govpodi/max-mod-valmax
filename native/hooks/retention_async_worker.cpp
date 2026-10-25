#include "retention_async_worker.h"

#include <windows.h>
#include <algorithm>
#include <set>
#include <utility>

namespace valmax::retention::pilot {
namespace { thread_local const AsyncWorker *running_worker = nullptr; }

struct AsyncWorker::Job final {
    enum class Type { Capture, Removed } type = Type::Capture;
    rows::Route route{};
    std::vector<CapturedText> captured;
    std::vector<int64_t> ids;
    std::vector<uint8_t> bits;
    size_t bytes = 0;
    ~Job() {
        if (!ids.empty()) SecureZeroMemory(ids.data(),ids.size()*sizeof(int64_t));
    }
};

void AsyncWorker::increment(uint64_t &value) noexcept {
    if (value < 1000000) ++value;
}
AsyncWorker::AsyncWorker(AsyncBackend backend, size_t queue_jobs,
                         size_t max_buffered_bytes, size_t max_states, uint32_t max_job_ms)
    : backend_(backend), queue_jobs_(queue_jobs),
      max_buffered_bytes_(max_buffered_bytes), max_states_(max_states),
      max_job_ms_(max_job_ms) {
    if (!backend.capture_batch || !backend.mark_batch || queue_jobs == 0
        || queue_jobs > 64 || max_buffered_bytes < rows::kMaxTextBytes
        || max_buffered_bytes > 16*1024*1024 || max_states == 0 || max_states > 4096
        || max_job_ms == 0 || max_job_ms > 5000) {
        stopped_ = true; increment(counters_.failures); return;
    }
    try { thread_ = std::thread([this] { run(); }); }
    catch (...) { stopped_ = true; increment(counters_.failures); }
}
AsyncWorker::~AsyncWorker() { shutdown(); }

void AsyncWorker::fail_locked(AsyncError error) noexcept {
    if (counters_.available) increment(counters_.failures);
    counters_.available = false;
    counters_.ready = false;
    counters_.error = error;
    sticky_failure_ = true;
    for (auto &[key,state] : states_) {
        (void)key;
        if (state == LocalState::Pending) { state = LocalState::Failed; increment(counters_.missed); }
    }
    for (const auto &job : queue_) buffered_bytes_ -= job->bytes;
    queue_.clear();
    changed_.notify_all();
}
void AsyncWorker::check_stall_locked() noexcept {
    if (counters_.available && in_flight_started_tick_ != 0
        && GetTickCount64()-in_flight_started_tick_ > max_job_ms_) fail_locked(AsyncError::Stalled);
}
bool AsyncWorker::activate(PassivePreflight proof) noexcept {
    const bool complete = proof.generation != 0 && proof.exact_images
        && proof.owner_join && proof.store_ready && proof.listeners == 3 && proof.gates == 0;
    std::lock_guard lock(mutex_);
    if (stopped_ || sticky_failure_) return false;
    if (!complete || proof.generation < generation_) {
        if (counters_.available) fail_locked(AsyncError::Preflight);
        return false;
    }
    generation_ = proof.generation;
    counters_.available = true;
    counters_.ready = proof.enabled;
    counters_.error = AsyncError::None;
    return true;
}

bool AsyncWorker::submit_capture(rows::Route route, std::vector<rows::Snapshot> &&messages,
                                 int64_t received_at_ms) noexcept {
    if (running_worker == this || route.owner <= 0 || route.chat == 0
        || received_at_ms < 0 || messages.size() > rows::kMaxBatchRows) {
        std::lock_guard lock(mutex_); increment(counters_.missed); fail_locked(AsyncError::InvalidRoute); return false;
    }
    if (messages.empty()) return true;
    try {
        auto job = std::make_shared<Job>();
        job->route = route;
        std::set<int64_t> unique;
        for (auto &message : messages) {
            if (message.kind != rows::Kind::Text || message.id <= 0
                || message.order_key < 0
                || message.sender <= 0 || message.text.bytes().empty()
                || message.text.bytes().size() > rows::kMaxTextBytes) {
                std::lock_guard lock(mutex_); increment(counters_.missed);
                fail_locked(AsyncError::InvalidRoute); return false;
            }
            if (message.sender == route.owner) continue;
            if (!unique.insert(message.id).second) {
                std::lock_guard lock(mutex_); increment(counters_.missed);
                fail_locked(AsyncError::InvalidRoute); return false;
            }
            job->bytes += message.text.bytes().size();
            CapturedText captured;
            captured.id = message.id;
            captured.order_key = message.order_key;
            captured.sender = message.sender;
            captured.received_at_ms = received_at_ms;
            captured.text = std::move(message.text);
            job->captured.push_back(std::move(captured));
        }
        if (job->captured.empty()) return true;
        std::lock_guard lock(mutex_);
        check_stall_locked();
        if (stopped_ || !counters_.ready) { increment(counters_.missed); return false; }
        if (queue_.size() >= queue_jobs_) {
            increment(counters_.missed); fail_locked(AsyncError::QueueFull); return false;
        }
        if (job->bytes > max_buffered_bytes_-buffered_bytes_) {
            increment(counters_.missed); fail_locked(AsyncError::BufferFull); return false;
        }
        const size_t bytes = job->bytes;
        queue_.push_back(std::move(job));
        buffered_bytes_ += bytes;
        changed_.notify_one();
        return true;
    } catch (...) {
        std::lock_guard lock(mutex_); increment(counters_.missed); fail_locked(AsyncError::Storage); return false;
    }
}
bool AsyncWorker::submit_removed(rows::Route route, std::span<const int64_t> ids) noexcept {
    if (running_worker == this || route.owner <= 0 || route.chat == 0
        || ids.empty() || ids.size() > rows::kMaxBatchRows) {
        std::lock_guard lock(mutex_); increment(counters_.missed); fail_locked(AsyncError::InvalidRoute); return false;
    }
    try {
        std::set<int64_t> unique;
        for (int64_t id : ids) if (id <= 0 || !unique.insert(id).second) {
            std::lock_guard lock(mutex_); increment(counters_.missed);
            fail_locked(AsyncError::InvalidRoute); return false;
        }
        auto job = std::make_shared<Job>();
        job->type = Job::Type::Removed;
        job->route = route;
        job->ids.assign(ids.begin(),ids.end());
        job->bits.resize((ids.size()+7)/8,0);
        job->bytes = ids.size_bytes()+job->bits.size();
        std::lock_guard lock(mutex_);
        check_stall_locked();
        if (stopped_ || !counters_.ready) { increment(counters_.missed); return false; }
        if (queue_.size() >= queue_jobs_) {
            increment(counters_.missed); fail_locked(AsyncError::QueueFull); return false;
        }
        if (job->bytes > max_buffered_bytes_-buffered_bytes_) {
            increment(counters_.missed); fail_locked(AsyncError::BufferFull); return false;
        }
        size_t additions = 0;
        for (int64_t id : ids) if (!states_.contains({route.owner,route.chat,id})) ++additions;
        while (states_.size()+additions > max_states_) {
            auto it = std::find_if(states_.begin(),states_.end(),[](const auto &entry) {
                return entry.second != LocalState::Pending;
            });
            if (it == states_.end()) {
                increment(counters_.missed); fail_locked(AsyncError::QueueFull); return false;
            }
            states_.erase(it);
        }
        const size_t bytes = job->bytes;
        queue_.push_back(std::move(job));
        buffered_bytes_ += bytes;
        for (int64_t id : ids) states_[{route.owner,route.chat,id}] = LocalState::Pending;
        changed_.notify_one();
        return true;
    } catch (...) {
        std::lock_guard lock(mutex_); increment(counters_.missed); fail_locked(AsyncError::Storage); return false;
    }
}
void AsyncWorker::run() noexcept {
    running_worker = this;
    for (;;) {
        std::shared_ptr<Job> job;
        {
            std::unique_lock lock(mutex_);
            changed_.wait(lock,[&] { return stopped_ || !queue_.empty(); });
            if (stopped_) { running_worker = nullptr; return; }
            job = std::move(queue_.front());
            queue_.pop_front();
            in_flight_started_tick_ = GetTickCount64();
        }
        bool durable = false;
        if (job->type == Job::Type::Capture) {
            durable = backend_.capture_batch(backend_.context,job->route,job->captured);
        } else {
            durable = backend_.mark_batch(backend_.context,job->route,job->ids,job->bits);
            if (durable && job->ids.size()%8 != 0) {
                const size_t used = job->ids.size()%8;
                if ((job->bits.back() & static_cast<uint8_t>(~((1u<<used)-1u))) != 0) durable = false;
            }
        }
        {
            std::lock_guard lock(mutex_);
            in_flight_started_tick_ = 0;
            buffered_bytes_ -= job->bytes;
            if (!durable) fail_locked(AsyncError::Storage);
            else if (counters_.available && !stopped_ && job->type == Job::Type::Capture) {
                for (size_t i=0;i<job->captured.size();++i) increment(counters_.captured);
            } else if (counters_.available && !stopped_) {
                for (size_t i=0;i<job->ids.size();++i) {
                    const bool retained = (job->bits[i/8] & (1u<<(i%8))) != 0;
                    const auto found = states_.find({job->route.owner,job->route.chat,job->ids[i]});
                    if (found == states_.end()) { fail_locked(AsyncError::Storage); break; }
                    found->second = retained ? LocalState::Durable : LocalState::Missing;
                    if (retained) increment(counters_.marked); else increment(counters_.missing);
                }
            }
        }
    }
}
LocalState AsyncWorker::state(rows::Route route, int64_t id) const noexcept {
    if (route.owner <= 0 || route.chat == 0 || id <= 0) return LocalState::None;
    std::lock_guard lock(mutex_);
    const auto found = states_.find({route.owner,route.chat,id});
    return found == states_.end() ? LocalState::None : found->second;
}
AsyncCounters AsyncWorker::status() noexcept {
    std::lock_guard lock(mutex_);
    check_stall_locked();
    auto result = counters_;
    result.queued = static_cast<uint32_t>(queue_.size());
    result.in_flight = in_flight_started_tick_ != 0 ? 1u : 0u;
    result.pending = static_cast<uint32_t>(std::count_if(states_.begin(),states_.end(),
        [](const auto &entry) { return entry.second == LocalState::Pending; }));
    return result;
}
void AsyncWorker::revoke(AsyncError reason) noexcept {
    std::lock_guard lock(mutex_);
    if (!stopped_) fail_locked(reason == AsyncError::None ? AsyncError::Preflight : reason);
}
void AsyncWorker::shutdown() noexcept {
    {
        std::lock_guard lock(mutex_);
        if (!stopped_) {
            stopped_ = true;
            counters_.available = false;
            counters_.ready = false;
            if (counters_.error == AsyncError::None) counters_.error = AsyncError::Shutdown;
            for (auto &[key,state] : states_) {
                (void)key;
                if (state == LocalState::Pending) state = LocalState::Failed;
            }
            for (const auto &job : queue_) buffered_bytes_ -= job->bytes;
            queue_.clear();
            changed_.notify_all();
        }
    }
    if (thread_.joinable() && thread_.get_id() != std::this_thread::get_id()) thread_.join();
}

} // namespace valmax::retention::pilot
