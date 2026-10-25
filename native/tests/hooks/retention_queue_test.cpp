#include "retention_queue.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstring>
#include <future>
#include <iostream>
#include <memory>
#include <thread>
#include <vector>
using namespace valmax::retention;
namespace {
unsigned checks=0;
void check(bool good,const char *name) {
    if(!good) {std::cerr<<"FAIL "<<name<<'\n';std::exit(1);} ++checks;
}
ValmaxRetentionPolicy policy(uint64_t generation=1,bool enabled=true) {
    ValmaxRetentionPolicy p{sizeof(p),1,generation,{},enabled?1u:0u,1,25,0};
    check(policy_revision(enabled,p.revision),"policy hash");return p;
}
ValmaxRetentionRequest request() {ValmaxRetentionRequest q{};q.size=sizeof(q);q.version=1;return q;}
ValmaxRetentionAck ack_for(const ValmaxRetentionRequest &r) {
    ValmaxRetentionAck a{};a.size=sizeof(a);a.version=1;a.request=r;return a;
}
ValmaxRetentionStatus status(Queue &q) {ValmaxRetentionStatus s{};q.status(s);return s;}
std::unique_ptr<Queue> enabled() {
    auto q=std::make_unique<Queue>();check(q->set_policy(policy())==0,"enable policy");return q;
}
constexpr std::array<int64_t,3> ids{9007199254740993LL,9223372036854775807LL,31};
void basic() {
    auto q=std::make_unique<Queue>();Bitmap bits;bits.fill(255);
    check(q->prepare(11,-21,ids,bits)==VALMAX_RETENTION_NOT_READY && std::ranges::all_of(bits,[](auto b){return b==0;}),"disabled no fake decision");
    check(q->set_policy(policy())==0,"ready storage policy");
    auto s=status(*q);check(!s.ready && !s.applied && !s.listeners && !s.gates,"queue alone never ready");
    check(q->received(INT64_MAX,INT64_MIN,ids)==0,"full int64 routing");
    auto r=request();std::array<int64_t,4096> output{};
    check(q->take(r,output.data(),4096,0)==0 && r.owner==INT64_MAX && r.chat==INT64_MIN
        && std::equal(ids.begin(),ids.end(),output.begin()),"copied routing lossless");
    char digest[64]{};check(digest_ids(ids,digest) && std::memcmp(r.ids_digest,digest,64)==0,"ordered IDs digest");
    auto a=ack_for(r);check(q->ack(a)==0 && status(*q).in_flight==0,"durable receipt ACK releases");
    check(q->ack(a)==VALMAX_RETENTION_ARGUMENT,"replayed ACK rejected");
    check(q->shutdown()==0,"clean shutdown");
}
void ownership_fifo() {
    auto q=enabled();auto values=ids;check(q->received(11,-21,values)==0,"enqueue copied IDs");values.fill(7);
    const std::array<int64_t,1> next{77};check(q->received(12,-21,next)==0,"second account enqueued");
    auto r=request();std::array<int64_t,4096> out{};
    check(q->take(r,out.data(),4096,0)==0 && out[0]==ids[0] && r.owner==11,"borrowed input not retained");
    auto second=request();check(q->take(second,out.data(),4096,0)==VALMAX_RETENTION_BUSY,"single consumer durable FIFO");
    auto a=ack_for(r);check(q->ack(a)==0,"first commit");
    check(q->take(second,out.data(),4096,0)==0 && second.nonce>r.nonce && second.owner==12 && out[0]==77,"FIFO route independent");
    check(q->ack(ack_for(second))==0,"second commit");
}
void invalid_and_capacity() {
    auto q=enabled();const std::array<int64_t,2> duplicate{31,31};const std::array<int64_t,1> zero{0},negative{-1};
    check(q->received(0,1,ids)==VALMAX_RETENTION_ARGUMENT && q->received(1,0,ids)==VALMAX_RETENTION_ARGUMENT
        && q->received(-1,1,ids)==VALMAX_RETENTION_ARGUMENT,"invalid routes");
    check(q->received(1,1,{})==VALMAX_RETENTION_ARGUMENT && q->received(1,1,duplicate)==VALMAX_RETENTION_ARGUMENT
        && q->received(1,1,zero)==VALMAX_RETENTION_ARGUMENT && q->received(1,1,negative)==VALMAX_RETENTION_ARGUMENT,"invalid ID sets");
    std::vector<int64_t> many(4097);for(size_t i=0;i<many.size();++i) many[i]=static_cast<int64_t>(i+1);
    check(q->received(1,1,many)==VALMAX_RETENTION_ARGUMENT,"4097 rejected");
    for(unsigned i=0;i<8;++i) check(q->received(11,21,ids)==0,"bounded slot enqueued");
    check(q->received(11,21,ids)==VALMAX_RETENTION_QUEUE_FULL && status(*q).queued==8,"queue full never expands");
    auto r=request();std::array<int64_t,4096> out{};
    check(q->take(r,out.data(),4095,0)==VALMAX_RETENTION_ARGUMENT
        && q->take(r,out.data(),4096,26)==VALMAX_RETENTION_ARGUMENT,"bounded caller buffers and wait");
    check(q->shutdown()==0 && status(*q).queued==0,"queued receipts cancelled at shutdown");
}
void decision_ack() {
    auto q=enabled();Bitmap bitmap{};
    auto future=std::async(std::launch::async,[&]{return q->prepare(11,-21,ids,bitmap);});
    auto r=request();std::array<int64_t,4096> out{};
    check(q->take(r,out.data(),4096,25)==0 && r.kind==VALMAX_RETENTION_PREPARE_DELETE,"delete delivered");
    auto a=ack_for(r);a.retained[0]=5;
    auto bad=a;bad.request.owner=12;check(q->ack(bad)==VALMAX_RETENTION_ARGUMENT,"owner collision rejected");
    bad=a;bad.request.chat=21;check(q->ack(bad)==VALMAX_RETENTION_ARGUMENT,"chat collision rejected");
    bad=a;bad.request.ids_digest[0]^=1;check(q->ack(bad)==VALMAX_RETENTION_ARGUMENT,"digest mutation rejected");
    bad=a;bad.request.epoch++;check(q->ack(bad)==VALMAX_RETENTION_ARGUMENT,"epoch mutation rejected");
    bad=a;bad.request.generation++;check(q->ack(bad)==VALMAX_RETENTION_ARGUMENT,"generation mutation rejected");
    bad=a;bad.retained[0]|=8;check(q->ack(bad)==VALMAX_RETENTION_ARGUMENT,"unused bit rejected");
    check(q->ack(a)==0 && future.get()==0 && bitmap[0]==5,"only exact durable ACK resolves decision");
    check(q->set_policy(policy(2,false))==0 && bitmap[0]==5,"returned two-pass decision immutable after disable");
}
void timeout_and_late() {
    auto q=enabled();Bitmap bitmap{};
    auto start=std::chrono::steady_clock::now();
    check(q->prepare(11,-21,ids,bitmap)==VALMAX_RETENTION_TIMEOUT && bitmap[0]==0,"no worker timeout fails open");
    check(std::chrono::steady_clock::now()-start<std::chrono::seconds(1),"wait finite even without consumer");
    check(status(*q).queued==0 && status(*q).missed==1,"timed-out queued request released and counted");
    auto future=std::async(std::launch::async,[&]{return q->prepare(11,-21,ids,bitmap);});
    auto r=request();std::array<int64_t,4096> out{};check(q->take(r,out.data(),4096,25)==0,"taken timeout request");
    check(future.get()==VALMAX_RETENTION_TIMEOUT && bitmap[0]==0,"in-flight timeout does not block indefinitely");
    auto a=ack_for(r);a.retained[0]=7;check(q->ack(a)==VALMAX_RETENTION_STALE,"late durable commit never authorizes skip");
    check(status(*q).in_flight==0 && bitmap[0]==0,"late request cleaned without changing old scope");
    check(q->received(22,-21,ids)==0,"slot reusable only after late ACK");
    auto next=request();check(q->take(next,out.data(),4096,0)==0 && next.nonce!=r.nonce,"fresh nonce after slot reuse");
    check(q->ack(a)==VALMAX_RETENTION_ARGUMENT && status(*q).in_flight==1,"late replay cannot ACK reused slot");
    check(q->ack(ack_for(next))==0,"reused slot proper ACK");
}
void failures_disable_shutdown() {
    auto q=enabled();Bitmap bitmap{};
    auto future=std::async(std::launch::async,[&]{return q->prepare(11,-21,ids,bitmap);});
    auto r=request();std::array<int64_t,4096> out{};check(q->take(r,out.data(),4096,25)==0,"failure request");
    auto a=ack_for(r);a.result=VALMAX_RETENTION_STORAGE_FAILED;
    check(q->ack(a)==VALMAX_RETENTION_STORAGE_ERROR && future.get()==VALMAX_RETENTION_STORAGE_ERROR,"storage failure no skip");
    check(q->received(11,21,ids)==VALMAX_RETENTION_NOT_READY && status(*q).storage_failures==1,"storage failure latched");
    check(q->set_policy(policy(2))==0,"explicit recovered storage refresh");
    future=std::async(std::launch::async,[&]{return q->prepare(11,-21,ids,bitmap);});r=request();
    check(q->take(r,out.data(),4096,25)==0,"disable request");
    check(q->set_policy(policy(3,false))==0 && future.get()==VALMAX_RETENTION_STALE,"disable cancels unreturned decision");
    check(q->shutdown()==VALMAX_RETENTION_BUSY,"shutdown retains worker-owned request");
    check(q->ack(ack_for(r))==VALMAX_RETENTION_STALE && q->shutdown()==0,"shutdown drains late worker then clean");
    check(q->set_policy(policy(4))==VALMAX_RETENTION_NOT_READY,"shutdown cannot accidentally reactivate queue");
}
void invalid_policy_exhaustion() {
    auto q=enabled();auto p=policy(2);p.revision[0]^=1;
    check(q->set_policy(p)==VALMAX_RETENTION_POLICY_ERROR && q->received(11,21,ids)==VALMAX_RETENTION_NOT_READY,"invalid revision revokes availability");
    check(q->set_policy(policy(3))==0,"fresh valid policy restores");
    check(q->set_policy(policy(3))==VALMAX_RETENTION_POLICY_ERROR,"generation replay rejected");
    check(q->set_policy(policy(UINT64_MAX))==VALMAX_RETENTION_EXHAUSTED,"generation wrap forbidden");
    auto n=enabled();n->exhaust_for_test(true,false);
    check(n->received(11,21,ids)==VALMAX_RETENTION_EXHAUSTED,"nonce wrap forbidden");
    auto e=enabled();e->exhaust_for_test(false,true);
    check(e->set_policy(policy(2))==VALMAX_RETENTION_EXHAUSTED,"epoch wrap forbidden");
}
void parallel_queue_test() {
    auto q=enabled();std::atomic<bool> done=false;std::atomic<unsigned> commits=0;
    std::thread worker([&]{std::array<int64_t,4096> out{};while(!done) {
        auto r=request();if(q->take(r,out.data(),4096,1)==0) {
            auto a=ack_for(r);if(q->ack(a)==0) ++commits;
        }
    }});
    std::vector<std::thread> producers;
    for(int n=0;n<4;++n) producers.emplace_back([&,n]{for(int i=0;i<100;++i) {
        q->received(11+n,-21,ids);const auto s=status(*q);
        if(s.queued>8 || s.in_flight>1) std::abort();
    }});
    for(auto &t:producers) t.join();
    check(q->set_policy(policy(2,false))==0,"concurrent producers drained by disable");
    done=true;worker.join();
    const auto s=status(*q);check(s.queued==0 && s.in_flight==0 && commits>0,"concurrent consumer bounded and clean");
    check(q->shutdown()==0,"concurrent lifecycle shutdown");
}
}
int main() {
    basic();ownership_fifo();invalid_and_capacity();decision_ack();timeout_and_late();
    failures_disable_shutdown();invalid_policy_exhaustion();parallel_queue_test();
    char digest[64]{},revision[64]{};
    check(digest_ids(ids,digest) && policy_revision(true,revision),"cross-language vector hashes");
    std::cout<<"{\"schema\":1,\"checks\":"<<checks<<",\"passed\":true,\"syntheticIdsDigest\":\""
        <<std::string(digest,64)<<"\",\"enabledRevision\":\""<<std::string(revision,64)
        <<"\",\"actualHooksInstalled\":false,\"realProfilesAccessed\":false}\n";
}
