#pragma once
#include <stdint.h>
#include <stddef.h>
#ifdef _WIN32
# ifdef VALMAX_PRIVACY_BUILD
#  define VALMAX_PRIVACY_API __declspec(dllexport)
# else
#  define VALMAX_PRIVACY_API __declspec(dllimport)
# endif
#else
# define VALMAX_PRIVACY_API
#endif
#ifdef __cplusplus
extern "C" {
#endif
enum ValmaxPrivacyError {
    VALMAX_PRIVACY_OK=0, VALMAX_PRIVACY_INVALID_ARGUMENT=1,
    VALMAX_PRIVACY_FILE_MISMATCH=2, VALMAX_PRIVACY_MODULE_NOT_LOADED=3,
    VALMAX_PRIVACY_MEMORY_MISMATCH=4, VALMAX_PRIVACY_IO_ERROR=5,
    VALMAX_PRIVACY_NOT_VERIFIED=6
};
enum ValmaxPrivacyRole { VALMAX_PRIVACY_MAIN=1, VALMAX_PRIVACY_SERVICE=2 };
enum ValmaxPrivacyRule {
    VALMAX_PRIVACY_TRACER_MAIN=1, VALMAX_PRIVACY_TRACER_SERVICE=2,
    VALMAX_PRIVACY_UI_ANALYTICS=4, VALMAX_PRIVACY_MYTRACKER=8,
    VALMAX_PRIVACY_LOG_MASKING=16, VALMAX_PRIVACY_ERROR_LOG_UPLOAD=32
};
// Status describes verified static changes, never observed network blocking.
// disk_rules covers all six packaged files; loaded_rules covers this process's
// applicable modules. No packets, arguments, identifiers or counters are read.
typedef struct ValmaxPrivacyStatus {
    uint32_t size, version, error, ready;
    uint32_t role, disk_rules, loaded_rules, files_verified;
    uint32_t modules_verified, patches_verified, required_loaded_rules, reserved;
    char manifest_sha256[64];
} ValmaxPrivacyStatus;
// The module reads fixed program-relative files only. It never modifies code,
// starts a server, installs a callback or discovers user profiles. Verification
// retains readonly file and module references until the next verify/shutdown.
VALMAX_PRIVACY_API uint32_t valmax_privacy_verify(const wchar_t *runtime_root,uint32_t role);
VALMAX_PRIVACY_API uint32_t valmax_privacy_status(ValmaxPrivacyStatus *status);
// Releases verification handles and clears readiness. Static protection remains
// in the packaged program bytes; this operation cannot undo any privacy patch.
VALMAX_PRIVACY_API uint32_t valmax_privacy_shutdown(void);
#ifdef __cplusplus
}
static_assert(sizeof(ValmaxPrivacyStatus)==112);
static_assert(offsetof(ValmaxPrivacyStatus,manifest_sha256)==48);
#endif
