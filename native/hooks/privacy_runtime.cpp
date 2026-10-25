#include "privacy_internal.h"
#include "privacy_pins.generated.h"
#include <bcrypt.h>
#include <algorithm>
#include <cstring>
#include <filesystem>
#include <mutex>
namespace valmax::privacy {
namespace {
bool read(uintptr_t address,void *output,size_t count) noexcept {
    if(!address||!output||!count||address>UINTPTR_MAX-count)return false;
    SIZE_T copied=0;return ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<const void *>(address),output,count,&copied)&&copied==count;
}
template<class T> bool read(uintptr_t address,T &output) noexcept {return read(address,&output,sizeof(output));}
bool equal_path(std::wstring a,std::wstring b) {
    std::replace(a.begin(),a.end(),L'/',L'\\');std::replace(b.begin(),b.end(),L'/',L'\\');
    return CompareStringOrdinal(a.c_str(),-1,b.c_str(),-1,TRUE)==CSTR_EQUAL;
}
bool final_matches(HANDLE file,const std::wstring &expected) {
    std::wstring actual(32768,L'\0');auto count=GetFinalPathNameByHandleW(file,actual.data(),static_cast<DWORD>(actual.size()),FILE_NAME_NORMALIZED|VOLUME_NAME_DOS);
    if(!count||count>=actual.size())return false;actual.resize(count);if(actual.starts_with(L"\\\\?\\"))actual.erase(0,4);return equal_path(actual,expected);
}
bool safe_root(const wchar_t *pointer,std::filesystem::path &output) {
    std::wstring value;
    if(!pointer)return false;
    for(size_t i=0;i<32768;++i){wchar_t ch=0;if(!read(reinterpret_cast<uintptr_t>(pointer)+i*sizeof(wchar_t),ch))return false;
        if(!ch)break;value+=ch;}
    if(value.size()<3||value.size()>=32767||value[1]!=L':'||(value[2]!=L'\\'&&value[2]!=L'/'))return false;
    output=std::filesystem::path(value).lexically_normal();
    if(!equal_path(output.wstring(),value))return false;
    for(const auto &part:output.relative_path()) {auto item=part.wstring();if(item.empty()||item.back()==L' '||item.back()==L'.'||item.find(L':')!=item.npos)return false;}
    return true;
}
struct Runtime {
    std::mutex mutex;
    std::vector<HANDLE> files,directories;
    std::vector<HMODULE> modules;
    struct Loaded {uintptr_t base;const FilePin *pin;bool synthetic;};
    std::vector<Loaded> checked;
    ValmaxPrivacyStatus status{sizeof(ValmaxPrivacyStatus),1,VALMAX_PRIVACY_NOT_VERIFIED};
    void clear() {
        for(auto module:modules)FreeLibrary(module);modules.clear();
        checked.clear();
        for(auto file:files)CloseHandle(file);files.clear();
        for(auto it=directories.rbegin();it!=directories.rend();++it)CloseHandle(*it);directories.clear();
        status={sizeof(ValmaxPrivacyStatus),1,VALMAX_PRIVACY_NOT_VERIFIED};
    }
};
Runtime &runtime(){static Runtime *value=new Runtime;return *value;}
bool hold_directory(Runtime &state,const std::filesystem::path &directory) {
    auto handle=CreateFileW(directory.c_str(),FILE_LIST_DIRECTORY|FILE_READ_ATTRIBUTES,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_BACKUP_SEMANTICS,nullptr);
    if(handle==INVALID_HANDLE_VALUE)return false;
    state.directories.push_back(handle);BY_HANDLE_FILE_INFORMATION info{};
    return GetFileInformationByHandle(handle,&info)&&(info.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)
        &&!(info.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT)&&final_matches(handle,directory.wstring());
}
uint32_t verify(Runtime &state,const std::filesystem::path &root,uint32_t role,std::span<const FilePin> files,
                std::span<const uintptr_t> synthetic={}) {
    state.status.role=role;state.status.required_loaded_rules=role==VALMAX_PRIVACY_MAIN?53u:58u;
    std::memcpy(state.status.manifest_sha256,pins::kManifestSha256,64);
    std::vector<std::filesystem::path> ancestors;
    auto cursor=root;
    while(!cursor.empty()){ancestors.push_back(cursor);if(ancestors.size()>256)return VALMAX_PRIVACY_INVALID_ARGUMENT;
        if(cursor==cursor.root_path())break;cursor=cursor.parent_path();}
    for(auto it=ancestors.rbegin();it!=ancestors.rend();++it)if(!hold_directory(state,*it))return VALMAX_PRIVACY_IO_ERROR;
    if(!hold_directory(state,root/L"max-service"))return VALMAX_PRIVACY_IO_ERROR;
    uint32_t disk_rules=0,loaded_rules=0;
    for(size_t i=0;i<files.size();++i){
        const auto &pin=files[i];const auto path=root/pin.relative;
        auto handle=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_SEQUENTIAL_SCAN,nullptr);
        if(handle==INVALID_HANDLE_VALUE)return VALMAX_PRIVACY_IO_ERROR;
        state.files.push_back(handle);
        if(!final_matches(handle,path.wstring())||!file_matches(handle,pin))return VALMAX_PRIVACY_FILE_MISMATCH;
        ++state.status.files_verified;state.status.patches_verified+=static_cast<uint32_t>(pin.patches.size());
        for(const auto &patch:pin.patches)disk_rules|=patch.rule;
        if(pin.role!=role)continue;
        uintptr_t base=0;
#ifdef VALMAX_PRIVACY_TESTING
        if(!synthetic.empty()){if(i>=synthetic.size())return VALMAX_PRIVACY_INVALID_ARGUMENT;base=synthetic[i];}
        else
#else
        (void)synthetic;
#endif
        {
            HMODULE module=nullptr;
            if(!GetModuleHandleExW(0,path.c_str(),&module))return GetModuleHandleW(path.filename().c_str())?VALMAX_PRIVACY_MEMORY_MISMATCH:VALMAX_PRIVACY_MODULE_NOT_LOADED;
            state.modules.push_back(module);
            std::wstring loaded(32768,L'\0');auto size=GetModuleFileNameW(module,loaded.data(),static_cast<DWORD>(loaded.size()));
            if(!size||size>=loaded.size())return VALMAX_PRIVACY_MEMORY_MISMATCH;loaded.resize(size);
            if(!equal_path(loaded,path.wstring()))return VALMAX_PRIVACY_MEMORY_MISMATCH;base=reinterpret_cast<uintptr_t>(module);
        }
        if(!loaded_patches(base,pin,synthetic.empty()))return VALMAX_PRIVACY_MEMORY_MISMATCH;
        state.checked.push_back({base,&pin,!synthetic.empty()});
        ++state.status.modules_verified;for(const auto &patch:pin.patches)loaded_rules|=patch.rule;
    }
    if(files.size()!=6||disk_rules!=63||loaded_rules!=state.status.required_loaded_rules)return VALMAX_PRIVACY_FILE_MISMATCH;
    state.status.disk_rules=disk_rules;state.status.loaded_rules=loaded_rules;
    state.status.ready=1;return VALMAX_PRIVACY_OK;
}
}
bool file_matches(HANDLE file,const FilePin &pin) {
    BY_HANDLE_FILE_INFORMATION info{};
    if(file==INVALID_HANDLE_VALUE||GetFileType(file)!=FILE_TYPE_DISK||!GetFileInformationByHandle(file,&info)
        ||(info.dwFileAttributes&(FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT))||info.nNumberOfLinks!=1
        ||((uint64_t(info.nFileSizeHigh)<<32)|info.nFileSizeLow)!=pin.file_size)return false;
    LARGE_INTEGER start{};if(!SetFilePointerEx(file,start,nullptr,FILE_BEGIN))return false;
    BCRYPT_ALG_HANDLE algorithm=nullptr;BCRYPT_HASH_HANDLE hash=nullptr;
    if(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0)return false;
    bool ok=BCryptCreateHash(algorithm,&hash,nullptr,0,nullptr,0,0)>=0;
    std::array<uint8_t,65536> buffer{};DWORD count=0;
    while(ok){if(!ReadFile(file,buffer.data(),static_cast<DWORD>(buffer.size()),&count,nullptr)){ok=false;break;}if(!count)break;
        ok=BCryptHashData(hash,buffer.data(),count,0)>=0;}
    std::array<uint8_t,32> digest{};if(ok)ok=BCryptFinishHash(hash,digest.data(),static_cast<ULONG>(digest.size()),0)>=0;
    if(hash)BCryptDestroyHash(hash);BCryptCloseAlgorithmProvider(algorithm,0);
    constexpr char hex[]="0123456789abcdef";
    for(size_t i=0;ok&&i<digest.size();++i)ok=pin.sha256[2*i]==hex[digest[i]>>4]&&pin.sha256[2*i+1]==hex[digest[i]&15];
    // Verify the exact patched spans independently of the complete file hash.
    for(const auto &patch:pin.patches){if(!ok||patch.bytes.empty()||patch.bytes.size()>32||patch.offset>pin.file_size||patch.bytes.size()>pin.file_size-patch.offset)return false;
        LARGE_INTEGER position{};position.QuadPart=patch.offset;std::array<uint8_t,32> actual{};
        if(!SetFilePointerEx(file,position,nullptr,FILE_BEGIN)||!ReadFile(file,actual.data(),static_cast<DWORD>(patch.bytes.size()),&count,nullptr)
            ||count!=patch.bytes.size()||!std::equal(patch.bytes.begin(),patch.bytes.end(),actual.begin()))return false;}
    return ok;
}
bool loaded_patches(uintptr_t base,const FilePin &pin,bool require_image) {
    IMAGE_DOS_HEADER dos{};IMAGE_NT_HEADERS64 pe{};
    if(!read(base,dos)||dos.e_magic!=IMAGE_DOS_SIGNATURE||dos.e_lfanew<=0||pin.image_size<sizeof(pe)
        ||static_cast<uint32_t>(dos.e_lfanew)>pin.image_size-sizeof(pe)||!read(base+dos.e_lfanew,pe)
        ||pe.Signature!=IMAGE_NT_SIGNATURE||pe.FileHeader.Machine!=IMAGE_FILE_MACHINE_AMD64
        ||pe.OptionalHeader.Magic!=IMAGE_NT_OPTIONAL_HDR64_MAGIC||pe.OptionalHeader.SizeOfImage!=pin.image_size)return false;
    for(const auto &patch:pin.patches){if(patch.bytes.empty()||patch.bytes.size()>32||patch.rva>=pin.image_size||patch.bytes.size()>pin.image_size-patch.rva)return false;
        MEMORY_BASIC_INFORMATION memory{};
        if(!VirtualQuery(reinterpret_cast<void *>(base+patch.rva),&memory,sizeof(memory))||memory.State!=MEM_COMMIT
            ||(memory.Protect&(PAGE_GUARD|PAGE_NOACCESS))||!(memory.Protect&(PAGE_EXECUTE_READ|PAGE_EXECUTE_READWRITE|PAGE_EXECUTE_WRITECOPY))
            ||(require_image&&(memory.Type!=MEM_IMAGE||memory.AllocationBase!=reinterpret_cast<void *>(base))))return false;
        std::array<uint8_t,32> actual{};if(!read(base+patch.rva,actual.data(),patch.bytes.size())||!std::equal(patch.bytes.begin(),patch.bytes.end(),actual.begin()))return false;}
    return true;
}
#ifdef VALMAX_PRIVACY_TESTING
uint32_t verify_synthetic(const wchar_t *input,uint32_t role,std::span<const FilePin> files,std::span<const uintptr_t> loaded) {
    auto &state=runtime();std::lock_guard guard(state.mutex);state.clear();std::filesystem::path root;
    if(!safe_root(input,root)||(role!=1&&role!=2))return VALMAX_PRIVACY_INVALID_ARGUMENT;
    state.status.error=verify(state,root,role,files,loaded);return state.status.error;
}
#endif
}
using namespace valmax::privacy;
uint32_t valmax_privacy_verify(const wchar_t *input,uint32_t role) {
    auto &state=runtime();std::lock_guard guard(state.mutex);state.clear();
    try {std::filesystem::path root;
        if(!safe_root(input,root)||(role!=VALMAX_PRIVACY_MAIN&&role!=VALMAX_PRIVACY_SERVICE))return state.status.error=VALMAX_PRIVACY_INVALID_ARGUMENT;
        return state.status.error=verify(state,root,role,pins::kFiles);
    }catch(...){state.clear();return state.status.error=VALMAX_PRIVACY_IO_ERROR;}
}
uint32_t valmax_privacy_status(ValmaxPrivacyStatus *output) {
    ValmaxPrivacyStatus request{};
    if(!read(reinterpret_cast<uintptr_t>(output),request)||request.size!=sizeof(request)||request.version!=1)return VALMAX_PRIVACY_INVALID_ARGUMENT;
    auto &state=runtime();std::lock_guard guard(state.mutex);SIZE_T written=0;
    if(state.status.ready)for(const auto &image:state.checked)if(!loaded_patches(image.base,*image.pin,!image.synthetic)){
        state.status.ready=0;state.status.loaded_rules=0;state.status.error=VALMAX_PRIVACY_MEMORY_MISMATCH;break;}
    return WriteProcessMemory(GetCurrentProcess(),output,&state.status,sizeof(state.status),&written)&&written==sizeof(state.status)?VALMAX_PRIVACY_OK:VALMAX_PRIVACY_INVALID_ARGUMENT;
}
uint32_t valmax_privacy_shutdown(){auto &state=runtime();std::lock_guard guard(state.mutex);state.clear();return VALMAX_PRIVACY_OK;}
