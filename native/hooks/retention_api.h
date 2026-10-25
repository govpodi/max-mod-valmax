#pragma once
#include <stdint.h>
#include <stddef.h>
#ifdef _WIN32
# ifdef VALMAX_RETENTION_BUILD
#  define VALMAX_RETENTION_API __declspec(dllexport)
# else
#  define VALMAX_RETENTION_API __declspec(dllimport)
# endif
#else
# define VALMAX_RETENTION_API
#endif
#ifdef __cplusplus
extern "C" {
#endif

enum ValmaxRetentionError {
    VALMAX_RETENTION_OK=0, VALMAX_RETENTION_ARGUMENT=1,
    VALMAX_RETENTION_NOT_READY=2, VALMAX_RETENTION_QUEUE_FULL=3,
    VALMAX_RETENTION_TIMEOUT=4, VALMAX_RETENTION_STORAGE_ERROR=5,
    VALMAX_RETENTION_STALE=6, VALMAX_RETENTION_POLICY_ERROR=7,
    VALMAX_RETENTION_BUSY=8, VALMAX_RETENTION_EXHAUSTED=9
};
enum ValmaxRetentionKind { VALMAX_RETENTION_RECEIVED=1, VALMAX_RETENTION_PREPARE_DELETE=2 };
enum ValmaxRetentionAckResult { VALMAX_RETENTION_DURABLE=0, VALMAX_RETENTION_STORAGE_FAILED=1 };
enum { VALMAX_RETENTION_MAX_IDS=4096, VALMAX_RETENTION_BITMAP_BYTES=512,
       VALMAX_RETENTION_QUEUE_SLOTS=8, VALMAX_RETENTION_MAX_WAIT_MS=25 };

// SHA256 of UTF-8 {"enabled":true/false,"schema":1}\n, matching the old store.
// Strictly increasing nonzero generation; failed/unready updates revoke new
// decisions. Previously returned decisions remain owned by their native scope.
typedef struct ValmaxRetentionPolicy {
    uint32_t size, version;
    uint64_t generation;
    char revision[64];
    uint32_t enabled, storage_ready, wait_ms, reserved;
} ValmaxRetentionPolicy;

// Host-only, routing metadata. Never expose this struct or IDs to diagnostics.
// IDs are positive int64; owner positive; chat signed/nonzero. Arrays are unique
// and ordered. digest = SHA256("valmax-retention-ids-v1\0" || u32LE(count) ||
// count * i64LE(id)). Every field below is echoed byte-for-byte in the ACK.
typedef struct ValmaxRetentionRequest {
    uint32_t size, version, kind, count;
    uint64_t nonce, epoch, generation;
    int64_t owner, chat;
    uint64_t deadline_tick; // GetTickCount64 domain; zero for receipt batches
    char ids_digest[64];
} ValmaxRetentionRequest;

// A PREPARE_DELETE bit may be set only after Rust has checked a durable receipt
// for the exact (owner,chat,id), AND durably committed its deletion marker.
// RECEIVED acknowledgements have a zero bitmap. Unused bitmap bits must be zero.
// An ACK is not proof that MAX committed its independent database transaction.
typedef struct ValmaxRetentionAck {
    uint32_t size, version, result, reserved;
    ValmaxRetentionRequest request;
    uint8_t retained[VALMAX_RETENTION_BITMAP_BYTES];
} ValmaxRetentionAck;

// No identifiers, paths, message contents or request digests are returned here.
typedef struct ValmaxRetentionStatus {
    uint32_t size, version, error, ready;
    uint32_t applied, enabled, listeners, gates;
    uint64_t generation;
    char revision[64];
    uint64_t epoch;
    uint32_t queued, in_flight;
    uint64_t received_batches, deletion_requests, acknowledged;
    uint64_t missed, rejected_ack, storage_failures;
} ValmaxRetentionStatus;

// These functions are a host-only queue contract. The first-stage queue module
// installs no MAX hooks and ALWAYS reports ready=applied=0. Full readiness will
// require all five listeners, two guarded branch gates and durable Rust storage.
VALMAX_RETENTION_API uint32_t valmax_retention_set_policy(const ValmaxRetentionPolicy *);
// Single consumer. Copies IDs into caller-owned storage; capacity must be4096.
// wait_ms is bounded to25; an empty/non-ready queue returns NOT_READY. No pointer
// from this call is retained. FIFO ordering includes RECEIVED before DELETE.
VALMAX_RETENTION_API uint32_t valmax_retention_take(ValmaxRetentionRequest *, int64_t *ids,
    uint32_t capacity, uint32_t wait_ms);
VALMAX_RETENTION_API uint32_t valmax_retention_ack(const ValmaxRetentionAck *);
VALMAX_RETENTION_API uint32_t valmax_retention_status(ValmaxRetentionStatus *);
// Cancels new work and pending decisions. BUSY means the consumer still owns a
// copied request; keep the worker/module alive until it ACKs, then retry. Never
// unload while a worker or producer is active. No durable markers are deleted.
VALMAX_RETENTION_API uint32_t valmax_retention_shutdown(void);
#ifdef __cplusplus
}
static_assert(sizeof(ValmaxRetentionPolicy)==96);
static_assert(offsetof(ValmaxRetentionPolicy,generation)==8);
static_assert(offsetof(ValmaxRetentionPolicy,revision)==16);
static_assert(offsetof(ValmaxRetentionPolicy,enabled)==80);
static_assert(sizeof(ValmaxRetentionRequest)==128);
static_assert(offsetof(ValmaxRetentionRequest,nonce)==16);
static_assert(offsetof(ValmaxRetentionRequest,owner)==40);
static_assert(offsetof(ValmaxRetentionRequest,deadline_tick)==56);
static_assert(offsetof(ValmaxRetentionRequest,ids_digest)==64);
static_assert(sizeof(ValmaxRetentionAck)==656);
static_assert(offsetof(ValmaxRetentionAck,request)==16);
static_assert(offsetof(ValmaxRetentionAck,retained)==144);
static_assert(sizeof(ValmaxRetentionStatus)==168);
static_assert(offsetof(ValmaxRetentionStatus,generation)==32);
static_assert(offsetof(ValmaxRetentionStatus,revision)==40);
static_assert(offsetof(ValmaxRetentionStatus,epoch)==104);
static_assert(offsetof(ValmaxRetentionStatus,received_batches)==120);
#endif
