#include "runtime_prepare.h"
#include <QCoreApplication>
#include <QDir>
#include <QStringDecoder>
#include <QThread>
#include <windows.h>
#include <bcrypt.h>
#include <array>
#include <set>
#include <vector>

// MSVC x64 import-address-table symbol: unlike &function in a consumer without
// dllimport decoration, this is the resolved DLL entry, not this EXE's thunk.
extern "C" decltype(&valmax_media_staging_abi_version) __imp_valmax_media_staging_abi_version;
extern "C" __declspec(dllimport) LONG NTAPI NtQueryVolumeInformationFile(HANDLE,void*,void*,ULONG,int);

namespace valmax::media {
class MediaRuntimeResources final {
public:
    HMODULE core=nullptr;
    HANDLE file=INVALID_HANDLE_VALUE;
    std::vector<HANDLE> directories;
    ~MediaRuntimeResources() {
        if(file!=INVALID_HANDLE_VALUE)CloseHandle(file);
        for(auto it=directories.rbegin();it!=directories.rend();++it)CloseHandle(*it);
        if(core)FreeLibrary(core);
    }
};
namespace {
// The default retains the older frozen fixture. A new combined build supplies
// an explicitly reviewed core pair pin; runtime validation remains exact.
#ifndef VALMAX_CORE_PIN_SHA256
#define VALMAX_CORE_PIN_SHA256 "6d51da3b847411e72f38b101c78e40573342e636cfdbadb3594aa9957c689b54"
#define VALMAX_CORE_PIN_BYTES 833024
#endif
constexpr char CoreHash[]=VALMAX_CORE_PIN_SHA256;
constexpr uint64_t CoreSize=VALMAX_CORE_PIN_BYTES;
static_assert(sizeof(CoreHash)==65 && CoreSize>0);
constexpr wchar_t StagingChild[]=L"inline-media-native-v1";
struct Config {QString profile,runtime;bool existing=false;};
struct Parser {
    QString text;qsizetype at=0;
    void ws(){while(at<text.size()&&(text[at]==u' '||text[at]==u'\r'||text[at]==u'\n'||text[at]==u'\t'))++at;}
    bool take(QChar c){ws();if(at==text.size()||text[at]!=c)return false;++at;return true;}
    int hex4(){int n=0;for(int i=0;i<4;++i){if(at==text.size())return -1;const auto c=text[at++].unicode();int d=-1;if(c>=u'0'&&c<=u'9')d=c-u'0';else if(c>=u'a'&&c<=u'f')d=c-u'a'+10;else if(c>=u'A'&&c<=u'F')d=c-u'A'+10;if(d<0)return -1;n=(n<<4)|d;}return n;}
    bool string(QString& out) {
        if(!take(u'"'))return false;
        while(at<text.size()) {
            QChar c=text[at++];if(c==u'"')return true;if(c.unicode()<0x20)return false;
            if(c==u'\\') {
                if(at==text.size())return false;c=text[at++];
                if(c==u'"'||c==u'\\'||c==u'/'){out+=c;continue;}
                if(c==u'b'){out+=QChar(8);continue;}if(c==u'f'){out+=QChar(12);continue;}
                if(c==u'n'){out+=u'\n';continue;}if(c==u'r'){out+=u'\r';continue;}if(c==u't'){out+=u'\t';continue;}
                if(c!=u'u')return false;const auto code=hex4();if(code<0)return false;c=QChar(ushort(code));
                if(c.isHighSurrogate()) {
                    if(at+2>text.size()||text[at++]!=u'\\'||text[at++]!=u'u')return false;
                    const auto low=hex4();if(low<0||!QChar(ushort(low)).isLowSurrogate())return false;
                    out+=c;out+=QChar(ushort(low));continue;
                }
                if(c.isLowSurrogate())return false;
            } else if(c.isHighSurrogate()) {
                if(at==text.size()||!text[at].isLowSurrogate())return false;out+=c;out+=text[at++];continue;
            } else if(c.isLowSurrogate())return false;
            out+=c;
        }
        return false;
    }
    bool boolean(bool& out) {
        ws();if(text.mid(at,4)==QStringLiteral("true")){at+=4;out=true;return true;}
        if(text.mid(at,5)==QStringLiteral("false")){at+=5;out=false;return true;}return false;
    }
    bool config(Config& result) {
        if(!take(u'{'))return false;std::set<QString> keys;
        for(int i=0;i<3;++i){if(i&&!take(u','))return false;QString key;if(!string(key)||!keys.insert(key).second||!take(u':'))return false;
            if(key==QStringLiteral("profileDirectory")){if(!string(result.profile))return false;}
            else if(key==QStringLiteral("runtimeDirectory")){if(!string(result.runtime))return false;}
            else if(key==QStringLiteral("existingClient")){if(!boolean(result.existing))return false;}
            else return false;
        }
        if(!take(u'}'))return false;ws();return at==text.size();
    }
};
bool parse(const QByteArray& bytes,Config& out) {
    if(bytes.isEmpty()||bytes.size()>16384||bytes.startsWith(QByteArray::fromHex("efbbbf")))return false;
    QStringDecoder decoder(QStringDecoder::Utf8,QStringDecoder::Flag::Stateless);Parser parser{decoder(bytes)};
    return !decoder.hasError()&&parser.config(out);
}
bool component(const QString& value) {
    if(value.isEmpty()||value==QStringLiteral(".")||value==QStringLiteral("..")||value.endsWith(u'.')||value.endsWith(u' '))return false;
    for(const auto c:value)if(c.unicode()<32||c.unicode()==127||QStringLiteral(":<>\"|?*\\/").contains(c))return false;
    const auto stem=value.section(u'.',0,0).toUpper();
    if(stem==QStringLiteral("CON")||stem==QStringLiteral("PRN")||stem==QStringLiteral("AUX")||stem==QStringLiteral("NUL"))return false;
    return !(stem.size()==4&&(stem.startsWith(QStringLiteral("COM"))||stem.startsWith(QStringLiteral("LPT")))&&stem[3]>=u'1'&&stem[3]<=u'9');
}
bool path(QString input,std::wstring& out) {
    input.replace(u'/',u'\\');
    if(input.size()<3||input.size()>30000||!((input[0]>=u'A'&&input[0]<=u'Z')||(input[0]>=u'a'&&input[0]<=u'z'))||input[1]!=u':'||input[2]!=u'\\')return false;
    if(input.size()>3)for(const auto& part:input.mid(3).split(u'\\'))if(!component(part))return false;
    out=input.toStdWString();return true;
}
bool equal(const std::wstring& left,const std::wstring& right){return CompareStringOrdinal(left.data(),int(left.size()),right.data(),int(right.size()),TRUE)==CSTR_EQUAL;}
bool final_path(HANDLE handle,const std::wstring& expected) {
    std::array<wchar_t,32768> value{};const auto n=GetFinalPathNameByHandleW(handle,value.data(),DWORD(value.size()),FILE_NAME_NORMALIZED|VOLUME_NAME_DOS);
    if(n<4||n>=value.size())return false;std::wstring actual(value.data(),n);
    if(actual.starts_with(L"\\\\?\\"))actual.erase(0,4);return equal(actual,expected);
}
struct IoStatus {uintptr_t status;uintptr_t information;};
struct DeviceInfo {ULONG device,characteristics;};
bool local_volume(HANDLE root) {
    IoStatus io{};DeviceInfo info{};
    return NtQueryVolumeInformationFile(root,&io,&info,sizeof(info),4)>=0&&io.information==sizeof(info)&&info.device==7&&!(info.characteristics&0x10);
}
MediaPrepareCode pin(MediaRuntimeResources& resources,const std::wstring& name,bool drive=false) {
    HANDLE h=CreateFileW(name.c_str(),FILE_LIST_DIRECTORY|FILE_READ_ATTRIBUTES,FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
    if(h==INVALID_HANDLE_VALUE)return MediaPrepareCode::StorageUnavailable;
    BY_HANDLE_FILE_INFORMATION info{};
    if(!GetFileInformationByHandle(h,&info)||GetFileType(h)!=FILE_TYPE_DISK||!(info.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)||(info.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT)||!final_path(h,name)||(drive&&!local_volume(h))){CloseHandle(h);return MediaPrepareCode::UnsafePath;}
    try{resources.directories.push_back(h);}catch(...){CloseHandle(h);throw;}return MediaPrepareCode::Ok;
}
MediaPrepareCode ancestors(MediaRuntimeResources& resources,const std::wstring& name) {
    auto code=pin(resources,name.substr(0,3),true);if(code!=MediaPrepareCode::Ok)return code;
    size_t at=3;unsigned count=1;
    while(at<name.size()) {const auto next=name.find(L'\\',at);const auto end=next==name.npos?name.size():next;if(++count>256)return MediaPrepareCode::UnsafePath;code=pin(resources,name.substr(0,end));if(code!=MediaPrepareCode::Ok)return code;at=end+1;}
    return MediaPrepareCode::Ok;
}
bool digest(HANDLE file) {
    BCRYPT_ALG_HANDLE alg=nullptr;BCRYPT_HASH_HANDLE hash=nullptr;
    if(BCryptOpenAlgorithmProvider(&alg,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0)return false;
    bool good=BCryptCreateHash(alg,&hash,nullptr,0,nullptr,0,0)>=0;
    std::array<unsigned char,65536> data{};uint64_t total=0;DWORD count=0;
    while(good){if(!ReadFile(file,data.data(),DWORD(data.size()),&count,nullptr)){good=false;break;}if(!count)break;total+=count;if(total>CoreSize){good=false;break;}good=BCryptHashData(hash,data.data(),count,0)>=0;}
    std::array<unsigned char,32> result{};if(good)good=total==CoreSize&&BCryptFinishHash(hash,result.data(),ULONG(result.size()),0)>=0;
    if(hash)BCryptDestroyHash(hash);BCryptCloseAlgorithmProvider(alg,0);
    constexpr char hex[]="0123456789abcdef";for(size_t i=0;good&&i<result.size();++i)good=CoreHash[2*i]==hex[result[i]>>4]&&CoreHash[2*i+1]==hex[result[i]&15];return good;
}
template<class T>bool symbol(HMODULE module,const char* name,T& target) {
    const auto address=GetProcAddress(module,name);if(!address)return false;
    HMODULE owner=nullptr;
    if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,reinterpret_cast<LPCWSTR>(address),&owner)||owner!=module)return false;
    static_assert(sizeof(target)==sizeof(address));memcpy(&target,&address,sizeof(target));return true;
}
MediaPrepareCode core(MediaRuntimeResources& resources,StagingApi& api) {
    const auto imported=__imp_valmax_media_staging_abi_version;if(!imported)return MediaPrepareCode::CoreUnavailable;
    if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,reinterpret_cast<LPCWSTR>(imported),&resources.core))return MediaPrepareCode::CoreUnavailable;
    std::array<wchar_t,32768> loaded{};const auto size=GetModuleFileNameW(resources.core,loaded.data(),DWORD(loaded.size()));
    if(!size||size>=loaded.size())return MediaPrepareCode::CoreMismatch;
    std::wstring application;if(!path(QCoreApplication::applicationDirPath(),application))return MediaPrepareCode::CoreMismatch;
    auto expected=application;if(!expected.ends_with(L'\\'))expected+=L'\\';expected+=L"valmax_core.dll";
    if(!equal(std::wstring(loaded.data(),size),expected))return MediaPrepareCode::CoreMismatch;
    const auto pinned=ancestors(resources,application);if(pinned!=MediaPrepareCode::Ok)return pinned;
    resources.file=CreateFileW(expected.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT|FILE_FLAG_SEQUENTIAL_SCAN,nullptr);
    if(resources.file==INVALID_HANDLE_VALUE)return MediaPrepareCode::StorageUnavailable;
    BY_HANDLE_FILE_INFORMATION info{};
    if(!GetFileInformationByHandle(resources.file,&info)||GetFileType(resources.file)!=FILE_TYPE_DISK||(info.dwFileAttributes&(FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT))||info.nNumberOfLinks!=1||!final_path(resources.file,expected)||((uint64_t(info.nFileSizeHigh)<<32)|info.nFileSizeLow)!=CoreSize||!digest(resources.file))return MediaPrepareCode::CoreMismatch;
    if(!symbol(resources.core,"valmax_media_staging_abi_version",api.version)||!symbol(resources.core,"valmax_media_staging_begin",api.begin)||!symbol(resources.core,"valmax_media_staging_stage",api.stage)||!symbol(resources.core,"valmax_media_staging_signal_cancel",api.signal_cancel)||!symbol(resources.core,"valmax_media_staging_cancel",api.cancel)||!symbol(resources.core,"valmax_media_staging_retain",api.retain)||!symbol(resources.core,"valmax_media_staging_release",api.release)||!symbol(resources.core,"valmax_core_buffer_free",api.free_buffer))return MediaPrepareCode::AbiMismatch;
    return api.version()==1?MediaPrepareCode::Ok:MediaPrepareCode::AbiMismatch;
}
} // namespace
const char* media_prepare_code(MediaPrepareCode code)noexcept {
    switch(code){case MediaPrepareCode::Ok:return "ok";case MediaPrepareCode::InvalidConfig:return "invalid_config";case MediaPrepareCode::WrongThread:return "wrong_thread";case MediaPrepareCode::CoreUnavailable:return "core_unavailable";case MediaPrepareCode::CoreMismatch:return "core_mismatch";case MediaPrepareCode::AbiMismatch:return "core_abi_mismatch";case MediaPrepareCode::UnsafePath:return "unsafe_path";case MediaPrepareCode::StorageUnavailable:return "storage_unavailable";default:return "internal_error";}
}
MediaPrepareResult prepare_media_runtime(const QByteArray& bytes)noexcept {
    if(!QCoreApplication::instance()||QThread::currentThread()==QCoreApplication::instance()->thread())return {MediaPrepareCode::WrongThread,{}};
    try {
        Config config;if(!parse(bytes,config))return {MediaPrepareCode::InvalidConfig,{}};
        std::wstring profile,runtime;if(!path(config.profile,profile)||!path(config.runtime,runtime))return {MediaPrepareCode::UnsafePath,{}};
        auto resources=std::make_shared<MediaRuntimeResources>();ServiceConfig service;
        auto code=core(*resources,service.staging);if(code!=MediaPrepareCode::Ok)return {code,{}};
        code=ancestors(*resources,profile);if(code!=MediaPrepareCode::Ok)return {code,{}};
        auto child=profile;if(!child.ends_with(L'\\'))child+=L'\\';child+=StagingChild;
        if(!CreateDirectoryW(child.c_str(),nullptr)&&GetLastError()!=ERROR_ALREADY_EXISTS)return {MediaPrepareCode::StorageUnavailable,{}};
        code=pin(*resources,child);if(code!=MediaPrepareCode::Ok)return {code,{}};
        service.explicitStagingRoot=std::move(child);
        return {MediaPrepareCode::Ok,PreparedMediaRuntime{std::move(service),std::move(resources)}};
    } catch(...) { return {MediaPrepareCode::InternalError,{}}; }
}
} // namespace valmax::media
