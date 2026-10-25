#pragma once

#include "preview_provider.h"
#include <QObject>
#include <memory>

class QQmlEngine;

namespace valmax::media {

// Copyable host-only delivery capability. It contains no QObject pointer.
// valid() may be read by native workers; it is an early cancellation hint,
// not a substitute for publish() on the engine thread or ControllerStamp.
class EngineMediaLease final {
public:
    EngineMediaLease() = default;
    bool valid() const noexcept;
    qint64 account_id() const noexcept;
    quint64 page_id() const noexcept;
    quint64 serial() const noexcept;
private:
    struct State;
    explicit EngineMediaLease(std::shared_ptr<State> state);
    std::shared_ptr<State> state_;
    friend class MediaEngineContext;
};

// One engine owns its provider, store and context. This is deliberately NOT a
// singleton: QQmlEngine::clearSingletons must not reset the media generation.
// The first integration permits one current composer per engine. Binding a new
// composer revokes the previous lease and all provider URLs immediately.
// All context methods (including install) require the engine thread. Native
// host adapters supply lossless IDs; this class is not registered with QML.
// Workers retain only EngineMediaLease and deliver through a host dispatcher;
// they must not retain/dereference this QObject from another thread.
class MediaEngineContext final : public QObject {
    Q_OBJECT
public:
    static MediaEngineContext* install(QQmlEngine* engine);
    ~MediaEngineContext() override;

    EngineMediaLease bind(qint64 accountId, quint64 pageId);
    EngineMediaLease advance(const EngineMediaLease& expected);
    bool detach(const EngineMediaLease& expected);
    bool current(const EngineMediaLease& lease) const;
    Result<QString> publish(const EngineMediaLease& lease, const QImage& image);
    PreviewStoreSnapshot snapshot() const;

signals:
    // Adapter must clear displayed image sources, including textures already
    // handed to Qt; QML Image.cache must also be false. No IDs/paths in signals.
    void invalidated();

private:
    explicit MediaEngineContext(QQmlEngine* engine, std::shared_ptr<PreviewStore> store);
    bool on_owner_thread() const noexcept;
    EngineMediaLease replace(qint64 accountId, quint64 pageId);
    void revoke() noexcept;
    std::shared_ptr<PreviewStore> store_;
    std::shared_ptr<EngineMediaLease::State> active_;
    quint64 nextSerial_ = 1;
};
} // namespace valmax::media
