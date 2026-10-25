#include "notification_internal.h"
#include "notification_pins.generated.h"
#include "hook_set.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <cstring>
#include <iostream>
#include <filesystem>
#include <fstream>
#include <thread>
#include <vector>
using namespace valmax::notifications;
namespace {
struct alignas(8) Text { char buffer[16]{}; uint64_t size=0,capacity=15; };
struct alignas(8) Data { std::array<uint8_t,328> raw{}; };
std::atomic<uint64_t> allocations{0},frees{0},headers{0},calls{0},masked{0};
std::atomic<uint64_t> setter_calls{0};
std::atomic<bool> hold{false},entered{false},released{false},bad{false},fail_destroy{false},fail_copy{false};
std::atomic<bool> after_flush_called{false},after_flush_suppressed{false};
std::atomic<void *> expected_original{nullptr};
std::atomic<uint8_t> last_flag{0};
const char *text(const Text &s) { if(s.capacity<=15)return s.buffer; const char *p=nullptr;std::memcpy(&p,s.buffer,sizeof(p));return p; }
void make_text(Text &out,const char *value) {
    out={};out.size=std::strlen(value);
    if(out.size<=15)std::memcpy(out.buffer,value,static_cast<size_t>(out.size)+1);
    else {out.capacity=out.size;auto p=HeapAlloc(GetProcessHeap(),0,static_cast<SIZE_T>(out.size)+1);if(!p)std::terminate();
        std::memcpy(p,value,static_cast<size_t>(out.size)+1);std::memcpy(out.buffer,&p,sizeof(p));++allocations;}
}
__declspec(noinline) void copy_text(void *output,const void *input) {
    if(fail_copy.exchange(false))RaiseException(0xe0000001,0,0,nullptr);
    make_text(*static_cast<Text *>(output),text(*static_cast<const Text *>(input)));
}
__declspec(noinline) void destroy_text(void *input) {
    const bool fail=fail_destroy.exchange(false);
    auto &s=*static_cast<Text *>(input);if(s.capacity>15){void *p=nullptr;std::memcpy(&p,s.buffer,sizeof(p));HeapFree(GetProcessHeap(),0,p);++frees;}s={};
    if(fail)RaiseException(0xe0000002,0,0,nullptr);
}
__declspec(noinline) void copy_data(void *output,const void *input) {
    std::memcpy(output,input,328);
    for(auto offset:pins::kStringOffsets)copy_text(static_cast<uint8_t *>(output)+offset,static_cast<const uint8_t *>(input)+offset);
}
__declspec(noinline) void destroy_header(void *) {++headers;}
void inspect(void *input,size_t which) {
    ++calls;
    if(hold.load()){entered.store(true);while(!released.load())std::this_thread::yield();}
    const auto &data=*static_cast<Data *>(input);
    const auto &title=*reinterpret_cast<const Text *>(data.raw.data()+192);
    if(std::strcmp(text(title),"ValMAX")==0){++masked;
        if(std::strcmp(text(*reinterpret_cast<const Text *>(data.raw.data()+224)),"")
            || std::strcmp(text(*reinterpret_cast<const Text *>(data.raw.data()+288)),"C:\\ValMAXBuild\\synthetic-transparent.png"))bad.store(true);
    }else if(input!=expected_original.load())bad.store(true);
    if(which>5)bad.store(true);
}
__declspec(noinline) void display(void *,void *data){inspect(data,0);}
__declspec(noinline) void edit(void *,void *data){inspect(data,1);}
__declspec(noinline) void native_display(void *,void *data){inspect(data,2);}
__declspec(noinline) void native_edit(void *,void *data){inspect(data,3);}
__declspec(noinline) void qt_display(void *,void *data,uint8_t flag){last_flag.store(flag);inspect(data,4);}
__declspec(noinline) void qt_edit(void *,void *data,uint8_t flag){last_flag.store(flag);inspect(data,5);}
__declspec(noinline) void setter(void *self){static_cast<volatile uint8_t *>(self)[0]=7;setter_calls.fetch_add(1);std::atomic_signal_fence(std::memory_order_seq_cst);}
__declspec(noinline) void metacall(void *self,int call,int index){static_cast<uint8_t *>(self)[8]=static_cast<uint8_t>(call+index);std::atomic_signal_fence(std::memory_order_seq_cst);}
Targets targets(){return {{reinterpret_cast<void *>(&display),reinterpret_cast<void *>(&edit),reinterpret_cast<void *>(&native_display),reinterpret_cast<void *>(&native_edit),reinterpret_cast<void *>(&qt_display),reinterpret_cast<void *>(&qt_edit)},
    {reinterpret_cast<void *>(&setter),reinterpret_cast<void *>(&metacall)},reinterpret_cast<void *>(&copy_data),reinterpret_cast<void *>(&copy_text),reinterpret_cast<void *>(&destroy_text),reinterpret_cast<void *>(&destroy_header),0x123456780,0x876543210};}
void invoke(size_t index,Data &data){expected_original=&data;const auto t=targets();
    if(index<4){using F=void(*)(void *,void *);F volatile f=reinterpret_cast<F>(t.replacements[index]);f(nullptr,&data);}
    else{using F=void(*)(void *,void *,uint8_t);F volatile f=reinterpret_cast<F>(t.replacements[index]);f(nullptr,&data,0xa7);}}
Data data(uint64_t native,int64_t chat){Data out;auto t=targets();std::memcpy(out.raw.data()+16,&chat,8);std::memcpy(out.raw.data()+24,&native,8);std::memcpy(out.raw.data()+320,&native,8);std::memcpy(out.raw.data()+56,&t.marker,8);
    for(auto offset:pins::kStringOffsets)make_text(*reinterpret_cast<Text *>(out.raw.data()+offset),"synthetic notification text long enough for heap");return out;}
void clear(Data &d){for(auto offset:pins::kStringOffsets)destroy_text(d.raw.data()+offset);}
void on_after_flush(){
    auto first=data(1,17);const auto before=calls.load();invoke(0,first);
    after_flush_suppressed.store(calls.load()==before);after_flush_called.store(true);clear(first);
}
uint64_t generation=0;
ValmaxNotificationPolicy policy(std::span<const ValmaxNotificationScope> scopes){ValmaxNotificationPolicy out{};out.size=sizeof(out);out.version=1;out.generation=++generation;out.ready=1;out.lease_ms=5000;out.count=static_cast<uint32_t>(scopes.size());
    if(!revision(scopes.data(),scopes.size(),out.revision))std::terminate();return out;}
uint32_t apply(std::span<const ValmaxNotificationScope> scopes){auto p=policy(scopes);return valmax_notifications_set_policy(&p,scopes.data());}
ValmaxNotificationStatus status(){ValmaxNotificationStatus s{};s.size=sizeof(s);s.version=1;if(valmax_notifications_status(&s))std::terminate();return s;}
int checks=0;bool check(bool ok){++checks;if(!ok)std::cerr<<"failed_check="<<checks<<'\n';return ok;}
#define CHECK(x) do{if(!check(x))return 1;}while(false)
}
int main(){
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    const auto t=targets();constexpr uint64_t native=0xfedcba9876543210ULL;constexpr int64_t owner=9007199254741993LL,chat=-9007199254742001LL;
    std::array<ValmaxNotificationScope,1> scopes{{{owner,chat}}};
    CHECK(sizeof(ValmaxNotificationPolicy)==96&&sizeof(ValmaxNotificationStatus)==144);
    set_synthetic_after_flush_probe(&on_after_flush);
    CHECK(install_synthetic(t,"C:\\ValMAXBuild\\synthetic-transparent.png")==0);
    set_synthetic_after_flush_probe(nullptr);
    CHECK(after_flush_called.load()&&after_flush_suppressed.load());
    CHECK(status().ready&&!status().applied&&status().replacements==6&&status().listeners==2);
    auto protected_data=data(native,chat);auto ordinary=data(native,17);auto other=data(native+1,chat);auto unknown=data(native+2,chat);
    invoke(0,ordinary);CHECK(calls==0&&status().suppressed==2); // no initial catalogue, no leaked preview
    CHECK(apply(scopes)==0);synthetic_remember(native,owner);synthetic_remember(native+1,owner+1);
    CHECK(status().applied);
    const auto original_bytes=protected_data.raw;
    for(size_t i=0;i<6;++i)invoke(i,protected_data);
    CHECK(calls==6&&masked==6&&!bad&&last_flag==0xa7);
    CHECK(protected_data.raw==original_bytes&&headers==6);
    invoke(0,ordinary);invoke(1,other);CHECK(calls==8&&masked==6);
    invoke(0,unknown);CHECK(calls==8&&status().scope_unknown>=1);
    synthetic_remember(native+1,owner);invoke(0,other);CHECK(calls==8); // collision never maps either owner
    auto unrelated_unknown=data(native+99,18);invoke(0,unrelated_unknown);CHECK(calls==8);
    synthetic_clock(5002);CHECK(!status().applied&&status().error==VALMAX_NOTIFICATION_LEASE_EXPIRED);
    invoke(0,protected_data);invoke(0,ordinary);CHECK(calls==10&&masked==7); // no expired global mask
    auto invalid=policy(scopes);invalid.revision[0]^=1;
    CHECK(valmax_notifications_set_policy(&invalid,scopes.data())==VALMAX_NOTIFICATION_INVALID_POLICY);
    invoke(0,protected_data);invoke(0,ordinary);CHECK(calls==12&&masked==8&&!status().applied);
    auto unavailable=policy({});unavailable.ready=0;CHECK(valmax_notifications_set_policy(&unavailable,nullptr)==VALMAX_NOTIFICATION_CATALOG_UNAVAILABLE);
    invoke(0,protected_data);CHECK(masked==9);
    CHECK(apply(scopes)==0);auto stale=policy(scopes);stale.generation=1;CHECK(valmax_notifications_set_policy(&stale,scopes.data())==VALMAX_NOTIFICATION_INVALID_POLICY);
    CHECK(apply(scopes)==0&&status().applied);
    // borrowed scopes copied before return
    auto borrowed=scopes;auto borrowed_policy=policy(borrowed);CHECK(valmax_notifications_set_policy(&borrowed_policy,borrowed.data())==0);borrowed[0].chat=19;
    invoke(0,protected_data);CHECK(masked==10);
    // Exact native setter and metacall listener paths, including high uint64 IDs.
    alignas(8) std::array<uint8_t,80> identity{};std::memcpy(identity.data(),&t.identity_vtable,8);std::memcpy(identity.data()+40,&owner,8);
    uint64_t new_native=native-2;std::memcpy(identity.data()+56,&new_native,8);
    using Setter=void(*)(void *);Setter volatile set=&setter;set(identity.data()+16);
    auto setter_data=data(new_native,chat);invoke(0,setter_data);CHECK(masked==11);
    new_native=native-3;std::memcpy(identity.data()+56,&new_native,8);
    using Meta=void(*)(void *,int,int);Meta volatile meta=&metacall;meta(identity.data(),1,0);
    auto meta_data=data(new_native,chat);const auto before=calls.load();invoke(0,meta_data);CHECK(calls==before);
    meta(identity.data(),1,1);invoke(0,meta_data);CHECK(calls==before+1&&masked==12);
    // Read identity and data guards cannot fabricate a scope.
    auto invalid_marker=ordinary;invalid_marker.raw[56]^=1;const auto guard_before=calls.load();invoke(0,invalid_marker);CHECK(calls==guard_before);
    invalid_marker=ordinary;invalid_marker.raw[320]^=1;invoke(0,invalid_marker);CHECK(calls==guard_before);
    // Policy ACK waits for callbacks admitted under the previous decision.
    hold.store(true);released.store(false);entered.store(false);
    std::thread worker([&]{invoke(0,ordinary);});while(!entered.load())std::this_thread::yield();
    const bool accepted=apply(scopes)==0;const auto busy_status=status();
    released.store(true);worker.join();hold.store(false);
    CHECK(accepted&&!busy_status.applied&&busy_status.in_flight==1&&status().applied);
    // Shutdown and reinstall cannot invalidate a live original trampoline.
    hold.store(true);released.store(false);entered.store(false);
    std::thread pending([&]{invoke(0,ordinary);});while(!entered.load())std::this_thread::yield();
    const auto stop=valmax_notifications_shutdown();const auto retry=install_synthetic(t,"C:\\ValMAXBuild\\synthetic-transparent.png");
    released.store(true);pending.join();hold.store(false);
    CHECK(stop==VALMAX_NOTIFICATION_BUSY&&retry==VALMAX_NOTIFICATION_BUSY);
    CHECK(valmax_notifications_shutdown()==0&&!status().ready);
    CHECK(install_synthetic(t,"C:\\ValMAXBuild\\synthetic-transparent.png")==0&&apply(scopes)==0);
    // Only a fresh ready policy can intentionally remove all PIN scopes.
    CHECK(apply({})==0);const auto mask_before=masked.load();invoke(0,protected_data);CHECK(masked==mask_before);
    CHECK(apply(scopes)==0);
    // Invalid MSVC string layout suppresses protected data and revokes applied.
    auto invalid_string=protected_data;uint64_t huge=UINT64_MAX;std::memcpy(invalid_string.raw.data()+192+16,&huge,8);
    const auto invalid_before=calls.load();invoke(0,invalid_string);CHECK(calls==invalid_before&&!status().applied&&status().faults>0);
    invoke(0,ordinary);CHECK(calls==invalid_before+1);
    CHECK(valmax_notifications_shutdown()==0);
    // Failed install after one successful replacement rolls back without
    // affecting an independently owned hook or publishing partial readiness.
    valmax::hooks::HookSet owner_hook;void *owner_original=nullptr;
    std::array<uint8_t,24> edit_code{};std::memcpy(edit_code.data(),t.replacements[1],edit_code.size());
    valmax::hooks::Replacement conflict{{t.replacements[1],edit_code},reinterpret_cast<void *>(&display),&owner_original};
    CHECK(owner_hook.install(std::span(&conflict,1))==valmax::hooks::Error::None);
    CHECK(install_synthetic(t,"C:\\ValMAXBuild\\synthetic-transparent.png")==VALMAX_NOTIFICATION_HOOK_FAILED);
    CHECK(!status().ready&&status().replacements==0&&status().listeners==0&&owner_hook.size()==1);
    CHECK(owner_hook.reset());
    CHECK(install_synthetic(t,"C:\\ValMAXBuild\\synthetic-transparent.png")==0&&apply(scopes)==0);
    // One original remains in-flight while independent callbacks and policy
    // revisions run concurrently; no old decision is acknowledged as applied.
    std::atomic<bool> concurrent_ok{true};std::vector<std::thread> workers;
    for(int i=0;i<4;++i)workers.emplace_back([&]{for(int j=0;j<200;++j)invoke(0,ordinary);});
    for(int i=0;i<100;++i){if(apply(scopes)!=0)concurrent_ok=false;auto s=status();if(s.in_flight&&s.applied)concurrent_ok=false;}
    for(auto &thread:workers)thread.join();CHECK(concurrent_ok&&!bad&&status().applied);
    // A native allocation/copy fault cannot fall through with the raw message.
    fail_copy=true;const auto copy_before=calls.load();invoke(0,protected_data);
    CHECK(calls==copy_before&&status().error==VALMAX_NOTIFICATION_CLONE_FAILED&&!status().applied);
    CHECK(valmax_notifications_shutdown()==0);
    CHECK(install_synthetic(t,"C:\\ValMAXBuild\\synthetic-transparent.png")==0&&apply(scopes)==0);
    // Destructor can fail after freeing its field. That uncertain field must
    // never be destroyed twice; the other three strings/header still clean up.
    fail_destroy=true;const auto cleanup_before=calls.load();invoke(0,protected_data);
    CHECK(calls==cleanup_before&&status().error==VALMAX_NOTIFICATION_CLEANUP_FAILED&&!status().applied);
    CHECK(valmax_notifications_shutdown()==0);
    // Independent owner limits and strict decimal sorting/canonical hashing.
    OwnerMap map;for(size_t i=0;i<kOwners+1;++i)map.remember(i,static_cast<int64_t>(i)+1);
    CHECK(map.overflow&&map.resolve(kOwners)==0&&map.resolve(0)==1);map.remember(0,22);CHECK(map.resolve(0)==0);
    PolicyState model;auto p=policy(scopes);CHECK(model.update(p,scopes.data(),1)==0);
    p.generation=++generation;p.count=257;CHECK(model.update(p,scopes.data(),1)==VALMAX_NOTIFICATION_INVALID_POLICY&&model.known);
    std::array<ValmaxNotificationScope,2> unsorted{{{2,1},{10,1}}};char hash[64];CHECK(!revision(unsorted.data(),2,hash));
    unsorted={{{10,1},{2,1}}};CHECK(revision(unsorted.data(),2,hash));
    std::array<ValmaxNotificationScope,1> zero_chat{{{owner,0}}};auto zero_policy=policy(zero_chat);
    CHECK(model.update(zero_policy,zero_chat.data(),1)==0);model.owners.remember(native,owner);
    CHECK(model.select(true,native,0)==Decision::Mask);
    std::array<ValmaxNotificationScope,1> minimum_chat{{{owner,INT64_MIN}}};CHECK(revision(minimum_chat.data(),1,hash));
    auto zero=policy({});zero.generation=++generation;zero.lease_ms=5001;CHECK(model.update(zero,nullptr,1)==VALMAX_NOTIFICATION_INVALID_POLICY);
    clear(protected_data);clear(ordinary);clear(other);clear(unknown);clear(unrelated_unknown);clear(setter_data);clear(meta_data);
    CHECK(allocations==frees&&!bad);
    // File guards accept only the pinned fixed PNG, hold its file/ancestors,
    // and reject byte changes, hard links and non-program filenames.
    auto directory=std::filesystem::path(L"C:/ValMAXBuild/fixtures")/(L"notification-assets-"+std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(GetTickCount64()));
    std::filesystem::create_directories(directory);auto image=directory/pins::kAssetName;auto main_path=directory/L"max.exe";
    {std::ofstream file(image,std::ios::binary);file.write(reinterpret_cast<const char *>(pins::kAsset),sizeof(pins::kAsset));}
    Asset asset;CHECK(validate_asset(image.c_str(),main_path.c_str(),asset));
    HANDLE writer=CreateFileW(image.c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,0,nullptr);
    CHECK(writer==INVALID_HANDLE_VALUE);if(writer!=INVALID_HANDLE_VALUE)CloseHandle(writer);asset.release();
    auto hardlink=directory/L"extra.png";CHECK(CreateHardLinkW(hardlink.c_str(),image.c_str(),nullptr)!=0);
    CHECK(!validate_asset(image.c_str(),main_path.c_str(),asset));asset.release();CHECK(DeleteFileW(hardlink.c_str())!=0);
    {std::fstream file(image,std::ios::binary|std::ios::in|std::ios::out);file.put('x');}
    CHECK(!validate_asset(image.c_str(),main_path.c_str(),asset));asset.release();
    CHECK(!validate_asset((directory/L"user.png").c_str(),main_path.c_str(),asset));asset.release();
    std::cout<<"{\"schema\":1,\"ok\":true,\"checks\":"<<checks<<",\"scope\":\"synthetic notification buffers only\",\"allocationsBalanced\":true}\n";
}
