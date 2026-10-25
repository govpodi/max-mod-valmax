#include "launch_session.h"
#include "activation.h"
#include "profile_client.h"
#include "../transfer/closed_clients.h"
#include <windows.h>
#include <tlhelp32.h>
#include <algorithm>
#include <array>
#include <cstring>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <utility>

namespace valmax::launcher {
namespace {
bool same(std::wstring_view a,std::wstring_view b) {
    return a.size()<32768&&b.size()<32768&&a.size()==b.size()
        && CompareStringOrdinal(a.data(),static_cast<int>(a.size()),b.data(),static_cast<int>(b.size()),TRUE)==CSTR_EQUAL;
}
std::string utf8(std::wstring_view value) {
    if(value.empty()||value.size()>32000||value.find(L'\0')!=value.npos)throw std::runtime_error("invalid_path");
    const int n=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,value.data(),static_cast<int>(value.size()),nullptr,0,nullptr,nullptr);
    if(n<=0||n>32000)throw std::runtime_error("invalid_path");
    std::string result(static_cast<size_t>(n),'\0');
    if(WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,value.data(),static_cast<int>(value.size()),result.data(),n,nullptr,nullptr)!=n)
        throw std::runtime_error("invalid_path");
    return result;
}
void quoted(std::string& out,std::string_view value) {
    out.push_back('"');
    for(const auto c:value){if(static_cast<unsigned char>(c)<32)throw std::runtime_error("invalid_path");
        if(c=='"'||c=='\\')out.push_back('\\');out.push_back(c);}
    out.push_back('"');
}
struct Handle {
    HANDLE value=nullptr;
    ~Handle(){reset();}
    void reset(HANDLE h=nullptr){if(value&&value!=INVALID_HANDLE_VALUE)CloseHandle(value);value=h;}
    Handle()=default;
    explicit Handle(HANDLE h):value(h){}
    Handle(const Handle&)=delete;
    Handle& operator=(const Handle&)=delete;
};
template<class Function> Function symbol(HMODULE module,const char* name) {
    const auto proc=GetProcAddress(module,name);Function result=nullptr;
    static_assert(sizeof(result)==sizeof(proc));std::memcpy(&result,&proc,sizeof(result));return result;
}
std::vector<wchar_t> inherited_environment() {
    const auto raw=GetEnvironmentStringsW();if(!raw)throw std::runtime_error("environment_failed");
    struct Free{wchar_t* p;~Free(){FreeEnvironmentStringsW(p);}}free{raw};
    size_t count=0;
    // Windows owns this readable, terminated block. Bound work/allocation.
    while(count+1<kEnvironmentMaximumUnits){if(raw[count]==0&&raw[count+1]==0){count+=2;return {raw,raw+count};}++count;}
    throw std::runtime_error("environment_failed");
}
LaunchResult create_child(const std::wstring& executable,const std::wstring& cwd,
                          std::vector<wchar_t>& environment,bool recovery,LaunchCode success) {
    if(executable.empty()||executable.find(L'"')!=executable.npos||environment.size()<2)return {LaunchCode::InvalidOptions};
    std::wstring command=L"\""+executable+L"\"";if(recovery)command+=L" --recover";
    STARTUPINFOW startup{};startup.cb=sizeof(startup);PROCESS_INFORMATION information{};
    if(!CreateProcessW(executable.c_str(),command.data(),nullptr,nullptr,FALSE,
        CREATE_UNICODE_ENVIRONMENT|CREATE_DEFAULT_ERROR_MODE,environment.data(),cwd.c_str(),&startup,&information))
        return {LaunchCode::SpawnFailed};
    Handle process(information.hProcess),thread(information.hThread);
    // Ordinary bounded startup wait only. No memory/hook inspection, remote
    // thread, suspension, readiness ACK or automatic kill/restart is involved.
    // Keep package handles until this wait completes; timeout is not readiness.
    WaitForInputIdle(process.value,10000);
    if(WaitForSingleObject(process.value,0)==WAIT_OBJECT_0)return {LaunchCode::ChildExited,true};
    return {success,true};
}
}
const char* launch_code(LaunchCode code) noexcept {
    switch(code){
    case LaunchCode::Launched:return "process_launched";case LaunchCode::Activated:return "window_activated";
    case LaunchCode::HelperLaunched:return "transfer_helper_launched";case LaunchCode::InvalidOptions:return "invalid_options";
    case LaunchCode::PayloadInvalid:return "payload_invalid";case LaunchCode::CoreUnavailable:return "core_unavailable";
    case LaunchCode::TransferBusy:return "transfer_busy";case LaunchCode::ProfileFailed:return "profile_guard_failed";
    case LaunchCode::RecoveryRequired:return "recovery_required";case LaunchCode::ProcessUnknown:return "process_scan_unavailable";
    case LaunchCode::ExistingUnavailable:return "existing_client_unavailable";case LaunchCode::ActivationFailed:return "window_activation_failed";
    case LaunchCode::RuntimeFailed:return "runtime_prepare_failed";case LaunchCode::EnvironmentFailed:return "environment_failed";
    case LaunchCode::SpawnFailed:return "native_spawn_failed";case LaunchCode::ChildExited:return "client_exited";
    case LaunchCode::ProfileInUse:return "profile_in_use";
    case LaunchCode::UnsupportedContext:return "unsupported_context";
    default:return "internal_error";}
}
bool parse_action(std::span<const std::wstring_view> args,LaunchAction& result) noexcept {
    result=LaunchAction::Client;if(args.empty())return true;
    if(args.size()!=1)return false;
    if(args[0]==L"launcher")return true;
    if(args[0]==L"mdata"||args[0]==L"import"){result=LaunchAction::Import;return true;}
    if(args[0]==L"recover"){result=LaunchAction::Recover;return true;}
    return false;
}
LaunchResult run_launch(LaunchDriver& driver,LaunchAction action) noexcept {
    try {
        auto code=driver.verify();if(code!=LaunchCode::Launched)return {code};
        if(action==LaunchAction::Import||action==LaunchAction::Recover)return driver.spawn_transfer(action==LaunchAction::Recover);
        if(action!=LaunchAction::Client)return {LaunchCode::InvalidOptions};
        code=driver.acquire_transfer();if(code!=LaunchCode::Launched)return {code};
        const auto absent_or_existing=[&](ClientPresence presence)->std::optional<LaunchResult>{
            if(presence==ClientPresence::Absent)return std::nullopt;
            if(presence==ClientPresence::Running){
                const auto guard=driver.check_profile();
                return guard==LaunchCode::Launched?driver.activate():LaunchResult{guard};
            }
            return LaunchResult{presence==ClientPresence::Unknown?LaunchCode::ProcessUnknown:
                presence==ClientPresence::ProfileInUse?LaunchCode::ProfileInUse:LaunchCode::ExistingUnavailable};
        };
        auto presence=driver.scan();
        if(const auto done=absent_or_existing(presence))return *done;
        for(const auto step:{&LaunchDriver::check_profile,&LaunchDriver::prepare_profile,
                            &LaunchDriver::prepare_runtime,&LaunchDriver::prepare_environment,&LaunchDriver::check_profile}){
            code=(driver.*step)();if(code!=LaunchCode::Launched)return {code};}
        // Recheck directly before creation. The transfer mutex serializes our
        // launchers/importers; no sampled scan excludes an unrelated direct start.
        presence=driver.scan();
        if(const auto done=absent_or_existing(presence))return *done;
        return driver.spawn();
    }catch(...){return {LaunchCode::InternalError};}
}
std::string runtime_config(std::wstring_view profile,std::wstring_view runtime,bool existing) {
    std::string value="{\"profileDirectory\":";quoted(value,utf8(profile));
    value+=",\"runtimeDirectory\":";quoted(value,utf8(runtime));
    value+=existing?",\"existingClient\":true}":",\"existingClient\":false}";
    if(value.size()>16384)throw std::runtime_error("config_too_large");return value;
}
ChildEnvironment configured_environment(std::span<const wchar_t> inherited,const LexicalEnvironmentPaths& paths,
                                        std::string_view config) {
    auto result=build_child_environment(inherited,paths);if(!result.ok()||config.empty())return result;
    if(config.size()>16384||config.find('\0')!=config.npos)return {EnvironmentError::InvalidEntry,{}};
    const int length=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,config.data(),static_cast<int>(config.size()),nullptr,0);
    if(length<=0)return {EnvironmentError::InvalidUnicode,{}};
    std::wstring value(static_cast<size_t>(length),L'\0');
    if(MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,config.data(),static_cast<int>(config.size()),value.data(),length)!=length)
        return {EnvironmentError::InvalidUnicode,{}};
    const std::wstring entry=L"VALMAX_NATIVE_RUNTIME_CONFIG="+value;
    if(result.block.size()+entry.size()+1>kEnvironmentMaximumUnits)return {EnvironmentError::TooLarge,{}};
    size_t at=0;
    while(at+1<result.block.size()){
        const auto end=std::find(result.block.begin()+static_cast<ptrdiff_t>(at),result.block.end(),L'\0');
        const auto count=static_cast<size_t>(end-(result.block.begin()+static_cast<ptrdiff_t>(at)));
        if(CompareStringOrdinal(result.block.data()+at,static_cast<int>(count),entry.data(),static_cast<int>(entry.size()),TRUE)==CSTR_GREATER_THAN)break;
        at+=count+1;
    }
    result.block.insert(result.block.begin()+static_cast<ptrdiff_t>(at),entry.begin(),entry.end());
    result.block.insert(result.block.begin()+static_cast<ptrdiff_t>(at+entry.size()),L'\0');return result;
}
struct Win32LaunchDriver::Impl {
    LaunchOptions options;std::span<const PayloadPin> pins;VerifiedPayload payload;
    std::wstring runtime,mod,profile,webview,executable,service;
    std::optional<TransferGuard> transfer;
    HMODULE core=nullptr;
    PrepareProfileFn prepare=nullptr;
    decltype(&valmax_runtime_open) open=nullptr;
    decltype(&valmax_runtime_close) close=nullptr;
    decltype(&valmax_core_buffer_free) free_buffer=nullptr;
    Handle existing;
    std::vector<wchar_t> environment;
    DWORD owner=GetCurrentThreadId();
#ifdef VALMAX_TRANSFER_TESTING
    std::wstring test_mutex;
    std::vector<uint32_t> test_pids;
#endif
    Impl(LaunchOptions value,std::span<const PayloadPin> input):options(std::move(value)),pins(input){}
    ~Impl(){transfer.reset();if(core)FreeLibrary(core);}
    bool owned()const{return owner==GetCurrentThreadId();}
    bool held()const{return owned()&&payload.verified()&&transfer&&transfer->owned_by_current_thread();}
    LaunchCode load_core(){
        const auto path=mod+L"\\valmax_core.dll";
        core=LoadLibraryExW(path.c_str(),nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_SYSTEM32);
        if(!core)return LaunchCode::CoreUnavailable;
        std::array<wchar_t,32768> name{};const DWORD count=GetModuleFileNameW(core,name.data(),static_cast<DWORD>(name.size()));
        if(!count||count>=name.size()||!same(std::wstring_view(name.data(),count),path))return LaunchCode::CoreUnavailable;
        const auto abi=symbol<decltype(&valmax_core_abi_version)>(core,"valmax_core_abi_version");
        prepare=symbol<PrepareProfileFn>(core,"valmax_launcher_prepare");
        open=symbol<decltype(open)>(core,"valmax_runtime_open");close=symbol<decltype(close)>(core,"valmax_runtime_close");
        free_buffer=symbol<decltype(free_buffer)>(core,"valmax_core_buffer_free");
        return abi&&abi()==1&&prepare&&open&&close&&free_buffer?LaunchCode::Launched:LaunchCode::CoreUnavailable;
    }
};
#ifdef VALMAX_TRANSFER_TESTING
Win32LaunchDriver::Win32LaunchDriver(LaunchOptions options,std::span<const PayloadPin> pins,std::wstring name,
                                   std::span<const uint32_t> pids)
    :impl_(std::make_unique<Impl>(std::move(options),pins)){
    impl_->test_mutex=std::move(name);impl_->test_pids.assign(pids.begin(),pids.end());}
#else
Win32LaunchDriver::Win32LaunchDriver(LaunchOptions options,std::span<const PayloadPin> pins)
    :impl_(std::make_unique<Impl>(std::move(options),pins)){}
#endif
Win32LaunchDriver::~Win32LaunchDriver()=default;
LaunchCode Win32LaunchDriver::verify(){
    auto& s=*impl_;if(!s.owned()||s.payload.verified()||s.options.package_directory.empty())return LaunchCode::InvalidOptions;
    s.runtime=s.options.package_directory+L"\\runtime";s.mod=s.runtime+L"\\mod";
    s.webview=s.runtime+L"\\webview2";s.profile=s.options.local_appdata+L"\\MXMOD";
    s.executable=s.mod+L"\\max.exe";s.service=s.mod+L"\\max-service\\max-service.exe";
    return verify_native_payload(s.runtime,s.pins,s.payload)==PreflightError::Ok?LaunchCode::Launched:LaunchCode::PayloadInvalid;
}
LaunchCode Win32LaunchDriver::acquire_transfer(){
    auto& s=*impl_;if(!s.owned()||!s.payload.verified()||s.transfer)return LaunchCode::InvalidOptions;
#ifdef VALMAX_TRANSFER_TESTING
    s.transfer.emplace(TestTransferName{s.test_mutex});
#else
    s.transfer.emplace();
#endif
    if(s.transfer->code()!=TransferCode::Ok)return s.transfer->code()==TransferCode::Busy?LaunchCode::TransferBusy:LaunchCode::InternalError;
    return s.load_core();
}
LaunchCode Win32LaunchDriver::prepare_profile(){
    auto& s=*impl_;if(!s.held())return LaunchCode::InvalidOptions;
    const auto result=valmax::launcher::prepare_profile(s.options.local_appdata,false,*s.transfer,s.prepare);
    if(result.code==ProfileCode::Ok)return LaunchCode::Launched;
    if(result.code==ProfileCode::RecoveryRequired)return LaunchCode::RecoveryRequired;
    if(result.code==ProfileCode::TransferBusy)return LaunchCode::TransferBusy;
    return LaunchCode::ProfileFailed;
}
LaunchCode Win32LaunchDriver::check_profile(){
    auto& s=*impl_;if(!s.held())return LaunchCode::InvalidOptions;
    // This technical marker is probed for existence only. No bytes are read,
    // no leaf reparse point is followed, and no profile/config is created.
    const auto marker=s.options.local_appdata+L"\\MXMOD-MData-Recovery.json";
    Handle existing(CreateFileW(marker.c_str(),FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_BACKUP_SEMANTICS,nullptr));
    if(existing.value!=INVALID_HANDLE_VALUE)return LaunchCode::RecoveryRequired;
    if(GetLastError()!=ERROR_FILE_NOT_FOUND)return LaunchCode::ProfileFailed;
    const auto result=valmax::launcher::prepare_profile(s.options.local_appdata,true,*s.transfer,s.prepare);
    if(result.code==ProfileCode::Ok)return LaunchCode::Launched;
    if(result.code==ProfileCode::RecoveryRequired)return LaunchCode::RecoveryRequired;
    if(result.code==ProfileCode::TransferBusy)return LaunchCode::TransferBusy;
    return LaunchCode::ProfileFailed;
}
ClientPresence Win32LaunchDriver::scan(){
    auto& s=*impl_;if(!s.held())return ClientPresence::Unknown;s.existing.reset();
    uint32_t matches=0;bool service=false,foreign=false;
    const auto now=GetTickCount64();if(now>UINT64_MAX-15000)return ClientPresence::Unknown;
    const valmax::transfer::ScanControl control{nullptr,now+15000};
    const auto inspect=[&](DWORD pid,std::wstring_view filename)->bool{
        if(GetTickCount64()>=control.deadline_ms||!pid)return false;
        Handle process(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,FALSE,pid));
        if(!process.value||GetProcessId(process.value)!=pid)return false;
        const auto wait=WaitForSingleObject(process.value,0);
        if(wait==WAIT_OBJECT_0)return true;
        if(wait!=WAIT_TIMEOUT)return false;
        std::array<wchar_t,32768> path{};DWORD length=static_cast<DWORD>(path.size());
        if(!QueryFullProcessImageNameW(process.value,0,path.data(),&length)||!length||length>=path.size())return false;
        const std::wstring image(path.data(),length);
        if(filename.empty())filename=std::wstring_view(image).substr(image.find_last_of(L"\\/")+1);
        if(!valmax::transfer::max_process_name(filename))return false;
        if(same(image,s.executable)){++matches;s.existing.reset(std::exchange(process.value,nullptr));return true;}
        if(same(image,s.service)){service=true;return true;}
        const auto disposition=valmax::transfer::original_image(image,filename,control);
        std::array<wchar_t,32768> after{};DWORD after_length=static_cast<DWORD>(after.size());
        if(WaitForSingleObject(process.value,0)!=WAIT_TIMEOUT
            ||!QueryFullProcessImageNameW(process.value,0,after.data(),&after_length)
            ||!after_length||after_length>=after.size()||!same(image,std::wstring_view(after.data(),after_length)))return false;
        if(disposition==valmax::transfer::CandidateDisposition::Original)return true;
        if(disposition!=valmax::transfer::CandidateDisposition::PotentialClient)return false;
        foreign=true;return true;
    };
#ifdef VALMAX_TRANSFER_TESTING
    if(s.test_pids.size()>65536)return ClientPresence::Unknown;
    for(const auto pid:s.test_pids)if(!inspect(pid,{}))return ClientPresence::Unknown;
#else
    Handle snapshot(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0));if(snapshot.value==INVALID_HANDLE_VALUE)return ClientPresence::Unknown;
    PROCESSENTRY32W entry{};entry.dwSize=sizeof(entry);uint32_t count=0;
    BOOL more=Process32FirstW(snapshot.value,&entry);
    while(more){
        if(++count>65536||GetTickCount64()>=control.deadline_ms)return ClientPresence::Unknown;
        const auto end=std::find(std::begin(entry.szExeFile),std::end(entry.szExeFile),L'\0');
        if(end==std::end(entry.szExeFile)||end==std::begin(entry.szExeFile))return ClientPresence::Unknown;
        const std::wstring_view filename(entry.szExeFile,static_cast<size_t>(end-std::begin(entry.szExeFile)));
        if(same(filename,L"max.exe")||same(filename,L"max-service.exe")){
            if(!inspect(entry.th32ProcessID,filename))return ClientPresence::Unknown;
        }
        more=Process32NextW(snapshot.value,&entry);
    }
    if(GetLastError()!=ERROR_NO_MORE_FILES)return ClientPresence::Unknown;
#endif
    if(GetTickCount64()>=control.deadline_ms)return ClientPresence::Unknown;
    if(foreign)return ClientPresence::ProfileInUse;
    if(matches>1)return ClientPresence::Unavailable;
    if(matches==1)return ClientPresence::Running;
    return service?ClientPresence::Unavailable:ClientPresence::Absent;
}
LaunchResult Win32LaunchDriver::activate(){
    auto& s=*impl_;if(!s.held()||!s.existing.value||WaitForSingleObject(s.existing.value,0)!=WAIT_TIMEOUT)return {LaunchCode::ExistingUnavailable};
    const auto result=activate_existing(s.executable);
    return {result.shown?LaunchCode::Activated:LaunchCode::ActivationFailed,false,result.shown,result.foreground};
}
LaunchCode Win32LaunchDriver::prepare_runtime(){
    auto& s=*impl_;if(!s.held()||!s.open||!s.close||!s.free_buffer)return LaunchCode::CoreUnavailable;
    const auto config=runtime_config(s.profile,s.mod,false);ValmaxRuntime* context=nullptr;ValmaxCoreBuffer output{};
    const auto status=s.open(reinterpret_cast<const uint8_t*>(config.data()),config.size(),&context,&output);
    const bool ok=status==0&&context!=nullptr;
    s.free_buffer(&output);if(context)s.close(&context);
    return ok&&context==nullptr?LaunchCode::Launched:LaunchCode::RuntimeFailed;
}
LaunchCode Win32LaunchDriver::prepare_environment(){
    auto& s=*impl_;if(!s.held())return LaunchCode::InvalidOptions;
    const auto input=inherited_environment();
    auto result=configured_environment(input,{s.webview,s.profile},runtime_config(s.profile,s.mod,true));
    if(!result.ok())return LaunchCode::EnvironmentFailed;s.environment=std::move(result.block);return LaunchCode::Launched;
}
LaunchResult Win32LaunchDriver::spawn(){
    auto& s=*impl_;if(!s.held()||s.environment.empty())return {LaunchCode::InvalidOptions};
    return create_child(s.executable,s.mod,s.environment,false,LaunchCode::Launched);
}
LaunchResult Win32LaunchDriver::spawn_transfer(bool recovery){
    auto& s=*impl_;if(!s.owned()||!s.payload.verified())return {LaunchCode::InvalidOptions};
    // Helper owns its own transfer transaction and profile selection. The
    // launcher neither prepares MXMOD nor acquires the transfer mutex here.
    const auto input=inherited_environment();auto env=configured_environment(input,{s.webview,s.profile},{});
    if(!env.ok())return {LaunchCode::EnvironmentFailed,false,false,false,env.error};
    const auto directory=s.runtime+L"\\transfer";
    return create_child(directory+L"\\valmax-transfer.exe",directory,env.block,recovery,LaunchCode::HelperLaunched);
}
}
