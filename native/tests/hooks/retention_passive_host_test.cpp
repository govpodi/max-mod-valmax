#include "retention_passive_host.h"
#include "retention_store_api.h"

#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <tuple>

#define CHECK(value) do { if (!(value)) { std::fprintf(stderr,"CHECK_FAIL_LINE:%d\n",__LINE__); std::abort(); } } while(false)
using namespace valmax::retention;
using namespace valmax::retention::passive;
namespace {
using Key=std::tuple<int64_t,int64_t,int64_t>;
struct Backend final {
    std::mutex mutex;
    std::map<Key,std::string> content;
    std::map<Key,int64_t> order_key;
    std::map<Key,bool> deleted;
    uint64_t captures=0,marks=0;
    static bool capture(void *raw,rows::Route route,
                        std::span<const pilot::CapturedText> values) noexcept {
        auto &self=*static_cast<Backend *>(raw);
        std::lock_guard lock(self.mutex);
        for (const auto &item:values) {
            const auto text=item.text.bytes();
            self.content[{route.owner,route.chat,item.id}]=std::string(
                reinterpret_cast<const char *>(text.data()),text.size());
            self.order_key[{route.owner,route.chat,item.id}]=item.order_key;
            ++self.captures;
        }
        return true;
    }
    static bool mark(void *raw,rows::Route route,std::span<const int64_t> ids,
                     std::span<uint8_t> bits) noexcept {
        auto &self=*static_cast<Backend *>(raw);
        std::lock_guard lock(self.mutex);
        for (size_t i=0;i<ids.size();++i) {
            const Key key{route.owner,route.chat,ids[i]};
            if (self.content.contains(key)) {
                self.deleted[key]=true;
                bits[i/8]|=static_cast<uint8_t>(1u<<(i%8));
                ++self.marks;
            }
        }
        return true;
    }
};
volatile uint32_t fake_calls=0;
std::atomic<uint32_t> fake_owner_digest{0},fake_rows_digest{0};
struct Vector {uintptr_t begin,end,cap;};
uint32_t consume_owner(void *owner) noexcept {
    const auto *bytes=static_cast<volatile const uint8_t *>(owner);
    uint32_t digest=0;
    for (size_t i=0;i<sizeof(uintptr_t);++i) digest=digest*33u+bytes[i];
    return digest;
}
extern "C" __declspec(noinline) void fake_owner_create(void *owner) {
    fake_owner_digest.fetch_xor(consume_owner(owner),std::memory_order_relaxed);
    ++fake_calls;
}
extern "C" __declspec(noinline) void fake_owner_destroy(void *owner) {
    // Different bodies prevent Release identical-code folding of hook targets.
    fake_owner_digest.fetch_add(consume_owner(owner),std::memory_order_relaxed);
    ++fake_calls;
}
extern "C" __declspec(noinline) void fake_rows(void *,void *,void *vector_input,void *,int) {
    // MAX reads its vector and rows. Consume the synthetic bytes too, or /O2
    // can discard writes which only the Gum callback would otherwise observe.
    const auto *vector=static_cast<volatile const Vector *>(vector_input);
    const uintptr_t begin=vector->begin,end=vector->end,cap=vector->cap;
    uint32_t digest=static_cast<uint32_t>(begin!=0 && end>=begin && cap>=end);
    if (digest && end-begin==rows::kMessageBytes) {
        const auto *bytes=reinterpret_cast<volatile const uint8_t *>(begin);
        for (size_t i=0;i<rows::kMessageBytes;++i) digest=digest*33u+bytes[i];
    }
    fake_rows_digest.store(digest,std::memory_order_relaxed);
    ++fake_calls;
}

template<class T,size_t N> void write(std::array<uint8_t,N> &object,size_t at,const T &value) {
    CHECK(at<=N-sizeof(T));std::memcpy(object.data()+at,&value,sizeof(value));
}
template<size_t N> uintptr_t address(std::array<uint8_t,N> &object) {
    return reinterpret_cast<uintptr_t>(object.data());
}
struct OwnerFixture final {
    std::array<uint8_t,0xe0> owner{},history{};
    std::array<uint8_t,0xa0> data{};
    std::array<uint8_t,0x98> history_owner{};
    std::array<uint8_t,0x58> factory{};
    std::array<uint64_t,2> pair{};
    void setup(const TestTargets &targets,int64_t owner_id,int64_t chat) {
        write(owner,0,targets.owner_vtable);write(owner,0x30,address(data));write(owner,0xd8,owner_id);
        write(data,0,targets.data_vtable);write(data,0x90,address(history_owner));
        write(history_owner,0,targets.history_owner_vtable);write(history_owner,0x88,address(factory));
        write(factory,0,targets.factory_vtable);
        pair={static_cast<uint64_t>(owner_id),static_cast<uint64_t>(chat)};
        const auto db=reinterpret_cast<uintptr_t>(&pair[0]);
        const auto control=reinterpret_cast<uintptr_t>(&pair[1]);
        write(factory,0x48,db);write(factory,0x50,control);
        write(history,0,targets.chat_history_vtable);write(history,0x30,db);
        write(history,0x38,control);write(history,0xd8,chat);
    }
};
void send(OwnerFixture &owner,int64_t id,int64_t sender,uint32_t status_code,
          const char *content) {
    alignas(8) std::array<uint8_t,rows::kMessageBytes> row{};
    write(row,0,id);write(row,0x80,sender);write(row,0x128,status_code);
    if (status_code != 2) {
        write(row,0x18,1700000000000 + id);
        const size_t len=std::strlen(content);
        CHECK(len>0 && len<=15);
        const uint64_t length=len,capacity=15;
        write(row,0x40,length);write(row,0x48,capacity);
        std::memcpy(row.data()+0x30,content,len);
    }
    const auto begin=address(row);
    Vector vector{begin,begin+rows::kMessageBytes,begin+rows::kMessageBytes};
    fake_rows(owner.history.data(),nullptr,&vector,nullptr,0);
}
bool wait_for(std::function<bool()> predicate) {
    const auto end=std::chrono::steady_clock::now()+std::chrono::seconds(2);
    while (std::chrono::steady_clock::now()<end) {
        if (predicate()) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return predicate();
}
}

// Synthetic in-memory backend is injected above. These storage exports must
// remain unreachable; stubs allow the listener object to link in isolation.
extern "C" uint32_t valmax_core_retention_preflight(const uint16_t *,size_t) { return 5; }
extern "C" uint32_t valmax_core_retention_capture_text_batch(
    const uint16_t *,size_t,int64_t,int64_t,const ValmaxRetentionCaptureRow *,size_t) { return 5; }
extern "C" uint32_t valmax_core_retention_capture_text_batch_v2(
    const uint16_t *,size_t,int64_t,int64_t,const ValmaxRetentionCaptureRowV2 *,size_t) { return 5; }
extern "C" uint32_t valmax_core_retention_mark_delete_batch(
    const uint16_t *,size_t,int64_t,int64_t,const int64_t *,size_t,uint8_t *,size_t) { return 5; }
extern "C" uint32_t valmax_core_retention_capture_text(
    const uint16_t *,size_t,int64_t,int64_t,int64_t,int64_t,const uint8_t *,size_t) { return 5; }
extern "C" uint32_t valmax_core_retention_store_process(
    const uint16_t *,size_t,const ValmaxRetentionRequest *,const int64_t *,ValmaxRetentionAck *) { return 5; }

int main() {
    static uint64_t owner_vtable=1,data_vtable=2,history_owner_vtable=3;
    static uint64_t factory_vtable=4,chat_history_vtable=5;
    Backend backend;
    const TestTargets targets{reinterpret_cast<void *>(&fake_owner_create),
        reinterpret_cast<void *>(&fake_owner_destroy),reinterpret_cast<void *>(&fake_rows),
        reinterpret_cast<uintptr_t>(&owner_vtable),reinterpret_cast<uintptr_t>(&data_vtable),
        reinterpret_cast<uintptr_t>(&history_owner_vtable),reinterpret_cast<uintptr_t>(&factory_vtable),
        reinterpret_cast<uintptr_t>(&chat_history_vtable)};
    CHECK(install_synthetic(L"E:\\ValMAXBuild\\retention-p11-sandbox\\synthetic-profile",
        targets,{&backend,&Backend::capture,&Backend::mark}));
    CHECK(profile_matches(L"E:\\ValMAXBuild\\retention-p11-sandbox\\synthetic-profile"));
    CHECK(!profile_matches(L"E:\\ValMAXBuild\\retention-p11-sandbox\\other-profile"));
    OwnerFixture first,second;
    first.setup(targets,11,-22);second.setup(targets,12,-22);
    fake_owner_create(first.owner.data());fake_owner_create(second.owner.data());
    CHECK(status().owner_constructed==2);
    send(first,31,12,0,"fixture start");
    CHECK(status().owner_join && status().joined_upserts==1);
    CHECK(!status().infrastructure_ready());
    CHECK(synthetic_store_ready());
    CHECK(set_enabled(true,std::string(64,'a'),1));
    CHECK(status().infrastructure_ready() && status().capture_enabled());
    send(first,31,12,0,"fixture edit");
    CHECK(wait_for([&]{return status().worker.captured==1;}));
    send(first,31,12,2,"");
    CHECK(wait_for([&]{return status().worker.marked==1;}));
    send(second,31,11,2,"");
    CHECK(wait_for([&]{return status().worker.missing==1;}));
    send(first,32,11,0,"own ignored");
    CHECK(status().worker.captured==1);
    {
        std::lock_guard lock(backend.mutex);
        CHECK(backend.content.at({11,-22,31})=="fixture edit");
        CHECK(backend.order_key.at({11,-22,31})==1700000000031);
        CHECK(backend.deleted.at({11,-22,31}));
        CHECK(!backend.content.contains({12,-22,31}));
        CHECK(!backend.content.contains({11,-22,32}));
    }
    CHECK(set_enabled(false,std::string(64,'b'),2));
    CHECK(status().infrastructure_ready() && !status().capture_enabled());
    send(first,33,12,0,"disabled");
    CHECK(status().worker.captured==1);
    fake_owner_destroy(first.owner.data());fake_owner_destroy(second.owner.data());
    CHECK(!status().owner_join && !status().infrastructure_ready());
    shutdown();
    CHECK(status().error==HostError::Shutdown && status().listeners==0);
    CHECK(profile_matches(L"E:\\ValMAXBuild\\retention-p11-sandbox\\synthetic-profile"));
    send(first,34,12,2,"");
    CHECK(status().worker.marked==1);
}
