#include "profile_client.h"
#include <cstddef>
#include <string>
namespace valmax::launcher {
static_assert(sizeof(ValmaxLauncherProfileResult)==16);
static_assert(offsetof(ValmaxLauncherProfileResult,size)==0 && offsetof(ValmaxLauncherProfileResult,version)==4
    && offsetof(ValmaxLauncherProfileResult,code)==8 && offsetof(ValmaxLauncherProfileResult,state)==12);
const char *profile_code(ProfileCode value) noexcept {
    switch(value){case ProfileCode::Ok:return "ok";case ProfileCode::InvalidInput:return "invalid_request";
        case ProfileCode::GuardRequired:return "transfer_guard_required";case ProfileCode::CoreUnavailable:return "core_unavailable";
        case ProfileCode::AbiMismatch:return "core_abi_mismatch";case ProfileCode::ProfileGuardFailed:return "profile_guard_failed";
        case ProfileCode::TransferBusy:return "transfer_busy";case ProfileCode::RecoveryRequired:return "recovery_required";
        default:return "internal_error";}
}
ProfileResult prepare_profile(std::wstring_view local,bool check_only,const TransferGuard &outer,PrepareProfileFn prepare) noexcept {
    const auto fail=[](ProfileCode code){return ProfileResult{code,ProfileState::Unavailable};};
    if(!outer.owned_by_current_thread())return fail(ProfileCode::GuardRequired);
    if(!prepare)return fail(ProfileCode::CoreUnavailable);
    if(local.empty()||local.size()>32000||local.find(L'\0')!=local.npos)return fail(ProfileCode::InvalidInput);
    try {
        const auto length=static_cast<int>(local.size());
        const int bytes=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,local.data(),length,nullptr,0,nullptr,nullptr);
        if(bytes<=0||bytes>32000)return fail(ProfileCode::InvalidInput);
        std::string utf8(static_cast<size_t>(bytes),'\0');
        if(WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,local.data(),length,utf8.data(),bytes,nullptr,nullptr)!=bytes)
            return fail(ProfileCode::InvalidInput);
        ValmaxLauncherProfileResult result{};
        const auto status=prepare(reinterpret_cast<const uint8_t *>(utf8.data()),utf8.size(),check_only?1u:0u,&result);
        if(status!=0||result.size!=16||result.version!=1||result.code>5)return fail(ProfileCode::AbiMismatch);
        if(result.code!=0){
            if(result.state!=0)return fail(ProfileCode::AbiMismatch);
            constexpr ProfileCode codes[]={ProfileCode::Ok,ProfileCode::InvalidInput,ProfileCode::ProfileGuardFailed,
                ProfileCode::TransferBusy,ProfileCode::RecoveryRequired,ProfileCode::InternalError};
            return fail(codes[result.code]);
        }
        if((check_only&&(result.state!=1&&result.state!=2))||(!check_only&&(result.state!=2&&result.state!=3)))
            return fail(ProfileCode::AbiMismatch);
        return {ProfileCode::Ok,static_cast<ProfileState>(result.state)};
    }catch(...){return fail(ProfileCode::InternalError);}
}
}
