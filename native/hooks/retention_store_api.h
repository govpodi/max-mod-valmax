#pragma once
#include "retention_api.h"

// Host-only exports from valmax_core.dll. These routines have no MAX hooks.
// The caller owns every buffer and must use the same explicit MXMOD profile
// for the capture receipt and for processing the queue request.
#ifdef __cplusplus
extern "C" {
#endif
typedef struct ValmaxRetentionCaptureRow {
    int64_t message;
    int64_t received_at_ms;
    const uint8_t *utf8_text;
    size_t text_length;
} ValmaxRetentionCaptureRow;
// V2 appends the raw positive `time` order key. Zero means unknown; the v1 ABI
// remains available for older callers and also stores a null key.
typedef struct ValmaxRetentionCaptureRowV2 {
    int64_t message;
    int64_t received_at_ms;
    const uint8_t *utf8_text;
    size_t text_length;
    int64_t order_key;
} ValmaxRetentionCaptureRowV2;
// Call preflight off the UI thread. 0=ready, 1=invalid route/path, 5=store error.
uint32_t valmax_core_retention_preflight(
    const uint16_t *profile_utf16, size_t profile_length);
// Passive capture/mark APIs are host-only. They return no content or IDs.
uint32_t valmax_core_retention_capture_text_batch(
    const uint16_t *profile_utf16, size_t profile_length,
    int64_t owner, int64_t chat,
    const ValmaxRetentionCaptureRow *rows, size_t count);
uint32_t valmax_core_retention_capture_text_batch_v2(
    const uint16_t *profile_utf16, size_t profile_length,
    int64_t owner, int64_t chat,
    const ValmaxRetentionCaptureRowV2 *rows, size_t count);
// bits_length must be exactly ceil(count/8), count 1..128. Set bits mean
// paired content and marker were durably committed; missing captures stay zero.
uint32_t valmax_core_retention_mark_delete_batch(
    const uint16_t *profile_utf16, size_t profile_length,
    int64_t owner, int64_t chat, const int64_t *ids, size_t count,
    uint8_t *bits, size_t bits_length);
uint32_t valmax_core_retention_capture_text(
    const uint16_t *profile_utf16, size_t profile_length,
    int64_t owner, int64_t chat, int64_t message,
    int64_t received_at_ms, const uint8_t *utf8_text, size_t text_length);
uint32_t valmax_core_retention_store_process(
    const uint16_t *profile_utf16, size_t profile_length,
    const ValmaxRetentionRequest *request, const int64_t *ids,
    ValmaxRetentionAck *ack);
#ifdef __cplusplus
}
static_assert(sizeof(ValmaxRetentionCaptureRow)==32);
static_assert(sizeof(ValmaxRetentionCaptureRowV2)==40);
#endif
