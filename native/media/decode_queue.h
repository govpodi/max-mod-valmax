#pragma once
#include "thumbnail.h"
#include <QObject>
#include <functional>
#include <memory>

namespace valmax::media {
enum class DecodeState { Completed, Cancelled, Failed };
struct DecodeResult {
    DecodeState state = DecodeState::Failed;
    std::optional<Thumbnail> thumbnail;
    Error error;
};
struct DecodeSnapshot { int waiting=0, active=0, pending=0; bool stopping=false; };
using ThumbnailDecoder = std::function<Result<Thumbnail>(const QByteArray&, const QString&, QSize)>;

// Owner-thread API; decoding alone runs on two native worker threads. A completed
// worker retains its active slot until delivery on the owner thread. Thus a slow
// event loop cannot accumulate unbounded decoded images. Cancellation never
// starts another decoder while the cancelled decoder is still executing.
// scope is a native host ticket, not an account number supplied by QML. Caller
// must check its current page/store generation again before publishing pixels.
// shutdown() is asynchronous. Destructor silently cancels pending callbacks and
// waits for running codecs; workers never wait for an owner-thread callback.
// Do not destroy/move concurrently or destroy from a decoder callback. No socket,
// file, QPixmap, GUI object, process-wide codec policy or global thread pool.
class DecodeQueue final : public QObject {
public:
    static constexpr int MaxActive=2, MaxWaiting=8;
    // Optional decoder is a trusted test seam, called concurrently on workers;
    // it must be thread-safe, not reenter/delete this queue, and eventually end.
    explicit DecodeQueue(QObject* parent=nullptr, ThumbnailDecoder decoder={},
                         std::function<void()> drained={});
    ~DecodeQueue() override;
    Result<quint64> submit(quint64 scope, QByteArray bytes, QString mime, QSize box,
                          std::function<void(quint64, DecodeResult)> finished);
    bool cancel(quint64 ticket);
    bool cancel_scope(quint64 scope);
    bool shutdown();
    Result<DecodeSnapshot> snapshot() const;
private:
    struct Impl;
    std::unique_ptr<Impl> d_;
    bool on_owner_thread() const noexcept;
    void schedule();
    void pump();
    void decoded(quint64 ticket, Result<Thumbnail> result);
};
} // namespace valmax::media
