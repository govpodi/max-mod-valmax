#include "../../core/valmax_core.h"
#include <windows.h>
#include <string>
#include <string_view>
#include <algorithm>

namespace {
bool owned(std::string value){std::replace(value.begin(),value.end(),'\\','/');return value.starts_with("D:/ValMAXBuild/fixtures/native-launcher/");}
std::string field(std::string_view input,std::string_view key){
    const std::string prefix="\""+std::string(key)+"\":\"";auto at=input.find(prefix);if(at==input.npos)return {};at+=prefix.size();
    std::string value;while(at<input.size()) {char c=input[at++];if(c=='"')return value;if(c=='\\'){if(at==input.size())return {};c=input[at++];if(c!='\\'&&c!='"')return {};}value.push_back(c);}return {};
}
}
extern "C" uint32_t valmax_core_abi_version(){return 1;}
extern "C" int32_t valmax_launcher_prepare(const uint8_t* input,size_t n,uint32_t only,ValmaxLauncherProfileResult* out){
    if(!out)return 1;*out={16,1,1,0};
    if(!input||n>32000||only>1||!owned(std::string(reinterpret_cast<const char*>(input),n)))return 0;
    const std::string marker=std::string(reinterpret_cast<const char*>(input),n)+"\\MXMOD-MData-Recovery.json";
    if(GetFileAttributesA(marker.c_str())!=INVALID_FILE_ATTRIBUTES){out->code=4;return 0;}
    *out={16,1,0,2};return 0;
}
extern "C" int32_t valmax_runtime_open(const uint8_t* input,size_t n,ValmaxRuntime** context,ValmaxCoreBuffer* out){
    if(!context||!out)return 1;*context=nullptr;*out={};if(!input||n>16384)return 1;
    const std::string_view config(reinterpret_cast<const char*>(input),n);
    const auto directory=field(config,"runtimeDirectory");
    if(!owned(directory)||!owned(field(config,"profileDirectory"))||config.find("\"existingClient\":false")==config.npos)return 0;
    const auto file=CreateFileA((directory+"\\optimization-active.json").c_str(),GENERIC_WRITE,0,nullptr,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
    if(file==INVALID_HANDLE_VALUE)return 0;
    constexpr char bytes[]="{\"schema\":1,\"syntheticOnly\":true}";DWORD written=0;
    const bool ok=WriteFile(file,bytes,sizeof(bytes)-1,&written,nullptr)&&written==sizeof(bytes)-1&&FlushFileBuffers(file);CloseHandle(file);
    if(ok)*context=reinterpret_cast<ValmaxRuntime*>(static_cast<uintptr_t>(1));return 0;
}
extern "C" void valmax_runtime_close(ValmaxRuntime** context){if(context)*context=nullptr;}
extern "C" void valmax_core_buffer_free(ValmaxCoreBuffer* out){if(out)*out={};}
