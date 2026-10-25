# Native selected-media staging

`native/core/src/media_staging.rs` prepares already downloaded, public Wikimedia
Commons media for the manual MAX draft attachment boundary. It has no network,
HTTP listener, Python dependency, QML input path, profile discovery, credentials,
message sending or SQLite access. A separate host-only C ABI is described below.
It is not a complete `@pic`
or `@vid` integration.

## Host contract

1. Prepare a dedicated local staging directory outside any chat export workspace.
   Pass its explicit absolute path to `StagingRoot::open`; the module neither
   creates nor discovers a root. Every existing ancestor is pinned without
   following reparses, including verification of the opened local volume.
2. On the service I/O thread, call `stage(bytes, MediaSpec { mime,
   expected_bytes }, now_unix_seconds, &Cancellation)`. MIME and expected length
   must come from the validated native Commons candidate. No filename or
   arbitrary path is accepted from QML.
3. Keep the returned `PreparedMedia` lease while checking the current account,
   page and controller generation. If stale, drop it or call `cancel()`.
4. Copy the lease's native-only `file_url()` into the native handoff operation.
   On the I/O thread, recheck generation and retain the file for handoff before
   invoking the MAX presenter. Then recheck GUI generation immediately before
   dispatch. The observed presenter method returns void and has no proven
   per-file acceptance signal: retention means terminal TTL ownership transfer,
   **not a confirmed attachment ACK**. A rejected or suppressed dispatch after
   retention leaves a quota-charged TTL file rather than deleting media that MAX
   may already have accepted. The C++ `retain_for_handoff()` clears its metadata,
   so copy the URL before that call. No URL is published into QML or logs.
5. Before retention, dropping the result attempts exact-identity deletion. A
   deletion blocked by another reader leaves an orphan charged to the next quota
   scan; explicit `cancel()` reports `cleanup_failed`. The composer retains its
   existing ten-attachment limit and manual send behavior. Attribution comes from
   the existing protocol/controller; staging neither creates nor sends a caption.

`Cancellation` can be signaled from another thread. Cancellation checks run
before file creation, between 64 KiB write/readback chunks, and around flush and
handoff. Filesystem calls themselves are synchronous and do not have a hard
deadline; this layer must not execute on the GUI thread. A `PreparedMedia` keeps
the root/ancestor handles alive even if its originating `StagingRoot` is dropped.

## Storage policy

- Maximum 25 MiB per file, exact expected length, and six exact MIME types:
  JPEG, PNG, GIF, WebP, WebM and MP4. Magic bytes match the legacy pure
  `valid_content` predicate, including the WebM marker window and MP4 brands.
- Maximum 128 MiB of recognized owned files. The zero-wait, cross-process
  `.valmedia.lock` covers scan, quota admission and the complete pending write.
  Existing staged leases, retained files and recognized orphan `.part` files
  count. Other files are left untouched and their contents are not read.
- Opaque filenames are `valmedia-<32 lowercase random hex>.<extension>`.
  Creation is exclusive and never overwrites. The persistent empty lock file
  is checked as a regular single-link file and is not deleted after use.
- Enumeration uses a separate pinned directory handle with a bounded 64 KiB
  buffer and at most 4096 entries, including unknown names and the lock.
  Admission reserves the new entry so the next scan remains possible. Invalid
  enumeration, unknown errors, duplicate entries and malformed bounds fail
  without using a partial quota total.
- Files older than 24 hours are eligible; the exact boundary and future
  timestamps are retained. Cleanup requires an exclusive delete handle, so an
  existing MAX-style read handle prevents deletion and the file stays charged.
- Child opens and deletes are relative to pinned handles. Reparses, directories
  in owned-file slots, hard links and object replacements are rejected.
  Cleanup checks the 128-bit file identity, volume, creation time, size and
  modification time. It never recursively removes a directory.
- The write handle is flushed and closed before a read-only lease is acquired.
  Under that lease, a bounded complete readback compares against the original
  bytes, checks exact EOF and revalidates identity. This detects same-file,
  same-length changes in the close/reopen interval. The lease permits readers
  but denies subsequent ordinary write/delete opens.

These are cooperating-native-writer and ordinary file-handle guarantees, not
isolation from arbitrary code running as the same Windows user. No claim is made
against hostile preexisting writable mappings or modifications after the host
deliberately releases the lease. The caller owns directory preparation and must
not mix this root with an unrelated application or an old staging writer.

## Finite errors and integration

`Error::code()` returns only `invalid_input`, `unsupported_media`,
`file_too_large`, `cache_full`, `download_busy`, `unsafe_path`,
`storage_unavailable`, `no_space`, `cancelled`, `cleanup_failed`, or
`unsupported_platform`. Paths, byte contents and raw OS errors do not escape in
error text. The future service adapter must map these to finite UI results;
it must retain controller generation checks and the separate serialized
selected-download gate. The global media scheduler's two slots do not replace
that selected-download gate.

The low-level Rust surface is `StagingRoot::open`, `stage`,
`cleanup_expired`, `PreparedMedia::{path,file_url,byte_length,cancel,
retain_for_draft}`, and `Cancellation::{cancel,check}`. No callback is used.
The historical Rust name `retain_for_draft` refers to leaving
the file for later use; the current native handoff boundary is the terminal TTL
transfer above, not proof that MAX attached it. A new DLL is not installed or
substituted by this change.

## Host C ABI v1

`native/core/src/media_staging_ffi.rs` and `native/core/valmax_core.h` define
`valmax_media_staging_{abi_version,begin,stage,signal_cancel,cancel,retain,release}`.
The version is 1, the stage request is 32 bytes and its result is 40 bytes on
Windows x64. A result holds an owned UTF-8 `ValmaxCoreBuffer` URL; free that buffer
with `valmax_core_buffer_free` independently of the operation handle.

`begin` accepts only an explicit trusted host root. One never-reused `uint64_t`
token owns cancellation, the one-shot stage and its prepared lease. Maximum 64
jobs includes pending begins and revoked jobs whose in-flight stage still owns
an `Arc`. Releasing a token cannot free an object still executing on a worker.
Input bytes remain borrowed until the synchronous stage call actually returns.

`signal_cancel` uses only a short registry lookup and an atomic flag. It never
becomes a last `Arc` owner and never performs filesystem cleanup, even if a
selection has already become Prepared. All other calls, including destruction
through the C++ wrapper, belong on an I/O thread. `cancel` cleans a prepared file;
`release` also revokes the token. Cleanup failure is visible as error10 and the
orphan remains charged. Release during Running acknowledges revocation, not that
its eventual cleanup has completed. The DLL must remain loaded until every call
and caller-owned buffer has finished.

Publication, release and retain are ordered under the registry/state locks.
Revocation before publication yields cancellation with an empty result. If
publication wins, an immediately subsequent cancellation can revoke the lease;
the host must still recheck generation before handoff. Retain is terminal: a
later cancel/release preserves the TTL file. Repeated release is a no-op; a live
cancelled job accepts repeated cancellation, and a retained job accepts repeated
retain. Unknown tokens in other operations produce finite `invalid_handle`.

The host-only C++ wrapper is `native/media/staging_client.{h,cpp}`. It keeps the
URL native, separates thread-safe cancellation from move-only I/O ownership, and
does not load a DLL, discover a root, attach a file or send a message itself.

## Verification

`native/core/tests/media_staging_proof.py` uses the pinned Rust 1.99.1 toolchain
and a separate `C:/ValMAXBuild/target-media-staging` target. It captures source
hashes, verifies the pure Python fixtures, runs the complete Rust release test
suite, and verifies the previously frozen DLLs remain byte-identical. The exact
result and log path are in
`analysis/native-migration-v1/media-staging-proof.json`.

Tests use only owned synthetic directories under `C:/ValMAXBuild/fixtures`.
Coverage includes 690 Python/Rust magic cases, real child-process lock
contention, held-reader TTL, quota/orphan charging, capacity bounds, cancellation
and failure checkpoints, exact-identity cleanup, junction/hardlink rejection,
same-file rewrite detection and Unicode URLs. The independent source review is
`analysis/native-migration-v1/media-staging-independent-review.json`.

Failure checkpoints return a simulated `NoSpace` before/after write and flush;
they are not a physical full-disk, OS flush-failure or power-loss test. The
reader tests use ordinary synthetic `File` handles, not a running MAX draft.
Actual MAX attachment behavior, generation handoff across the Qt/Rust boundary,
installer packaging and production lifecycle remain integration work. The C ABI
targeted proof is `analysis/native-migration-v1/media-staging-ffi-targeted-proof.json`;
its full regression is recorded separately in `media-staging-ffi-proof.json`.

The final host-ABI candidate passed 10 targeted tests and the complete 178-test
Rust release suite with zero ignored tests. A separate, genuinely linked C++
run passed 226 synthetic ABI checks and 438 real DLL checks; its exact inputs
and limitations are in `media-staging-client-proof.json`. All three reports pin
the same DLL SHA-256
`6d51da3b847411e72f38b101c78e40573342e636cfdbadb3594aa9957c689b54`.
Source hashes were unchanged during each run, and the four previously frozen
core DLLs were verified unchanged. These results do not establish real MAX
attachment acceptance, an installer update, or a complete native migration.
