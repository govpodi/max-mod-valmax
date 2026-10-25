#pragma once
#include <QPointer>
#include <QString>
#include <optional>

class QQmlEngine;

namespace valmax::media {
enum class ComposerError { None, WrongThread, Unavailable, Unsupported, InvalidIdentity };

// GUI/engine-thread only. These pointers are never sent to service/I/O workers.
// The caller supplies the actual composer from pinned host QML, not an object
// or account identifier selected by a search/selection request. Public model
// metadata cannot prove arbitrary private presenter/account ownership.
struct ResolvedComposer {
    qint64 accountId = 0;
    QString chatId; // Canonical signed 64-bit decimal, including Saved Messages 0.
    QPointer<QQmlEngine> engine;
    QPointer<QObject> composer, common, selfInfo, sender, upload, model;
};
struct ComposerResolution {
    std::optional<ResolvedComposer> value;
    ComposerError error = ComposerError::Unavailable;
    explicit operator bool() const noexcept { return value.has_value(); }
};

// Reads the current native model and invokes only the pinned QmlUtils typed-ID
// formatters. No input ID, private offsets, QVariant numeric casts, network,
// filesystem or presenter initialization. Requires live arguments owned by this
// thread. Failed/changed/deleted properties produce finite unavailable results.
ComposerResolution resolve_composer(QQmlEngine* engine, QObject* composer);
// Final GUI delivery must resolve again and compare this complete identity.
// The frontend separately assigns a non-recycled native page ID and checks its
// EngineMediaLease/search stamp. No QObject dereference occurs in this comparison.
bool same_composer(const ResolvedComposer& left, const ResolvedComposer& right);
} // namespace valmax::media
