#pragma once
#include "engine_context.h"
#include "service_generation.h"
#include <QPointer>
#include <QUrl>
#include <functional>
#include <optional>

namespace valmax::media {
enum class DraftDispatchResult { Dispatched, Stale, Unavailable, Limit };

// In-process native DTO only, after successful retain_for_handoff on the I/O
// worker. No QVariantMap/QML serialization. The file is already in terminal TTL
// ownership: every result below leaves it there, including suppressed dispatch.
struct RetainedDraftFile {
    QUrl local_url;
    EngineMediaLease lease;
    SearchGeneration search;
};

// A trusted host supplies the existing active composer's objects; this helper
// does not create a sender or traverse reflective QObject properties to find it.
// Create/copy/destroy bindings on the GUI thread. Never send one to a worker.
// Revoke the generation and refresh this binding if the composer, upload view
// or model changes. QObject existence alone does not identify an account.
// The shared generation identity belongs to this engine/composer; its owner
// uses monotonic nonzero values without reuse, and zero permanently revokes it.
class DraftBinding final {
public:
    static std::optional<DraftBinding> create(MediaEngineContext* context,
        const EngineMediaLease& lease, QPointer<QObject> sender,
        QPointer<QObject> upload, QPointer<QObject> model,
        const SearchGeneration& search, std::function<bool()> stillCurrent);
private:
    QPointer<MediaEngineContext> context_;
    EngineMediaLease lease_;
    QPointer<QObject> sender_, upload_, model_;
    std::shared_ptr<const std::atomic<uint64_t>> generation_;
    // Trusted GUI identity check, copied before any foreign getter. Must check
    // the live account/chat/composer chain; may reenter or delete bound objects.
    std::function<bool()> stillCurrent_;
    friend DraftDispatchResult dispatch_retained(const DraftBinding&, const RetainedDraftFile&) noexcept;
};

// GUI-only direct dispatch. Wrong-thread calls return Unavailable before any
// binding/QPointer access (the caller still owns both argument lifetimes).
// Checks both leases, exact search capability identity/current generation,
// public void uploadPhotoOrVideo(QList<QUrl>) and typed qsizetype count <10.
// After reading the count, calls the required host identity guard and rechecks
// every weak pointer and capability before invoking the native upload method.
// A successful invoke returns Dispatched even if it synchronously invalidates
// or deletes the composer: the call already happened. This is NEVER acceptance
// of an attachment. No send, row rollback, filesystem access or logging occurs.
DraftDispatchResult dispatch_retained(const DraftBinding&, const RetainedDraftFile&) noexcept;
} // namespace valmax::media
