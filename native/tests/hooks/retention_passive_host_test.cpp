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
#include <vector>

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
struct alignas(8) TestRow final { std::array<uint8_t,rows::kMessageBytes> bytes{}; };
static_assert(sizeof(TestRow)==rows::kMessageBytes);
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
    const auto byte_count=end-begin;
    if (digest && byte_count>=rows::kMessageBytes
        && byte_count<=1024*rows::kMessageBytes && byte_count%rows::kMessageBytes==0) {
        const auto *data=reinterpret_cast<volatile const uint8_t *>(begin);
        for (size_t i=0;i<byte_count;++i) digest=digest*33u+data[i];
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
void send_many(OwnerFixture &owner,int64_t first_id,size_t count,
               uint32_t status_code,const char *content,bool repeat_id=false) {
    CHECK(count>0 && count<=1024 && (status_code==0 || status_code==2));
    std::vector<TestRow> batch(count);
    for (size_t i=0;i<count;++i) {
        auto &row=batch[i].bytes;
        const int64_t id=first_id+(repeat_id ? 0 : static_cast<int64_t>(i));
        write(row,0,id);write(row,0x80,int64_t{12});write(row,0x128,status_code);
        if (status_code != 2) {
            write(row,0x18,1700000000000 + id);
            const size_t length=std::strlen(content);
            CHECK(length>0 && length<=15);
            write(row,0x40,static_cast<uint64_t>(length));
            write(row,0x48,uint64_t{15});
            std::memcpy(row.data()+0x30,content,length);
        }
    }
    const auto begin=reinterpret_cast<uintptr_t>(batch.data());
    const auto end=begin+count*rows::kMessageBytes;
    Vector vector{begin,end,end};
    fake_rows(owner.history.data(),nullptr,&vector,nullptr,0);
}
void send_text_budget_overflow(OwnerFixture &owner) {
    std::vector<TestRow> batch(33);
    std::vector<uint8_t> text(rows::kMaxTextBytes,static_cast<uint8_t>('x'));
    for (size_t i=0;i<batch.size();++i) {
        auto &row=batch[i].bytes;
        const int64_t id=3000+static_cast<int64_t>(i);
        write(row,0,id);write(row,0x80,int64_t{12});write(row,0x128,uint32_t{0});
        write(row,0x18,1700000000000 + id);
        write(row,0x30,reinterpret_cast<uintptr_t>(text.data()));
        write(row,0x40,static_cast<uint64_t>(text.size()));
        write(row,0x48,static_cast<uint64_t>(text.size()));
    }
    const auto begin=reinterpret_cast<uintptr_t>(batch.data());
    const auto end=begin+batch.size()*rows::kMessageBytes;
    Vector vector{begin,end,end};
    fake_rows(owner.history.data(),nullptr,&vector,nullptr,0);
}
void send_mixed_zero_sender(OwnerFixture &owner,int64_t own_sender,
                            int64_t incoming_sender) {
    std::array<TestRow,6> batch{};
    for (size_t i=0;i<4;++i) {
        auto &row=batch[i].bytes;
        write(row,0,int64_t{6100+static_cast<int64_t>(i)*3});
        write(row,0x80,int64_t{0});
        write(row,0x128,static_cast<uint32_t>(i%2));
        const uint64_t length=i>=2 ? 7 : 0;
        write(row,0x40,length);write(row,0x48,uint64_t{15});
        if (length) std::memcpy(row.data()+0x30,"unknown",length);
    }
    const auto set_text=[&](size_t index,int64_t id,int64_t sender,const char *content) {
        auto &row=batch[index].bytes;
        write(row,0,id);write(row,0x80,sender);write(row,0x128,uint32_t{0});
        const size_t length=std::strlen(content);
        CHECK(length>0 && length<=15);
        write(row,0x40,static_cast<uint64_t>(length));write(row,0x48,uint64_t{15});
        std::memcpy(row.data()+0x30,content,length);
    };
    set_text(4,6101,own_sender,"own");
    set_text(5,6102,incoming_sender,"incoming");
    const auto begin=reinterpret_cast<uintptr_t>(batch.data());
    const auto end=begin+batch.size()*rows::kMessageBytes;
    Vector vector{begin,end,end};
    fake_rows(owner.history.data(),nullptr,&vector,nullptr,0);
}
void send_malformed_zero_sender(OwnerFixture &owner,bool malformed_shape) {
    std::array<TestRow,2> batch{};
    auto &valid=batch[0].bytes;
    write(valid,0,int64_t{32});write(valid,0x80,int64_t{12});
    write(valid,0x128,uint32_t{0});write(valid,0x18,int64_t{1700000000032});
    write(valid,0x40,uint64_t{5});write(valid,0x48,uint64_t{15});
    std::memcpy(valid.data()+0x30,"valid",5);
    auto &malformed=batch[1].bytes;
    write(malformed,0,malformed_shape ? int64_t{33} : int64_t{0});
    write(malformed,0x80,int64_t{0});write(malformed,0x128,uint32_t{0});
    write(malformed,0x40,malformed_shape ? uint64_t{16} : uint64_t{7});
    write(malformed,0x48,uint64_t{15});
    const auto begin=reinterpret_cast<uintptr_t>(batch.data());
    Vector vector{begin,begin+batch.size()*rows::kMessageBytes,
                  begin+batch.size()*rows::kMessageBytes};
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

int main(int argc,char **argv) {
    static uint64_t owner_vtable=1,data_vtable=2,history_owner_vtable=3;
    static uint64_t factory_vtable=4,chat_history_vtable=5;
    Backend backend;
    const TestTargets targets{reinterpret_cast<void *>(&fake_owner_create),
        reinterpret_cast<void *>(&fake_owner_destroy),reinterpret_cast<void *>(&fake_rows),
        reinterpret_cast<uintptr_t>(&owner_vtable),reinterpret_cast<uintptr_t>(&data_vtable),
        reinterpret_cast<uintptr_t>(&history_owner_vtable),reinterpret_cast<uintptr_t>(&factory_vtable),
        reinterpret_cast<uintptr_t>(&chat_history_vtable)};
    if (argc==2) {
        const bool invalid_sender=std::strcmp(argv[1],"--invalid-sender")==0;
        const bool invalid_sender_shape=std::strcmp(argv[1],"--invalid-sender-shape")==0;
        const bool duplicate_text_id=std::strcmp(argv[1],"--duplicate-text-id")==0;
        const bool owner_capacity=std::strcmp(argv[1],"--owner-capacity")==0;
        CHECK(invalid_sender || invalid_sender_shape || duplicate_text_id || owner_capacity);
        CHECK(install_synthetic(L"E:\\ValMAXBuild\\retention-p11-sandbox\\synthetic-profile",
            targets,{&backend,&Backend::capture,&Backend::mark}));
        if (owner_capacity) {
            std::vector<OwnerFixture> owners(130);
            for (size_t i=0;i<owners.size();++i) {
                owners[i].setup(targets,11+static_cast<int64_t>(i),
                    -22-static_cast<int64_t>(i));
                fake_owner_create(owners[i].owner.data());
                if (i==128) {
                    CHECK(status().error==HostError::OwnerScope);
                    CHECK(status().route_failures==1);
                    CHECK(status().last_scope_failure_reason==ScopeFailureReason::OwnerCapacity);
                }
            }
            CHECK(status().error==HostError::OwnerScope);
            CHECK(status().route_failures==2);
            CHECK(status().active_owners==128);
            CHECK(status().last_scope_failure_reason==ScopeFailureReason::OwnerCapacity);
            CHECK(status().last_invalid_sender_reason==rows::InvalidSenderReason::None);
            CHECK(status().worker.error==pilot::AsyncError::InvalidRoute);
            shutdown();
            return 0;
        }
        OwnerFixture owner,second;
        owner.setup(targets,11,-22);
        fake_owner_create(owner.owner.data());
        if (invalid_sender || invalid_sender_shape) {
            second.setup(targets,12,-22);
            fake_owner_create(second.owner.data());
        }
        send(owner,31,12,0,"fixture start");
        CHECK(synthetic_store_ready());
        CHECK(set_enabled(true,std::string(64,'a'),1));
        CHECK(status().capture_enabled());
        CHECK(status().last_scope_failure_reason==ScopeFailureReason::None);
        if (invalid_sender || invalid_sender_shape)
            send_malformed_zero_sender(owner,invalid_sender_shape);
        else send_many(owner,32,2,0,"duplicate",true);
        const auto failed=status();
        CHECK(failed.error==HostError::OwnerScope);
        CHECK(failed.route_failures==1);
        CHECK(failed.last_scope_failure_reason==(invalid_sender || invalid_sender_shape
            ? ScopeFailureReason::InvalidSender : ScopeFailureReason::DuplicateTextId));
        CHECK(failed.last_invalid_sender_reason==(invalid_sender
            ? rows::InvalidSenderReason::InvalidId
            : invalid_sender_shape ? rows::InvalidSenderReason::InvalidTextShape
                                   : rows::InvalidSenderReason::None));
        CHECK(failed.active_owners==(invalid_sender || invalid_sender_shape ? 2u : 1u));
        CHECK(failed.worker.error==pilot::AsyncError::InvalidRoute);
        CHECK(failed.bounded_skips==0);
        CHECK(!failed.capture_enabled() && failed.worker.captured==0);
        if (invalid_sender || invalid_sender_shape) send(second,33,11,0,"other account");
        else send(owner,33,12,0,"after failure");
        CHECK(status().last_scope_failure_reason==failed.last_scope_failure_reason);
        CHECK(status().last_invalid_sender_reason==failed.last_invalid_sender_reason);
        CHECK(status().route_failures==failed.route_failures);
        {
            std::lock_guard lock(backend.mutex);
            CHECK(backend.content.empty());
        }
        shutdown();
        return 0;
    }
    CHECK(argc==1);
    // An account owner that exists before hook attachment must never be
    // borrowed from another account just to make the receipt look ready.
    OwnerFixture preAttached;
    preAttached.setup(targets,13,-22);
    fake_owner_create(preAttached.owner.data());
    CHECK(install_synthetic(L"E:\\ValMAXBuild\\retention-p11-sandbox\\synthetic-profile",
        targets,{&backend,&Backend::capture,&Backend::mark}));
    CHECK(profile_matches(L"E:\\ValMAXBuild\\retention-p11-sandbox\\synthetic-profile"));
    CHECK(!profile_matches(L"E:\\ValMAXBuild\\retention-p11-sandbox\\other-profile"));
    CHECK(status().active_owners==0 && status().owner_constructed==0);
    send(preAttached,29,12,0,"before attach");
    CHECK(status().no_owner_callbacks==1 && status().bounded_skips==0);
    CHECK(!status().owner_join && !status().infrastructure_ready());
    OwnerFixture first,second;
    first.setup(targets,11,-22);second.setup(targets,12,-22);
    fake_owner_create(first.owner.data());
    write(first.factory,0,uintptr_t{0});
    send(first,29,12,0,"invalid owner");
    CHECK(status().invalid_owner_callbacks==1 && status().active_owners==1);
    write(first.factory,0,targets.factory_vtable);
    fake_owner_create(second.owner.data());
    CHECK(status().owner_constructed==2 && status().active_owners==2);
    send(first,31,12,0,"fixture start");
    CHECK(status().owner_join && status().joined_upserts==1);
    CHECK(!status().infrastructure_ready());
    CHECK(synthetic_store_ready());
    CHECK(set_enabled(true,std::string(64,'a'),1));
    CHECK(status().infrastructure_ready() && status().capture_enabled());
    const auto before_pre_attached=status();
    send(preAttached,30,12,0,"unmatched");
    CHECK(status().unmatched_owner_callbacks==before_pre_attached.unmatched_owner_callbacks+1);
    CHECK(status().bounded_skips==before_pre_attached.bounded_skips+1);
    CHECK(status().worker.captured==0 && status().capture_enabled());
    fake_owner_destroy(preAttached.owner.data());
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
    // One MAX write may contain multiple worker-sized batches. It must not
    // revoke the process-wide owner scope or poison later incremental events.
    send_many(first,1000,200,0,"batch text");
    CHECK(wait_for([&]{return status().worker.captured==201;}));
    CHECK(status().error==HostError::None && status().worker.ready);
    send_many(first,1000,200,2,"");
    CHECK(wait_for([&]{return status().worker.marked==201;}));
    {
        std::lock_guard lock(backend.mutex);
        CHECK(backend.content.at({11,-22,1000})=="batch text");
        CHECK(backend.content.at({11,-22,1199})=="batch text");
        CHECK(backend.deleted.at({11,-22,1000}));
        CHECK(backend.deleted.at({11,-22,1199}));
        CHECK(!backend.content.contains({12,-22,1000}));
    }
    send(first,5000,12,0,"later text");
    CHECK(wait_for([&]{return status().worker.captured==202;}));
    send(first,5000,12,2,"");
    CHECK(wait_for([&]{return status().worker.marked==202;}));
    const auto before_oversized=status();
    send_many(first,2000,513,0,"bounded skip");
    CHECK(status().bounded_skips==before_oversized.bounded_skips+1);
    CHECK(status().route_failures==before_oversized.route_failures);
    CHECK(status().error==HostError::None && status().worker.ready);
    CHECK(status().worker.captured==202);
    const auto before_text_overflow=status();
    send_text_budget_overflow(first);
    CHECK(status().bounded_skips==before_text_overflow.bounded_skips+1);
    CHECK(status().route_failures==before_text_overflow.route_failures);
    CHECK(status().error==HostError::None && status().worker.ready);
    CHECK(status().worker.captured==202);
    send(first,5001,12,0,"after skip");
    CHECK(wait_for([&]{return status().worker.captured==203;}));
    send(first,5001,12,2,"");
    CHECK(wait_for([&]{return status().worker.marked==203;}));
    CHECK(set_enabled(false,std::string(64,'b'),2));
    CHECK(status().infrastructure_ready() && !status().capture_enabled());
    const auto disabled_skips=status().bounded_skips;
    write(first.history,0x30,uintptr_t{0});
    send(first,35,12,0,"disabled late");
    write(first.history,0x30,reinterpret_cast<uintptr_t>(&first.pair[0]));
    CHECK(status().bounded_skips==disabled_skips);
    CHECK(status().error==HostError::None);
    send(first,33,12,0,"disabled");
    CHECK(status().worker.captured==203);
    CHECK(set_enabled(true,std::string(64,'c'),3));
    // A write already queued by a destroyed account may arrive while another
    // account remains active. Do not borrow the remaining account's route or
    // revoke its later captures; report the skipped callback as missed.
    const auto before_late=status();
    fake_owner_destroy(first.owner.data());
    CHECK(status().owner_join && status().worker.ready);
    send(first,6000,12,0,"late owner");
    CHECK(status().bounded_skips==before_late.bounded_skips+1);
    CHECK(status().route_failures==before_late.route_failures);
    CHECK(status().error==HostError::None && status().worker.ready);
    send(second,6001,11,0,"survivor");
    CHECK(wait_for([&]{return status().worker.captured==204;}));
    send(second,6001,11,2,"");
    CHECK(wait_for([&]{return status().worker.marked==204;}));
    {
        std::lock_guard lock(backend.mutex);
        CHECK(!backend.content.contains({11,-22,6000}));
        CHECK(backend.content.at({12,-22,6001})=="survivor");
        CHECK(backend.deleted.at({12,-22,6001}));
    }
    fake_owner_create(first.owner.data());
    const auto before_zero_sender=status();
    send_mixed_zero_sender(first,11,12);
    CHECK(status().bounded_skips==before_zero_sender.bounded_skips+1);
    CHECK(status().route_failures==before_zero_sender.route_failures);
    CHECK(status().last_scope_failure_reason==ScopeFailureReason::None);
    CHECK(status().capture_enabled() && status().worker.ready);
    CHECK(wait_for([&]{return status().worker.captured==205;}));
    send(second,6102,11,0,"second account");
    CHECK(wait_for([&]{return status().worker.captured==206;}));
    {
        std::lock_guard lock(backend.mutex);
        CHECK(!backend.content.contains({11,-22,6100}));
        CHECK(!backend.content.contains({11,-22,6101}));
        CHECK(!backend.content.contains({11,-22,6103}));
        CHECK(!backend.content.contains({11,-22,6106}));
        CHECK(!backend.content.contains({11,-22,6109}));
        CHECK(backend.content.at({11,-22,6102})=="incoming");
        CHECK(backend.content.at({12,-22,6102})=="second account");
    }
    const auto before_bad_scope=status();
    write(first.history,0x30,uintptr_t{0});
    send(first,6000,12,0,"bad route");
    CHECK(status().bounded_skips==before_bad_scope.bounded_skips+1);
    CHECK(status().invalid_history_callbacks==before_bad_scope.invalid_history_callbacks+1);
    CHECK(status().route_failures==before_bad_scope.route_failures);
    CHECK(status().error==HostError::None && status().worker.ready);
    write(first.history,0x30,reinterpret_cast<uintptr_t>(&first.pair[0]));
    // Two live account owners claiming the same exact storage route are an
    // actual ambiguity. This remains terminal rather than guessing an owner.
    write(first.factory,0x48,reinterpret_cast<uintptr_t>(&second.pair[0]));
    write(first.factory,0x50,reinterpret_cast<uintptr_t>(&second.pair[1]));
    send(second,6002,11,0,"ambiguous");
    CHECK(status().error==HostError::OwnerScope);
    CHECK(status().route_failures==before_bad_scope.route_failures+1);
    CHECK(status().last_scope_failure_reason==ScopeFailureReason::AmbiguousRoute);
    CHECK(status().last_invalid_sender_reason==rows::InvalidSenderReason::None);
    CHECK(status().worker.error==pilot::AsyncError::InvalidRoute);
    fake_owner_destroy(first.owner.data());fake_owner_destroy(second.owner.data());
    CHECK(!status().owner_join && !status().infrastructure_ready());
    shutdown();
    CHECK(status().error==HostError::Shutdown && status().listeners==0);
    CHECK(profile_matches(L"E:\\ValMAXBuild\\retention-p11-sandbox\\synthetic-profile"));
    send(first,34,12,2,"");
    CHECK(status().worker.marked==204);
}
