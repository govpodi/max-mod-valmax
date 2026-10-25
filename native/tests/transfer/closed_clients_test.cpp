#include "closed_clients.h"
#include <windows.h>
#include <bcrypt.h>
#include <array>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

using namespace valmax::transfer;
namespace {
uint32_t checks=0;
void check(bool value,const char *description) {++checks;if(!value)throw std::runtime_error(description);}
class Fake final:public ScanSource {
public:
    std::vector<ProcessEntry> entries;
    ScanCode result=ScanCode::Complete;
    CandidateDisposition candidate=CandidateDisposition::PotentialClient;
    uint32_t classified=0;
    bool throw_snapshot=false,throw_classify=false;
    std::atomic_bool *cancel_during=nullptr;
    ScanCode snapshot(std::vector<ProcessEntry>&out,const ScanControl&) override {
        if(throw_snapshot)throw std::runtime_error("synthetic enumeration failure");out=entries;return result;
    }
    CandidateDisposition classify(const ProcessEntry&,const ScanControl&) override {
        ++classified;if(throw_classify)throw std::runtime_error("synthetic image failure");
        if(cancel_during)cancel_during->store(true);return candidate;
    }
};
void algorithms() {
    check(max_process_name(L"MAX.EXE")&&max_process_name(L"max-SERVICE.exe"),"both canonical MAX names");
    for(auto name:{L"max",L"max.exe.old",L"other.exe",L"directory/max.exe",L"max-service"})
        check(!max_process_name(name),"only exact basename candidates");
    Fake source;source.entries={{1,L"other.exe"},{2,L"MAX.exe"},{3,L"max-service.exe"}};
    auto scan=scan_closed_clients(source);check(scan.complete&&scan.code==ScanCode::Complete&&scan.candidates==2&&source.classified==2,"all directories and both names count");
    source.candidate=CandidateDisposition::Original;scan=scan_closed_clients(source);
    check(scan.complete&&scan.candidates==0,"only verified original pair can be excluded");
    source.candidate=CandidateDisposition::Unknown;scan=scan_closed_clients(source);
    check(!scan.complete&&scan.code==ScanCode::Incomplete,"unavailable image path cannot permit import");
    source.throw_classify=true;scan=scan_closed_clients(source);check(!scan.complete,"classification exception incomplete");source.throw_classify=false;
    source.throw_snapshot=true;scan=scan_closed_clients(source);check(!scan.complete,"enumeration exception incomplete");source.throw_snapshot=false;
    source.result=ScanCode::Incomplete;scan=scan_closed_clients(source);check(!scan.complete,"partial enumeration refused");
    source.result=ScanCode::Cancelled;check(scan_closed_clients(source).code==ScanCode::Cancelled,"snapshot cancel retained");
    source.result=ScanCode::Deadline;check(scan_closed_clients(source).code==ScanCode::Deadline,"snapshot deadline retained");
    source.result=ScanCode::Complete;source.candidate=CandidateDisposition::Cancelled;check(scan_closed_clients(source).code==ScanCode::Cancelled,"hash cancel retained");
    source.candidate=CandidateDisposition::Deadline;check(scan_closed_clients(source).code==ScanCode::Deadline,"hash deadline retained");
    source.candidate=CandidateDisposition::Original;source.entries={{0,L"max.exe"}};check(!scan_closed_clients(source).complete,"invalid candidate PID incomplete");
    source.entries={{1,std::wstring(L"max.exe\0hidden",14)}};check(!scan_closed_clients(source).complete,"embedded NUL refuses metadata");
    source.entries={{1,L""}};check(!scan_closed_clients(source).complete,"empty name refuses metadata");
    source.entries.assign(kMaximumProcessEntries+1,{1,L"other.exe"});check(!scan_closed_clients(source).complete,"snapshot cap fail closed");
    source.entries={{1,L"max.exe"}};std::atomic_bool cancel=true;
    check(scan_closed_clients(source,{&cancel,UINT64_MAX}).code==ScanCode::Cancelled,"pre-cancelled scan does no work");
    cancel=false;source.cancel_during=&cancel;check(scan_closed_clients(source,{&cancel,UINT64_MAX}).code==ScanCode::Cancelled,"cancel after original classification cannot report complete");
    check(scan_closed_clients(source,{nullptr,0}).code==ScanCode::Deadline,"expired scan cannot report complete");

    const ClientScan empty{0,true,ScanCode::Complete},active{1,true,ScanCode::Complete},unknown{0,false,ScanCode::Incomplete};
    ClosedWait wait(100);check(wait.deadline_ms()==120100,"absolute 120 second deadline");
    check(wait.observe(empty,100)==WaitState::Waiting,"first absence starts settle");
    check(wait.observe(empty,2099)==WaitState::Waiting,"settle not shortened");
    check(wait.next_delay_ms(2099)==1,"poll respects remaining settle");
    check(wait.observe(empty,2100)==WaitState::Closed,"exact two second settle");
    check(wait.observe(active,2200)==WaitState::Closed&&wait.next_delay_ms(2200)==0,"terminal immutable; caller needs final fresh scan");
    ClosedWait reset(0);reset.observe(empty,0);reset.observe(active,1999);reset.observe(empty,2000);
    check(reset.observe(empty,3999)==WaitState::Waiting,"candidate resets settle");
    reset.observe(unknown,3999);reset.observe(empty,4000);
    check(reset.observe(empty,5999)==WaitState::Waiting&&reset.observe(empty,6000)==WaitState::Closed,"incomplete resets settle even when count zero");
    ClosedWait inconsistent(0);inconsistent.observe(empty,0);
    inconsistent.observe({0,true,ScanCode::Incomplete},1999);inconsistent.observe(empty,2000);
    check(inconsistent.observe(empty,3999)==WaitState::Waiting,"code and complete must both agree");
    ClosedWait cancelled(0);check(cancelled.observe(empty,0,true)==WaitState::Cancelled,"pure cancellation");
    ClosedWait deadline(0);deadline.observe(empty,119000);
    check(deadline.next_delay_ms(119999)==1&&deadline.observe(empty,120000)==WaitState::Deadline,"deadline takes precedence over late settle");
    ClosedWait rollback(100);check(rollback.observe(empty,99)==WaitState::ScanFailed,"clock rollback fails closed");
    ClosedWait overflow(UINT64_MAX-1);check(overflow.state()==WaitState::ScanFailed,"clock overflow fails closed");
    ClosedWait native_cancel(0);check(native_cancel.observe({0,false,ScanCode::Cancelled},1)==WaitState::Cancelled,"scanner cancel ends wait");
    ClosedWait native_deadline(0);check(native_deadline.observe({0,false,ScanCode::Deadline},1)==WaitState::Deadline,"scanner deadline ends wait");
}
struct Fixture {
    std::filesystem::path base,root;
    Fixture():base(L"C:/ValMAXBuild/fixtures/closed-clients") {
        std::array<unsigned char,16> bytes{};
        if(BCryptGenRandom(nullptr,bytes.data(),static_cast<ULONG>(bytes.size()),BCRYPT_USE_SYSTEM_PREFERRED_RNG)<0)throw std::runtime_error("fixture random unavailable");
        constexpr wchar_t hex[]=L"0123456789abcdef";std::wstring suffix;
        for(auto byte:bytes){suffix.push_back(hex[byte>>4]);suffix.push_back(hex[byte&15]);}
        std::filesystem::create_directories(base);root=base/suffix;
        if(!std::filesystem::create_directory(root))throw std::runtime_error("fixture collision");
    }
    ~Fixture(){
        std::error_code error;const auto canonical=std::filesystem::canonical(root,error);
        const auto expected=std::filesystem::canonical(base,error);
        if(!error&&canonical.parent_path()==expected&&canonical!=expected)std::filesystem::remove_all(canonical,error);
    }
};
void files() {
    for(auto path:{L"relative/max.exe",L"\\\\server\\share\\max.exe",L"C:\\bad\\..\\max.exe",L"C:\\bad.\\max.exe",L"C:\\bad\\max.exe:stream",L"C:\\bad\\max.exe\\"})
        check(original_image(path,L"max.exe")==CandidateDisposition::Unknown,"unsafe image path not excluded");
    check(original_image(L"C:\\owned\\max-service.exe",L"max.exe")==CandidateDisposition::Unknown,"reported basename mismatch not excluded");
    Fixture fixture;const auto exe=fixture.root/L"max.exe",config=fixture.root/L"config.dll";
    {std::ofstream a(exe,std::ios::binary);a<<"synthetic executable";std::ofstream b(config,std::ios::binary);b<<"synthetic config";}
    check(original_image(exe.wstring(),L"max.exe")==CandidateDisposition::PotentialClient,"unfamiliar normal pair counts as client");
    std::filesystem::remove(config);std::filesystem::create_directory(config);
    check(original_image(exe.wstring(),L"max.exe")==CandidateDisposition::PotentialClient,"directory config never excluded");
    std::filesystem::remove(config);{std::ofstream b(config,std::ios::binary);b<<"synthetic config";}
    std::filesystem::create_hard_link(exe,fixture.root/L"second-link");
    check(original_image(exe.wstring(),L"max.exe")==CandidateDisposition::PotentialClient,"hardlinked image never excluded");
    std::atomic_bool cancel=true;check(original_image(exe.wstring(),L"max.exe",{&cancel,UINT64_MAX})==CandidateDisposition::Cancelled,"pair check honors cancellation");
}
}
int wmain(int argc,wchar_t **argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    try {
        algorithms();files();
        // One read-only production enumeration; never assert the user's MAX is
        // closed. PIDs/paths are neither printed nor persisted.
        const auto real=scan_closed_clients({nullptr,GetTickCount64()+15000});
        check(real.candidates<=kMaximumProcessEntries,"real scan bounded");
        check(real.complete==(real.code==ScanCode::Complete),"real scan finite consistency");
        if(argc==3&&std::wstring_view(argv[1])==L"--original-image") {
            const std::filesystem::path path(argv[2]);
            check(original_image(path.wstring(),path.filename().wstring())==CandidateDisposition::Original,"immutable original pair verified");
            check(original_image(path.wstring(),path.filename().wstring())==CandidateDisposition::Original,"held original pair cache reopens safely");
        }else check(argc==1,"unsupported test argument");
        std::cout<<"{\"verified\":true,\"checks\":"<<checks<<",\"realEnumeration\":true,\"processControl\":false,\"profilesRead\":false,\"credentialsRead\":false,\"complete\":"
            <<(real.complete?"true":"false")<<",\"candidates\":"<<real.candidates<<"}\n";return 0;
    }catch(const std::exception &error){std::cerr<<error.what()<<'\n';return 1;}
}
