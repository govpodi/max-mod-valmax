#include "preview_provider.h"
#include "thumbnail.h"
#include <QColorSpace>
#include <atomic>
#include <cstring>
#include <limits>
#include <list>
#include <mutex>

namespace valmax::media {
namespace {
bool hex_string(QStringView text, qsizetype length) {
    if (text.size() != length) return false;
    for (QChar c : text) if (!((c >= u'0' && c <= u'9') || (c >= u'a' && c <= u'f'))) return false;
    return true;
}
QString serial(quint64 value) { return QString::number(value, 16).rightJustified(16, u'0'); }
struct Generation { std::atomic<bool> valid{true}; };
struct Counts { std::atomic<int> responses{0}, images{0}; };
struct ImageSlotGuard {
    std::shared_ptr<Counts> counts;
    bool held = false;
    ~ImageSlotGuard() { if (held) counts->images.fetch_sub(1); }
};
class Response final : public QQuickImageResponse {
public:
    Response(QImage image, std::shared_ptr<Generation> generation, std::shared_ptr<Counts> counts,
             QString error, bool ownsImageSlot)
        : image_(std::move(image)), generation_(std::move(generation)), counts_(std::move(counts)),
          error_(std::move(error)), ownsImageSlot_(ownsImageSlot) {
        counts_->responses.fetch_add(1);
    }
    ~Response() override {
        if (ownsImageSlot_) counts_->images.fetch_sub(1);
        counts_->responses.fetch_sub(1);
    }
    void complete() { emit finished(); } // Last action; Qt owns cleanup.
    void cancel() override { cancelled_.store(true); }
    QString errorString() const override {
        if (cancelled_.load()) return QStringLiteral("cancelled");
        if (generation_ && !generation_->valid.load()) return QStringLiteral("stale_preview");
        return error_;
    }
    QQuickTextureFactory* textureFactory() const override {
        if (!errorString().isEmpty() || image_.isNull()) return nullptr;
        return QQuickTextureFactory::textureFactoryForImage(image_);
    }
private:
    const QImage image_;
    const std::shared_ptr<Generation> generation_;
    const std::shared_ptr<Counts> counts_;
    const QString error_;
    const bool ownsImageSlot_;
    std::atomic<bool> cancelled_{false};
};
Result<QString> failure(Code code = Code::ProviderUnavailable, int status = 503) {
    return {std::nullopt, {code, status, std::nullopt}};
}
}

struct PreviewStore::State {
    struct Entry { QString key; QImage image; qsizetype bytes; };
    mutable std::mutex mutex;
    QString nonce;
    std::list<Entry> entries;
    qsizetype bytes = 0;
    quint64 epoch = 1, nextSerial = 1;
    bool closed = false;
    std::shared_ptr<Generation> generation = std::make_shared<Generation>();
    std::shared_ptr<Counts> counts = std::make_shared<Counts>();
};
PreviewStore::PreviewStore(QString nonce) : state_(std::make_shared<State>()) {
    state_->nonce = std::move(nonce);
    if (!hex_string(state_->nonce, 32)) { state_->closed = true; state_->generation->valid.store(false); }
}
PreviewStore::~PreviewStore() { close(); }
Result<QString> PreviewStore::publish(quint64 expectedEpoch, const QImage& ready) {
    if (ready.isNull() || ready.format() != QImage::Format_ARGB32_Premultiplied
        || ready.colorSpace() != QColorSpace(QColorSpace::SRgb)
        || ready.width() < 1 || ready.height() < 1 || ready.width() > MaxThumbnailDimension
        || ready.height() > MaxThumbnailDimension) return failure(Code::InvalidInput, 400);
    // Own the pixels even if a C++ caller supplied an externally backed image.
    // Fresh storage also removes metadata/DPR and excessive source row stride.
    QImage owned(ready.size(), QImage::Format_ARGB32_Premultiplied);
    if (owned.isNull()) return failure();
    owned.setColorSpace(QColorSpace::SRgb);
    for (int row = 0; row < owned.height(); ++row)
        std::memcpy(owned.scanLine(row), ready.constScanLine(row), size_t(owned.width()) * 4);
    std::lock_guard lock(state_->mutex);
    if (state_->closed || expectedEpoch != state_->epoch || state_->nextSerial == 0) return failure();
    const QString key = state_->nonce + u'/' + serial(state_->epoch) + u'/' + serial(state_->nextSerial++);
    const auto bytes = owned.sizeInBytes();
    while (!state_->entries.empty() && (state_->entries.size() >= MaxEntries || state_->bytes + bytes > MaxResidentBytes)) {
        state_->bytes -= state_->entries.front().bytes;
        state_->entries.pop_front();
    }
    state_->entries.push_back({key, std::move(owned), bytes});
    state_->bytes += bytes;
    return {QStringLiteral("image://valmaxmedia/") + key, {}};
}
quint64 PreviewStore::advance() {
    std::lock_guard lock(state_->mutex);
    if (state_->closed) return 0;
    state_->generation->valid.store(false);
    state_->entries.clear(); state_->bytes = 0;
    if (state_->epoch == std::numeric_limits<quint64>::max()) { state_->closed = true; return 0; }
    state_->generation = std::make_shared<Generation>();
    return ++state_->epoch;
}
void PreviewStore::close() {
    std::lock_guard lock(state_->mutex);
    state_->closed = true; state_->generation->valid.store(false);
    state_->entries.clear(); state_->bytes = 0;
}
PreviewStoreSnapshot PreviewStore::snapshot() const {
    std::lock_guard lock(state_->mutex);
    return {int(state_->entries.size()), state_->bytes, state_->counts->images.load(),
            state_->counts->responses.load(), state_->epoch, state_->closed};
}
PreviewProvider::PreviewProvider(std::shared_ptr<PreviewStore> store) : store_(std::move(store)) {}
PreviewProvider::~PreviewProvider() { if (store_) store_->close(); }
QQuickImageResponse* PreviewProvider::requestImageResponse(const QString& id, const QSize& wanted) {
    QImage image;
    std::shared_ptr<Generation> generation;
    std::shared_ptr<Counts> counts = store_ ? store_->state_->counts : std::make_shared<Counts>();
    QString error = QStringLiteral("preview_unavailable");
    ImageSlotGuard imageSlot{counts};
    const bool validId = id.size() == 66 && id[32] == u'/' && id[49] == u'/'
        && hex_string(QStringView(id).first(32), 32)
        && hex_string(QStringView(id).sliced(33, 16), 16) && hex_string(QStringView(id).sliced(50), 16);
    if (store_ && validId) {
        auto& state = *store_->state_;
        std::lock_guard lock(state.mutex);
        if (!state.closed) {
            for (auto entry = state.entries.begin(); entry != state.entries.end(); ++entry) {
                if (entry->key != id) continue;
                if (counts->images.load() >= PreviewStore::MaxImageResponses) { error = QStringLiteral("preview_busy"); break; }
                counts->images.fetch_add(1); imageSlot.held = true;
                image = entry->image; generation = state.generation;
                state.entries.splice(state.entries.end(), state.entries, entry);
                error.clear(); break;
            }
        }
    }
    if (!image.isNull()) {
        QSize box = wanted;
        if (box.width() < 1 || box.height() < 1) box = QSize(240, 180);
        box.setWidth(qMin(box.width(), MaxThumbnailDimension));
        box.setHeight(qMin(box.height(), MaxThumbnailDimension));
        if (image.width() > box.width() || image.height() > box.height()) {
            auto size = image.size().scaled(box, Qt::KeepAspectRatio);
            size.setWidth(qMax(1, size.width())); size.setHeight(qMax(1, size.height()));
            image = image.scaled(size, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
        }
        if (image.isNull()) error = QStringLiteral("preview_unavailable");
    }
    auto* response = new Response(std::move(image), std::move(generation), std::move(counts), std::move(error), imageSlot.held);
    imageSlot.held = false;
    // Qt 6.11.2 QQuickPixmapReader explicitly checks its atomic finished flag
    // after hookup, so even this immediate memory-only result cannot be lost.
    response->complete();
    return response;
}
} // namespace valmax::media
