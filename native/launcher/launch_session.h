#pragma once
#include "preflight.h"
#include "environment.h"
#include <memory>
#include <string>
#include <string_view>

namespace valmax::launcher {
enum class LaunchCode : uint32_t {
    Launched, Activated, HelperLaunched, InvalidOptions, PayloadInvalid, CoreUnavailable,
    TransferBusy, ProfileFailed, RecoveryRequired, ProcessUnknown,
    ExistingUnavailable, ActivationFailed, RuntimeFailed, EnvironmentFailed,
    SpawnFailed, ChildExited, InternalError, ProfileInUse, UnsupportedContext
};
const char* launch_code(LaunchCode) noexcept;
struct LaunchResult {
    LaunchCode code=LaunchCode::InternalError;
    bool process_created=false, window_shown=false, foreground=false;
    // Finite validation result only; never attach inherited names or values.
    EnvironmentError environment_error=EnvironmentError::Ok;
    // Neither successful code acknowledges privacy/ghost/notification readiness.
    bool success() const noexcept {return code==LaunchCode::Launched||code==LaunchCode::Activated||code==LaunchCode::HelperLaunched;}
};
enum class ClientPresence {Absent, Running, Unavailable, Unknown, ProfileInUse};
// Trusted host-only inputs. Production main obtains its own application folder
// and LocalAppData via Windows; command-line/environment path overrides do not
// reach this object. Runtime layout is fixed below package_directory/runtime.
enum class LaunchAction {Client, Import, Recover};
struct LaunchOptions {std::wstring package_directory, local_appdata;};
bool parse_action(std::span<const std::wstring_view>,LaunchAction&) noexcept;

// Narrow seam for order/failure tests. An instance owns all guards and handles;
// keep it on the same thread for the complete run, then destroy it there.
class LaunchDriver {
public:
    virtual ~LaunchDriver()=default;
    virtual LaunchCode verify()=0;
    virtual LaunchCode acquire_transfer()=0;
    virtual LaunchCode check_profile()=0;
    virtual LaunchCode prepare_profile()=0;
    virtual ClientPresence scan()=0;
    virtual LaunchResult activate()=0;
    virtual LaunchCode prepare_runtime()=0;
    virtual LaunchCode prepare_environment()=0;
    virtual LaunchResult spawn()=0;
    virtual LaunchResult spawn_transfer(bool recovery)=0;
};
// Internal step success uses Launched; only spawn() can set process_created.
LaunchResult run_launch(LaunchDriver&,LaunchAction=LaunchAction::Client) noexcept;

class Win32LaunchDriver final : public LaunchDriver {
public:
#ifdef VALMAX_TRANSFER_TESTING
    // Compile-time seam: only these explicitly owned fake PIDs are sampled.
    Win32LaunchDriver(LaunchOptions,std::span<const PayloadPin>,std::wstring test_mutex,
                      std::span<const uint32_t> test_pids={});
#else
    Win32LaunchDriver(LaunchOptions,std::span<const PayloadPin>);
#endif
    ~Win32LaunchDriver() override;
    Win32LaunchDriver(const Win32LaunchDriver&)=delete;
    Win32LaunchDriver& operator=(const Win32LaunchDriver&)=delete;
    LaunchCode verify() override;
    LaunchCode acquire_transfer() override;
    LaunchCode check_profile() override;
    LaunchCode prepare_profile() override;
    ClientPresence scan() override;
    LaunchResult activate() override;
    LaunchCode prepare_runtime() override;
    LaunchCode prepare_environment() override;
    LaunchResult spawn() override;
    LaunchResult spawn_transfer(bool recovery) override;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
// Pure bounded JSON/child-environment assembly, also used by the real driver.
std::string runtime_config(std::wstring_view profile,std::wstring_view runtime,bool existing);
ChildEnvironment configured_environment(std::span<const wchar_t>,const LexicalEnvironmentPaths&,
                                        std::string_view trusted_config);
}
