#include "preflight.h"
#include <bcrypt.h>
#include <winioctl.h>
#include <array>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <utility>
using namespace valmax::launcher;
namespace {
int checks=0;
bool check(bool value){++checks;if(!value)std::cerr<<"failed_check="<<checks<<",win32="<<GetLastError()<<'\n';return value;}
#define CHECK(x) do{if(!check(x))return 1;}while(false)
std::string sha(std::string_view bytes){
    std::array<unsigned char,32> digest{};
    if(BCryptHash(BCRYPT_SHA256_ALG_HANDLE,nullptr,0,reinterpret_cast<PUCHAR>(const_cast<char *>(bytes.data())),
        static_cast<ULONG>(bytes.size()),digest.data(),static_cast<ULONG>(digest.size()))<0)std::terminate();
    constexpr char hex[]="0123456789abcdef";std::string output;
    for(auto byte:digest){output+=hex[byte>>4];output+=hex[byte&15];}return output;
}
void write(const std::filesystem::path &path,std::string_view bytes){
    std::ofstream output(path,std::ios::binary|std::ios::trunc);output.write(bytes.data(),static_cast<std::streamsize>(bytes.size()));
    if(!output)std::terminate();
}
bool writable(const std::filesystem::path &path){
    const auto h=CreateFileW(path.c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,0,nullptr);
    if(h==INVALID_HANDLE_VALUE)return false;CloseHandle(h);return true;
}
// Mount-point reparse on an owned empty directory requires no symlink privilege.
bool junction(const std::filesystem::path &link,const std::filesystem::path &target){
    const std::wstring substitute=L"\\??\\"+target.wstring(),display=target.wstring();
    struct Reparse {DWORD tag;WORD length,reserved,sub_offset,sub_length,print_offset,print_length;wchar_t path[2048];} data{};
    if(substitute.size()+display.size()+2>std::size(data.path))return false;
    data.tag=IO_REPARSE_TAG_MOUNT_POINT;data.sub_length=static_cast<WORD>(substitute.size()*sizeof(wchar_t));
    data.print_offset=data.sub_length+sizeof(wchar_t);data.print_length=static_cast<WORD>(display.size()*sizeof(wchar_t));
    data.length=static_cast<WORD>(8+data.print_offset+data.print_length+sizeof(wchar_t));
    std::memcpy(data.path,substitute.c_str(),data.sub_length+sizeof(wchar_t));
    std::memcpy(reinterpret_cast<unsigned char *>(data.path)+data.print_offset,display.c_str(),data.print_length+sizeof(wchar_t));
    const auto h=CreateFileW(link.c_str(),GENERIC_WRITE,0,nullptr,OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_BACKUP_SEMANTICS,nullptr);
    if(h==INVALID_HANDLE_VALUE)return false;DWORD returned=0;
    const bool ok=DeviceIoControl(h,FSCTL_SET_REPARSE_POINT,&data,8+data.length,nullptr,0,&returned,nullptr)!=0;CloseHandle(h);return ok;
}
struct Fixture {
    std::filesystem::path parent,root;
    std::array<std::string,3> contents{"synthetic executable bytes", "synthetic UI", std::string(200000,'x')+"синтетический файл"};
    std::array<std::string,3> hashes;
    std::array<PayloadPin,3> pins{};
    Fixture(){parent=std::filesystem::path(L"C:/ValMAXBuild/fixtures")/(L"preflight-"+std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(GetTickCount64()));
        root=parent/L"runtime";std::filesystem::create_directories(root/L"sub");
        std::array<const wchar_t *,3> names{L"max.exe",L"sub/ui.qml",L"sub/Аватар.png"};
        for(size_t i=0;i<pins.size();++i){hashes[i]=sha(contents[i]);pins[i]={names[i],contents[i].size(),hashes[i].c_str()};restore(i);}}
    void restore(size_t i){write(root/pins[i].relative,contents[i]);}
    PreflightError verify(VerifiedPayload &out){return verify_payload(root.wstring(),pins,out);}
};
}
int main(){
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    Fixture fixture;VerifiedPayload result;
    CHECK(fixture.verify(result)==PreflightError::Ok&&result.verified()&&result.file_count()==3);
    CHECK(!writable(fixture.root/L"max.exe"));
    CHECK(!DeleteFileW((fixture.root/L"max.exe").c_str()));
    const auto moved=fixture.parent/L"moved";
    CHECK(!MoveFileExW(fixture.root.c_str(),moved.c_str(),0));
    CHECK(!MoveFileExW((fixture.root/L"sub").c_str(),(fixture.root/L"other").c_str(),0));
    const auto moved_parent=fixture.parent.parent_path()/(fixture.parent.filename().wstring()+L"-moved");
    CHECK(!MoveFileExW(fixture.parent.c_str(),moved_parent.c_str(),0));
    VerifiedPayload transferred(std::move(result));
    CHECK(!result.verified()&&result.file_count()==0&&transferred.verified()&&!writable(fixture.root/L"max.exe"));
    result=std::move(transferred);CHECK(result.verified()&&!transferred.verified());
    result.release();CHECK(!result.verified()&&writable(fixture.root/L"max.exe"));
    CHECK(MoveFileExW(fixture.root.c_str(),moved.c_str(),0));
    CHECK(MoveFileExW(moved.c_str(),fixture.root.c_str(),0));
    // Reject path aliases before touching the filesystem.
    for(const auto *path:{L"",L"relative",L"C:relative",L"\\\\server\\share",L"\\\\?\\C:\\x",L"C://",L"C:/x/../y",L"C:/x/./y",L"C:/x//y",L"C:/x//",L"C:/x.",L"C:/x ",L"C:/x:ads",L"C:/NUL",L"C:/COM1.txt"}){
        CHECK(verify_payload(path,fixture.pins,result)==PreflightError::InvalidRoot&&!result.verified());}
    auto slash=fixture.root.generic_wstring();slash[0]=L'c';slash+=L'/';
    CHECK(verify_payload(slash,fixture.pins,result)==PreflightError::Ok);result.release();
    const auto original=fixture.pins;
    for(const auto *path:{L"",L"/absolute",L"sub//ui.qml",L"sub/../ui.qml",L"sub/",L"sub\\ui.qml",L"sub/x:ads",L"CON.txt",L"sub/NUL"}){
        fixture.pins[0].relative=path;CHECK(fixture.verify(result)==PreflightError::InvalidPin&&!result.verified());}
    fixture.pins=original;fixture.pins[1].relative=L"MAX.EXE";CHECK(fixture.verify(result)==PreflightError::InvalidPin);fixture.pins=original;
    fixture.pins[0].relative=nullptr;CHECK(fixture.verify(result)==PreflightError::InvalidPin);fixture.pins=original;
    fixture.pins[0].sha256="abcdef";CHECK(fixture.verify(result)==PreflightError::InvalidPin);fixture.pins=original;
    fixture.pins[0].size=1ull<<35;CHECK(fixture.verify(result)==PreflightError::InvalidPin);fixture.pins=original;
    CHECK(verify_payload(fixture.root.wstring(),{},result)==PreflightError::InvalidPin);
    CHECK(verify_payload(fixture.root.wstring(),std::span(fixture.pins).subspan(1),result)==PreflightError::InvalidPin);
    const std::vector<PayloadPin> oversized(4097,fixture.pins[0]);
    CHECK(verify_payload(fixture.root.wstring(),oversized,result)==PreflightError::InvalidPin);
    const auto active_writer=CreateFileW((fixture.root/L"max.exe").c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,0,nullptr);
    CHECK(active_writer!=INVALID_HANDLE_VALUE);
    CHECK(fixture.verify(result)==PreflightError::IoError&&!result.verified());CloseHandle(active_writer);
    CHECK(fixture.verify(result)==PreflightError::Ok);result.release();
    CHECK(DeleteFileW((fixture.root/L"max.exe").c_str()));
    CHECK(fixture.verify(result)==PreflightError::MissingFile&&!result.verified());fixture.restore(0);
    write(fixture.root/L"max.exe","x");CHECK(fixture.verify(result)==PreflightError::FileMismatch&&!result.verified());fixture.restore(0);
    auto tamper=fixture.contents[0];tamper[0]^=1;write(fixture.root/L"max.exe",tamper);
    CHECK(fixture.verify(result)==PreflightError::FileMismatch&&!result.verified());fixture.restore(0);
    const auto hardlink=fixture.parent/L"hardlink.exe";
    CHECK(CreateHardLinkW(hardlink.c_str(),(fixture.root/L"max.exe").c_str(),nullptr));
    CHECK(fixture.verify(result)==PreflightError::UnsafePath&&!result.verified());CHECK(DeleteFileW(hardlink.c_str()));
    write(fixture.root/L"unexpected.dll","unexpected");
    CHECK(fixture.verify(result)==PreflightError::UnexpectedEntry&&!result.verified());CHECK(DeleteFileW((fixture.root/L"unexpected.dll").c_str()));
    std::filesystem::create_directory(fixture.root/L"unexpected");
    CHECK(fixture.verify(result)==PreflightError::UnexpectedEntry&&!result.verified());CHECK(RemoveDirectoryW((fixture.root/L"unexpected").c_str()));
    const auto redirect=fixture.parent/L"redirect";std::filesystem::create_directory(redirect);
    CHECK(junction(redirect,fixture.root));
    CHECK(verify_payload(redirect.wstring(),fixture.pins,result)==PreflightError::UnsafePath&&!result.verified());
    CHECK(RemoveDirectoryW(redirect.c_str()));
    const auto child=fixture.root/L"sub",child_saved=fixture.parent/L"saved-sub";
    CHECK(MoveFileExW(child.c_str(),child_saved.c_str(),0));std::filesystem::create_directory(child);
    CHECK(junction(child,child_saved));CHECK(fixture.verify(result)==PreflightError::UnsafePath&&!result.verified());
    CHECK(RemoveDirectoryW(child.c_str()));CHECK(MoveFileExW(child_saved.c_str(),child.c_str(),0));
    // Existing successful state is revoked by a subsequent failure, including handles.
    CHECK(fixture.verify(result)==PreflightError::Ok);
    CHECK(verify_payload(L"relative",fixture.pins,result)==PreflightError::InvalidRoot&&!result.verified()&&writable(fixture.root/L"max.exe"));
    DWORD before=0,after=0;CHECK(GetProcessHandleCount(GetCurrentProcess(),&before));
    bool repeat=true;for(int i=0;i<40;++i){if(fixture.verify(result)!=PreflightError::Ok)repeat=false;result.release();}
    CHECK(GetProcessHandleCount(GetCurrentProcess(),&after));CHECK(repeat&&after<=before+1);
    CHECK(fixture.verify(result)==PreflightError::Ok);result.release();
    std::cout<<"{\"schema\":1,\"ok\":true,\"checks\":"<<checks<<",\"scope\":\"owned synthetic file integrity, sharing, reparse and lifetime fixtures; no client execution\"}\n";
}
