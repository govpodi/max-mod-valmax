#include "activation.h"
#include <windows.h>
#include <algorithm>
#include <array>
#include <map>
#include <utility>

namespace valmax::launcher {
namespace {
bool equal_ordinal(std::wstring_view left,std::wstring_view right) {
    return left.size()==right.size() && !left.empty() && left.size()<32768
        && CompareStringOrdinal(left.data(),static_cast<int>(left.size()),right.data(),
            static_cast<int>(right.size()),TRUE)==CSTR_EQUAL;
}
bool ascii_hex(wchar_t value) {
    return (value>=L'0' && value<=L'9') || (value>=L'a' && value<=L'f') || (value>=L'A' && value<=L'F');
}
bool main_class(std::wstring_view name) {
    if(name.size()>254 || !name.starts_with(L"Qt6")) return false;
    name.remove_prefix(3);
    size_t digits=0;
    while(digits<name.size() && name[digits]>=L'0' && name[digits]<=L'9') ++digits;
    if(digits>5) return false;
    name.remove_prefix(digits);
    if(!name.starts_with(L"QWindow")) return false;
    name.remove_prefix(7);
    if(name.starts_with(L"OwnDC")) name.remove_prefix(5);
    if(!name.starts_with(L"Icon")) return false;
    name.remove_prefix(4);
    if(name.empty()) return true;
    if(name.size()!=38 || name[0]!=L'{' || name[37]!=L'}') return false;
    for(size_t i=1;i<37;++i) {
        const bool separator=i==9 || i==14 || i==19 || i==24;
        if(separator?name[i]!=L'-':!ascii_hex(name[i])) return false;
    }
    return true;
}
struct HeldProcess {
    WindowApi *api=nullptr;Process value=0;
    HeldProcess()=default;
    HeldProcess(WindowApi &a,Process p):api(&a),value(p) {}
    ~HeldProcess() {if(value) api->close(value);}
    HeldProcess(const HeldProcess &)=delete;
    HeldProcess &operator=(const HeldProcess &)=delete;
    HeldProcess(HeldProcess &&other) noexcept:api(other.api),value(std::exchange(other.value,0)) {}
    HeldProcess &operator=(HeldProcess &&other) noexcept {
        if(this!=&other) {if(value) api->close(value);api=other.api;value=std::exchange(other.value,0);}return *this;
    }
};
bool checked(WindowApi &api,Process process,Window window,uint32_t pid,
             std::wstring_view expected,WindowInfo &info) {
    std::wstring image;
    return api.alive(process) && api.image(process,image) && same_activation_path(image,expected)
        && api.info(window,info) && eligible_main_window(info) && info.pid==pid;
}
ActivationOutcome scan(std::wstring_view expected,WindowApi &api,uint64_t deadline,ActivationResult &result) {
    std::vector<Window> windows;
    if(!api.windows(windows) || windows.size()>kMaximumWindows) return ActivationOutcome::MetadataUnavailable;
    std::map<uint32_t,HeldProcess> handles;
    std::vector<std::pair<Window,uint32_t>> candidates;
    for(auto window:windows) {
        if(api.now_ms()>=deadline) return ActivationOutcome::Deadline;
        ++result.examined;
        WindowInfo info;
        if(!window || !api.info(window,info) || !eligible_main_window(info)) continue;
        auto found=handles.find(info.pid);
        if(found==handles.end()) {
            HeldProcess handle(api,api.open_process(info.pid));
            std::wstring image;
            if(handle.value && (!api.alive(handle.value) || !api.image(handle.value,image)
                || !same_activation_path(image,expected))) handle=HeldProcess{};
            found=handles.emplace(info.pid,std::move(handle)).first;
        }
        if(found->second.value) candidates.emplace_back(window,info.pid);
    }
    if(candidates.empty()) return ActivationOutcome::NoWindow;
    if(candidates.size()!=1) return ActivationOutcome::Ambiguous;
    const auto [window,pid]=candidates[0];
    const auto process=handles.at(pid).value;
    WindowInfo info;
    if(api.now_ms()>=deadline) return ActivationOutcome::Deadline;
    if(!checked(api,process,window,pid,expected,info)) return ActivationOutcome::ChangedIdentity;
    if(info.iconic || !info.visible) {
        if(!api.show(window,info.iconic?SW_RESTORE:SW_SHOW)) return ActivationOutcome::ShowFailed;
        const auto now=api.now_ms();
        const auto show_deadline=std::min(deadline,now>UINT64_MAX-kShowMs?UINT64_MAX:now+kShowMs);
        bool shown=false;
        for(uint32_t attempt=0;attempt<kShowAttempts;++attempt) {
            if(!checked(api,process,window,pid,expected,info)) return ActivationOutcome::ChangedIdentity;
            if(info.visible && !info.iconic) {shown=true;break;}
            const auto at=api.now_ms();
            if(at>=show_deadline) return ActivationOutcome::Deadline;
            api.sleep_ms(static_cast<uint32_t>(std::min<uint64_t>(kRetryMs,show_deadline-at)));
        }
        if(!shown) return ActivationOutcome::Deadline;
    }
    // Recheck immediately before foreground too: HWND/PID may have changed
    // during asynchronous restoration. There is no AttachThreadInput bypass.
    if(api.now_ms()>=deadline) return ActivationOutcome::Deadline;
    if(!checked(api,process,window,pid,expected,info) || !info.visible || info.iconic)
        return ActivationOutcome::ChangedIdentity;
    result.foreground=api.foreground(window);
    result.shown=true;
    return ActivationOutcome::Shown;
}
}
std::optional<std::wstring> activation_path(std::wstring_view input) {
    if(input.empty() || input.size()>=32768 || input.find(L'\0')!=input.npos) return {};
    if(input.starts_with(L"\\\\?\\")) input.remove_prefix(4);
    if(input.size()<4 || !((input[0]>=L'A' && input[0]<=L'Z') || (input[0]>=L'a' && input[0]<=L'z'))
        || input[1]!=L':' || (input[2]!=L'\\' && input[2]!=L'/')) return {};
    std::wstring result(input.substr(0,2));
    size_t at=2;std::wstring_view last;
    while(at<input.size()) {
        if(input[at]==L'\\' || input[at]==L'/') {++at;continue;}
        const auto end=input.find_first_of(L"\\/",at);
        const auto part=input.substr(at,end==input.npos?input.size()-at:end-at);
        if(part==L"." || part==L".." || part.back()==L'.' || part.back()==L' ') return {};
        for(auto value:part) if(value<32 || std::wstring_view(L":<>\"|?*").find(value)!=std::wstring_view::npos) return {};
        result.push_back(L'\\');result.append(part);last=part;
        at=end==input.npos?input.size():end;
    }
    if(!equal_ordinal(last,L"max.exe") || input.back()==L'\\' || input.back()==L'/') return {};
    return result;
}
bool same_activation_path(std::wstring_view left,std::wstring_view right) {
    const auto a=activation_path(left),b=activation_path(right);
    return a && b && equal_ordinal(*a,*b);
}
bool eligible_main_window(const WindowInfo &info) {
    return info.pid!=0 && info.owner==0 && !(info.style&WS_CHILD)
        && !(info.exstyle&(WS_EX_TOOLWINDOW|WS_EX_NOACTIVATE))
        && main_class(info.window_class) && info.width>0 && info.height>0;
}
ActivationResult activate_existing(std::wstring_view executable,WindowApi &api) noexcept {
    ActivationResult result;
    try {
        const auto expected=activation_path(executable);
        if(!expected) {result.outcome=ActivationOutcome::InvalidPath;return result;}
        const auto start=api.now_ms();
        if(start>UINT64_MAX-kActivationMs) {result.outcome=ActivationOutcome::Deadline;return result;}
        const auto deadline=start+kActivationMs;
        for(uint32_t attempt=0;attempt<kScanAttempts;++attempt) {
            if(api.now_ms()>=deadline) {result.outcome=ActivationOutcome::Deadline;break;}
            ++result.scans;result.outcome=scan(*expected,api,deadline,result);
            if(result.shown) return result;
            const auto at=api.now_ms();
            if(at>=deadline) {result.outcome=ActivationOutcome::Deadline;break;}
            api.sleep_ms(static_cast<uint32_t>(std::min<uint64_t>(kRetryMs,deadline-at)));
        }
    } catch(...) {result.shown=false;result.foreground=false;result.outcome=ActivationOutcome::NativeFailure;}
    return result;
}
ActivationResult activate_existing(std::wstring_view executable) noexcept {
    Win32WindowApi api;return activate_existing(executable,api);
}
bool Win32WindowApi::windows(std::vector<Window> &output) {
    struct State {std::vector<Window> *out;bool overflow=false,failed=false;};
    output.clear();output.reserve(kMaximumWindows);State state{&output};
    const auto callback=[](HWND window,LPARAM raw)->BOOL {
        auto &current=*reinterpret_cast<State *>(raw);
        if(current.out->size()>=kMaximumWindows) {current.overflow=true;return FALSE;}
        try {current.out->push_back(reinterpret_cast<Window>(window));return TRUE;}
        catch(...) {current.failed=true;return FALSE;}
    };
    if(!EnumWindows(callback,reinterpret_cast<LPARAM>(&state)) || state.overflow || state.failed) {output.clear();return false;}
    return true;
}
bool Win32WindowApi::info(Window value,WindowInfo &output) {
    const auto window=reinterpret_cast<HWND>(value);DWORD pid=0;
    if(!GetWindowThreadProcessId(window,&pid) || !pid) return false;
    std::array<wchar_t,256> name{};const auto length=GetClassNameW(window,name.data(),static_cast<int>(name.size()));
    if(length<=0 || length>=static_cast<int>(name.size()-1)) return false;
    RECT rectangle{};if(!GetWindowRect(window,&rectangle)) return false;
    std::array<uint32_t,2> styles{};size_t index=0;
    for(const auto which:{GWL_STYLE,GWL_EXSTYLE}) {
        SetLastError(ERROR_SUCCESS);const auto style=GetWindowLongPtrW(window,which);
        if(style==0 && GetLastError()!=ERROR_SUCCESS) return false;
        styles[index++]=static_cast<uint32_t>(style);
    }
    WindowInfo result{pid,reinterpret_cast<Window>(GetWindow(window,GW_OWNER)),styles[0],styles[1],
        std::wstring(name.data(),static_cast<size_t>(length)),
        static_cast<int64_t>(rectangle.right)-rectangle.left,static_cast<int64_t>(rectangle.bottom)-rectangle.top,
        IsWindowVisible(window)!=FALSE,IsIconic(window)!=FALSE};
    DWORD again=0;if(!GetWindowThreadProcessId(window,&again) || again!=pid) return false;
    output=std::move(result);return true;
}
Process Win32WindowApi::open_process(uint32_t pid) {
    return reinterpret_cast<Process>(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,FALSE,pid));
}
bool Win32WindowApi::image(Process process,std::wstring &output) {
    std::array<wchar_t,32768> name{};DWORD count=static_cast<DWORD>(name.size());
    if(!QueryFullProcessImageNameW(reinterpret_cast<HANDLE>(process),0,name.data(),&count) || count==0 || count>=name.size()) return false;
    output.assign(name.data(),count);return true;
}
bool Win32WindowApi::alive(Process process) {return WaitForSingleObject(reinterpret_cast<HANDLE>(process),0)==WAIT_TIMEOUT;}
void Win32WindowApi::close(Process process) noexcept {if(process) CloseHandle(reinterpret_cast<HANDLE>(process));}
bool Win32WindowApi::show(Window window,int command) {return ShowWindowAsync(reinterpret_cast<HWND>(window),command)!=FALSE;}
bool Win32WindowApi::foreground(Window window) {return SetForegroundWindow(reinterpret_cast<HWND>(window))!=FALSE;}
uint64_t Win32WindowApi::now_ms() {return GetTickCount64();}
void Win32WindowApi::sleep_ms(uint32_t milliseconds) {Sleep(milliseconds);}
}
