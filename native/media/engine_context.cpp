#include "engine_context.h"
#include <QPointer>
#include <QQmlEngine>
#include <QRandomGenerator>
#include <QThread>
#include <atomic>
#include <limits>

namespace valmax::media {
struct EngineMediaLease::State {
    std::atomic<bool> valid{true};
    const qint64 account;
    const quint64 page, serial, epoch;
    State(qint64 a, quint64 p, quint64 s, quint64 e)
        : account(a), page(p), serial(s), epoch(e) {}
};

EngineMediaLease::EngineMediaLease(std::shared_ptr<State> state) : state_(std::move(state)) {}
bool EngineMediaLease::valid() const noexcept { return state_ && state_->valid.load(std::memory_order_acquire); }
qint64 EngineMediaLease::account_id() const noexcept { return state_ ? state_->account : 0; }
quint64 EngineMediaLease::page_id() const noexcept { return state_ ? state_->page : 0; }
quint64 EngineMediaLease::serial() const noexcept { return state_ ? state_->serial : 0; }

MediaEngineContext* MediaEngineContext::install(QQmlEngine* engine) {
    if (!engine || QThread::currentThread() != engine->thread()) return nullptr;
    const auto contexts = engine->findChildren<MediaEngineContext*>(QString(), Qt::FindDirectChildrenOnly);
    if (!contexts.isEmpty()) {
        auto context = contexts.first();
        // Do not replace a provider removed by some other native component.
        if (contexts.size() != 1 || context->store_->snapshot().closed) return nullptr;
        return context;
    }
    const auto name = QStringLiteral("valmaxmedia");
    if (engine->imageProvider(name)) return nullptr;
    quint32 random[4];
    QRandomGenerator::system()->fillRange(random);
    const auto nonce = QString::fromLatin1(QByteArray(reinterpret_cast<const char*>(random), sizeof random).toHex());
    auto store = std::make_shared<PreviewStore>(nonce);
    if (store->snapshot().closed) return nullptr;
    auto context = new MediaEngineContext(engine, store);
    engine->addImageProvider(name, new PreviewProvider(std::move(store)));
    return context;
}

MediaEngineContext::MediaEngineContext(QQmlEngine* engine, std::shared_ptr<PreviewStore> store)
    : QObject(engine), store_(std::move(store)) {}

MediaEngineContext::~MediaEngineContext() {
    revoke();
    store_->close();
}

bool MediaEngineContext::on_owner_thread() const noexcept { return QThread::currentThread() == thread(); }

void MediaEngineContext::revoke() noexcept {
    if (active_) active_->valid.store(false, std::memory_order_release);
    active_.reset();
}

EngineMediaLease MediaEngineContext::bind(qint64 accountId, quint64 pageId) {
    if (!on_owner_thread() || accountId <= 0 || !pageId) return {};
    return replace(accountId, pageId);
}

EngineMediaLease MediaEngineContext::replace(qint64 accountId, quint64 pageId) {
    revoke();
    const auto epoch = store_->advance();
    if (!epoch || !nextSerial_) {
        emit invalidated();
        return {};
    }
    const auto serial = nextSerial_;
    nextSerial_ = serial == std::numeric_limits<quint64>::max() ? 0 : serial + 1;
    active_ = std::make_shared<EngineMediaLease::State>(accountId, pageId, serial, epoch);
    EngineMediaLease lease(active_);
    QPointer<MediaEngineContext> alive(this);
    emit invalidated();
    // A synchronous adapter can close/rebind/delete the context in this signal.
    if (!alive || !alive->current(lease)) return {};
    return lease;
}

EngineMediaLease MediaEngineContext::advance(const EngineMediaLease& expected) {
    if (!current(expected)) return {};
    return replace(expected.account_id(), expected.page_id());
}

bool MediaEngineContext::detach(const EngineMediaLease& expected) {
    if (!current(expected)) return false;
    revoke();
    store_->advance();
    emit invalidated();
    return true;
}

bool MediaEngineContext::current(const EngineMediaLease& lease) const {
    if (!on_owner_thread() || !lease.valid() || lease.state_ != active_) return false;
    const auto store = store_->snapshot();
    return !store.closed && store.epoch == lease.state_->epoch;
}

Result<QString> MediaEngineContext::publish(const EngineMediaLease& lease, const QImage& image) {
    if (!current(lease)) return {std::nullopt, {Code::ProviderUnavailable, 0, std::nullopt}};
    return store_->publish(lease.state_->epoch, image);
}

PreviewStoreSnapshot MediaEngineContext::snapshot() const {
    if (!on_owner_thread()) return {0, 0, 0, 0, 0, true};
    return store_->snapshot();
}
} // namespace valmax::media
