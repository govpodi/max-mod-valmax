#include "staging_client.h"
#include <windows.h>
#include <atomic>
#include <barrier>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <thread>
#include <type_traits>
#include <vector>
using namespace valmax::media;
namespace {
size_t checks=0;
std::atomic<size_t> begins{0},stages{0},signals{0},cancels{0},releases{0},frees{0},retains{0};
uint32_t abi=1,beginCode=0,stageCode=0,signalCode=0,cancelCode=0,retainCode=0,releaseCode=0;
uint64_t beginToken=7;
ValmaxMediaStageResult response{40,1,8,{}};
ValmaxMediaStageRequest last{};
std::string root,url="file:///C:/ValMAXBuild/valmedia-0123456789abcdef0123456789abcdef.png";
bool emitUrl=true,beginThrows=false,stageThrows=false;
std::function<void()> stageHook;
void check(bool ok) { if(!ok) { std::cerr<<"failed_check="<<checks+1<<'\n'; std::exit(1); } ++checks; }
uint32_t version() { return abi; }
uint32_t begin(const uint8_t* p,size_t n,uint64_t* h) {
    ++begins; root.assign(reinterpret_cast<const char*>(p),n); *h=beginToken;
    if(beginThrows) throw 1;
    return beginCode;
}
uint32_t stage(uint64_t,const ValmaxMediaStageRequest* q,const uint8_t*,size_t,ValmaxMediaStageResult* r) {
    ++stages; last=*q; *r=response;
    if(emitUrl) r->file_url={reinterpret_cast<uint8_t*>(url.data()),url.size(),url.size()};
    if(stageHook) stageHook();
    if(stageThrows) throw 1;
    return stageCode;
}
uint32_t signal(uint64_t) { ++signals; return signalCode; }
uint32_t cancel(uint64_t) { ++cancels; return cancelCode; }
uint32_t retain(uint64_t) { ++retains; return retainCode; }
uint32_t release(uint64_t) { ++releases; return releaseCode; }
void free_buffer(ValmaxCoreBuffer* p) { ++frees; *p={}; }
StagingApi api(){return{version,begin,stage,signal,cancel,retain,release,free_buffer};}
std::vector<uint8_t> bytes{137,80,78,71,13,10,26,10};
const std::wstring path=L"C:/ValMAXBuild/fixtures/Тест \U0001f600";
auto new_job() { return StagingJob::begin(api(),path); }
auto prepare(StagingJob& job) { return job.stage(bytes,StagingMime::Png,bytes.size(),1800000000); }
void defaults() {
    abi=1; beginCode=stageCode=signalCode=cancelCode=retainCode=releaseCode=0; beginToken=7;
    response={40,1,8,{}}; emitUrl=true; beginThrows=stageThrows=false;
    stageHook={};
    url="file:///C:/ValMAXBuild/valmedia-0123456789abcdef0123456789abcdef.png";
}
}
int main() {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    static_assert(!std::is_copy_constructible_v<StagingJob> && !std::is_copy_constructible_v<PreparedFile>);
    static_assert(std::is_nothrow_move_constructible_v<StagingJob> && std::is_nothrow_move_constructible_v<PreparedFile>);
    static_assert(std::is_copy_constructible_v<StagingCancellation>);
    {
        auto j=new_job(); check(bool(j));
        check(root=="C:/ValMAXBuild/fixtures/Тест \xf0\x9f\x98\x80");
        auto c=j.value->cancellation(); const auto r=releases.load();
        auto p=prepare(*j.value); check(bool(p));
        check(last.size==32 && last.version==1 && last.mime==2 && last.reserved==0 && last.expected_bytes==8 && last.now_unix_seconds==1800000000);
        check(p.value->native_file_url()==url && p.value->byte_length()==8 && !p.value->cancellation_requested());
        check(prepare(*j.value).code==StagingCode::InvalidHandle);
        auto moved=std::move(*p.value); check(p.value->native_file_url().empty() && p.value->byte_length()==0);
        check(c.signal()==StagingCode::Ok && moved.cancellation_requested());
        check(moved.retain_for_handoff()==StagingCode::Cancelled);
        check(releases==r+1 && c.signal()==StagingCode::InvalidHandle);
        check(moved.cancel()==StagingCode::Ok && moved.retain_for_handoff()==StagingCode::InvalidState);
    }
    {
        auto j=new_job(); auto p=prepare(*j.value); const auto r=releases.load(),t=retains.load();
        check(p.value->retain_for_handoff()==StagingCode::Ok && retains==t+1 && releases==r+1);
        check(p.value->native_file_url().empty() && p.value->byte_length()==0);
    }
    const auto b=begins.load();
    for(const auto& invalid:{std::wstring(),std::wstring(31001,L'x'),std::wstring(16000,L'я'),
        std::wstring(L"C:/x\0y",7),std::wstring(1,static_cast<wchar_t>(0xd800)),std::wstring(1,static_cast<wchar_t>(0xdc00))})
        check(StagingJob::begin(api(),invalid).code==StagingCode::InvalidInput && begins==b);
    auto a=api(); a.release=nullptr; check(StagingJob::begin(a,path).code==StagingCode::CoreUnavailable && begins==b);
    a=api(); a.signal_cancel=nullptr; check(StagingJob::begin(a,path).code==StagingCode::CoreUnavailable && begins==b);
    abi=2; check(new_job().code==StagingCode::AbiMismatch && begins==b); defaults();
    for(uint32_t code=1;code<=15;++code) { beginCode=code; beginToken=0; check(new_job().code==static_cast<StagingCode>(code)); }
    defaults(); beginToken=0; check(new_job().code==StagingCode::AbiMismatch);
    defaults(); beginCode=99; auto r=releases.load(); check(new_job().code==StagingCode::AbiMismatch && releases==r+1);
    defaults(); beginThrows=true; r=releases.load(); check(new_job().code==StagingCode::InternalError && releases==r+1); defaults();
    {
        auto j=new_job(); const auto s=stages.load();
        check(j.value->stage({},StagingMime::Png,0,1).code==StagingCode::InvalidInput);
        check(j.value->stage(bytes,StagingMime::Png,9,1).code==StagingCode::InvalidInput);
        check(j.value->stage(bytes,static_cast<StagingMime>(0),8,1).code==StagingCode::InvalidInput);
        check(j.value->stage(bytes,static_cast<StagingMime>(7),8,1).code==StagingCode::InvalidInput);
        check(j.value->stage(bytes,StagingMime::Png,25*1024*1024+1,1).code==StagingCode::FileTooLarge && stages==s);
        check(bool(prepare(*j.value)));
    }
    for(uint32_t code=1;code<=16;++code) {
        auto j=new_job(); emitUrl=false; response={40,1,0,{}}; stageCode=code;
        const auto f=frees.load(); check(prepare(*j.value).code==(code<=15?static_cast<StagingCode>(code):StagingCode::AbiMismatch));
        check(frees==f+1 && prepare(*j.value).code==StagingCode::InvalidState); defaults();
    }
    for(const auto bad:{ValmaxMediaStageResult{39,1,8,{}},{40,2,8,{}},{40,1,9,{}}}) {
        auto j=new_job(); response=bad; r=releases.load(); check(prepare(*j.value).code==StagingCode::AbiMismatch && releases==r+1); defaults();
    }
    for(const auto& bad:{std::string("https://upload.wikimedia.org/a.png"),std::string("file:///C:/x\n"),
        std::string("file:///C:/x%GG"),std::string("file:///C:/x%"),std::string("file://server/path"),std::string("file:///C:/фото.png")}) {
        auto j=new_job(); url=bad; check(prepare(*j.value).code==StagingCode::AbiMismatch); defaults();
    }
    { auto j=new_job(); emitUrl=false; check(prepare(*j.value).code==StagingCode::AbiMismatch); defaults(); }
    { auto j=new_job(); stageCode=9; check(prepare(*j.value).code==StagingCode::AbiMismatch); defaults(); }
    { auto j=new_job(); stageThrows=true; const auto f=frees.load(); r=releases.load();
        check(prepare(*j.value).code==StagingCode::InternalError && frees==f+1 && releases==r+1); defaults(); }
    { auto j=new_job(); auto c=j.value->cancellation(); c.signal(); check(prepare(*j.value).code==StagingCode::Cancelled); }
    { auto j=new_job(); auto p=prepare(*j.value); cancelCode=10; check(p.value->cancel()==StagingCode::CleanupFailed); defaults(); }
    { auto j=new_job(); auto p=prepare(*j.value); auto c=j.value->cancellation(); (void)c; // moved owner no longer holds token
        retainCode=9; check(p.value->retain_for_handoff()==StagingCode::Cancelled); defaults(); }
    { auto j=new_job(); auto c=j.value->cancellation(); auto p=prepare(*j.value); c.signal(); cancelCode=10;
        check(p.value->retain_for_handoff()==StagingCode::CleanupFailed); defaults(); }
    { auto j=new_job(); auto c=j.value->cancellation(); stageHook=[c]{(void)c.signal();}; cancelCode=10;
        check(prepare(*j.value).code==StagingCode::CleanupFailed); defaults(); }
    { auto j=new_job(); auto p=prepare(*j.value); retainCode=9; releaseCode=10;
        check(p.value->retain_for_handoff()==StagingCode::CleanupFailed); defaults(); }
    {
        auto left=new_job(); auto right=new_job(); auto old=left.value->cancellation();
        r=releases.load(); *left.value=std::move(*right.value); check(releases==r+1 && old.signal()==StagingCode::InvalidHandle);
        auto a1=prepare(*left.value); auto other=new_job(); auto a2=prepare(*other.value);
        r=releases.load(); *a1.value=std::move(*a2.value); check(releases==r+1 && a2.value->native_file_url().empty());
    }
    // Cancellation owns no raw pointer; independent weak handles can race an
    // I/O-thread release. This stress is not a TSAN/proof of all interleavings.
    for(int i=0;i<128;++i) {
        auto j=new_job(); auto c=j.value->cancellation(); std::barrier start(2);
        std::thread worker([c,&start]{start.arrive_and_wait(); for(int n=0;n<32;++n)(void)c.signal();});
        start.arrive_and_wait(); j.value.reset(); worker.join(); check(c.signal()==StagingCode::InvalidHandle);
    }
    std::cout<<"{\"ok\":true,\"checks\":"<<checks<<",\"realCoreCalled\":false,\"filesystemTouched\":false,\"cancellationReleaseStress\":128}\n";
}
