#include "worker.h"
#include <QVariantList>

namespace valmax::transfer::app {
void Control::cancel() noexcept {
    cancelled.store(true, std::memory_order_release);
    const std::lock_guard lock(mutex);
    if (host) host->request_cancel();
}
Worker::Worker(std::shared_ptr<Backend> backend, QString local)
    : backend_(std::move(backend)), local_(std::move(local)) {}
void Worker::reset() {
    secret_.reset(); file_.clear(); fingerprint_.clear(); generation_ = 0;
}
void Worker::inspect(quint64 generation, QUrl file, std::shared_ptr<Secret> secret,
    std::shared_ptr<Control> control) {
    reset();
    QVariantMap result{{QStringLiteral("operation"), QStringLiteral("preview")}};
    try {
        PreviewReply reply;
        if (!control->cancelled.load(std::memory_order_acquire)) reply = backend_->inspect(file, secret->bytes);
        if (control->cancelled.load(std::memory_order_acquire)) reply = {false, QStringLiteral("cancelled"), {}, {}};
        if (reply.ok) {
            QVariantList rows;
            for (const auto& account : reply.accounts) rows.push_back(QVariantMap{
                {QStringLiteral("accountId"), account.id}, {QStringLiteral("displayName"), account.name},
                {QStringLiteral("shortName"), account.shortName}});
            generation_ = generation; file_ = std::move(file); fingerprint_ = std::move(reply.fingerprint);
            secret_ = std::move(secret);
            result.insert(QStringLiteral("ok"), true); result.insert(QStringLiteral("accounts"), rows);
        } else result.insert(QStringLiteral("error"), reply.error);
    } catch (...) { reset(); result.insert(QStringLiteral("error"), QStringLiteral("internal_error")); }
    emit completed(generation, std::move(result));
}
void Worker::importLogin(quint64 generation, std::shared_ptr<Control> control) {
    QVariantMap result{{QStringLiteral("operation"), QStringLiteral("import")}};
    try {
        if (generation != generation_ || !secret_) result.insert(QStringLiteral("error"), QStringLiteral("preview_required"));
        else if (control->cancelled.load(std::memory_order_acquire)) result.insert(QStringLiteral("error"), QStringLiteral("cancelled"));
        else {
            // Construct and destroy on this importing worker. The shared control
            // mutex serializes cancellation with unbinding before destruction.
            NativeTransferHost host;
            struct Binding {
                Control& control;
                Binding(Control& c, NativeTransferHost& host) : control(c) {
                    const std::lock_guard lock(control.mutex);
                    control.host = &host;
                    if (control.cancelled.load(std::memory_order_acquire)) host.request_cancel();
                }
                ~Binding() { const std::lock_guard lock(control.mutex); control.host = nullptr; }
            } binding(*control, host);
            const auto reply = backend_->importLogin(local_, file_, secret_->bytes, fingerprint_, host);
            // A cancellation arriving after Rust starts apply must not overwrite
            // the actual commit/rollback outcome with a synthetic cancellation.
            if (reply.ok) {
                result.insert(QStringLiteral("ok"), true); result.insert(QStringLiteral("result"), reply.result);
                result.insert(QStringLiteral("profiles"), reply.profiles);
            } else result.insert(QStringLiteral("error"), reply.error);
        }
    } catch (...) { result.insert(QStringLiteral("error"), QStringLiteral("internal_error")); }
    reset(); // Success and every failure require a fresh preview for another try.
    emit completed(generation, std::move(result));
}
void Worker::recoveryStatus(quint64 generation, std::shared_ptr<Control> control) {
    reset();
    QVariantMap result{{QStringLiteral("operation"), QStringLiteral("recovery_status")}};
    try {
        RecoveryStatusReply reply;
        if (!control->cancelled.load(std::memory_order_acquire)) reply = backend_->recoveryStatus(local_);
        if (control->cancelled.load(std::memory_order_acquire)) reply = {false, false, QStringLiteral("cancelled"), {}};
        if (reply.ok) {
            if (reply.required) { generation_ = generation; fingerprint_ = std::move(reply.fingerprint); }
            result.insert(QStringLiteral("ok"), true);
            result.insert(QStringLiteral("required"), reply.required);
        } else result.insert(QStringLiteral("error"), reply.error);
    } catch (...) { reset(); result.insert(QStringLiteral("error"), QStringLiteral("internal_error")); }
    emit completed(generation, std::move(result));
}
void Worker::recover(quint64 generation, std::shared_ptr<Secret> secret, std::shared_ptr<Control> control) {
    QVariantMap result{{QStringLiteral("operation"), QStringLiteral("recover")}};
    try {
        if (generation != generation_ || fingerprint_.isEmpty()) result.insert(QStringLiteral("error"), QStringLiteral("preview_required"));
        else if (control->cancelled.load(std::memory_order_acquire)) result.insert(QStringLiteral("error"), QStringLiteral("cancelled"));
        else {
            NativeTransferHost host;
            struct Binding {
                Control& control;
                Binding(Control& c, NativeTransferHost& host) : control(c) {
                    const std::lock_guard lock(control.mutex); control.host = &host;
                    if (control.cancelled.load(std::memory_order_acquire)) host.request_cancel();
                }
                ~Binding() { const std::lock_guard lock(control.mutex); control.host = nullptr; }
            } binding(*control, host);
            const auto reply = backend_->recover(local_, secret->bytes, fingerprint_, host);
            if (reply.ok) {
                result.insert(QStringLiteral("ok"), true);
                result.insert(QStringLiteral("files"), reply.files);
            } else result.insert(QStringLiteral("error"), reply.error);
        }
    } catch (...) { result.insert(QStringLiteral("error"), QStringLiteral("internal_error")); }
    reset(); secret.reset(); // A retry needs a fresh marker check and confirmation.
    emit completed(generation, std::move(result));
}
}
