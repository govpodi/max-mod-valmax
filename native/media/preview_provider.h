#pragma once

#include "protocol.h"
#include <QImage>
#include <QQuickImageProvider>
#include <memory>

namespace valmax::media {
struct PreviewStoreSnapshot {
    int entries = 0;
    qsizetype residentBytes = 0;
    int imageResponses = 0;
    int responses = 0;
    quint64 epoch = 0;
    bool closed = false;
};

// Host-only, memory-only store. Never exposed as a QML type. The caller passes
// its captured epoch after decoding; stale work cannot publish into a new scope.
// IDs are generated here and never recycled, including after eviction/revocation.
// Input must be bounded, premultiplied ARGB32 tagged sRGB (decoder output).
class PreviewStore final {
public:
    static constexpr int MaxEntries = 16;
    static constexpr qsizetype MaxResidentBytes = 8 * 1024 * 1024;
    static constexpr int MaxImageResponses = 8;
    explicit PreviewStore(QString engineNonce);
    ~PreviewStore();
    Result<QString> publish(quint64 epoch, const QImage& readyImage);
    quint64 advance();
    void close();
    PreviewStoreSnapshot snapshot() const;
private:
    friend class PreviewProvider;
    struct State;
    std::shared_ptr<State> state_;
};

// The QQmlEngine owns this object after addImageProvider("valmaxmedia", ...).
// Reentrant requests return already-completed, immutable responses. Qt 6.11.2
// explicitly handles finish-before-connect; no private executor or GUI callback
// is required. Only ready images are read; no decoder/network/files are invoked.
class PreviewProvider final : public QQuickAsyncImageProvider {
public:
    explicit PreviewProvider(std::shared_ptr<PreviewStore> store);
    ~PreviewProvider() override;
    QQuickImageResponse* requestImageResponse(const QString& id, const QSize& requestedSize) override;
private:
    std::shared_ptr<PreviewStore> store_;
};
} // namespace valmax::media
