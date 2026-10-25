#pragma once
#include <stdint.h>
#include <wchar.h>
#if defined(VALMAX_GHOST_BUILD)
#define VALMAX_GHOST_API __declspec(dllexport)
#else
#define VALMAX_GHOST_API __declspec(dllimport)
#endif
#ifdef __cplusplus
extern "C" {
#endif
enum ValmaxGhostError {
    VALMAX_GHOST_OK = 0, VALMAX_GHOST_ARGUMENT = 1,
    VALMAX_GHOST_MODULE_MISMATCH = 2, VALMAX_GHOST_HOOK_FAILED = 3,
    VALMAX_GHOST_NOT_READY = 4, VALMAX_GHOST_BUSY = 5,
    VALMAX_GHOST_POLICY_CONFLICT = 6
};
typedef struct ValmaxGhostPolicy {
    uint32_t size;
    uint32_t version; /* 1 */
    char revision[64]; /* lowercase ASCII SHA-256, no terminator */
    uint8_t enabled, hide_typing, hide_read, hide_online, hide_stories;
    uint8_t reserved[3]; /* zero */
} ValmaxGhostPolicy;
typedef struct ValmaxGhostStatus {
    uint32_t size;
    uint32_t version; /* 1 */
    uint32_t error;
    uint32_t ready; /* only after every code/layout guard and all 12 hooks */
    ValmaxGhostPolicy policy;
    uint64_t blocked_typing, blocked_read, blocked_presence, blocked_stories;
    uint64_t unread_faults; /* bounded counter, no account/chat identifiers */
    uint32_t replacements, listeners;
} ValmaxGhostStatus;
/* Call after MAX and core.dll are loaded, outside DllMain/loader lock.
 * Paths are absolute UTF-16, <=32767 characters, same directory, expected
 * filenames core.dll/max.exe. Production accepts only build-generated hashes.
 * No paths, identifiers, message data, arguments or credentials are logged.
 * The DLL and verified modules are pinned for process lifetime after install.
 */
VALMAX_GHOST_API uint32_t valmax_ghost_install(const wchar_t *core_path, const wchar_t *main_path);
VALMAX_GHOST_API uint32_t valmax_ghost_set_policy(const ValmaxGhostPolicy *policy);
VALMAX_GHOST_API uint32_t valmax_ghost_status(ValmaxGhostStatus *status);
/* Invalidate after account switch, deletion sync, or changes to read history. */
VALMAX_GHOST_API void valmax_ghost_invalidate_unread(void);
/* Disables policy, detaches, then flushes. BUSY means callbacks still in flight:
 * keep the DLL loaded and retry later. Never call from an intercepted callback.
 * Successful shutdown does not unpin modules or permit unloading this DLL.
 */
VALMAX_GHOST_API uint32_t valmax_ghost_shutdown(void);
#ifdef __cplusplus
}
#endif
