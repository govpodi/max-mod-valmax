#include "retention_async_worker.h"

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <tuple>
#include <vector>

#define CHECK(condition) do { if (!(condition)) { std::fprintf(stderr,"CHECK_FAIL_LINE:%d\n",__LINE__); std::abort(); } } while (false)
using namespace valmax::retention;
namespace {
using Key = std::tuple<int64_t,int64_t,int64_t>;
constexpr pilot::PassivePreflight proof{1,3,0,true,true,true,true};
struct MemoryBackend final {
    std::mutex mutex;
    std::condition_variable wake;
    std::map<Key,std::string> content;
    std::map<Key,bool> deleted;
    std::vector<char> order;
    bool block_capture = false;
    bool entered_capture = false;
    bool release_capture = false;
    bool fail_capture = false;
    bool fail_mark = false;
    static bool capture(void *self, rows::Route route,
                        std::span<const pilot::CapturedText> rows) noexcept {
        auto &store = *static_cast<MemoryBackend *>(self);
        std::unique_lock lock(store.mutex);
        store.order.push_back('C');
        store.entered_capture = true;
        store.wake.notify_all();
        if (store.block_capture)
            store.wake.wait(lock,[&] { return store.release_capture; });
        if (store.fail_capture) return false;
        for (const auto &row : rows) {
            store.content[{route.owner,route.chat,row.id}] = std::string(
                reinterpret_cast<const char *>(row.text.bytes().data()),row.text.bytes().size());
        }
        return true;
    }
    static bool mark(void *self, rows::Route route,
                     std::span<const int64_t> ids, std::span<uint8_t> bits) noexcept {
        auto &store = *static_cast<MemoryBackend *>(self);
        std::lock_guard lock(store.mutex);
        store.order.push_back('D');
        if (store.fail_mark || bits.size() != (ids.size()+7)/8) return false;
        for (size_t i=0;i<ids.size();++i) {
            const Key key{route.owner,route.chat,ids[i]};
            if (store.content.contains(key)) {
                store.deleted[key] = true;
                bits[i/8] |= static_cast<uint8_t>(1u<<(i%8));
            }
        }
        return true;
    }
    pilot::AsyncBackend callbacks() { return {this,&capture,&mark}; }
    void await_capture() {
        std::unique_lock lock(mutex);
        CHECK(wake.wait_for(lock,std::chrono::seconds(2),[&] { return entered_capture; }));
    }
    void unblock() {
        std::lock_guard lock(mutex); release_capture = true; wake.notify_all();
    }
};
rows::Snapshot message(int64_t id, int64_t sender, const char *text) {
    rows::Snapshot item;
    item.kind = rows::Kind::Text;
    item.id = id;
    item.order_key = 1700000000000 + id;
    item.sender = sender;
    const auto len = std::char_traits<char>::length(text);
    CHECK(item.text.assign(reinterpret_cast<const uint8_t *>(text),len));
    return item;
}
std::vector<rows::Snapshot> batch(int64_t id, int64_t sender, const char *text) {
    std::vector<rows::Snapshot> values;
    values.push_back(message(id,sender,text));
    return values;
}
void await_state(pilot::AsyncWorker &worker, rows::Route route, int64_t id,
                 pilot::LocalState expected) {
    const auto deadline = std::chrono::steady_clock::now()+std::chrono::seconds(2);
    while (worker.state(route,id) != expected && std::chrono::steady_clock::now()<deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    CHECK(worker.state(route,id) == expected);
}
void activate(pilot::AsyncWorker &worker) {
    CHECK(!worker.status().ready);
    CHECK(worker.activate(proof));
    CHECK(worker.status().ready);
}
void passive_capture_edit_delete_is_ordered() {
    MemoryBackend store;
    store.block_capture = true;
    pilot::AsyncWorker worker(store.callbacks());
    activate(worker);
    CHECK(worker.submit_capture({11,-22},batch(31,12,"synthetic initial"),100));
    store.await_capture();
    CHECK(worker.submit_capture({11,-22},batch(31,12,"synthetic edited"),101));
    const std::array<int64_t,1> ids{31};
    const auto begin = std::chrono::steady_clock::now();
    CHECK(worker.submit_removed({11,-22},ids));
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now()-begin).count();
    CHECK(elapsed < 100 && worker.state({11,-22},31) == pilot::LocalState::Pending);
    store.unblock();
    await_state(worker,{11,-22},31,pilot::LocalState::Durable);
    const auto status = worker.status();
    CHECK(status.ready && status.captured == 2 && status.marked == 1 && status.pending == 0);
    worker.shutdown();
    std::lock_guard lock(store.mutex);
    CHECK(store.content.at({11,-22,31}) == "synthetic edited");
    CHECK(store.deleted.at({11,-22,31}));
    CHECK(store.order.size() == 3);
    CHECK(store.order[0] == 'C' && store.order[1] == 'C' && store.order[2] == 'D');
}
void route_isolation_and_missing_receipt() {
    MemoryBackend store;
    pilot::AsyncWorker worker(store.callbacks());
    activate(worker);
    CHECK(worker.submit_capture({11,-22},batch(31,12,"synthetic scoped"),100));
    const std::array<int64_t,1> ids{31};
    CHECK(worker.submit_removed({12,-22},ids));
    await_state(worker,{12,-22},31,pilot::LocalState::Missing);
    CHECK(worker.submit_removed({11,-23},ids));
    await_state(worker,{11,-23},31,pilot::LocalState::Missing);
    CHECK(worker.submit_removed({11,-22},ids));
    await_state(worker,{11,-22},31,pilot::LocalState::Durable);
    CHECK(worker.status().ready && worker.status().missing == 2);
    worker.shutdown();
}
void preflight_and_incoming_filter() {
    MemoryBackend store;
    pilot::AsyncWorker worker(store.callbacks());
    CHECK(!worker.submit_capture({11,-22},batch(31,12,"synthetic before preflight"),100));
    auto incomplete = proof; incomplete.listeners = 2;
    CHECK(!worker.activate(incomplete));
    activate(worker);
    CHECK(worker.submit_capture({11,-22},batch(32,11,"synthetic own"),100));
    CHECK(worker.status().ready && worker.status().captured == 0);
    CHECK(!worker.submit_capture({11,-22},batch(33,0,"synthetic unknown"),100));
    CHECK(!worker.status().ready && worker.status().error == pilot::AsyncError::InvalidRoute);
    CHECK(!worker.activate(proof));
    worker.shutdown();
    std::lock_guard lock(store.mutex);
    CHECK(store.content.empty());
}
void batch_ack_and_missing_bit() {
    MemoryBackend store;
    pilot::AsyncWorker worker(store.callbacks());
    activate(worker);
    std::vector<rows::Snapshot> messages;
    std::array<int64_t,65> ids{};
    for (size_t i=0;i<64;++i) {
        ids[i] = static_cast<int64_t>(100+i);
        messages.push_back(message(ids[i],12,"synthetic batch"));
    }
    ids.back() = 999;
    CHECK(worker.submit_capture({11,-22},std::move(messages),100));
    CHECK(worker.submit_removed({11,-22},ids));
    await_state(worker,{11,-22},ids[0],pilot::LocalState::Durable);
    await_state(worker,{11,-22},ids.back(),pilot::LocalState::Missing);
    const auto status = worker.status();
    CHECK(status.ready && status.captured == 64 && status.marked == 64 && status.missing == 1);
    worker.shutdown();
    std::lock_guard lock(store.mutex);
    CHECK(store.order.size() == 2 && store.order[0] == 'C' && store.order[1] == 'D');
}
void queue_saturation_revokes_pending() {
    MemoryBackend store;
    store.block_capture = true;
    pilot::AsyncWorker worker(store.callbacks(),1);
    activate(worker);
    CHECK(worker.submit_capture({11,-22},batch(31,12,"synthetic blocked"),100));
    store.await_capture();
    const std::array<int64_t,1> first{31},second{32};
    CHECK(worker.submit_removed({11,-22},first));
    CHECK(worker.state({11,-22},31) == pilot::LocalState::Pending);
    CHECK(!worker.submit_removed({11,-22},second));
    CHECK(!worker.status().ready && worker.status().error == pilot::AsyncError::QueueFull);
    CHECK(worker.state({11,-22},31) == pilot::LocalState::Failed);
    store.unblock(); worker.shutdown();
}
void stalled_job_revokes_pending() {
    MemoryBackend store;
    store.block_capture = true;
    pilot::AsyncWorker worker(store.callbacks(),64,8*1024*1024,4096,20);
    activate(worker);
    CHECK(worker.submit_capture({11,-22},batch(31,12,"synthetic stalled"),100));
    store.await_capture();
    const std::array<int64_t,1> ids{31};
    CHECK(worker.submit_removed({11,-22},ids));
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    CHECK(!worker.status().ready && worker.status().error == pilot::AsyncError::Stalled);
    CHECK(worker.state({11,-22},31) == pilot::LocalState::Failed);
    store.unblock(); worker.shutdown();
}
void storage_failure_revokes_pending() {
    MemoryBackend store;
    store.fail_mark = true;
    pilot::AsyncWorker worker(store.callbacks());
    activate(worker);
    CHECK(worker.submit_capture({11,-22},batch(31,12,"synthetic failure"),100));
    const std::array<int64_t,1> ids{31};
    CHECK(worker.submit_removed({11,-22},ids));
    await_state(worker,{11,-22},31,pilot::LocalState::Failed);
    CHECK(!worker.status().ready && worker.status().error == pilot::AsyncError::Storage);
    worker.shutdown();
}
void shutdown_marks_pending_failed() {
    MemoryBackend store;
    store.block_capture = true;
    pilot::AsyncWorker worker(store.callbacks());
    activate(worker);
    CHECK(worker.submit_capture({11,-22},batch(31,12,"synthetic shutdown"),100));
    store.await_capture();
    const std::array<int64_t,1> ids{31};
    CHECK(worker.submit_removed({11,-22},ids));
    std::thread closer([&] { worker.shutdown(); });
    const auto deadline = std::chrono::steady_clock::now()+std::chrono::seconds(2);
    while (worker.status().ready && std::chrono::steady_clock::now()<deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    CHECK(!worker.status().ready);
    CHECK(worker.state({11,-22},31) == pilot::LocalState::Failed);
    store.unblock(); closer.join();
}
void disabled_preflight_drains_prior_work() {
    MemoryBackend store;
    store.block_capture = true;
    pilot::AsyncWorker worker(store.callbacks());
    auto disabled = proof; disabled.enabled = false;
    CHECK(worker.activate(disabled));
    CHECK(worker.status().available && !worker.status().ready);
    CHECK(worker.activate(proof));
    CHECK(worker.submit_capture({11,-22},batch(31,12,"synthetic drain"),100));
    store.await_capture();
    const std::array<int64_t,1> ids{31};
    CHECK(worker.submit_removed({11,-22},ids));
    disabled.generation = 2;
    CHECK(worker.activate(disabled));
    CHECK(worker.status().available && !worker.status().ready);
    store.unblock();
    await_state(worker,{11,-22},31,pilot::LocalState::Durable);
    CHECK(worker.status().marked == 1 && worker.status().error == pilot::AsyncError::None);
    worker.shutdown();
}
}
int main() {
    passive_capture_edit_delete_is_ordered();
    route_isolation_and_missing_receipt();
    preflight_and_incoming_filter();
    batch_ack_and_missing_bit(); queue_saturation_revokes_pending();
    stalled_job_revokes_pending(); storage_failure_revokes_pending();
    shutdown_marks_pending_failed(); disabled_preflight_drains_prior_work();
}
