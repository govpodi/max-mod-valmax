# Native preview provider: integration contract

Status: standalone provider implemented and tested; no production plugin,
network transport or user interface is connected. Reviewed against Qt 6.11.2 and
the existing `native/qt/plugin.cpp`, `native_bridge.*`, `native/media/protocol.*`,
`scheduler.*` and `thumbnail.*`.

## Integration boundary

`ValMAXNativePlugin::initializeEngine()` is the installation point for an
engine-owned `QQuickAsyncImageProvider` named `valmaxmedia`. The native build
links its provider target to `Qt6::Quick` and `Qt6::Gui`; the current plugin
links directly only to Core/Qml. Existing type registration and the legacy
QML build remain unchanged.

The provider and the future media controller share an engine-local native
preview store. Neither uses the lifetime of the `Bridge` singleton as its
owner: `QQmlEngine::clearSingletons()` can delete that singleton while the
engine and image provider remain alive. There is no process-global cache of
account state, no QML method that inserts arbitrary bytes, and no raw engine
pointer retained by a worker.

Source: `image://valmaxmedia/<engineNonce32>/<epoch16>/<serial16>`.
All components are lowercase hexadecimal. The host creates a unique nonce for
each engine. The store generates an epoch and a monotonically increasing serial;
neither is recycled after eviction or generation changes, and overflow closes
admission. The provider rejects other
forms and unknown, expired or revoked entries. It never accepts a filename,
remote URL, account identifier, query string or MIME choice from QML.
Fresh engine nonces and never-reused store IDs prevent a Qt image-cache hit
from representing a different result after an engine/search lifecycle change.

## Memory-only first stage

The initial provider reads only immutable, already decoded `QImage` snapshots.
`decode_thumbnail()` runs earlier in a bounded worker queue: compressed data
is at most 2 MiB; source dimensions at most 4096 per side and 4 Mi pixels;
the output box is at most 512 per side. Provider lookup never starts a fetch,
opens a file, invokes Rust, or reads the caller's profile. A missing entry
returns a finite error. A future controller publishes a preview URL only
after the corresponding snapshot is ready.

Store budget: 16 entries and 8 MiB of resident image pixels,
with eviction and revocation under a short mutex. Count `sizeInBytes()`, not
only entry count. Take an implicitly shared immutable image snapshot under the
mutex, then release it before scaling, allocating a response, or emitting a
signal. At most eight responses may hold image snapshots; an
evicted entry can remain alive in an already admitted response. Account/chat
changes revoke the relevant ticket generation before any later publication.
The decoder adapter must retain `ControllerResult.stamp` and recheck
`MediaController::current(ControllerStamp)` after decoding, on its service thread,
before publishing. Checking only `current(ControllerScope)` misses a newer search
on the same page. The store epoch is an additional independent publication guard.

The thumbnail decoder's output is self-owned premultiplied ARGB32 pixel storage
tagged sRGB. Publication rejects other formats/profiles and copies pixels into
fresh storage with the same known sRGB tag; arbitrary ICC/text/DPR are not retained. Do not
construct a `QImage` that borrows a reply buffer, use `QPixmap`, or allocate a
GPU texture in the decode worker. Default/invalid requested sizes use the
documented 240×180 box; positive sizes are bounded to the decoder's limit and
keep aspect ratio. Size variants must not trigger another network request.

## Response and shutdown invariants

`requestImageResponse()` is reentrant. Response bookkeeping therefore cannot
directly call the owner-thread-only `ProviderScheduler`. That scheduler belongs
to the media transport/controller; the memory-only provider does not create
another network scheduler or retry gate.

Each returned response has a native cancellation flag and exactly one terminal
notification. Memory-only lookup and optional bounded downscaling finish before
returning the response. `finished()` is the last action of completion, including
errors. The engine owns response cleanup. No executor, worker queue or pending
GUI callback belongs to this provider.
`textureFactory()` constructs the factory lazily from the finalized image,
and ownership of that factory passes to Qt. See the
[Qt 6.11 response contract](https://doc.qt.io/qt-6.11/qquickimageresponse.html).

Qt 6.11.2 explicitly handles finishing before the reader connects its completion
slot: `QQuickImageResponsePrivate::finished` is atomic, and
`QQuickPixmapReader::processJob()` checks it after connecting. This behavior was
verified in two actual QQmlEngine instances using the MAX Qt DLLs. See the
[pinned reader source](https://github.com/qt/qtdeclarative/blob/v6.11.2/src/quick/util/qquickpixmapcache.cpp).

Provider destruction closes admission and revokes its store. Remaining responses
hold only independent image/generation/counter state, never a provider, Bridge or
engine pointer. A revoked response cannot create a new texture factory. A texture
already handed to Qt cannot be recalled: the later QML controller must clear
Image.source on scope changes and always use Image.cache=false. The eight-image
response cap bounds retained pixels, not the number of tiny Qt-owned terminal
error objects. It is separate from Qt's image/texture caches.

Qt owns an installed provider and can call image requests from multiple
threads. Its destructor must be thread-safe. The intended plugin installation
hook and these requirements are documented in
[QQmlExtensionPlugin](https://doc.qt.io/qt-6.11/qqmlextensionplugin.html) and
[QQuickImageProvider](https://doc.qt.io/qt-6.11/qquickimageprovider.html).

## Standalone proof and remaining integration

- Two engines with distinct stores/nonces; singleton clearing and engine
  recreation cannot reveal another engine's image or revive revoked tickets.
- Valid/missing/expired IDs, oversized sizes, cache eviction and concurrent
  requests stay within explicit entry/byte/response limits.
- Cancellation of a returned response prevents subsequent texture creation;
  no provider-owned worker or queued task is involved.
- Engine/provider destruction during reader delivery and repeated immediate
  teardown leaves no tracked response alive.
- Real MAX Qt DLLs in a standalone hidden fixture produce Image.Ready for a
  generated in-memory image. This is a loading/lifetime test, not a visible
  GPU rendering, live network request or MAX draft attachment test.

Evidence: `analysis/native-migration-v1/media-preview-provider-proof.json`,
197 native checks, 800 concurrent memory-only requests (including 100 concurrent
generation changes) and 22 engine teardowns with unique engine nonces.
Production plugin registration, scheduler/decoder integration and source clearing
in the future media controller remain separate reviewable steps.

## Engine-owned composer capability

`native/media/engine_context.{h,cpp}` now supplies the native engine lifetime
boundary. `MediaEngineContext::install(engine)` installs one provider/context
without replacing an existing provider. It is an engine child, not a QML
singleton. An existing context with a closed/removed provider fails admission
rather than silently creating another provider under the same name.

Only one composer is active per engine. A host-only `bind(accountId,pageId)`
revokes the previous capability; `advance(expected)` does the same for a new
search. Both clear the pixel epoch before notifying the GUI adapter through
`invalidated()`. The adapter must clear its displayed image sources. The native
IDs retain their full 64-bit representation and are not accepted through a
QML-facing method. A copied `EngineMediaLease` contains immutable values and a
shared atomic revocation flag, with no QObject pointer. Workers may read its
validity as an early cancellation hint. Final GUI-thread publication still
checks exact lease identity and the store epoch; controller generation checks
on the service thread remain independently required.

`analysis/native-migration-v1/media-engine-context-proof.json` records 401
checks using the pinned MAX Qt DLLs. Twelve engine cycles instantiate a real
disposable singleton, clear it, verify the provider/context remain current,
load an actual QtQuick Image and then revoke/destroy the engine. Other cases
cover reentrant bind/deletion, cross-engine isolation, lossless IDs above
JavaScript's safe integer bound and worker observation of revocation. No
production plugin or MAX composer is registered by this fixture.
An independent source review found no blocking issues; it is separate from the
native test result. A failed rebind after provider removal also emits source
invalidation and revokes the previous lease.

Metadata remains `Text.PlainText` in the later composer. Decoder bounds do not
constitute a codec sandbox or a hard cap on every codec's internal allocation.
This component changes neither the early-startup protection gate nor retention.
