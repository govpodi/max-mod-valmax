#pragma once
#include "transfer_mutex.h"
#include "../core/valmax_core.h"
#include <string_view>
namespace valmax::launcher {
using PrepareProfileFn=decltype(&valmax_launcher_prepare);
enum class ProfileCode:uint32_t {Ok,InvalidInput,GuardRequired,CoreUnavailable,AbiMismatch,ProfileGuardFailed,TransferBusy,RecoveryRequired,InternalError};
enum class ProfileState:uint32_t {Unavailable,Absent,Existing,Created};
struct ProfileResult {
    ProfileCode code=ProfileCode::InternalError;
    ProfileState state=ProfileState::Unavailable;
};
const char *profile_code(ProfileCode) noexcept;
// Host-only synchronous call. The function pointer must come from the trusted,
// already verified/loaded core DLL; its owner must retain that module for this
// entire call. This binding neither loads modules nor discovers profile paths.
// Keep the outer TransferGuard alive on this OS thread through future startup.
// Ok describes only config preparation, never permission/readiness to launch.
ProfileResult prepare_profile(std::wstring_view explicit_local,bool check_only,
    const TransferGuard &outer,PrepareProfileFn trusted_prepare) noexcept;
}
