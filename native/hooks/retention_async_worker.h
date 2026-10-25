#pragma once

#include "retention_row_codec.h"

#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <span>
#include <thread>
#include <tuple>
#include <vector>

namespace valmax::retention::pilot {

struct CapturedText final {
    int64_t id = 0;
    int64_t order_key = 0;
    int64_t sender = 0;
    int64_t received_at_ms = 0;
    rows::SecureText text;
};
struct AsyncBackend final {
    void *context = nullptr;
    bool (*capture_batch)(void *, rows::Route, std::span<const CapturedText>) noexcept = nullptr;
    bool (*mark_batch)(void *, rows::Route, std::span<const int64_t>,
                       std::span<uint8_t>) noexcept = nullptr;
};
struct PassivePreflight final {
    uint64_t generation = 0;
    uint32_t listeners = 0;
    uint32_t gates = 0;
    bool enabled = false;
    bool exact_images = false;
    bool owner_join = false;
    bool store_ready = false;
};
enum class LocalState : uint8_t { None, Pending, Durable, Missing, Failed };
enum class AsyncError : uint8_t {
    None, Preflight, InvalidRoute, QueueFull, BufferFull, Storage,
    Stalled, Shutdown
};
struct AsyncCounters final {
    uint64_t captured = 0;
    uint64_t marked = 0;
    uint64_t missing = 0;
    uint64_t missed = 0;
    uint64_t failures = 0;
    uint32_t queued = 0;
    uint32_t in_flight = 0;
    uint32_t pending = 0;
    bool available = false;
    bool ready = false;
    AsyncError error = AsyncError::Preflight;
};

// Passive producer: MAX's original deletion always proceeds immediately.
// Only durable DB rows survive restart; Pending is bounded process memory.
class AsyncWorker final {
public:
    explicit AsyncWorker(AsyncBackend backend, size_t queue_jobs = 64,
                         size_t max_buffered_bytes = 8 * 1024 * 1024,
                         size_t max_states = 4096, uint32_t max_job_ms = 5000);
    AsyncWorker(const AsyncWorker &) = delete;
    AsyncWorker &operator=(const AsyncWorker &) = delete;
    ~AsyncWorker();

    bool activate(PassivePreflight proof) noexcept;
    bool submit_capture(rows::Route route, std::vector<rows::Snapshot> &&messages,
                        int64_t received_at_ms) noexcept;
    bool submit_removed(rows::Route route, std::span<const int64_t> ids) noexcept;
    LocalState state(rows::Route route, int64_t id) const noexcept;
    AsyncCounters status() noexcept;
    void revoke(AsyncError reason) noexcept;
    void shutdown() noexcept;

private:
    struct Job;
    using Key = std::tuple<int64_t,int64_t,int64_t>;
    static void increment(uint64_t &value) noexcept;
    void fail_locked(AsyncError error) noexcept;
    void check_stall_locked() noexcept;
    void run() noexcept;
    AsyncBackend backend_;
    size_t queue_jobs_;
    size_t max_buffered_bytes_;
    size_t max_states_;
    uint32_t max_job_ms_;
    mutable std::mutex mutex_;
    std::condition_variable changed_;
    std::deque<std::shared_ptr<Job>> queue_;
    std::map<Key,LocalState> states_;
    std::thread thread_;
    AsyncCounters counters_{};
    size_t buffered_bytes_ = 0;
    uint64_t in_flight_started_tick_ = 0;
    bool stopped_ = false;
    bool sticky_failure_ = false;
    uint64_t generation_ = 0;
};

} // namespace valmax::retention::pilot
