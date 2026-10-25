// Actual Rust DLL boundary; invalid/read-only inputs and synthetic host callbacks.
// No successful import, credential inventory, or real profile access.
#include "../../core/valmax_core.h"
#include <windows.h>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace fs = std::filesystem;
namespace {
unsigned checks = 0;
void require(bool condition) { ++checks; if (!condition) throw std::runtime_error("check_failed"); }
struct Context { unsigned waits=0, checks=0, cancels=0; uint32_t waitResult=2, cancelled=0; };
uint32_t wait_closed(void* p) { auto& c=*static_cast<Context*>(p); ++c.waits; return c.waitResult; }
uint32_t check_closed(void* p) { ++static_cast<Context*>(p)->checks; return 2; }
uint32_t cancelled(void* p) { auto& c=*static_cast<Context*>(p); ++c.cancels; return c.cancelled; }
using Free = decltype(&valmax_core_buffer_free);
struct Buffer {
    ValmaxCoreBuffer value{}; Free free;
    explicit Buffer(Free f):free(f){}
    ~Buffer(){ free(&value); }
    std::string text() const { return value.data ? std::string(reinterpret_cast<const char*>(value.data),value.len) : ""; }
    void clear() { free(&value); require(!value.data && !value.len && !value.capacity); }
};
const uint8_t* bytes(std::string_view value) { return reinterpret_cast<const uint8_t*>(value.data()); }
struct Fixture {
    fs::path root;
    Fixture() {
        const fs::path base=L"C:/ValMAXBuild/fixtures/transfer-ffi";
        fs::create_directories(base);
        root=base/(L"case-"+std::to_wstring(GetCurrentProcessId())+L"-"+std::to_wstring(GetTickCount64()));
        require(fs::create_directory(root));
    }
    ~Fixture() {
        // Only the exclusively created empty local directory is removable.
        std::error_code ignored;
        if(root.parent_path()==fs::path(L"C:/ValMAXBuild/fixtures/transfer-ffi")) fs::remove(root,ignored);
    }
};
}
int wmain(int argc,wchar_t** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    try {
        require(argc==2);
        const fs::path dll=fs::absolute(argv[1]);
        const auto canonical=fs::weakly_canonical(dll).wstring();
        require(canonical.starts_with(L"C:\\ValMAXBuild\\") && dll.filename()==L"valmax_core.dll");
        const HMODULE library=LoadLibraryExW(dll.c_str(),nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_SYSTEM32);
        require(library!=nullptr);
        const auto preview=reinterpret_cast<decltype(&valmax_transfer_preview)>(GetProcAddress(library,"valmax_transfer_preview"));
        const auto import_call=reinterpret_cast<decltype(&valmax_transfer_import)>(GetProcAddress(library,"valmax_transfer_import"));
        const auto free=reinterpret_cast<Free>(GetProcAddress(library,"valmax_core_buffer_free"));
        require(preview && import_call && free);
        static_assert(sizeof(ValmaxTransferHost)==40 && offsetof(ValmaxTransferHost,wait_closed)==16);
        {
            Fixture fixture;
            const std::string local=fixture.root.generic_string();
            Context context;
            ValmaxTransferHost host{40,1,&context,wait_closed,check_closed,cancelled};
            Buffer output(free);
            require(preview(nullptr,1,&output.value)==1 && !output.value.data);
            const std::string empty="{}";
            require(preview(bytes(empty),empty.size(),&output.value)==0);
            require(output.text().find("\"error\":\"invalid_request\"")!=std::string::npos);
            output.clear();
            const std::string unsafe=R"({"file":"https://example.invalid/archive.mdata","password":"synthetic password"})";
            require(preview(bytes(unsafe),unsafe.size(),&output.value)==0);
            require(output.text().find("\"error\":\"unsafe_path\"")!=std::string::npos);
            output.clear();
            const std::string request="{\"file\":\"file:///"+local+"/selected.mdata\",\"password\":\"synthetic password\",\"fingerprint\":\""+std::string(64,'0')+"\"}";
            auto invalid=host; invalid.version=2;
            require(import_call(bytes(local),local.size(),bytes(request),request.size(),&invalid,&output.value)==1);
            require(!context.waits && !context.checks && !context.cancels && !output.value.data);
            require(import_call(bytes(local),local.size(),bytes(request),request.size(),&host,&output.value)==0);
            require(output.text().find("\"error\":\"process_scan_failed\"")!=std::string::npos);
            require(context.waits==1 && context.checks==0);
            require(fs::is_empty(fixture.root));
            output.clear();
            context.cancelled=1;
            require(import_call(bytes(local),local.size(),bytes(request),request.size(),&host,&output.value)==0);
            require(output.text().find("\"error\":\"cancelled\"")!=std::string::npos);
            require(context.waits==1 && context.checks==0 && fs::is_empty(fixture.root));
            output.clear();
        }
        require(FreeLibrary(library)!=0);
        std::cout << "{\"schema\":1,\"ok\":true,\"checks\":"<<checks
                  <<",\"realCredentialCalls\":false,\"successfulImportTested\":false}" << std::endl;
        return 0;
    } catch (...) {
        std::cout << "{\"schema\":1,\"ok\":false,\"error\":\"transfer_ffi_check_failed\",\"checks\":"<<checks<<"}" << std::endl;
        return 1;
    }
}
