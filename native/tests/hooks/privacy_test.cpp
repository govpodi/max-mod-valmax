#include "privacy_internal.h"
#include "privacy_pins.generated.h"
#include <bcrypt.h>
#include <algorithm>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <thread>
#include <atomic>
using namespace valmax::privacy;
namespace {
int checks=0;bool check(bool ok){++checks;if(!ok)std::cerr<<"failed_check="<<checks<<'\n';return ok;}
#define CHECK(x) do{if(!check(x))return 1;}while(false)
std::string sha(std::span<const uint8_t> bytes){std::array<uint8_t,32> output{};
    if(BCryptHash(BCRYPT_SHA256_ALG_HANDLE,nullptr,0,const_cast<PUCHAR>(bytes.data()),static_cast<ULONG>(bytes.size()),output.data(),static_cast<ULONG>(output.size()))<0)std::terminate();
    constexpr char hex[]="0123456789abcdef";std::string result;for(auto byte:output){result+=hex[byte>>4];result+=hex[byte&15];}return result;}
struct Fixture {
    std::filesystem::path root;
    std::array<std::array<uint8_t,4096>,6> bytes{};
    std::array<std::vector<PatchPin>,6> patches;
    std::array<std::string,6> hashes;
    std::array<FilePin,6> files{};
    std::array<uintptr_t,6> loaded{};
    Fixture(){root=std::filesystem::path(L"C:/ValMAXBuild/fixtures")/(L"privacy-"+std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(GetTickCount64()));std::filesystem::create_directories(root/L"max-service");
        for(size_t i=0;i<files.size();++i){
            IMAGE_DOS_HEADER dos{};dos.e_magic=IMAGE_DOS_SIGNATURE;dos.e_lfanew=128;std::memcpy(bytes[i].data(),&dos,sizeof(dos));
            IMAGE_NT_HEADERS64 pe{};pe.Signature=IMAGE_NT_SIGNATURE;pe.FileHeader.Machine=IMAGE_FILE_MACHINE_AMD64;pe.OptionalHeader.Magic=IMAGE_NT_OPTIONAL_HDR64_MAGIC;pe.OptionalHeader.SizeOfImage=4096;
            std::memcpy(bytes[i].data()+128,&pe,sizeof(pe));
            for(size_t j=0;j<pins::kFiles[i].patches.size();++j){auto patch=pins::kFiles[i].patches[j];patch.rva=patch.offset=1024+static_cast<uint32_t>(j)*32;
                patches[i].push_back(patch);std::copy(patch.bytes.begin(),patch.bytes.end(),bytes[i].begin()+patch.offset);}
            hashes[i]=sha(bytes[i]);files[i]={pins::kFiles[i].relative,hashes[i].c_str(),4096,4096,pins::kFiles[i].role,patches[i]};
            auto memory=VirtualAlloc(nullptr,4096,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);if(!memory)std::terminate();loaded[i]=reinterpret_cast<uintptr_t>(memory);
            std::memcpy(memory,bytes[i].data(),4096);DWORD old=0;if(!VirtualProtect(memory,4096,PAGE_EXECUTE_READ,&old))std::terminate();write(i);
        }}
    ~Fixture(){valmax_privacy_shutdown();for(auto memory:loaded)VirtualFree(reinterpret_cast<void *>(memory),0,MEM_RELEASE);}
    void write(size_t i){std::ofstream file(root/files[i].relative,std::ios::binary);file.write(reinterpret_cast<const char *>(bytes[i].data()),4096);}
    void memory_byte(size_t i,uint32_t offset,uint8_t value){auto pointer=reinterpret_cast<void *>(loaded[i]);DWORD old=0;VirtualProtect(pointer,4096,PAGE_READWRITE,&old);reinterpret_cast<uint8_t *>(pointer)[offset]=value;DWORD unused=0;VirtualProtect(pointer,4096,old,&unused);}
    uint32_t verify(uint32_t role=1){return verify_synthetic(root.c_str(),role,files,loaded);}
};
ValmaxPrivacyStatus status(){ValmaxPrivacyStatus value{};value.size=sizeof(value);value.version=1;if(valmax_privacy_status(&value))std::terminate();return value;}
}
int main(){SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    Fixture fixture;
    CHECK(sizeof(ValmaxPrivacyStatus)==112);
    CHECK(fixture.verify()==0);auto main=status();CHECK(main.ready&&main.disk_rules==63&&main.loaded_rules==53&&main.files_verified==6&&main.modules_verified==3&&main.patches_verified==31);
    CHECK(fixture.verify(2)==0);auto service=status();CHECK(service.ready&&service.disk_rules==63&&service.loaded_rules==58&&service.modules_verified==3);
    auto path=fixture.root/fixture.files[0].relative;
    HANDLE writer=CreateFileW(path.c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,0,nullptr);
    CHECK(writer==INVALID_HANDLE_VALUE);if(writer!=INVALID_HANDLE_VALUE)CloseHandle(writer);
    // Metadata-only directory handles do not enforce rename sharing on Windows.
    // Both the runtime root and child directory must remain at their verified paths.
    const auto moved_root=fixture.root.parent_path()/(fixture.root.filename().wstring()+L"-moved");
    const auto child=fixture.root/L"max-service", moved_child=fixture.root/L"moved-service";
    CHECK(!MoveFileExW(fixture.root.c_str(),moved_root.c_str(),0));
    CHECK(!MoveFileExW(child.c_str(),moved_child.c_str(),0));
    CHECK(valmax_privacy_shutdown()==0&&!status().ready);
    CHECK(MoveFileExW(child.c_str(),moved_child.c_str(),0));
    CHECK(MoveFileExW(moved_child.c_str(),child.c_str(),0));
    CHECK(MoveFileExW(fixture.root.c_str(),moved_root.c_str(),0));
    CHECK(MoveFileExW(moved_root.c_str(),fixture.root.c_str(),0));
    // Shutdown releases handles but never changes any static prevention byte.
    for(size_t i=0;i<6;++i){std::ifstream stream(fixture.root/fixture.files[i].relative,std::ios::binary);std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(stream)),{});CHECK(sha(bytes)==fixture.hashes[i]);}
    CHECK(fixture.verify()==0);valmax_privacy_shutdown();
    // Full SHA rejects any tampering, including outside a patched span.
    fixture.bytes[0][3000]^=1;fixture.write(0);CHECK(fixture.verify()==VALMAX_PRIVACY_FILE_MISMATCH&&!status().ready);valmax_privacy_shutdown();fixture.bytes[0][3000]^=1;fixture.write(0);
    // Even a matching synthetic hash cannot falsely accept unpatched/partial
    // spans. The production build never accepts caller-provided hashes.
    auto offset=fixture.patches[0][0].offset;fixture.bytes[0][offset]=0x48;fixture.write(0);
    auto old_hash=fixture.hashes[0];fixture.hashes[0]=sha(fixture.bytes[0]);fixture.files[0].sha256=fixture.hashes[0].c_str();
    CHECK(fixture.verify()==VALMAX_PRIVACY_FILE_MISMATCH&&!status().ready);valmax_privacy_shutdown();
    fixture.bytes[0][offset]=fixture.patches[0][0].bytes[0];fixture.write(0);fixture.hashes[0]=old_hash;fixture.files[0].sha256=fixture.hashes[0].c_str();
    // Loaded code can differ even when the exact disk image is still correct.
    constexpr size_t main_config=1;auto loaded_offset=fixture.patches[main_config][0].rva;auto original=fixture.bytes[main_config][loaded_offset];
    fixture.memory_byte(main_config,loaded_offset,0x48);CHECK(fixture.verify()==VALMAX_PRIVACY_MEMORY_MISMATCH&&!status().ready);valmax_privacy_shutdown();fixture.memory_byte(main_config,loaded_offset,original);
    // Non-executable mappings cannot masquerade as loaded code.
    DWORD old=0;VirtualProtect(reinterpret_cast<void *>(fixture.loaded[main_config]),4096,PAGE_READONLY,&old);
    CHECK(fixture.verify()==VALMAX_PRIVACY_MEMORY_MISMATCH);valmax_privacy_shutdown();DWORD unused=0;VirtualProtect(reinterpret_cast<void *>(fixture.loaded[main_config]),4096,old,&unused);
    CHECK(fixture.verify()==0);valmax_privacy_shutdown();
    // File links and partial inventories cannot produce a complete status.
    auto link=fixture.root/L"extra.dll";CHECK(CreateHardLinkW(link.c_str(),path.c_str(),nullptr)!=0);CHECK(fixture.verify()==VALMAX_PRIVACY_FILE_MISMATCH);valmax_privacy_shutdown();CHECK(DeleteFileW(link.c_str())!=0);
    CHECK(verify_synthetic(fixture.root.c_str(),1,std::span(fixture.files).first(5),fixture.loaded)==VALMAX_PRIVACY_FILE_MISMATCH&&!status().ready);valmax_privacy_shutdown();
    CHECK(valmax_privacy_verify(nullptr,1)==VALMAX_PRIVACY_INVALID_ARGUMENT);
    CHECK(valmax_privacy_verify(L"relative",1)==VALMAX_PRIVACY_INVALID_ARGUMENT);
    CHECK(valmax_privacy_verify(fixture.root.c_str(),3)==VALMAX_PRIVACY_INVALID_ARGUMENT);
    CHECK(valmax_privacy_status(nullptr)==VALMAX_PRIVACY_INVALID_ARGUMENT);
    CHECK(fixture.verify()==0);
    std::atomic<bool> concurrent_ok{true};std::thread reader([&]{for(int i=0;i<100;++i){auto value=status();if(value.ready&&(value.disk_rules!=63||value.loaded_rules!=53))concurrent_ok=false;}});
    for(int i=0;i<3;++i){valmax_privacy_shutdown();if(fixture.verify()!=0)concurrent_ok=false;}reader.join();CHECK(concurrent_ok);
    // MEM_PRIVATE fixtures are accepted only by the synthetic entry point.
    CHECK(!loaded_patches(fixture.loaded[1],fixture.files[1],true));
    CHECK(loaded_patches(fixture.loaded[1],fixture.files[1],false));
    fixture.memory_byte(main_config,loaded_offset,0x48);CHECK(!status().ready&&status().error==VALMAX_PRIVACY_MEMORY_MISMATCH);
    fixture.memory_byte(main_config,loaded_offset,original);CHECK(!status().ready); // fresh full verify required
    CHECK(fixture.verify()==0&&status().ready);
    std::cout<<"{\"schema\":1,\"ok\":true,\"checks\":"<<checks<<",\"callbacks\":0,\"codeWrites\":0,\"scope\":\"synthetic static privacy verification; owned buffers only\"}\n";
}
