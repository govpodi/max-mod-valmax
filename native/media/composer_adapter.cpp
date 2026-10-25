#include "composer_adapter.h"
#include <QCoreApplication>
#include <QEvent>
#include <QMetaProperty>
#include <QQmlEngine>
#include <QThread>
#include <limits>

namespace valmax::media {
namespace {
bool gui_thread() {
    const auto app = QCoreApplication::instance();
    return app && app->thread() == QThread::currentThread();
}
quint64 nextPage = 1; // Accessed only on the GUI thread, never reused.
quint64 page_id() {
    if (!nextPage || nextPage == std::numeric_limits<quint64>::max()) return 0;
    return nextPage++;
}
class EngineComposerService final : public QObject {
    Q_OBJECT
public:
    explicit EngineComposerService(QQmlEngine* engine) : QObject(engine) {}
    bool configure(MediaServiceHost* service) {
        if (configured_) return service_ && service_ == service;
        service_ = service; configured_ = true;
        connect(service, &QObject::destroyed, this, [this] { emit readyChanged(); });
        emit readyChanged();
        return true;
    }
    MediaServiceHost* service() const { return service_.data(); }
signals:
    void readyChanged();
private:
    QPointer<MediaServiceHost> service_;
    bool configured_ = false;
};
EngineComposerService* holder(QQmlEngine* engine) {
    if (auto* existing = engine->findChild<EngineComposerService*>(QString(), Qt::FindDirectChildrenOnly)) return existing;
    return new EngineComposerService(engine);
}
}
bool configure_media_composer_engine(QQmlEngine* engine, MediaServiceHost* service) {
    if (!gui_thread() || !engine || !service || engine->thread() != QThread::currentThread()
        || service->thread() != QThread::currentThread()) return false;
    const auto state = service->snapshot();
    if (!state || state.value->stopping || !MediaEngineContext::install(engine)) return false;
    return holder(engine)->configure(service);
}
MediaComposerAdapter::MediaComposerAdapter(QObject* parent) : QObject(parent) {}
MediaComposerAdapter::~MediaComposerAdapter() {
    destroying_ = true;
    if (capability_) capability_->valid = false;
    for (const auto& connection : connections_) disconnect(connection);
    disconnect(composerDestroyed_);
    if (holder_) disconnect(holder_, nullptr, this, nullptr);
    const auto frontend = frontend_; frontend_.clear();
    if (frontend) { disconnect(frontend, nullptr, this, nullptr); delete frontend.data(); }
    QCoreApplication::removePostedEvents(this, QEvent::MetaCall);
}
bool MediaComposerAdapter::owner() const noexcept { return gui_thread() && thread() == QThread::currentThread(); }
void MediaComposerAdapter::componentComplete() {
    if (!owner() || complete_ || destroying_) return;
    complete_ = true; engine_ = qmlEngine(this);
    if (!engine_ || engine_->thread() != thread()) { emit changed(); return; }
    auto* configured = holder(engine_); holder_ = configured;
    connect(configured, &EngineComposerService::readyChanged, this, &MediaComposerAdapter::serviceReady);
    connect(configured, &QObject::destroyed, this, [this] { invalidateBinding(); });
    serviceReady();
}
void MediaComposerAdapter::serviceReady() {
    if (!owner() || destroying_ || !complete_ || !engine_) return;
    const auto* configured = qobject_cast<EngineComposerService*>(holder_.data());
    auto* service = configured ? configured->service() : nullptr;
    if (!service) { invalidateBinding(); return; }
    if (!frontend_) {
        auto* context = MediaEngineContext::install(engine_);
        if (!context) { invalidateBinding(); return; }
        frontend_ = new MediaComposerFrontend(context, service, this);
        connect(frontend_, &MediaComposerFrontend::changed, this, [this] { if (!destroying_) emit changed(); });
        connect(frontend_, &MediaComposerFrontend::draftDispatched, this, [this](const QString& caption) {
            QPointer<MediaComposerAdapter> alive(this);
            if (bindingCurrent() && alive) emit draftDispatched(caption);
        });
    }
    refreshBinding();
}
void MediaComposerAdapter::setComposer(QObject* value) {
    if (!owner() || destroying_ || (value && value->thread() != thread()) || composer_ == value) return;
    QPointer<MediaComposerAdapter> alive(this);
    disconnect(composerDestroyed_);
    composer_ = value;
    if (value) composerDestroyed_ = connect(value, &QObject::destroyed, this, [this] {
        QPointer<MediaComposerAdapter> alive(this);
        invalidateBinding();
        if (alive) emit composerChanged();
    });
    invalidateBinding();
    if (!alive) return;
    const auto revision = revision_;
    emit composerChanged();
    if (alive && revision_ == revision && complete_) refreshBinding();
}
bool MediaComposerAdapter::available() const {
    return owner() && !destroying_ && capability_ && capability_->valid && resolved_
        && frontend_ && frontend_->available();
}
QVariantList MediaComposerAdapter::rows() const { return available() ? frontend_->rows() : QVariantList{}; }
bool MediaComposerAdapter::busy() const { return available() && frontend_->busy(); }
QString MediaComposerAdapter::errorCode() const { return available() ? frontend_->errorCode() : QStringLiteral("unavailable"); }
int MediaComposerAdapter::retryAfterSeconds() const { return available() ? frontend_->retryAfterSeconds() : 0; }
void MediaComposerAdapter::invalidateBinding() {
    if (destroying_) return;
    if (capability_) capability_->valid = false;
    capability_.reset(); resolved_.reset();
    revision_ = !revision_ || revision_ == std::numeric_limits<quint64>::max() ? 0 : revision_ + 1;
    for (const auto& connection : connections_) disconnect(connection);
    connections_.clear();
    // Last operation: frontend notification may synchronously delete/rebind us.
    if (frontend_) frontend_->unbind();
    else emit changed();
}
bool MediaComposerAdapter::bindingCurrent() const {
    if (!available() || !engine_ || !composer_) return false;
    const auto capability = capability_; const auto expected = *resolved_;
    const auto actual = resolve_composer(expected.engine, expected.composer);
    return capability->valid && actual && same_composer(expected, *actual.value);
}
void MediaComposerAdapter::watch(const ResolvedComposer& binding) {
    const auto slot = metaObject()->method(metaObject()->indexOfSlot("hostPropertyChanged()"));
    const auto watchProperty = [&](QObject* object, const char* name) {
        const auto index = object->metaObject()->indexOfProperty(name);
        if (index < 0) return;
        const auto property = object->metaObject()->property(index);
        if (property.hasNotifySignal()) connections_.push_back(connect(object, property.notifySignal(), this, slot));
    };
    for (const auto& object : {binding.common, binding.selfInfo, binding.sender, binding.upload, binding.model})
        connections_.push_back(connect(object, &QObject::destroyed, this, [this] { invalidateBinding(); }));
    for (const auto* name : {"commonDependency", "messageSenderViewContext", "attachmentUploadModel", "visible", "enabled", "isCurrentChat"})
        watchProperty(binding.composer, name);
    watchProperty(binding.selfInfo, "id"); watchProperty(binding.sender, "chatId");
}
bool MediaComposerAdapter::refreshBinding() {
    if (!owner() || destroying_ || refreshing_ || !complete_ || !revision_ || !engine_ || !composer_ || !frontend_) return false;
    refreshing_ = true;
    QPointer<MediaComposerAdapter> alive(this);
    struct Finish { QPointer<MediaComposerAdapter> owner; ~Finish() { if (owner) owner->refreshing_ = false; } } finish{alive};
    const auto start = revision_;
    const auto actual = resolve_composer(engine_, composer_);
    if (!alive || revision_ != start) return false;
    if (!actual) { invalidateBinding(); return false; }
    if (capability_ && capability_->valid && resolved_ && same_composer(*resolved_, *actual.value) && frontend_->available()) return true;
    const auto expectedRevision = !revision_ || revision_ == std::numeric_limits<quint64>::max() ? 0 : revision_ + 1;
    invalidateBinding();
    if (!alive || revision_ != expectedRevision || !expectedRevision || !frontend_) return false;
    const auto page = page_id();
    if (!page) return false;
    resolved_ = *actual.value; capability_ = std::make_shared<Capability>();
    const auto expected = *resolved_; const auto capability = capability_;
    const auto current = [expected, capability] {
        if (!gui_thread() || !capability->valid || !expected.engine || !expected.composer) return false;
        const auto found = resolve_composer(expected.engine, expected.composer);
        return capability->valid && found && same_composer(expected, *found.value);
    };
    watch(expected);
    const bool bound = frontend_->bind({expected.accountId, page, expected.composer, expected.sender,
        expected.upload, expected.model, current});
    if (!alive || revision_ != expectedRevision) return false;
    if (!bound || !capability->valid) { invalidateBinding(); return false; }
    return true;
}
void MediaComposerAdapter::hostPropertyChanged() { reset(); }
void MediaComposerAdapter::reset() {
    if (!owner() || destroying_) return;
    QPointer<MediaComposerAdapter> alive(this);
    const auto expected = !revision_ || revision_ == std::numeric_limits<quint64>::max() ? 0 : revision_ + 1;
    invalidateBinding();
    if (alive && revision_ == expected) refreshBinding();
}
bool MediaComposerAdapter::search(const QString& mode, const QString& query) {
    if (!owner() || destroying_) return false;
    QPointer<MediaComposerAdapter> alive(this);
    if (!refreshBinding() || !alive || !frontend_) return false;
    return frontend_->search(mode, query);
}
bool MediaComposerAdapter::select(const QString& opaqueId) {
    if (!owner() || destroying_) return false;
    QPointer<MediaComposerAdapter> alive(this);
    const bool current = bindingCurrent();
    if (!alive) return false;
    if (!current) { invalidateBinding(); return false; }
    return frontend_->select(opaqueId);
}
} // namespace valmax::media
#include "composer_adapter.moc"
