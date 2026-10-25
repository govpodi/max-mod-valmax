#ifndef VALMAX_CORE_H
#define VALMAX_CORE_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct ValmaxCoreBuffer { uint8_t *data; size_t len; size_t capacity; } ValmaxCoreBuffer;
/* ABI 1. Thread-safe, synchronous; call away from the GUI thread for crypto.
 * dispatch accepts bounded UTF-8 JSON (1..16 MiB). Caller retains ownership of
 * input and must erase sensitive input bytes. Input and output must not alias.
 * output must point to a writable, empty {NULL,0,0} struct: a previous live
 * result must be freed first. No arbitrary/untrusted pointers may cross here.
 * Return 0 means an owned JSON result is available; operation failures are
 * represented by {schema:1,ok:false,error:<finite code>} in that JSON.
 * Return 1 means invalid ABI arguments; a non-NULL output is reset to empty.
 * Free successful results ONLY with valmax_core_buffer_free, not C/C++ free.
 * The result belongs to this library; do not change any of its three fields.
 * buffer_free erases logical bytes and resets the struct. NULL and an already
 * cleared struct are accepted. Erasure is best-effort, not a secure-heap claim.
 */
uint32_t valmax_core_abi_version(void);
int32_t valmax_core_dispatch(const uint8_t *input, size_t input_len, ValmaxCoreBuffer *output);
void valmax_core_buffer_free(ValmaxCoreBuffer *buffer);
/* Launcher-only API. Never expose through QML/Runtime::call.
 * explicit_local is 1..32000 UTF-8 bytes, without NUL. check_only is exactly0/1.
 * Caller retains the outer transfer mutex on THIS OS THREAD through startup;
 * the inner recursive guard covers only this function's filesystem operation.
 * Existing config is validated without changing its bytes. Missing config is
 * created only when !check_only; an existing recovery marker prevents creation
 * or mutating preparation. check_only skips the recovery check and never
 * authorizes startup. Recheck recovery/readiness before starting the client.
 * Result ABI1 size16. code:0 ok,1 invalid_request,2 profile_guard_failed,
 * 3 transfer_busy,4 recovery_required,5 internal_error.
 * state:0 unavailable,1 absent/check-only,2 existing,3 created.
 * No buffers/paths are returned. output is writable, aligned and nonoverlapping.
 * Return1 rejects invalid ABI arguments (non-NULL output resets to invalid);
 * return0 provides the finite result. This does not launch any application. */
typedef struct ValmaxLauncherProfileResult {
    uint32_t size, version, code, state;
} ValmaxLauncherProfileResult;
int32_t valmax_launcher_prepare(const uint8_t *explicit_local, size_t local_len,
    uint32_t check_only, ValmaxLauncherProfileResult *output);
/* Host-only startup receipts; never expose through QML/Runtime::call.
 * helper is an explicit trusted program .mxmod-helper-temp directory,
 * 1..31000 UTF-8 bytes. No directory/profile discovery or process/UI action.
 * Both structs ABI1 size64; operation_id is exactly32 lowercase hex bytes.
 * action:1 read,2 write. Read requires stage/state/code/claim=0.
 * role:1 launcher,2 observe (legacy receipt role),3 recover,4 mdata.
 * stage:1 bootstrap,2 import,3 verify,4 desktop,5 profile,6 observer,
 * 7 optimization,8 spawn,9 protection,10 recovery,11 complete,
 * 12 frida_initialize,13 setup_stores,14 bind,15 native_spawn,
 * 16 protection_attach,17 ready. These retain the legacy receipt vocabulary.
 * state:1 starting,2 ready,3 error. claim:0/1; only a non-observe role
 * in starting state may explicitly claim a new root operation.
 * code:0 none;1 startup_failed,2 invalid_command,3 unsupported_platform,
 * 4 import_failed,5 payload_invalid,6 desktop_unavailable,
 * 7 desktop_bootstrap_failed,8 desktop_bootstrap_timeout,9 profile_guard_failed,
 * 10 recovery_required,11 transfer_busy,12 port_unavailable,13 port_busy,
 * 14 port_other_runtime,15 observer_idle,16 observer_start_failed,
 * 17 observer_timeout,18 observer_worker_stopped,19 observer_identity_invalid,
 * 20 observer_unavailable,21 optimization_prepare_failed,22 native_spawn_failed,
 * 23 window_activation_failed,24 protection_ack_failed,25 ghost_ack_failed,
 * 26 retention_ack_failed,27 notification_ack_failed.
 * code must be nonzero exactly when state=error.
 * result.error:0 ok,1 invalid_request,2 invalid_receipt,3 unsafe_path,
 * 4 store_busy,5 io_error,6 internal_error.
 * result.outcome:0 absent/not written,1 written,2 receipt; receipt fields
 * are all zero except for outcome2. Read filters by exact operation_id.
 * An older child cannot overwrite a new operation; the first error is sticky.
 * Return1 rejects invalid ABI arguments; non-null result resets to error1.
 * Return0 gives a finite result. Pointers must be aligned, disjoint and valid
 * for the full structs/input. Caller owns all memory; no buffer is returned.
 * Diagnostic persistence MUST NOT replace primary startup failure or satisfy
 * any profile/recovery/protection readiness gate. Failure may be ignored only
 * after the original launch outcome is retained independently. */
typedef struct ValmaxStartupRequest {
    uint32_t size, version, action, role, stage, state, code, claim;
    uint8_t operation_id[32];
} ValmaxStartupRequest;
typedef struct ValmaxStartupResult {
    uint32_t size, version, error, outcome, role, stage, state, code;
    uint8_t operation_id[32];
} ValmaxStartupResult;
int32_t valmax_startup_status(const uint8_t *helper, size_t helper_len,
    const ValmaxStartupRequest *request, ValmaxStartupResult *output);
/* Host-only selected PUBLIC Commons media staging. Never expose these functions
 * or root paths through QML/Runtime::call. ABI1 is Windows x64; request32/result40.
 * begin accepts an existing dedicated local root (1..31000 UTF-8 bytes, no NUL),
 * writes a nonzero opaque token on success, and never discovers/creates roots.
 * Maximum64 live/reserved jobs, monotonic never-reused tokens; 0 is invalid.
 * stage is synchronous, one-shot, and borrows immutable data until it RETURNS,
 * even if another thread has already cancelled/released its token. Size1..25MiB.
 * MIME:1 JPEG,2 PNG,3 GIF,4 WebP,5 WebM,6 MP4; reserved must be0.
 * Expected length must equal data_len; now is trusted host Unix seconds.
 * Result file_url is owned UTF-8 (no NUL terminator), not a borrowed pointer.
 * Free ONLY with valmax_core_buffer_free; input/output/result must be disjoint,
 * aligned and valid. Result must be empty: free any previous URL first.
 * Every non-null result resets to {40,1,0,{NULL,0,0}} before validation.
 * No raw paths/errors/content enter diagnostics. All functions return the finite
 * error enum directly; ABI getter is separate. No callbacks or auto-send.
 *
 * begin/stage/retain/cancel/release can perform filesystem work: use an I/O
 * thread. signal_cancel ONLY signals cancellation, including a Ready/Prepared
 * job, and is safe from another thread without waiting on filesystem work.
 * signal_cancel does not delete a Prepared file; cancel/release does that later.
 * A running stage holds its own Arc: concurrent release cannot cause UAF.
 * Publication and release are serialized. Revocation accepted before publish
 * produces CANCELLED and no URL. A publish that wins may be revoked immediately
 * afterwards: ALWAYS recheck host account/page generation before draft use.
 * retain consumes a Prepared lease only after !cancel was checked under its
 * state lock. After successful retain, later cancel/release preserve the file.
 * Otherwise cancel/release invalidate the lease; cancelled retain is rejected.
 * cancel is idempotent on a live cancelled/retained job; retain is idempotent
 * after successful retain. release revokes any token (including0/already gone),
 * and repeated release returnsOK. Other unknown-token operations returnINVALID_HANDLE.
 * Pending cleanup may fail (file in use): token is still terminal/revoked and
 * the orphan remains quota-charged. ReleaseOK during Running acknowledges token
 * revocation, not completion of its later cleanup. Caller must not unload this
 * DLL until all calls and owned buffers/handles have finished, even after release.
 * catch_unwind handles Rust panics, not invalid pointers or allocation abort.
 */
typedef uint64_t ValmaxMediaStagingHandle;
typedef enum ValmaxMediaStagingError {
    VALMAX_MEDIA_OK=0, VALMAX_MEDIA_INVALID_REQUEST=1,
    VALMAX_MEDIA_UNSUPPORTED_MEDIA=2, VALMAX_MEDIA_FILE_TOO_LARGE=3,
    VALMAX_MEDIA_CACHE_FULL=4, VALMAX_MEDIA_DOWNLOAD_BUSY=5,
    VALMAX_MEDIA_UNSAFE_PATH=6, VALMAX_MEDIA_STORAGE_UNAVAILABLE=7,
    VALMAX_MEDIA_NO_SPACE=8, VALMAX_MEDIA_CANCELLED=9,
    VALMAX_MEDIA_CLEANUP_FAILED=10, VALMAX_MEDIA_UNSUPPORTED_PLATFORM=11,
    VALMAX_MEDIA_INVALID_HANDLE=12, VALMAX_MEDIA_INVALID_STATE=13,
    VALMAX_MEDIA_HANDLE_LIMIT=14, VALMAX_MEDIA_INTERNAL_ERROR=15
} ValmaxMediaStagingError;
typedef struct ValmaxMediaStageRequest {
    uint32_t size, version, mime, reserved;
    uint64_t expected_bytes, now_unix_seconds;
} ValmaxMediaStageRequest;
typedef struct ValmaxMediaStageResult {
    uint32_t size, version;
    uint64_t byte_length;
    ValmaxCoreBuffer file_url;
} ValmaxMediaStageResult;
uint32_t valmax_media_staging_abi_version(void);
uint32_t valmax_media_staging_begin(const uint8_t *root, size_t root_len, ValmaxMediaStagingHandle *handle);
uint32_t valmax_media_staging_stage(ValmaxMediaStagingHandle handle, const ValmaxMediaStageRequest *request,
    const uint8_t *data, size_t data_len, ValmaxMediaStageResult *result);
uint32_t valmax_media_staging_signal_cancel(ValmaxMediaStagingHandle handle);
uint32_t valmax_media_staging_cancel(ValmaxMediaStagingHandle handle);
uint32_t valmax_media_staging_retain(ValmaxMediaStagingHandle handle);
uint32_t valmax_media_staging_release(ValmaxMediaStagingHandle handle);

/* One-shot native MData helper only. Never register as a QML/Runtime method.
 * ABI1 callbacks size40 on Windows x64. Context and callbacks remain valid until
 * import returns; they must not throw/unwind/reenter or unload this library.
 * wait_closed/check_closed run on this worker while Rust holds the transfer
 * mutex. Return0: verified full closed scan;1: client still running/deadline;
 * 2: incomplete/failed scan;3: cancelled. Other codes fail closed.
 * cancelled returns0 to continue, any other value requests cancellation.
 * wait_closed must implement bounded waiting plus the settle interval;
 * check_closed must perform a fresh complete scan. A supplied UI boolean is
 * never sufficient. No callback performs application restart.
 *
 * preview input: exact UTF-8 JSON {file,password}.
 * import input: exact UTF-8 JSON {file,password,fingerprint}; fingerprint is the
 * SHA256 from the user-confirmed authenticated preview. local is a separate
 * trusted explicit LocalAppData directory (1..32000 UTF-8 bytes).
 * Input JSON 1..128KiB, file a selected local file URL. Own all input until
 * return; erase password/request bytes afterward. No argv/env/job-file secrets.
 * output must be an empty aligned disjoint CoreBuffer, freed only by
 * valmax_core_buffer_free. Return1 invalid ABI, output reset; return0 carries
 * {schema:1,ok:true,result:{...}} or {schema:1,ok:false,error:<finite code>}.
 * Preview returns public display metadata/fingerprint, never tokens/auth bytes.
 * Import supports compact v2 login-add only; no full-profile restore/backup.
 * Once apply starts, finish commit/rollback even if cancellation arrives.
 * Errors may leave a valid newly created config, or partial accounts when
 * rollback_failed. Do not claim unchanged state or automatically restart on
 * failure. Caller keeps this DLL/context alive until worker completion.
 */
typedef uint32_t (*ValmaxTransferCallback)(void *context);
typedef struct ValmaxTransferHost {
    uint32_t size, version;
    void *context;
    ValmaxTransferCallback wait_closed, check_closed, cancelled;
} ValmaxTransferHost;
int32_t valmax_transfer_preview(const uint8_t *input, size_t input_len, ValmaxCoreBuffer *output);
int32_t valmax_transfer_import(const uint8_t *local, size_t local_len,
    const uint8_t *input, size_t input_len, const ValmaxTransferHost *host, ValmaxCoreBuffer *output);
/* Worker-only recovery status reads the existing marker under trusted local.
 * result is exactly {required:bool,fingerprint:string}; false has an empty
 * fingerprint. Read/malformed errors are recovery_required, never required=false.
 * No profile/backup paths or credential values are returned.
 * recover requires exact JSON {password,fingerprint} and explicit user approval.
 * It invokes full recovery of that marker only, with the same native closed-
 * client callbacks/ownership as import. Fingerprint is SHA256 of the canonical
 * marker, not an archive path. A changed marker fails source_changed. No marker
 * fails recovery_not_required. Success result is {schema:1,result:"restored",files:N}.
 * Never launch automatically. rollback_failed or recovery_required after apply
 * may mean a partial/committed result requiring attention; do not hide on close.
 */
int32_t valmax_transfer_recovery_status(const uint8_t *local, size_t local_len, ValmaxCoreBuffer *output);
int32_t valmax_transfer_recover(const uint8_t *local, size_t local_len,
    const uint8_t *input, size_t input_len, const ValmaxTransferHost *host, ValmaxCoreBuffer *output);

typedef struct ValmaxRuntime ValmaxRuntime;
/* Worker-only byte sink for one authorized pending attachment lease. No caller
 * path/URL accepted. open input is exact UTF8 JSON {jobId,jobToken,messageId,
 * attachmentId},1..1024 bytes; first admit media-start for that pending asset.
 * out must point to a NULL owning slot. Return0ok,1invalid,2state/notfound,
 * 3busy,4cancelled,5io,6limit,7space. Sink writes1..1MiB per call, at most16GiB
 * total, checks cancellation each call, never retains input. Unique ownership;
 * no concurrent write/close, clone or reuse of a freed pointer. Keep the DLL
 * loaded until close. Runtime stays alive during open; sink retains its job and
 * shutdown immediately signals cancellation. close(NULL-owning-slot) is a no-op;
 * close resets the slot and closes the writer even on flush error. It does NOT
 * mark media ready: host sends existing media terminal command after close.
 */
typedef struct ValmaxExportDownload ValmaxExportDownload;
uint32_t valmax_export_download_open(const ValmaxRuntime *,const uint8_t *,size_t,ValmaxExportDownload **);
uint32_t valmax_export_download_write(ValmaxExportDownload *,const uint8_t *,size_t);
uint32_t valmax_export_download_close(ValmaxExportDownload **);
/* Host-only selected history view evidence. Never register these as a QML/JSON
 * assertion method. ABI1 binding112/observation136 bytes on Windows x64. Source
 * is a nonzero opaque native view generation, never an address. The host must
 * validate the native callback contract before pending_done=1; View.hasMore
 * alone is not evidence of a server-terminal page. No message data crosses.
 * job_id/job_token are fixed canonical lower-hex without a terminating NUL.
 * Pointers are readable/aligned and runtime remains alive through each call;
 * concurrent close is forbidden. Return0success,1invalidABI,2job/state/panic,
 * 3busy (bind/observe only; retain the bounded native observation and retry).
 * invalidate verifies job+source and signals cancellation immediately, including
 * a busy worker; it does not wait for cleanup or delete completed archives.
 */
typedef struct ValmaxExportHistoryBinding {
    uint32_t size, version; uint64_t source; uint8_t job_id[32], job_token[64];
} ValmaxExportHistoryBinding;
typedef struct ValmaxExportHistoryObservation {
    ValmaxExportHistoryBinding binding;
    uint32_t direction, count, pending_done, more_backward, more_forward, reserved;
} ValmaxExportHistoryObservation;
int32_t valmax_runtime_export_history_bind(const ValmaxRuntime *, const ValmaxExportHistoryBinding *);
int32_t valmax_runtime_export_history_observe(const ValmaxRuntime *, const ValmaxExportHistoryObservation *);
int32_t valmax_runtime_export_history_invalidate(const ValmaxRuntime *, const ValmaxExportHistoryBinding *);
/* open config is exact JSON: {profileDirectory:<absolute UTF-8 path>,
 * runtimeDirectory:<absolute UTF-8 path>,existingClient:<bool>}.
 * Only the native host supplies this configuration; user pages cannot choose
 * arbitrary store paths. A fresh client writes its bool-only active snapshot;
 * existingClient reads the previously fixed snapshot without rewriting it.
 * runtime points to a NULL slot. Return 0 uses the same owned result envelope;
 * on an operation error the runtime remains NULL. Workers may dispatch in
 * parallel. The host must finish them all before close; close resets the slot.
 * Runtime get/set operations expose no arbitrary file/path/credential API.
 */
int32_t valmax_runtime_open(const uint8_t *config, size_t config_len, ValmaxRuntime **runtime, ValmaxCoreBuffer *output);
int32_t valmax_runtime_dispatch(const ValmaxRuntime *runtime, const uint8_t *input, size_t input_len, ValmaxCoreBuffer *output);
/* Host shutdown only: permanently stop export admission and signal its current
 * work, including a start already being admitted. No I/O or worker join here.
 * Return 0 acknowledges the signal, NOT cleanup. Null/panic returns1. Concurrent
 * dispatch is allowed; keep the handle alive, join every worker, then close. */
int32_t valmax_runtime_cancel_pending(const ValmaxRuntime *runtime);
void valmax_runtime_close(ValmaxRuntime **runtime);
struct ValmaxGhostStatus;
/* Host-only native hook acknowledgement; never expose this method in QML.
 * Accepts the versioned 144-byte status from native/hooks/ghost.h (policy80).
 * NULL revokes a previous ACK. Invalid/stale/revision-mismatched observations
 * never report saved preferences as active protection. Return 0/1 success/error.
 */
int32_t valmax_runtime_ghost_ack(const ValmaxRuntime *runtime, const struct ValmaxGhostStatus *status);
/* Host-only passive retention receipt. The struct is ABI1, 184 bytes on
 * Windows x64, pointer-free and count-only. No path, account, chat, message ID
 * or text crosses this boundary. NULL revokes the current observation.
 * Only the trusted C++ host may call this; QML cannot supply a receipt.
 * `applied` binds `revision` and `generation` to the actual host preference.
 * `worker_ready` means active capture, while `worker_available` means the
 * infrastructure can accept a new preference. Return 0/1 success/error.
 */
typedef struct ValmaxRetentionNativeStatus {
    uint32_t size, version, error, installed;
    uint32_t images_exact, listeners, gates, owner_join;
    uint32_t store_ready, worker_available, worker_ready, preference_enabled;
    uint32_t applied, reserved;
    uint64_t generation;
    uint8_t revision[64];
    uint64_t captured, marked, missing, missed, failures;
    uint32_t queued, in_flight, pending, reserved_tail;
} ValmaxRetentionNativeStatus;
int32_t valmax_runtime_retention_ack(const ValmaxRuntime *runtime,
                                     const ValmaxRetentionNativeStatus *status);
/* Host-only, never a QML method. ABI v1 size32 on Windows x64.
 * user_data and callbacks must remain valid until detach/replace returns or
 * runtime closes after all workers finish. Callbacks must not unwind/reenter
 * Rust, and must be safe on worker threads. prepare receives bounded JSON:
 * {schema:1,ready:true,protected:[{ownerId:string,chatId:string}],
 * revision:64-lower-hex,leaseMs:5000}. At most256 scopes, positive int64 owner,
 * signed int64 chat including0, sorted lexicographically by owner/chat.
 * No PIN/verifier/name/message crosses this callback.
 * prepare must confirm real hooks and the matching policy, then return0 with
 * nonzero lease while retaining its transaction lock. Failure returns nonzero
 * with lease0. release runs exactly once on the SAME OS THREAD after the PIN
 * write/grant (or write failure), and only releases the lock, without reverting
 * to an earlier policy. Rust keeps the lease !Send and serializes registration.
 * Never hold the host transaction lock before calling Rust: refresh must use
 * the export below, which acquires the PIN/store lock before prepare.
 */
typedef struct ValmaxNotificationCallbacks {
    uint32_t size; uint32_t version; void *user_data;
    int32_t (*prepare)(void *, const uint8_t *, size_t, uint64_t *);
    void (*release)(void *, uint64_t);
} ValmaxNotificationCallbacks;
/* NULL callbacks detaches; waits for an active lease to release. Return0/1.
 * Invalid registration preserves the current valid registration. */
int32_t valmax_runtime_notifications_attach(const ValmaxRuntime *, const ValmaxNotificationCallbacks *);
/* Host-only periodic refresh; 0=confirmed,1=invalid argument,
 * 2=notification unavailable,3=store/internal failure. No catalogue is logged. */
int32_t valmax_runtime_notifications_refresh(const ValmaxRuntime *);
#ifdef __cplusplus
}
#endif
#endif
