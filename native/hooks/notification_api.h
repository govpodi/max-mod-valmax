#pragma once
#include <stdint.h>
#include <stddef.h>
#ifdef _WIN32
# ifdef VALMAX_NOTIFICATION_BUILD
#  define VALMAX_NOTIFICATION_API __declspec(dllexport)
# else
#  define VALMAX_NOTIFICATION_API __declspec(dllimport)
# endif
#else
# define VALMAX_NOTIFICATION_API
#endif
#ifdef __cplusplus
extern "C" {
#endif
enum ValmaxNotificationError {
    VALMAX_NOTIFICATION_OK = 0, VALMAX_NOTIFICATION_INVALID_ARGUMENT = 1,
    VALMAX_NOTIFICATION_MODULE_MISMATCH = 2, VALMAX_NOTIFICATION_NOT_READY = 3,
    VALMAX_NOTIFICATION_HOOK_FAILED = 4, VALMAX_NOTIFICATION_BUSY = 5,
    VALMAX_NOTIFICATION_INVALID_POLICY = 6, VALMAX_NOTIFICATION_LEASE_EXPIRED = 7,
    VALMAX_NOTIFICATION_SCOPE_GUARD = 8, VALMAX_NOTIFICATION_CLONE_FAILED = 9,
    VALMAX_NOTIFICATION_CLEANUP_FAILED = 10, VALMAX_NOTIFICATION_INVALID_DATA = 11,
    VALMAX_NOTIFICATION_INVALID_ASSET = 12, VALMAX_NOTIFICATION_CATALOG_UNAVAILABLE = 13
};
typedef struct ValmaxNotificationScope { int64_t owner; int64_t chat; } ValmaxNotificationScope;
// Revision is the lowercase SHA256 of Python-compatible canonical UTF-8 JSON:
// {"protected":[{"chatId":"...","ownerId":"..."}],"ready":true,"schema":1}
// scopes are unique, sorted lexicographically by DECIMAL owner then chat strings.
// They are copied synchronously; the DLL retains no host pointers. Each accepted
// update (including refresh) requires a strictly increasing nonzero generation.
// A missing/invalid/unready update revokes applied but NEVER clears known PINs.
typedef struct ValmaxNotificationPolicy {
    uint32_t size, version;
    uint64_t generation;
    char revision[64];
    uint32_t ready, lease_ms, count, reserved;
} ValmaxNotificationPolicy;
// Status contains no account/chat identifiers or notification content.
typedef struct ValmaxNotificationStatus {
    uint32_t size, version, error, ready;
    uint32_t applied, replacements, listeners, in_flight;
    uint64_t generation;
    char revision[64];
    uint64_t masked, scope_unknown, suppressed, faults;
    uint32_t reserved[2];
} ValmaxNotificationStatus;
// Exact loaded sibling modules and program-owned fixed PNG under the same
// runtime directory. Caller must keep DLL loaded until process exit. All calls
// are thread safe; no foreign MAX/Qt call is made under the policy mutex.
VALMAX_NOTIFICATION_API uint32_t valmax_notifications_install(const wchar_t *main_path,
    const wchar_t *backend_path, const wchar_t *asset_path);
VALMAX_NOTIFICATION_API uint32_t valmax_notifications_set_policy(const ValmaxNotificationPolicy *,
    const ValmaxNotificationScope *scopes);
VALMAX_NOTIFICATION_API uint32_t valmax_notifications_status(ValmaxNotificationStatus *);
// BUSY means old callbacks still run; retry shutdown, never unload the DLL.
VALMAX_NOTIFICATION_API uint32_t valmax_notifications_shutdown(void);
#ifdef __cplusplus
}
static_assert(sizeof(ValmaxNotificationScope) == 16);
static_assert(sizeof(ValmaxNotificationPolicy) == 96);
static_assert(offsetof(ValmaxNotificationPolicy, generation) == 8);
static_assert(offsetof(ValmaxNotificationPolicy, revision) == 16);
static_assert(offsetof(ValmaxNotificationPolicy, ready) == 80);
static_assert(sizeof(ValmaxNotificationStatus) == 144);
static_assert(offsetof(ValmaxNotificationStatus, generation) == 32);
static_assert(offsetof(ValmaxNotificationStatus, revision) == 40);
static_assert(offsetof(ValmaxNotificationStatus, masked) == 104);
#endif
