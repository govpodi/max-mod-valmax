# Native Qt module

`ValMAX.Native 1.0` is a C++ QML extension compiled for the Windows x64 MSVC
ABI and Qt 6.11.2 used by MAX 26.35.1. The module executes inside the QML
engine. It opens no HTTP listener and requires no Python process.

`Bridge.invoke(requestJson)` dispatches bounded asynchronous requests through
the Rust C ABI and emits `operationFinished(id, responseJson)` on the QML
thread. There are at most two workers and eight outstanding operations per
engine. Stateless Rust operations include `version`, `session.validate`,
`session.encrypt` and `session.decrypt`. If the native host sets the explicit
`VALMAX_NATIVE_RUNTIME_CONFIG` JSON before engine creation, the bridge opens
an opaque Rust Runtime asynchronously. Configuration contains only
`profileDirectory`, `runtimeDirectory` (absolute paths), and `existingClient`.
QML cannot configure these paths. Without that variable, no store is opened
and no profile is discovered. `runtimeState` reports `unconfigured`, `opening`,
`ready`, or `error`; `runtimeAvailable` is true only in the ready state.
The Runtime handles `optimization.get/set`, `ghost.get/set`,
`accounts.get/set`, and configuration-only `retention.get/set` alongside stateless operations. The context is closed only
after all workers finish. The singleton also exposes
a small identifier-queue integration probe. `ListWorkQueue` contains
the actual admission queue ported from `modkit/ValMAXListWorkQueue.qml`.
The thin QML adapter in this directory retains the existing frame-driven
integration with `ValMAXAsyncListRow.qml`: the native queue invokes
`valmaxGrantLoad(key, serial)`, and Qt's asynchronous Loader still creates
the visible content. It retains visible-first scheduling, generation checks,
urgent input, five quiet admissions, a single background admission, and
cancellation when a row is pooled or leaves the cache.

`RetentionTransport.qml` migrates the legacy bool preference/status directly
to Rust, without HTTP. The archive and capture event source remain unavailable:
the old `enabled:true` is readable as intent, never reported as active capture.
Enabling returns `retention_unavailable` before writing; disabling uses the old
canonical bytes and a guarded revision check. Unknown archive counts are null.
The generated controller does not poll while lookup capability is false, and
an attempted lookup reports unavailable rather than a successful empty result.
The settings UI explains this limitation. This is not retention integration.

Current configuration receipts: `retention-preferences-unit-proof-20261021.json`
has 13 Rust/C ABI checks; the native UI final/repeat receipts have 76 checks
each; `retention-config-real-runtime-20261021.json` has 36 actual plugin/core
checks across three processes, including old-byte preservation, denied enable,
CAS disable and persistence after restart. The matching common plugin receipt
`qt-retention-config-runtime-20261021.json` has 437 checks. These use synthetic
profiles and do not run MAX or prove installed account compatibility.
`settings-transport-reentrant-regression-20261021.json` separately has 52
checks of the actual shared SettingsTransport with a fake Bridge: cancellation
during invoke, admission exceptions, invalid/duplicate IDs, stale completions
and canceled opening requests do not leak pending work.

The queue publishes `stateChanged` only when one of its five exposed counters
changes, with the snapshot committed before the signal. A no-op request or
cancellation therefore does not reevaluate QML bindings. Synchronous grants,
completion/requeue, destroyed rows and generation reuse are covered by
`native/tests/qt/run_list_work_queue.py`. The A/B receipt
`native-list-work-queue-proof-20261021.json` reports 52 current checks and
40,000 avoided notifications in a synthetic QQmlEngine workload. Its timing
is not a measurement of MAX's rendering or frame rate.

`HistoryModelCursor` reads the ordinary `messageContext` role from a supplied
`QAbstractItemModel` on the model's owning GUI thread. It maintains disjoint
pending row intervals and leases at most 200 persistent indices per batch;
it does not create a delegate for each message or repeatedly scan the entire
loaded prefix. Insert/remove/data changes update pending work; reset, layout,
move, destruction or a model thread change invalidate stale leases. A foreign
thread model is rejected. The cursor does not change object ownership or read
an account database, listen on a port, or call a private history method.

The generated native exporter uses `export_harvest.js` as build-time QML
source. It returns the unprocessed suffix of a lease after byte/time limits,
retries wrappers that are not initialized, and checks job/token/generation
after commit before submitting DTOs to the Rust writer. Its cooperative
2 ms budget is checked between messages; a single SDK call is not preempted.
The 16 ms timer runs only during explicit export. `exhausted` means no pending
local model rows, never authoritative server EOF. Full history completion is
still unverified and the private EOF host stays disabled.

Current receipts (21 October): `history-model-cursor-proof-v2-20261021.json`
has 80 C++ plus 10 QML checks; `history-harvest-integration-final-20261021.json`
and its clock-repeat receipt each have 109 checks of the actual generated
harvest function and real native cursor. DTO construction and writer replies
are synthetic. `qt-model-cursor-runtime-20261021.json` verifies the rebuilt
common plugin with 437 checks in a copied Qt runtime. No real MAX process,
account, chat or external network was used by these checks.

The subsequent caller review found that `historyStep` could page while a
not-yet-initialized local wrapper was still pending. The native generator
now checks `historyCursor.pendingCount` before advancing pagination/proof;
the existing 30-second timeout still bounds waiting. The final caller and
repeat receipts (`history-harvest-caller-*-20261021.json`) each contain
131 checks of both actual generated functions, including readiness without
a model dataChanged signal, cancellation and a partial timeout result.

The plugin is not enabled in the installed client yet. The opt-in generator
`native/tooling/build_native_qml.py --native-runtime` replaces the list queue
and transport of the optimization, ghost, account-preference, quick-reply and
PIN controller pages. Quick-reply composer requests are migrated as well.
The default generator result is verified identical to the existing build.
`SettingsTransport.qml` keeps their existing completion/error contracts while
dispatching directly through C++; it creates no XMLHttpRequest or network
connection. Ghost settings remain unavailable until native hooks acknowledge
the policy; a saved preference never means active protection. The plugin,
`qmldir` and the native QML transports, including `SettingsTransport.qml`,
`ChatLockTransport.qml`, and `RetentionTransport.qml`, belong under
`runtime/mod/qml/ValMAX/Native`; `valmax_core.dll` belongs in `runtime/mod`.
The current generated UI has no legacy localhost helper references. This
does not complete the retention archive, authoritative server history EOF,
early dynamic protection readiness or installed full-client validation.

The separate opt-in build argument
`-DVALMAX_GHOST_LIBRARY=C:/ValMAXBuild/build/hooks/valmax_ghost.lib` enables
`GhostHost`. It obtains `max.exe` and `core.dll` paths only from the actual
native application directory. The hook DLL contains build-generated image
pins; neither QML nor environment variables can override them. The host
installs once, reads the desired policy on a worker, applies it, then forwards
only a complete matching native status to `valmax_runtime_ghost_ack`. This
refresh repeats every two seconds. Saving settings itself never creates an
ACK. Errors revoke the ACK; destruction revokes it before bounded shutdown.
A second engine cannot replace the first engine's hook ownership, and a BUSY
result from a pre-existing native owner does not authorize detaching it.

`-DVALMAX_NOTIFICATION_LIBRARY=C:/ValMAXBuild/build/hooks/valmax_notifications.lib`
enables the host-only `NotificationHost`. Rust calls its prepare callback with
at most 256 normalized scope IDs and a canonical revision, never a PIN,
verifier, sender name or message. The host verifies the revision and actual
six-replacement/two-listener status before returning a transaction lease.
The lease stays locked until Rust finishes durable PIN persistence and grants;
release never restores an old policy. Periodic refresh enters through Rust's
store lock first, so it cannot overwrite a newly staged policy with a stale
catalog. Callback registration, asset paths and hook receipts are unavailable
to QML. Missing startup modules get at most eight delayed retries; permanent
module or asset mismatches remain unavailable. Destruction drains workers,
detaches callbacks, then shuts down only its own hook installation.

`ChatLockTransport.qml` preserves the existing controller's validation, secret
clearing, grants and native MAX widgets while dispatching directly to Rust.
If notification hooks are unavailable, existing ordinary chats still open,
but creating a new PIN is rejected before a verifier is persisted. QML string
heaps are not claimed to be securely erased; owned UTF-8 bridge buffers are
erased after use and no PIN is included in logs, counters or command arguments.

## Build and isolated runtime validation

Use the MSVC 2022 x64 environment, CMake/Ninja and the Qt 6.11.2 MSVC SDK:

```powershell
. D:/ValMAX/native/tooling/msvc_env.ps1
cmake -S D:/ValMAX/native/qt -B C:/ValMAXBuild/build/qt-bridge -G Ninja `
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=C:/ValMAXBuild/Qt `
  -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL `
  -DCMAKE_TRY_COMPILE_CONFIGURATION=Release `
  -DVALMAX_CORE_LIBRARY=C:/ValMAXBuild/target/release/valmax_core.dll.lib
cmake --build C:/ValMAXBuild/build/qt-bridge
python D:/ValMAX/native/tests/qt/run_max_runtime.py `
  --max-runtime D:/ValMAX/release/valmax-v8.8.4-20261016/build/dist/ValMAX/runtime/mod `
  --build C:/ValMAXBuild/build/qt-bridge `
  --scratch C:/ValMAXBuild/build/qt-probes `
  --proof D:/ValMAX/analysis/native-migration-v1/qt-runtime-proof.json `
  --rust-dll C:/ValMAXBuild/target/release/valmax_core.dll
```

Python here is a build/test tool. The executable being tested is native C++.
The probe copies the original MAX Qt runtime into an isolated fixture and
checks the paths and SHA-256 hashes of the DLLs actually loaded by the native
process. It never launches MAX or opens account data. The second QML test
uses the existing asynchronous row component, a synthetic appearance module
and synthetic rows to exercise real Loader creation, scrolling, cache
release, identity replacement and pooling. The Rust test exercises the actual
native boundary, including encryption, decryption, rejection of a wrong
password, queue limits and a responsive QML timer during cryptography.
Negative controls remove the native plugin, disable admission and request a
missing platform plugin. Loader failures go to captured stderr, never a
Windows error dialog. The synthetic window is transparent, cannot receive
input or focus, and is not shown in the taskbar.

`run_settings_runtime.py` uses the actual three page controllers with only
their visual SDK dependencies and current account replaced by synthetic
fixtures. It checks disk persistence across processes, existing-client versus
restart semantics, revision conflicts, cancellation, the eight-request bound,
and missing/malformed/relative host configuration. It is not a visual SDK test.
The separate `max_engine_probe.py` proves native Bridge and actual Loader queue
loading inside the real MAX QQmlEngine. That probe uses a fresh anonymous
namespace and a development-only pre-resume guard on main/service processes:
network and credentials are denied and Qt paths point at synthetic data.
Evidence is saved in `analysis/native-migration-v1/qt-*-proof.json`.

`max_ghost_probe.py` copies the exact fixture pinned by `ghost_generate_pins.py`
and proves installation of all five replacements and seven listeners inside
real MAX, with disabled policy only. It observes genuine C++ host -> Rust ACKs,
then explicitly detaches its own fixture's hooks and observes ACK revocation.
`ghost_host_test.cpp` separately tests failure/partial-status/ownership branches
using a synthetic in-EXE C ABI and the real Rust Runtime. These tests do not
claim server-visible ghost behavior or real-account unread-count validation.

`run_notification_host.py` checks real Rust persistence with synthetic in-EXE
notification ABI responses, including wrong modules, partial hook sets,
mismatched revision, delayed startup, callback draining, concurrent refresh,
64-bit scopes and ownership-aware shutdown. `run_chat_lock_runtime.py` uses
the actual PIN controller statements on MAX's real Qt DLLs and checks that
missing native protection blocks set, clears input fields, preserves ordinary
chat access and propagates finite errors. Its visual SDK dialog is stubbed;
these checks do not establish notification behavior inside MAX.

`max_notification_probe.py` separately uses the exact pinned MAX fixture,
with pre-resume network/credential guards. It verifies the actual C++ host's
six replacements and two listeners, then exercises a synthetic Rust PIN
set/lock/unlock/remove through the trusted lease, observes the empty-policy
refresh, and detaches its own hooks to prove protection becomes unavailable.
It observes only finite status fields and fixed synthetic inputs. No real
account or OS notification delivery is exercised.
