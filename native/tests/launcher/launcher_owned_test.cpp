#include "launch_session.h"
#include <windows.h>
#include <bcrypt.h>
#include <winioctl.h>
#include <array>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <source_location>
#include <stdexcept>
#include <vector>
#include <cstring>
using namespace valmax::launcher;
namespace fs=std::filesystem;
namespace {
int checks=0;
void check(bool value,std::source_location location=std::source_location::current()){
    ++checks;if(!value){std::cerr<<"check_line="<<location.line()<<"\n";throw std::runtime_error("owned_launcher_failed");}
}
struct Handle {HANDLE value=nullptr;~Handle(){if(value&&value!=INVALID_HANDLE_VALUE)CloseHandle(value);}explicit Handle(HANDLE h):value(h){}Handle(const Handle&)=delete;};
std::string read(const fs::path& p){std::ifstream f(p,std::ios::binary);return {std::istreambuf_iterator<char>(f),{}};}
void write(const fs::path& p,std::string_view bytes){std::ofstream f(p,std::ios::binary);f.write(bytes.data(),static_cast<std::streamsize>(bytes.size()));f.flush();check(f.good());}
std::string hash(const fs::path& p){
    const auto bytes=read(p);BCRYPT_ALG_HANDLE algorithm=nullptr;BCRYPT_HASH_HANDLE context=nullptr;
    check(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)>=0);
    check(BCryptCreateHash(algorithm,&context,nullptr,0,nullptr,0,0)>=0);
    check(bytes.size()<UINT32_MAX&&BCryptHashData(context,reinterpret_cast<PUCHAR>(const_cast<char*>(bytes.data())),static_cast<ULONG>(bytes.size()),0)>=0);
    std::array<uint8_t,32> digest{};check(BCryptFinishHash(context,digest.data(),static_cast<ULONG>(digest.size()),0)>=0);
    BCryptDestroyHash(context);BCryptCloseAlgorithmProvider(algorithm,0);
    std::string result;constexpr char hex[]="0123456789abcdef";for(const auto n:digest){result.push_back(hex[n>>4]);result.push_back(hex[n&15]);}return result;
}
uint64_t number(std::string_view json,std::string_view key){
    const auto prefix="\""+std::string(key)+"\":";const auto at=json.find(prefix);check(at!=json.npos);
    return std::stoull(std::string(json.substr(at+prefix.size())));
}
struct Child {
    Handle event;Handle process{nullptr};fs::path report;
    Child(const fs::path& root,const wchar_t* name):event(CreateEventW(nullptr,TRUE,FALSE,(L"Local\\ValMAXLauncherChild-"+std::to_wstring(GetCurrentProcessId())+name).c_str())),report(root/(std::wstring(name)+L".json")){
        check(event.value!=nullptr);
        check(SetEnvironmentVariableW(L"VALMAX_LAUNCH_TEST_REPORT",report.c_str())!=FALSE);
        check(SetEnvironmentVariableW(L"VALMAX_LAUNCH_TEST_STOP",(L"Local\\ValMAXLauncherChild-"+std::to_wstring(GetCurrentProcessId())+name).c_str())!=FALSE);
    }
    std::string await(){
        const auto deadline=GetTickCount64()+12000;std::string text;
        do{if(fs::exists(report)){text=read(report);if(text.ends_with('}'))break;}Sleep(10);}while(GetTickCount64()<deadline);
        check(text.ends_with('}'));
        check(text.find("\"configOk\":true")!=text.npos&&text.find("\"pathsOk\":true")!=text.npos
            &&text.find("\"sanitized\":true")!=text.npos&&text.find("\"cwdOk\":true")!=text.npos);
        process.value=OpenProcess(SYNCHRONIZE|PROCESS_QUERY_LIMITED_INFORMATION,FALSE,static_cast<DWORD>(number(text,"pid")));check(process.value!=nullptr);return text;
    }
    void stop(){check(SetEvent(event.value)!=FALSE);check(process.value&&WaitForSingleObject(process.value,5000)==WAIT_OBJECT_0);DWORD exit=1;check(GetExitCodeProcess(process.value,&exit)&&exit==0);}
    ~Child(){SetEvent(event.value);if(process.value)WaitForSingleObject(process.value,5000);}
};
// Same ordinary public mount-point fixture used by preflight tests. Only owned
// D directories are passed; a missing owned target exercises a broken junction.
bool junction(const fs::path& link,const fs::path& target){
    const std::wstring substitute=L"\\??\\"+target.wstring(),display=target.wstring();
    struct Reparse {DWORD tag;WORD length,reserved,sub_offset,sub_length,print_offset,print_length;wchar_t path[2048];} data{};
    if(substitute.size()+display.size()+2>std::size(data.path))return false;
    data.tag=IO_REPARSE_TAG_MOUNT_POINT;data.sub_length=static_cast<WORD>(substitute.size()*sizeof(wchar_t));
    data.print_offset=data.sub_length+sizeof(wchar_t);data.print_length=static_cast<WORD>(display.size()*sizeof(wchar_t));
    data.length=static_cast<WORD>(8+data.print_offset+data.print_length+sizeof(wchar_t));
    std::memcpy(data.path,substitute.c_str(),data.sub_length+sizeof(wchar_t));
    std::memcpy(reinterpret_cast<unsigned char*>(data.path)+data.print_offset,display.c_str(),data.print_length+sizeof(wchar_t));
    const auto file=CreateFileW(link.c_str(),GENERIC_WRITE,0,nullptr,OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_BACKUP_SEMANTICS,nullptr);
    if(file==INVALID_HANDLE_VALUE)return false;DWORD returned=0;
    const bool ok=DeviceIoControl(file,FSCTL_SET_REPARSE_POINT,&data,8+data.length,nullptr,0,&returned,nullptr)!=0;CloseHandle(file);return ok;
}
}
int wmain(int argc,wchar_t** argv){
    try{
        check(argc==4||argc==5);const bool real_core=argc==5;const fs::path root(argv[1]),child(argv[2]),core(argv[3]);
        check(root.is_absolute()&&root.lexically_normal()==root&&root.wstring().starts_with(L"D:\\ValMAXBuild\\fixtures\\native-launcher\\"));
        check(!fs::exists(root));check(fs::create_directories(root));
        for(auto p=root;!p.empty();p=p.parent_path()){
            const auto attributes=GetFileAttributesW(p.c_str());check(attributes!=INVALID_FILE_ATTRIBUTES&&!(attributes&FILE_ATTRIBUTE_REPARSE_POINT));if(p==p.root_path())break;
        }
        const auto runtime=root/L"runtime",mod=runtime/L"mod",local=root/L"local",transfer=runtime/L"transfer";
        fs::create_directories(mod);fs::create_directories(local/L"MXMOD");fs::create_directories(transfer);fs::create_directories(runtime/L"webview2");
        fs::copy_file(child,mod/L"max.exe");fs::copy_file(core,mod/L"valmax_core.dll");fs::copy_file(child,transfer/L"valmax-transfer.exe");
        if(real_core){
            const fs::path dependency(argv[4]);
            check(dependency.is_absolute()&&dependency.filename()==L"vcruntime140.dll"
                &&dependency.wstring().starts_with(L"D:\\ValMAXBuild\\build\\"));
            fs::copy_file(dependency,mod/L"vcruntime140.dll");
        }
        const auto service_directory=mod/L"max-service";
        fs::create_directories(service_directory);fs::copy_file(child,service_directory/L"max-service.exe");
        // Keep every inventory branch tracked; these are inert fixture bytes.
        write(runtime/L"webview2"/L"stub.bin","owned-stub");
        std::vector<std::wstring> paths={L"mod/max.exe",L"mod/max-service/max-service.exe",L"mod/valmax_core.dll",L"transfer/valmax-transfer.exe",L"webview2/stub.bin"};
        if(real_core)paths.push_back(L"mod/vcruntime140.dll");
        std::vector<std::string> hashes;for(const auto& p:paths)hashes.push_back(hash(runtime/p));
        std::vector<PayloadPin> pins;for(size_t i=0;i<paths.size();++i)pins.push_back({paths[i].c_str(),fs::file_size(runtime/paths[i]),hashes[i].c_str()});
        const auto opaque=local/L"MXMOD"/L"opaque-owned-account-state.bin";
        constexpr char opaque_bytes[]="synthetic-only-no-account-token\0\xff\x80";
        write(opaque,std::string_view(opaque_bytes,sizeof(opaque_bytes)-1));const auto opaque_before=read(opaque);
        const LaunchOptions options{root.wstring(),local.wstring()};const auto mutex=L"Local\\ValMAXNativeTransferTestV1-Launcher-"+std::to_wstring(GetCurrentProcessId());
        check(SetEnvironmentVariableW(L"VALMAX_LAUNCH_TEST_EXPECT_PROFILE",nullptr)!=FALSE);
        check(SetEnvironmentVariableW(L"VALMAX_LAUNCH_TEST_EXPECT_RUNTIME",nullptr)!=FALSE);
        check(SetEnvironmentVariableW(L"QT_PLUGIN_PATH",L"untrusted")!=FALSE);
        check(SetEnvironmentVariableW(L"VALMAX_NATIVE_RUNTIME_CONFIG",L"untrusted")!=FALSE);
        // Own read-only guards never create MXMOD/config, regardless of marker
        // type. No real profile or production mutex participates in these calls.
        for(const auto* kind:{L"absent",L"empty",L"regular",L"directory",L"junction",L"broken",L"missing-parent"}){
            const auto probe_local=root/L"guard-cases"/kind/L"local";
            if(std::wstring_view(kind)!=L"missing-parent")fs::create_directories(probe_local);
            const auto marker=probe_local/L"MXMOD-MData-Recovery.json";
            if(std::wstring_view(kind)==L"empty")write(marker,"");
            if(std::wstring_view(kind)==L"regular")write(marker,"OWNED-TECHNICAL-MARKER");
            if(std::wstring_view(kind)==L"directory")fs::create_directory(marker);
            if(std::wstring_view(kind)==L"junction"||std::wstring_view(kind)==L"broken"){
                fs::create_directory(marker);const auto target=probe_local/L"owned-target";
                if(std::wstring_view(kind)==L"junction")fs::create_directory(target);
                check(junction(marker,target));
            }
            const LaunchOptions probe_options{root.wstring(),probe_local.wstring()};
            {Win32LaunchDriver driver(probe_options,pins,mutex);
                check(driver.verify()==LaunchCode::Launched&&driver.acquire_transfer()==LaunchCode::Launched);
                const auto expected=std::wstring_view(kind)==L"absent"?LaunchCode::Launched:
                    std::wstring_view(kind)==L"missing-parent"?LaunchCode::ProfileFailed:LaunchCode::RecoveryRequired;
                const auto actual=driver.check_profile();
                if(actual!=expected)std::wcerr<<L"marker_kind="<<kind<<L",actual="<<static_cast<uint32_t>(actual)<<L"\n";
                check(actual==expected);}
            check(!fs::exists(probe_local/L"MXMOD"));check(!fs::exists(mod/L"optimization-active.json"));
        }
        {
            // Stock MAX keeps its service and that service's DLLs in a separate
            // directory. A matching owned service without a client must prevent
            // a second launch, even though there is no mod/max-service.exe.
            Child service(root,L"service");
            check(SetEnvironmentVariableW(L"QT_PLUGIN_PATH",nullptr)!=FALSE);
            check(SetEnvironmentVariableW(L"VALMAX_NATIVE_RUNTIME_CONFIG",L"{\"existingClient\":true}")!=FALSE);
            const auto image=(service_directory/L"max-service.exe").wstring();
            std::wstring command=L"\""+image+L"\"";
            STARTUPINFOW startup{};startup.cb=sizeof(startup);PROCESS_INFORMATION info{};
            check(CreateProcessW(image.c_str(),command.data(),nullptr,nullptr,FALSE,
                CREATE_DEFAULT_ERROR_MODE,nullptr,service_directory.c_str(),&startup,&info)!=FALSE);
            Handle process(info.hProcess),thread(info.hThread);
            const auto report=service.await();check(number(report,"pid")==info.dwProcessId);
            check(SetEnvironmentVariableW(L"QT_PLUGIN_PATH",L"untrusted")!=FALSE);
            check(SetEnvironmentVariableW(L"VALMAX_NATIVE_RUNTIME_CONFIG",L"untrusted")!=FALSE);
            const uint32_t pids[]={info.dwProcessId};
            {Win32LaunchDriver driver(options,pins,mutex,pids);check(run_launch(driver).code==LaunchCode::ExistingUnavailable);}
            check(!fs::exists(mod/L"optimization-active.json"));
            check(!fs::exists(local/L"MXMOD"/L"config.json"));
            service.stop();
        }
        const auto config_path=local/L"MXMOD"/L"config.json";std::string config_before;
        for(const auto* kind:{L"foreign-main",L"foreign-service"}){
            const auto directory=root/kind;fs::create_directory(directory);
            const auto image=directory/(std::wstring_view(kind)==L"foreign-main"?L"max.exe":L"max-service.exe");
            fs::copy_file(child,image);write(directory/L"config.dll","OWNED-NONORIGINAL-CONFIG");
            Child foreign(root,kind);
            check(SetEnvironmentVariableW(L"QT_PLUGIN_PATH",nullptr)!=FALSE);
            check(SetEnvironmentVariableW(L"VALMAX_NATIVE_RUNTIME_CONFIG",L"{\"existingClient\":true}")!=FALSE);
            std::wstring command=L"\""+image.wstring()+L"\"";STARTUPINFOW startup{};startup.cb=sizeof(startup);PROCESS_INFORMATION info{};
            check(CreateProcessW(image.c_str(),command.data(),nullptr,nullptr,FALSE,CREATE_DEFAULT_ERROR_MODE,nullptr,directory.c_str(),&startup,&info)!=FALSE);
            Handle process(info.hProcess),thread(info.hThread);foreign.await();
            const uint32_t pids[]={info.dwProcessId};
            {Win32LaunchDriver driver(options,pins,mutex,pids);check(run_launch(driver).code==LaunchCode::ProfileInUse);}
            check(!fs::exists(config_path)&&!fs::exists(mod/L"optimization-active.json"));check(read(opaque)==opaque_before);
            foreign.stop();
        }
        check(SetEnvironmentVariableW(L"QT_PLUGIN_PATH",L"untrusted")!=FALSE);
        check(SetEnvironmentVariableW(L"VALMAX_NATIVE_RUNTIME_CONFIG",L"untrusted")!=FALSE);
        {const uint32_t invalid_pids[]={0};Win32LaunchDriver driver(options,pins,mutex,invalid_pids);
            check(run_launch(driver).code==LaunchCode::ProcessUnknown);}
        check(!fs::exists(config_path)&&!fs::exists(mod/L"optimization-active.json"));
        check(SetEnvironmentVariableW(L"VALMAX_LAUNCH_TEST_EXPECT_PROFILE",(local/L"MXMOD").c_str())!=FALSE);
        check(SetEnvironmentVariableW(L"VALMAX_LAUNCH_TEST_EXPECT_RUNTIME",mod.c_str())!=FALSE);
        {
            Child running(root,L"client");LaunchResult first;
            {Win32LaunchDriver driver(options,pins,mutex);first=run_launch(driver);
                if(real_core){
                    const auto module=GetModuleHandleW(L"vcruntime140.dll");check(module!=nullptr);
                    std::array<wchar_t,32768> loaded{};const auto length=GetModuleFileNameW(module,loaded.data(),static_cast<DWORD>(loaded.size()));
                    const auto expected=(mod/L"vcruntime140.dll").wstring();
                    const bool bundled=length&&length<loaded.size()&&CompareStringOrdinal(loaded.data(),static_cast<int>(length),expected.data(),static_cast<int>(expected.size()),TRUE)==CSTR_EQUAL;
                    if(!bundled)std::wcerr<<L"loaded_crt_path="<<loaded.data()<<L"\n";
                    check(bundled);
                }
            }
            if(first.code!=LaunchCode::Launched)std::cerr<<"first_launch_code="<<launch_code(first.code)<<"\n";
            check(first.code==LaunchCode::Launched&&first.process_created&&!first.window_shown);
            const auto report=running.await();check(report.find("\"helper\":false")!=report.npos);
            if(real_core){
                check(fs::exists(config_path));config_before=read(config_path);
                check(config_before.find("\"app_folder_name\": \"MXMOD\"")!=config_before.npos);
                check(config_before.find("\"auto_update_enabled\": false")!=config_before.npos);
                const auto end=config_before.find_last_of('}');check(end!=config_before.npos);
                config_before.insert(end,",\n\"ownedSyntheticExtra\":{\"preserve\":\"fixture\",\"count\":2}\n");
                config_before=" \r\n"+config_before+"\r\n ";write(config_path,config_before);
            }
            check(fs::exists(mod/L"optimization-active.json"));
            const auto before=read(mod/L"optimization-active.json");
            if(real_core){
                check(before.find("\"schema\":1")!=before.npos&&before.find("\"smoothWheel\":true")!=before.npos);
                check(before.find("syntheticOnly")==before.npos);check(read(config_path)==config_before);
            }
            const auto window=reinterpret_cast<HWND>(number(report,"window"));DWORD pid=0;
            check(GetWindowThreadProcessId(window,&pid)!=0&&pid==number(report,"pid"));check(!IsWindowVisible(window));
            const uint32_t pids[]={static_cast<uint32_t>(number(report,"pid"))};
            {Win32LaunchDriver driver(options,pins,mutex,pids);const auto again=run_launch(driver);check(again.code==LaunchCode::Activated&&!again.process_created&&again.window_shown);}
            check(IsWindowVisible(window)!=FALSE);check(read(mod/L"optimization-active.json")==before);
            // Repeat tray-style hide + ordinary activation, without a new child.
            ShowWindow(window,SW_HIDE);check(!IsWindowVisible(window));
            {Win32LaunchDriver driver(options,pins,mutex,pids);const auto again=run_launch(driver);check(again.code==LaunchCode::Activated&&!again.process_created);}
            const auto marker=local/L"MXMOD-MData-Recovery.json";write(marker,"OWNED-RECOVERY-MARKER");
            {Win32LaunchDriver driver(options,pins,mutex,pids);check(run_launch(driver).code==LaunchCode::RecoveryRequired);}
            check(read(marker)=="OWNED-RECOVERY-MARKER"&&read(mod/L"optimization-active.json")==before);
            check(fs::remove(marker));
            if(real_core){
                check(read(config_path)==config_before);
                const auto backup=local/L"MXMOD"/L"config-owned-backup.json";
                fs::rename(config_path,backup);
                {Win32LaunchDriver driver(options,pins,mutex,pids);check(run_launch(driver).code==LaunchCode::Activated);}
                check(!fs::exists(config_path)&&read(backup)==config_before&&read(opaque)==opaque_before);
                fs::rename(backup,config_path);
            }
            running.stop();
        }
        check(read(opaque)==opaque_before);
        if(real_core){
            check(read(config_path)==config_before);
            const auto active_before=read(mod/L"optimization-active.json");
            write(config_path,"{malformed-owned-fixture");
            {Child blocked(root,L"bad-config");Win32LaunchDriver driver(options,pins,mutex);
                check(run_launch(driver).code==LaunchCode::ProfileFailed);check(!fs::exists(blocked.report));}
            check(read(config_path)=="{malformed-owned-fixture");check(read(opaque)==opaque_before);
            check(read(mod/L"optimization-active.json")==active_before);write(config_path,config_before);
            const auto settings=local/L"MXMOD"/L"optimization-settings.json";
            write(settings,"{malformed-owned-fixture");
            {Child blocked(root,L"bad-settings");Win32LaunchDriver driver(options,pins,mutex);
                check(run_launch(driver).code==LaunchCode::RuntimeFailed);check(!fs::exists(blocked.report));}
            check(read(settings)=="{malformed-owned-fixture");check(read(config_path)==config_before);
            check(read(mod/L"optimization-active.json")==active_before);check(fs::remove(settings));
        }
        write(local/L"MXMOD-MData-Recovery.json","{\"ownedSyntheticMarker\":true}");
        {Win32LaunchDriver driver(options,pins,mutex);check(run_launch(driver).code==LaunchCode::RecoveryRequired);}
        check(!fs::exists(root/L"recover.json"));
        for(const auto action:{LaunchAction::Import,LaunchAction::Recover}){
            const auto* name=action==LaunchAction::Recover?L"recover":L"import";Child helper(root,name);
            {Win32LaunchDriver driver(options,pins,mutex);const auto result=run_launch(driver,action);check(result.code==LaunchCode::HelperLaunched&&result.process_created);}
            const auto report=helper.await();check(report.find("\"helper\":true")!=report.npos);
            check(report.find(action==LaunchAction::Recover?"\"recovery\":true":"\"recovery\":false")!=report.npos);helper.stop();
        }
        check(read(opaque)==opaque_before);if(real_core)check(read(config_path)==config_before);
        // A changed pinned byte fails before profile/core/spawn.
        write(mod/L"max.exe","tampered");
        {Win32LaunchDriver driver(options,pins,mutex);check(run_launch(driver).code==LaunchCode::PayloadInvalid);}
        check(SetEnvironmentVariableW(L"QT_PLUGIN_PATH",nullptr)!=FALSE);check(SetEnvironmentVariableW(L"VALMAX_NATIVE_RUNTIME_CONFIG",nullptr)!=FALSE);
        check(SetEnvironmentVariableW(L"VALMAX_LAUNCH_TEST_EXPECT_PROFILE",nullptr)!=FALSE);
        check(SetEnvironmentVariableW(L"VALMAX_LAUNCH_TEST_EXPECT_RUNTIME",nullptr)!=FALSE);
        std::cout<<"{\"ok\":true,\"checks\":"<<checks<<",\"realCore\":"<<(real_core?"true":"false")
            <<",\"freshConfigPrepared\":"<<(real_core?"true":"false")<<",\"existingConfigBytesPreserved\":"<<(real_core?"true":"false")
            <<",\"invalidConfigAndRuntimeBlockedBeforeChild\":"<<(real_core?"true":"false")<<",\"bundledCrtUsed\":"<<(real_core?"true":"false")
            <<",\"hiddenWindowActivationTested\":true,\"mdataRoutingTested\":true,\"ownChildOnly\":true,\"opaqueAccountBytesPreserved\":true,\"realProfilesTouched\":false,\"realCredentialsTouched\":false,\"protectionReadinessTested\":false}\n";return 0;
    }catch(...){std::cerr<<"owned_launcher_failed\n";return 1;}
}
