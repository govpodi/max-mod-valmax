#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace valmax::launcher {
using Window=uintptr_t;
using Process=uintptr_t;
struct WindowInfo {
    uint32_t pid=0;
    Window owner=0;
    uint32_t style=0,exstyle=0;
    std::wstring window_class;
    int64_t width=0,height=0;
    bool visible=false,iconic=false;
};
// Metadata/actions only: no title, text, command line, credential or VM_READ.
class WindowApi {
public:
    virtual ~WindowApi()=default;
    virtual bool windows(std::vector<Window> &)=0;
    virtual bool info(Window,WindowInfo &)=0;
    virtual Process open_process(uint32_t pid)=0;
    virtual bool image(Process,std::wstring &)=0;
    virtual bool alive(Process)=0;
    virtual void close(Process) noexcept=0;
    virtual bool show(Window,int command)=0;
    virtual bool foreground(Window)=0;
    virtual uint64_t now_ms()=0;
    virtual void sleep_ms(uint32_t)=0;
};
class Win32WindowApi final:public WindowApi {
public:
    bool windows(std::vector<Window> &) override;
    bool info(Window,WindowInfo &) override;
    Process open_process(uint32_t) override;
    bool image(Process,std::wstring &) override;
    bool alive(Process) override;
    void close(Process) noexcept override;
    bool show(Window,int) override;
    bool foreground(Window) override;
    uint64_t now_ms() override;
    void sleep_ms(uint32_t) override;
};
enum class ActivationOutcome:uint32_t {
    Shown,InvalidPath,NoWindow,Ambiguous,MetadataUnavailable,ChangedIdentity,
    ShowFailed,Deadline,NativeFailure
};
struct ActivationResult {
    ActivationOutcome outcome=ActivationOutcome::NoWindow;
    bool shown=false,foreground=false;
    uint32_t scans=0,examined=0;
};
inline constexpr uint32_t kMaximumWindows=4096,kActivationMs=2500,kScanAttempts=51,
    kShowMs=750,kShowAttempts=16,kRetryMs=50;

// Strict drive-absolute max.exe spelling. UNC, ADS, dot segments, NUL and
// trailing-dot/space components are rejected. No filesystem access or resolve.
std::optional<std::wstring> activation_path(std::wstring_view);
bool same_activation_path(std::wstring_view,std::wstring_view);
bool eligible_main_window(const WindowInfo &);
// PRECONDITION: caller has verified package hash, exact expected executable and
// running-instance identity/readiness. This helper checks limited process-path
// metadata again, but does not replace that verifier. It NEVER spawns a client,
// kills a process or changes profile state. A false result never permits spawn.
// Foreground is best effort under normal Windows foreground restrictions.
ActivationResult activate_existing(std::wstring_view verified_executable,WindowApi &) noexcept;
ActivationResult activate_existing(std::wstring_view verified_executable) noexcept;
}
