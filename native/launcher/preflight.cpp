#include "preflight.h"
#include <bcrypt.h>
#include <algorithm>
#include <array>
#include <cstring>
#include <filesystem>
#include <map>
#include <set>
#include <utility>
namespace valmax::launcher {
namespace {
struct Less {
    bool operator()(std::wstring_view a,std::wstring_view b) const {
        return CompareStringOrdinal(a.data(),static_cast<int>(a.size()),b.data(),static_cast<int>(b.size()),TRUE)==CSTR_LESS_THAN;
    }
};
bool equal(std::wstring_view a,std::wstring_view b) {return !Less{}(a,b) && !Less{}(b,a);}
bool component(std::wstring_view part) {
    if(part.empty() || part==L"." || part==L".." || part.back()==L'.' || part.back()==L' ')return false;
    for(auto c:part)if(c<32 || c==127 || std::wstring_view(L":<>\"|?*\\/").find(c)!=std::wstring_view::npos)return false;
    const auto stem=part.substr(0,part.find(L'.'));
    if(equal(stem,L"CON")||equal(stem,L"PRN")||equal(stem,L"AUX")||equal(stem,L"NUL"))return false;
    if(stem.size()==4 && (equal(stem.substr(0,3),L"COM")||equal(stem.substr(0,3),L"LPT"))
        && (stem[3]>=L'1'&&stem[3]<=L'9'))return false;
    return true;
}
bool local_root(std::wstring_view input,std::wstring &output) {
    if(input.size()<3 || input.size()>32000 || !((input[0]>=L'a'&&input[0]<=L'z')||(input[0]>=L'A'&&input[0]<=L'Z'))
        || input[1]!=L':' || (input[2]!=L'/'&&input[2]!=L'\\'))return false;
    output.assign(input);std::replace(output.begin(),output.end(),L'/',L'\\');
    if(output.size()==4 && output.back()==L'\\')return false;
    if(output.size()>3 && output.back()==L'\\')output.pop_back();
    if(output.size()>3 && output.back()==L'\\')return false;
    size_t at=3;while(at<output.size()) {
        const auto end=output.find(L'\\',at);const auto part=std::wstring_view(output).substr(at,end==output.npos?output.size()-at:end-at);
        if(!component(part))return false;at=end==output.npos?output.size():end+1;
    }
    return output.size()==3 || output[3]!=L'\\';
}
bool relative(const wchar_t *raw,std::wstring &output) {
    if(!raw)return false;const auto length=wcsnlen(raw,32768);if(!length||length>=32768)return false;
    std::wstring_view value(raw,length);if(value.front()==L'/'||value.back()==L'/'||value.find(L'\\')!=value.npos)return false;
    size_t at=0;while(at<value.size()) {
        const auto end=value.find(L'/',at);const auto part=value.substr(at,end==value.npos?value.size()-at:end-at);
        if(!component(part))return false;at=end==value.npos?value.size():end+1;
    }
    output.assign(value);std::replace(output.begin(),output.end(),L'/',L'\\');return true;
}
bool hash_format(const char *hash) {
    if(!hash || strnlen(hash,65)!=64)return false;
    for(size_t i=0;i<64;++i)if(!((hash[i]>='0'&&hash[i]<='9')||(hash[i]>='a'&&hash[i]<='f')))return false;
    return true;
}
std::wstring join(std::wstring_view root,std::wstring_view path) {
    std::wstring result(root);if(!result.ends_with(L'\\'))result.push_back(L'\\');result.append(path);return result;
}
bool final_matches(HANDLE handle,const std::wstring &expected) {
    std::array<wchar_t,32768> value{};const auto size=GetFinalPathNameByHandleW(handle,value.data(),static_cast<DWORD>(value.size()),FILE_NAME_NORMALIZED|VOLUME_NAME_DOS);
    if(!size||size>=value.size())return false;
    std::wstring_view actual(value.data(),size);if(actual.starts_with(L"\\\\?\\"))actual.remove_prefix(4);
    return equal(actual,expected);
}
PreflightError open_error() {
    const auto code=GetLastError();return code==ERROR_FILE_NOT_FOUND || code==ERROR_PATH_NOT_FOUND?PreflightError::MissingFile:PreflightError::IoError;
}
PreflightError hold_directory(const std::wstring &path,std::vector<HANDLE> &held) {
    const auto h=CreateFileW(path.c_str(),FILE_LIST_DIRECTORY|FILE_READ_ATTRIBUTES,FILE_SHARE_READ|FILE_SHARE_WRITE,
        nullptr,OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_BACKUP_SEMANTICS,nullptr);
    if(h==INVALID_HANDLE_VALUE)return open_error();
    try{held.push_back(h);}catch(...){CloseHandle(h);throw;}
    BY_HANDLE_FILE_INFORMATION info{};
    if(!GetFileInformationByHandle(h,&info)||GetFileType(h)!=FILE_TYPE_DISK)return PreflightError::IoError;
    if(!(info.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)||(info.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT)||!final_matches(h,path))return PreflightError::UnsafePath;
    return PreflightError::Ok;
}
bool file_hash(HANDLE file,const char *expected) {
    BCRYPT_ALG_HANDLE algorithm=nullptr;BCRYPT_HASH_HANDLE hash=nullptr;
    if(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0)return false;
    bool ok=BCryptCreateHash(algorithm,&hash,nullptr,0,nullptr,0,0)>=0;
    std::array<uint8_t,65536> buffer{};DWORD count=0;
    while(ok){if(!ReadFile(file,buffer.data(),static_cast<DWORD>(buffer.size()),&count,nullptr)){ok=false;break;}if(!count)break;
        ok=BCryptHashData(hash,buffer.data(),count,0)>=0;}
    std::array<uint8_t,32> value{};if(ok)ok=BCryptFinishHash(hash,value.data(),static_cast<ULONG>(value.size()),0)>=0;
    if(hash)BCryptDestroyHash(hash);BCryptCloseAlgorithmProvider(algorithm,0);
    constexpr char hex[]="0123456789abcdef";
    for(size_t i=0;ok&&i<value.size();++i)ok=expected[2*i]==hex[value[i]>>4]&&expected[2*i+1]==hex[value[i]&15];
    return ok;
}
PreflightError mutable_snapshot(const std::wstring &path) {
    const auto h=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,
        nullptr,OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
    if(h==INVALID_HANDLE_VALUE)return open_error();
    BY_HANDLE_FILE_INFORMATION info{};
    const bool ok=GetFileInformationByHandle(h,&info) && GetFileType(h)==FILE_TYPE_DISK
        && !(info.dwFileAttributes&(FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT))
        && info.nNumberOfLinks==1 && !info.nFileSizeHigh && info.nFileSizeLow<=4096 && final_matches(h,path);
    CloseHandle(h);return ok?PreflightError::Ok:PreflightError::UnsafePath;
}
PreflightError inventory(const std::wstring &root,const std::set<std::wstring,Less> &directories,
                         const std::map<std::wstring,const PayloadPin *,Less> &files,bool production) {
    struct FindGuard {HANDLE value;~FindGuard(){FindClose(value);}};
    uint32_t count=0;std::set<std::wstring,Less> found_files,found_directories;
    for(const auto &directory:directories) {
        WIN32_FIND_DATAW data{};const auto search=join(join(root,directory),L"*");
        const auto handle=FindFirstFileW(search.c_str(),&data);if(handle==INVALID_HANDLE_VALUE)return open_error();
        const FindGuard held{handle};
        PreflightError error=PreflightError::Ok;
        do {
            const std::wstring_view name(data.cFileName);if(name==L"."||name==L"..")continue;
            if(++count>(production?32768u:8192u)){error=PreflightError::Capacity;break;}
            if(!component(name)||(data.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT)){error=PreflightError::UnsafePath;break;}
            const auto path=directory.empty()?std::wstring(name):join(directory,name);
            if(production && equal(path,L"mod\\optimization-active.json")) {
                error=mutable_snapshot(join(root,path));if(error!=PreflightError::Ok)break;
            } else if(data.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY) {
                if(!directories.contains(path)||!found_directories.insert(path).second){error=PreflightError::UnexpectedEntry;break;}
            } else if(!files.contains(path)||!found_files.insert(path).second){error=PreflightError::UnexpectedEntry;break;}
        }while(FindNextFileW(handle,&data));
        const auto last=GetLastError();
        if(error!=PreflightError::Ok)return error;
        if(last!=ERROR_NO_MORE_FILES)return PreflightError::IoError;
    }
    return found_files.size()==files.size() && found_directories.size()+1==directories.size()?PreflightError::Ok:PreflightError::MissingFile;
}
}
VerifiedPayload::~VerifiedPayload(){release();}
VerifiedPayload::VerifiedPayload(VerifiedPayload &&other) noexcept {*this=std::move(other);}
VerifiedPayload &VerifiedPayload::operator=(VerifiedPayload &&other) noexcept {
    if(this!=&other){release();root_=std::move(other.root_);files_=std::move(other.files_);directories_=std::move(other.directories_);
        verified_=std::exchange(other.verified_,false);other.files_.clear();other.directories_.clear();}return *this;
}
void VerifiedPayload::release() noexcept {
    verified_=false;for(auto handle:files_)CloseHandle(handle);files_.clear();
    for(auto it=directories_.rbegin();it!=directories_.rend();++it)CloseHandle(*it);directories_.clear();root_.clear();
}
const char *preflight_code(PreflightError error) noexcept {
    switch(error){case PreflightError::Ok:return "verified";case PreflightError::InvalidRoot:return "invalid_root";
        case PreflightError::InvalidPin:return "invalid_pins";case PreflightError::MissingFile:return "missing_file";
        case PreflightError::UnsafePath:return "unsafe_path";case PreflightError::FileMismatch:return "file_mismatch";
        case PreflightError::UnexpectedEntry:return "unexpected_entry";case PreflightError::Capacity:return "capacity";
        default:return "io_error";}
}
PreflightError verify_payload_impl(std::wstring_view input,std::span<const PayloadPin> pins,VerifiedPayload &output,bool production) noexcept {
    output.release();VerifiedPayload candidate;
    try {
        if(!local_root(input,candidate.root_))return PreflightError::InvalidRoot;
        if(pins.empty()||pins.size()>(production?16384u:4096u))return PreflightError::InvalidPin;
        std::map<std::wstring,const PayloadPin *,Less> files;std::set<std::wstring,Less> directories{L""};
        for(const auto &pin:pins){std::wstring path;
            if(!relative(pin.relative,path)||!hash_format(pin.sha256)||pin.size>(1ull<<34)
                ||(production&&equal(path,L"mod\\optimization-active.json"))||!files.emplace(path,&pin).second
                ||join(candidate.root_,path).size()>=32768)return PreflightError::InvalidPin;
            auto parent=std::filesystem::path(path).parent_path();while(!parent.empty()){
                directories.insert(parent.wstring());if(directories.size()>(production?8192u:4096u))return PreflightError::Capacity;parent=parent.parent_path();}}
        if(!files.contains(production?L"mod\\max.exe":L"max.exe"))return PreflightError::InvalidPin;
        std::vector<std::wstring> ancestors;auto root=std::filesystem::path(candidate.root_);
        for(auto at=root;!at.empty();at=at.parent_path()){ancestors.push_back(at.wstring());if(ancestors.size()>256)return PreflightError::InvalidRoot;if(at==at.root_path())break;}
        for(auto it=ancestors.rbegin();it!=ancestors.rend();++it){const auto error=hold_directory(*it,candidate.directories_);if(error!=PreflightError::Ok)return error;}
        for(const auto &directory:directories)if(!directory.empty()){const auto error=hold_directory(join(candidate.root_,directory),candidate.directories_);if(error!=PreflightError::Ok)return error;}
        for(const auto &[relative_path,pin]:files){const auto path=join(candidate.root_,relative_path);
            const auto handle=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_SEQUENTIAL_SCAN,nullptr);
            if(handle==INVALID_HANDLE_VALUE)return open_error();try{candidate.files_.push_back(handle);}catch(...){CloseHandle(handle);throw;}
            BY_HANDLE_FILE_INFORMATION info{};
            if(!GetFileInformationByHandle(handle,&info)||GetFileType(handle)!=FILE_TYPE_DISK)return PreflightError::IoError;
            if((info.dwFileAttributes&(FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT))||info.nNumberOfLinks!=1||!final_matches(handle,path))return PreflightError::UnsafePath;
            if(((uint64_t(info.nFileSizeHigh)<<32)|info.nFileSizeLow)!=pin->size||!file_hash(handle,pin->sha256))return PreflightError::FileMismatch;
        }
        const auto error=inventory(candidate.root_,directories,files,production);if(error!=PreflightError::Ok)return error;
        candidate.verified_=true;output=std::move(candidate);return PreflightError::Ok;
    }catch(...){return PreflightError::IoError;}
}
PreflightError verify_payload(std::wstring_view input,std::span<const PayloadPin> pins,VerifiedPayload &output) noexcept {
    return verify_payload_impl(input,pins,output,false);
}
PreflightError verify_native_payload(std::wstring_view input,std::span<const PayloadPin> pins,VerifiedPayload &output) noexcept {
    return verify_payload_impl(input,pins,output,true);
}
}
