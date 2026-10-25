#include "closed_clients.h"
#include <windows.h>
#include <tlhelp32.h>
#include <bcrypt.h>
#include <algorithm>
#include <array>
#include <filesystem>
#include <mutex>
#include <utility>

namespace valmax::transfer {
namespace {
struct Handle {
    HANDLE value=nullptr;
    explicit Handle(HANDLE h=nullptr):value(h){}
    ~Handle(){if(value&&value!=INVALID_HANDLE_VALUE)CloseHandle(value);}
    Handle(const Handle&)=delete;Handle& operator=(const Handle&)=delete;
    Handle(Handle&&other) noexcept:value(std::exchange(other.value,nullptr)){}
    Handle& operator=(Handle&&other) noexcept {
        if(this!=&other){if(value&&value!=INVALID_HANDLE_VALUE)CloseHandle(value);value=std::exchange(other.value,nullptr);}return *this;
    }
    explicit operator bool() const noexcept{return value&&value!=INVALID_HANDLE_VALUE;}
};
bool equal(std::wstring_view a,std::wstring_view b) noexcept {
    return a.size()==b.size() && a.size()<32768 && CompareStringOrdinal(a.data(),static_cast<int>(a.size()),
        b.data(),static_cast<int>(b.size()),TRUE)==CSTR_EQUAL;
}
ScanCode control_code(const ScanControl &control) noexcept {
    if(control.cancelled&&control.cancelled->load(std::memory_order_acquire))return ScanCode::Cancelled;
    return GetTickCount64()>=control.deadline_ms?ScanCode::Deadline:ScanCode::Complete;
}
CandidateDisposition stopped(ScanCode code) noexcept {
    return code==ScanCode::Cancelled?CandidateDisposition::Cancelled:
        code==ScanCode::Deadline?CandidateDisposition::Deadline:CandidateDisposition::Unknown;
}
bool component(std::wstring_view part) {
    if(part.empty()||part==L"."||part==L".."||part.back()==L'.'||part.back()==L' ')return false;
    for(auto c:part)if(c<32||c==127||std::wstring_view(L":<>\"|?*\\/").find(c)!=std::wstring_view::npos)return false;
    const auto stem=part.substr(0,part.find(L'.'));
    if(equal(stem,L"CON")||equal(stem,L"PRN")||equal(stem,L"AUX")||equal(stem,L"NUL"))return false;
    return !(stem.size()==4&&(equal(stem.substr(0,3),L"COM")||equal(stem.substr(0,3),L"LPT"))&&stem[3]>=L'1'&&stem[3]<=L'9');
}
bool image_path(std::wstring_view input,std::wstring_view name,std::wstring &path) {
    if(input.size()<4||input.size()>32000||input.find(L'\0')!=input.npos||!max_process_name(name))return false;
    if(input[1]!=L':'||!((input[0]>=L'A'&&input[0]<=L'Z')||(input[0]>=L'a'&&input[0]<=L'z'))
        ||(input[2]!=L'/'&&input[2]!=L'\\'))return false;
    path.assign(input);std::replace(path.begin(),path.end(),L'/',L'\\');
    size_t at=3;
    while(at<path.size()) {
        const auto end=path.find(L'\\',at);const auto part=std::wstring_view(path).substr(at,end==path.npos?path.size()-at:end-at);
        if(!component(part))return false;
        if(end==path.npos)return equal(part,name);
        at=end+1;
    }
    return false;
}
bool final_path(HANDLE file,const std::wstring &expected) {
    std::array<wchar_t,32768> buffer{};
    const DWORD size=GetFinalPathNameByHandleW(file,buffer.data(),static_cast<DWORD>(buffer.size()),FILE_NAME_NORMALIZED|VOLUME_NAME_DOS);
    if(!size||size>=buffer.size())return false;
    std::wstring_view actual(buffer.data(),size);if(actual.starts_with(L"\\\\?\\"))actual.remove_prefix(4);
    return equal(actual,expected);
}
bool hold_parents(const std::wstring &image,std::vector<Handle>&pins) {
    std::vector<std::wstring> paths;
    for(auto path=std::filesystem::path(image).parent_path();!path.empty();path=path.parent_path()) {
        paths.push_back(path.wstring());if(paths.size()>256)return false;
        if(path==path.root_path())break;
    }
    if(paths.empty()||GetDriveTypeW(paths.back().c_str())!=DRIVE_FIXED)return false;
    for(auto it=paths.rbegin();it!=paths.rend();++it) {
        Handle file(CreateFileW(it->c_str(),FILE_LIST_DIRECTORY|FILE_READ_ATTRIBUTES,FILE_SHARE_READ|FILE_SHARE_WRITE,
            nullptr,OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_BACKUP_SEMANTICS,nullptr));
        BY_HANDLE_FILE_INFORMATION info{};
        if(!file||GetFileType(file.value)!=FILE_TYPE_DISK||!GetFileInformationByHandle(file.value,&info)
            ||!(info.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)||(info.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT)
            ||!final_path(file.value,*it))return false;
        pins.push_back(std::move(file));
    }
    return true;
}
struct Identity {
    uint64_t volume=0,size=0;std::array<uint8_t,16> id{};
    int64_t write=0,change=0;DWORD attributes=0,links=0;
    bool operator==(const Identity&) const=default;
};
bool identity(HANDLE file,const std::wstring &path,Identity &value) {
    BY_HANDLE_FILE_INFORMATION info{};FILE_BASIC_INFO basic{};FILE_ID_INFO id{};
    if(GetFileType(file)!=FILE_TYPE_DISK||!GetFileInformationByHandle(file,&info)
        ||!GetFileInformationByHandleEx(file,FileBasicInfo,&basic,sizeof(basic))
        ||!GetFileInformationByHandleEx(file,FileIdInfo,&id,sizeof(id))
        ||info.dwFileAttributes&(FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT)
        ||info.nNumberOfLinks!=1||!final_path(file,path))return false;
    value.volume=id.VolumeSerialNumber;std::copy(std::begin(id.FileId.Identifier),std::end(id.FileId.Identifier),value.id.begin());
    value.size=(uint64_t(info.nFileSizeHigh)<<32)|info.nFileSizeLow;
    value.write=basic.LastWriteTime.QuadPart;value.change=basic.ChangeTime.QuadPart;
    value.attributes=info.dwFileAttributes;value.links=info.nNumberOfLinks;return true;
}
struct Pin {std::wstring_view name;uint64_t exe_size;const char *exe_hash;uint64_t config_size;const char *config_hash;};
// Exact ORIGINAL_IMAGES from modkit/max_client_policy.py; a newer official
// version is deliberately a potential client until its pair is reviewed.
constexpr std::array<Pin,2> originals{{
    {L"max.exe",79444816,"d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182",875856,
        "8e7d9d53f77d0f2599ed2bb5b117b3c8a4256ee361434ebd3d977b01d4685035"},
    {L"max-service.exe",123454288,"bd82b3fa0a6d24ee87bb9e0ec32ee311df7c40d8d233a71537685af00d50e0eb",875856,
        "950531233749920b60fdcb05a0465eb84f8f2acf3fbb76fc4d1279cb67f714f3"}
}};
struct CacheEntry {Identity identity;uint32_t pin=0;bool match=false;};
std::mutex cache_mutex;
std::vector<CacheEntry> cache; // 64 finite metadata-only records, never paths.
bool cached(const Identity &value,uint32_t pin,bool &match) {
    std::lock_guard lock(cache_mutex);
    const auto found=std::find_if(cache.begin(),cache.end(),[&](const auto &entry){return entry.pin==pin&&entry.identity==value;});
    if(found==cache.end())return false;
    const auto entry=*found;cache.erase(found);cache.push_back(entry);match=entry.match;return true;
}
void remember(const Identity &value,uint32_t pin,bool match) {
    std::lock_guard lock(cache_mutex);
    const auto found=std::find_if(cache.begin(),cache.end(),[&](const auto &entry){return entry.pin==pin&&entry.identity==value;});
    if(found!=cache.end())cache.erase(found);
    if(cache.size()>=64)cache.erase(cache.begin());cache.push_back({value,pin,match});
}
struct Hash {
    BCRYPT_ALG_HANDLE algorithm=nullptr;BCRYPT_HASH_HANDLE hash=nullptr;
    ~Hash(){if(hash)BCryptDestroyHash(hash);if(algorithm)BCryptCloseAlgorithmProvider(algorithm,0);}
};
bool matches(HANDLE file,const std::wstring &path,const Identity &before,uint64_t size,const char *expected,
             uint32_t pin,const ScanControl &control) {
    if(before.size!=size||control_code(control)!=ScanCode::Complete)return false;
    bool match=false;Identity after{};
    if(cached(before,pin,match))return match&&identity(file,path,after)&&after==before&&control_code(control)==ScanCode::Complete;
    Hash hash;
    if(BCryptOpenAlgorithmProvider(&hash.algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0
        ||BCryptCreateHash(hash.algorithm,&hash.hash,nullptr,0,nullptr,0,0)<0)return false;
    std::array<uint8_t,65536> buffer{};uint64_t remaining=size;
    while(remaining) {
        if(control_code(control)!=ScanCode::Complete)return false;
        const auto wanted=static_cast<DWORD>(std::min<uint64_t>(remaining,buffer.size()));DWORD count=0;
        if(!ReadFile(file,buffer.data(),wanted,&count,nullptr)||!count||count>wanted
            ||BCryptHashData(hash.hash,buffer.data(),count,0)<0)return false;
        remaining-=count;
    }
    DWORD count=0;std::array<uint8_t,32> digest{};
    if(control_code(control)!=ScanCode::Complete||!ReadFile(file,buffer.data(),1,&count,nullptr)||count
        ||!identity(file,path,after)||!(after==before)
        ||BCryptFinishHash(hash.hash,digest.data(),static_cast<ULONG>(digest.size()),0)<0)return false;
    constexpr char hex[]="0123456789abcdef";match=true;
    for(size_t i=0;i<digest.size();++i)match=match&&expected[2*i]==hex[digest[i]>>4]&&expected[2*i+1]==hex[digest[i]&15];
    remember(before,pin,match);return match&&control_code(control)==ScanCode::Complete;
}
bool process_path(HANDLE process,std::wstring &path) {
    std::array<wchar_t,32768> buffer{};DWORD length=static_cast<DWORD>(buffer.size());
    if(!QueryFullProcessImageNameW(process,0,buffer.data(),&length)||!length||length>=buffer.size())return false;
    path.assign(buffer.data(),length);return path.find(L'\0')==path.npos;
}
class WindowsSource final:public ScanSource {
public:
    ScanCode snapshot(std::vector<ProcessEntry> &entries,const ScanControl &control) override {
        Handle snapshot(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0));if(!snapshot)return ScanCode::Incomplete;
        PROCESSENTRY32W entry{};entry.dwSize=sizeof(entry);SetLastError(ERROR_SUCCESS);
        bool valid=Process32FirstW(snapshot.value,&entry)!=FALSE;
        if(!valid)return GetLastError()==ERROR_NO_MORE_FILES?ScanCode::Complete:ScanCode::Incomplete;
        for(;;) {
            const auto stop=control_code(control);if(stop!=ScanCode::Complete)return stop;
            if(entries.size()>=kMaximumProcessEntries||entry.dwSize!=sizeof(entry))return ScanCode::Incomplete;
            const auto end=std::find(std::begin(entry.szExeFile),std::end(entry.szExeFile),L'\0');
            if(end==std::end(entry.szExeFile)||end==std::begin(entry.szExeFile))return ScanCode::Incomplete;
            entries.push_back({entry.th32ProcessID,std::wstring(entry.szExeFile,end)});
            entry={};entry.dwSize=sizeof(entry);SetLastError(ERROR_SUCCESS);
            if(!Process32NextW(snapshot.value,&entry))return GetLastError()==ERROR_NO_MORE_FILES?ScanCode::Complete:ScanCode::Incomplete;
        }
    }
    CandidateDisposition classify(const ProcessEntry &entry,const ScanControl &control) override {
        Handle process(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,FALSE,entry.pid));
        if(!process||GetProcessId(process.value)!=entry.pid||WaitForSingleObject(process.value,0)!=WAIT_TIMEOUT)return CandidateDisposition::Unknown;
        std::wstring path,normalized;
        if(!process_path(process.value,path)||!image_path(path,entry.name,normalized))return CandidateDisposition::Unknown;
        const auto disposition=original_image(normalized,entry.name,control);
        if(disposition==CandidateDisposition::Cancelled||disposition==CandidateDisposition::Deadline)return disposition;
        std::wstring after,checked;
        if(WaitForSingleObject(process.value,0)!=WAIT_TIMEOUT||!process_path(process.value,after)
            ||!image_path(after,entry.name,checked)||!equal(checked,normalized))return CandidateDisposition::Unknown;
        return disposition;
    }
};
}
bool max_process_name(std::wstring_view name) noexcept {return equal(name,L"max.exe")||equal(name,L"max-service.exe");}
CandidateDisposition original_image(std::wstring_view input,std::wstring_view name,const ScanControl &control) noexcept {
    try {
        auto stop=control_code(control);if(stop!=ScanCode::Complete)return stopped(stop);
        std::wstring path;if(!image_path(input,name,path))return CandidateDisposition::Unknown;
        const auto selected=equal(name,originals[0].name)?0u:1u;const auto &pin=originals[selected];
        std::vector<Handle> parents;if(!hold_parents(path,parents))return CandidateDisposition::PotentialClient;
        const auto config=(std::filesystem::path(path).parent_path()/L"config.dll").wstring();
        Handle executable(CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_SEQUENTIAL_SCAN,nullptr));
        Handle configuration(CreateFileW(config.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_SEQUENTIAL_SCAN,nullptr));
        Identity a{},b{},last_a{},last_b{};
        const bool verified=executable&&configuration&&identity(executable.value,path,a)&&identity(configuration.value,config,b)
            &&matches(executable.value,path,a,pin.exe_size,pin.exe_hash,selected*2,control)
            &&matches(configuration.value,config,b,pin.config_size,pin.config_hash,selected*2+1,control)
            &&identity(executable.value,path,last_a)&&last_a==a&&identity(configuration.value,config,last_b)&&last_b==b;
        stop=control_code(control);if(stop!=ScanCode::Complete)return stopped(stop);
        return verified?CandidateDisposition::Original:CandidateDisposition::PotentialClient;
    } catch(...) {const auto stop=control_code(control);return stop==ScanCode::Complete?CandidateDisposition::Unknown:stopped(stop);}
}
ClientScan scan_closed_clients(ScanSource &source,const ScanControl &control) noexcept {
    ClientScan result;
    try {
        auto stop=control_code(control);if(stop!=ScanCode::Complete){result.code=stop;return result;}
        std::vector<ProcessEntry> entries;const auto status=source.snapshot(entries,control);
        if(status!=ScanCode::Complete){result.code=status;return result;}
        if(entries.size()>kMaximumProcessEntries)return result;
        bool complete=true;
        for(const auto &entry:entries) {
            stop=control_code(control);if(stop!=ScanCode::Complete){result.code=stop;return result;}
            if(entry.name.empty()||entry.name.size()>=32768||entry.name.find(L'\0')!=entry.name.npos){complete=false;continue;}
            if(!max_process_name(entry.name))continue;
            if(!entry.pid){complete=false;continue;}
            const auto candidate=source.classify(entry,control);
            if(candidate==CandidateDisposition::Cancelled||candidate==CandidateDisposition::Deadline) {
                result.code=candidate==CandidateDisposition::Cancelled?ScanCode::Cancelled:ScanCode::Deadline;return result;
            }
            if(candidate==CandidateDisposition::Original)continue;
            if(candidate==CandidateDisposition::PotentialClient)++result.candidates;
            else complete=false;
        }
        stop=control_code(control);if(stop!=ScanCode::Complete){result.code=stop;return result;}
        result.complete=complete;result.code=complete?ScanCode::Complete:ScanCode::Incomplete;return result;
    }catch(...){return result;}
}
ClientScan scan_closed_clients(const ScanControl &control) noexcept {WindowsSource source;return scan_closed_clients(source,control);}
ClosedWait::ClosedWait(uint64_t start) noexcept:last_(start) {
    if(start>UINT64_MAX-kWaitMs){deadline_=UINT64_MAX;state_=WaitState::ScanFailed;}
    else deadline_=start+kWaitMs;
}
WaitState ClosedWait::observe(const ClientScan &scan,uint64_t now,bool cancelled) noexcept {
    if(state_!=WaitState::Waiting)return state_;
    if(cancelled||scan.code==ScanCode::Cancelled)return state_=WaitState::Cancelled;
    if(now<last_)return state_=WaitState::ScanFailed;
    last_=now;
    if(now>=deadline_||scan.code==ScanCode::Deadline)return state_=WaitState::Deadline;
    if(!scan.complete||scan.code!=ScanCode::Complete||scan.candidates)empty_=false;
    else {
        if(!empty_){empty_=true;empty_since_=now;}
        if(now-empty_since_>=kSettleMs)return state_=WaitState::Closed;
    }
    return state_;
}
uint32_t ClosedWait::next_delay_ms(uint64_t now) const noexcept {
    if(state_!=WaitState::Waiting||now<last_||now>=deadline_)return 0;
    const auto until_settle=empty_&&now>=empty_since_&&now-empty_since_<kSettleMs?kSettleMs-(now-empty_since_):uint64_t(kPollMs);
    return static_cast<uint32_t>(std::min({uint64_t(kPollMs),deadline_-now,until_settle}));
}
}
