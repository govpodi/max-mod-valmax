#include "staging_client.h"
#include <windows.h>
#include <algorithm>
#include <atomic>
#include <barrier>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <thread>
#include <vector>
using namespace valmax::media;
namespace fs=std::filesystem;
namespace {
size_t checks=0;
void check(bool good) { if(!good){std::cerr<<"failed_check="<<checks+1<<'\n';std::exit(1);} ++checks; }
StagingApi api() { return {valmax_media_staging_abi_version,valmax_media_staging_begin,valmax_media_staging_stage,
    valmax_media_staging_signal_cancel,valmax_media_staging_cancel,valmax_media_staging_retain,
    valmax_media_staging_release,valmax_core_buffer_free}; }
std::vector<uint8_t> png(size_t n=4096) {
    std::vector<uint8_t> b(n,static_cast<uint8_t>('S'));
    constexpr uint8_t magic[]={137,80,78,71,13,10,26,10};
    std::copy(std::begin(magic),std::end(magic),b.begin());return b;
}
uint64_t now() { return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count()); }
fs::path path_from_url(std::string_view u,const fs::path& root) {
    check(u.starts_with("file:///")); std::string raw;
    const auto digit=[](char c){return c<='9'?c-'0':c-'A'+10;};
    for(size_t i=8;i<u.size();++i) {
        if(u[i]=='%') {check(i+2<u.size());raw.push_back(static_cast<char>(digit(u[i+1])*16+digit(u[i+2])));i+=2;}
        else raw.push_back(u[i]);
    }
    const int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,raw.data(),static_cast<int>(raw.size()),nullptr,0);
    check(n>0);std::wstring wide(static_cast<size_t>(n),L'\0');
    check(MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,raw.data(),static_cast<int>(raw.size()),wide.data(),n)==n);
    fs::path p(wide); check(fs::equivalent(p.parent_path(),root));
    const auto file=p.filename().wstring();check(file.starts_with(L"valmedia-") && file.ends_with(L".png") && file.size()==45);
    return p;
}
size_t files(const fs::path& root) {
    size_t count=0;
    for(const auto& e:fs::directory_iterator(root)) {
        check(e.is_regular_file());
        if(e.path().filename()==L".valmedia.lock")continue;
        const auto n=e.path().filename().wstring();
        check(n.starts_with(L"valmedia-") && n.size()==45 && n.ends_with(L".png"));++count;
    }
    return count;
}
HANDLE open_reader(const fs::path& p) {
    HANDLE h=CreateFileW(p.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    check(h!=INVALID_HANDLE_VALUE);return h;
}
void read_exact(const fs::path& p,const std::vector<uint8_t>& expected) {
    HANDLE h=open_reader(p); std::vector<uint8_t> b(expected.size());DWORD read=0;
    check(ReadFile(h,b.data(),static_cast<DWORD>(b.size()),&read,nullptr) && read==b.size() && b==expected);
    uint8_t tail=0;check(ReadFile(h,&tail,1,&read,nullptr) && read==0);check(CloseHandle(h)!=FALSE);
}
auto new_job(const fs::path& root) { auto r=StagingJob::begin(api(),root.wstring());check(bool(r));return r; }
auto stage(StagingJob& job,const std::vector<uint8_t>& bytes) {
    return job.stage(bytes,StagingMime::Png,bytes.size(),now());
}
}
int wmain(int argc,wchar_t** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    check(argc==3); const fs::path base=fs::canonical(argv[1]);
    const fs::path permitted=fs::canonical(L"C:/ValMAXBuild/fixtures/media-staging-client");
    check(base.parent_path()==permitted);
    const auto id=base.filename().wstring();check(id.size()==32 && std::all_of(id.begin(),id.end(),[](wchar_t c){return(c>=L'0'&&c<=L'9')||(c>=L'a'&&c<=L'f');}));
    std::ifstream marker(base/L".owned");std::string tag;std::getline(marker,tag);check(tag=="valmax-staging-client-owned-v1");
    // Without dllimport decoration, &function can point at this EXE's import
    // thunk. Resolve the already-loaded DLL export and verify its real module.
    const auto imported=GetModuleHandleW(L"valmax_core.dll");check(imported!=nullptr);
    const auto exportAddress=GetProcAddress(imported,"valmax_media_staging_begin");check(exportAddress!=nullptr);
    HMODULE module=nullptr;
    check(GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCWSTR>(exportAddress),&module)!=FALSE);
    std::wstring actual(32768,L'\0');const DWORD length=GetModuleFileNameW(module,actual.data(),static_cast<DWORD>(actual.size()));
    check(length>0 && length<actual.size());actual.resize(length);check(fs::equivalent(actual,argv[2]));
    check(valmax_media_staging_abi_version()==1);
    const fs::path root=base/L"root space-Ю-\U0001f600";check(fs::create_directory(root));
    const auto bytes=png(); const auto t=now();
    {
        auto j=new_job(root); auto cancel=j.value->cancellation();
        auto p=stage(*j.value,bytes);check(bool(p));
        check(p.value->native_file_url().find("%20")!=std::string_view::npos && p.value->native_file_url().find("%D0%AE")!=std::string_view::npos);
        const auto file=path_from_url(p.value->native_file_url(),root);check(p.value->byte_length()==bytes.size());read_exact(file,bytes);
        HANDLE writer=CreateFileW(file.c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
        check(writer==INVALID_HANDLE_VALUE && GetLastError()==ERROR_SHARING_VIOLATION);
        check(!DeleteFileW(file.c_str()) && GetLastError()==ERROR_SHARING_VIOLATION);
        auto lease=std::move(*p.value);check(p.value->native_file_url().empty());
        check(lease.cancel()==StagingCode::Ok && !fs::exists(file));check(cancel.signal()==StagingCode::InvalidHandle);
    }
    check(files(root)==0);
    {
        fs::path file;
        {auto j=new_job(root);auto p=stage(*j.value,bytes);check(bool(p));file=path_from_url(p.value->native_file_url(),root);}
        check(!fs::exists(file));
    }
    {
        auto j=new_job(root);auto cancel=j.value->cancellation();
        std::thread worker([cancel]{(void)cancel.signal();});worker.join();
        check(stage(*j.value,bytes).code==StagingCode::Cancelled);check(j.value->cancel()==StagingCode::Ok);check(files(root)==0);
    }
    {
        auto j=new_job(root);auto cancel=j.value->cancellation();auto p=stage(*j.value,bytes);check(bool(p));
        auto file=path_from_url(p.value->native_file_url(),root);
        std::atomic<uint32_t> status{99};std::thread worker([&]{status=static_cast<uint32_t>(cancel.signal());});worker.join();
        check(status==0 && p.value->cancellation_requested() && fs::exists(file));
        check(p.value->retain_for_handoff()==StagingCode::Cancelled && !fs::exists(file));
    }
    {
        auto j=new_job(root);auto p=stage(*j.value,bytes);check(bool(p));auto file=path_from_url(p.value->native_file_url(),root);
        HANDLE reader=open_reader(file);check(p.value->cancel()==StagingCode::CleanupFailed);
        check(fs::exists(file));check(CloseHandle(reader)!=FALSE);
        // Only this exact synthetic orphan is explicitly removed by the test.
        check(DeleteFileW(file.c_str())!=FALSE);
    }
    {
        auto j=new_job(root);auto cancel=j.value->cancellation();auto p=stage(*j.value,bytes);check(bool(p));
        auto file=path_from_url(p.value->native_file_url(),root);
        check(p.value->retain_for_handoff()==StagingCode::Ok);p.value.reset();j.value.reset();
        check(fs::exists(file));read_exact(file,bytes);check(cancel.signal()==StagingCode::InvalidHandle);
        HANDLE writer=CreateFileW(file.c_str(),GENERIC_WRITE,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
        check(writer!=INVALID_HANDLE_VALUE);check(CloseHandle(writer)!=FALSE);
        auto later=new_job(root);
        auto fresh=later.value->stage(bytes,StagingMime::Png,bytes.size(),now()+24*60*60+2);
        check(bool(fresh) && !fs::exists(file));check(fresh.value->cancel()==StagingCode::Ok);
    }
    {
        auto j=new_job(root);const std::vector<uint8_t> invalid(8,'x');
        check(stage(*j.value,invalid).code==StagingCode::UnsupportedMedia);
        check(stage(*j.value,bytes).code==StagingCode::InvalidState);check(j.value->cancel()==StagingCode::Ok);check(files(root)==0);
    }
    check(StagingJob::begin(api(),root.wstring()+L"/missing").code==StagingCode::Unavailable);
    check(StagingJob::begin(api(),L"C:/ValMAXBuild/../unsafe").code==StagingCode::UnsafePath);
    // Real token capacity, releasing the C++ owners recovers all 64 slots.
    {
        std::vector<StagingJob> jobs;jobs.reserve(64);
        for(int i=0;i<64;++i){auto j=new_job(root);jobs.push_back(std::move(*j.value));}
        check(StagingJob::begin(api(),root.wstring()).code==StagingCode::HandleLimit);
        jobs.clear();auto j=new_job(root);check(j.value->cancel()==StagingCode::Ok);
    }
    // Independent copied cancellation signals against a synchronous actual
    // Rust stage. Scheduling decides whether cancellation is seen before or
    // during its filesystem work; no deterministic fault checkpoint is claimed.
    size_t racedCancelled=0,racedPrepared=0;
    const auto large=png(1024*1024);
    for(int i=0;i<48;++i) {
        auto j=new_job(root);auto cancel=j.value->cancellation();std::barrier start(2);
        std::thread worker([&]{start.arrive_and_wait();for(int n=0;n<8;++n)(void)cancel.signal();});
        start.arrive_and_wait();auto p=stage(*j.value,large);worker.join();
        if(p) {++racedPrepared;check(p.value->cancel()==StagingCode::Ok);}
        else {++racedCancelled;check(p.code==StagingCode::Cancelled);check(j.value->cancel()==StagingCode::Ok);}
        check(files(root)==0);
    }
    check(t>0 && files(root)==0);
    check(fs::remove(root/L".valmedia.lock"));check(fs::remove(root));
    std::cout<<"{\"ok\":true,\"checks\":"<<checks<<",\"realCoreCalled\":true,\"actualDllPathVerified\":true,"
        "\"preparedLeaseReadOnly\":true,\"cancelDropRemoved\":true,\"retainKeptFile\":true,\"retainedTtlCleanup\":true,\"heldReaderCleanupFailure\":true,"
        "\"registryCapacity\":64,\"cancelStageRaces\":48,\"raceCancelled\":"<<racedCancelled<<",\"racePrepared\":"<<racedPrepared
        <<",\"remainingMediaFiles\":0,\"fixtureRootRemoved\":true,\"realProfilesTouched\":false,\"draftAttachmentProven\":false}\n";
}
