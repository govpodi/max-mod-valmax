#pragma once
#include "retention_api.h"
#include <array>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <span>

namespace valmax::retention {
using Bitmap = std::array<uint8_t,VALMAX_RETENTION_BITMAP_BYTES>;
bool digest_ids(std::span<const int64_t> ids, char (&output)[64]);
bool policy_revision(bool enabled, char (&output)[64]);
class Queue {
public:
    uint32_t set_policy(const ValmaxRetentionPolicy &);
    uint32_t received(int64_t owner,int64_t chat,std::span<const int64_t> ids);
    // Future hook producer only. Copies all IDs before waking the Rust worker.
    // No queued/failed/timed-out request may alter native arguments or jump.
    // Once OK returns, this bitmap belongs to the caller's two-pass scope; a
    // later policy update must not undo its paired second-gate decision.
    uint32_t prepare(int64_t owner,int64_t chat,std::span<const int64_t> ids,Bitmap &);
    uint32_t take(ValmaxRetentionRequest &,int64_t *ids,uint32_t capacity,uint32_t wait_ms);
    uint32_t ack(const ValmaxRetentionAck &);
    void status(ValmaxRetentionStatus &);
    uint32_t shutdown();
#ifdef VALMAX_RETENTION_TESTING
    void exhaust_for_test(bool nonce,bool epoch);
#endif
private:
    enum class State { Free, Queued, Taken, Completed, Abandoned };
    struct Slot {
        ValmaxRetentionRequest request{};
        std::array<int64_t,VALMAX_RETENTION_MAX_IDS> ids{};
        Bitmap retained{};
        State state=State::Free;
        bool waiter=false;
        uint32_t result=VALMAX_RETENTION_NOT_READY;
    };
    static constexpr uint64_t kCounterLimit=1000000;
    static void increment(uint64_t &value) { if(value<kCounterLimit) ++value; }
    uint32_t enqueue(int64_t,int64_t,std::span<const int64_t>,uint32_t,size_t &);
    void cancel_locked(uint32_t);
    bool active_locked() const;
    bool exhausted_locked() const;
    void release_locked(Slot &);
    std::mutex mutex_;
    std::condition_variable changed_;
    std::array<Slot,VALMAX_RETENTION_QUEUE_SLOTS> slots_{};
    ValmaxRetentionPolicy policy_{};
    std::array<uint64_t,6> counters_{};
    uint64_t nonce_=0,epoch_=1;
    uint32_t error_=VALMAX_RETENTION_NOT_READY;
    bool storage_failed_=false,stopped_=false;
};
}
