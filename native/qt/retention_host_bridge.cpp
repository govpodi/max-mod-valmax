#include "retention_host_bridge.h"
#include "retention_passive_host.h"
#include "valmax_core.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <limits>
#include <string_view>

namespace valmax {
namespace {
namespace passive = retention::passive;
std::mutex hostSync;
constexpr uint64_t MaximumCount = 1'000'000'000;

bool lower_hex_revision(const QByteArray &bytes)
{
    if (bytes.size() != 64) return false;
    for (const char byte : bytes)
        if (!((byte >= '0' && byte <= '9') || (byte >= 'a' && byte <= 'f'))) return false;
    return true;
}

std::string_view view(const QByteArray &bytes)
{
    return {bytes.constData(), size_t(bytes.size())};
}

bool read_set_request(const QByteArray &input, bool &enabled, QByteArray &revision)
{
    // No need to parse large exports or media chunks on the retention path.
    if (input.isEmpty() || input.size() > 1024) return false;
    const QJsonDocument document = QJsonDocument::fromJson(input);
    if (!document.isObject()) return false;
    const QJsonObject object = document.object();
    if (object.size() != 3
        || object.value(QStringLiteral("operation")).toString() != QStringLiteral("retention.set")
        || !object.value(QStringLiteral("enabled")).isBool()) return false;
    revision = object.value(QStringLiteral("revision")).toString().toLatin1();
    if (!lower_hex_revision(revision)) return false;
    enabled = object.value(QStringLiteral("enabled")).toBool();
    return true;
}

bool read_set_reply(const QByteArray &output, bool expected, QByteArray &revision)
{
    if (output.isEmpty() || output.size() > 16384) return false;
    const QJsonDocument document = QJsonDocument::fromJson(output);
    if (!document.isObject()) return false;
    const QJsonObject envelope = document.object();
    if (envelope.value(QStringLiteral("schema")) != QJsonValue(1)
        || envelope.value(QStringLiteral("ok")) != QJsonValue(true)) return false;
    const QJsonObject result = envelope.value(QStringLiteral("result")).toObject();
    if (result.value(QStringLiteral("schema")) != QJsonValue(2)
        || !result.value(QStringLiteral("enabled")).isBool()
        || result.value(QStringLiteral("enabled")).toBool() != expected) return false;
    revision = result.value(QStringLiteral("revision")).toString().toLatin1();
    return lower_hex_revision(revision);
}

uint32_t native_error(const passive::NativeStatus &host)
{
    using passive::HostError;
    using retention::pilot::AsyncError;
    switch (host.error) {
    case HostError::None: break;
    case HostError::Store: return 5;
    case HostError::OwnerScope: return 2;
    case HostError::Shutdown: return 7;
    case HostError::Worker:
        return host.worker.error == AsyncError::None ? 1
            : static_cast<uint32_t>(host.worker.error);
    case HostError::NotInstalled:
    case HostError::InvalidConfig:
    case HostError::ImageMismatch:
    case HostError::HookFailed: return 1;
    }
    if (host.worker.error == AsyncError::Preflight && !host.worker.available
        && (!host.owner_join || !host.store_ready || !host.applied)) return 0;
    return static_cast<uint32_t>(host.worker.error);
}

uint64_t bounded(uint64_t count) { return std::min(count, MaximumCount); }
uint32_t bounded(uint32_t count) { return std::min(count, uint32_t(MaximumCount)); }
uint64_t bounded_sum(uint64_t first, uint64_t second)
{
    first = bounded(first);
    return first + std::min(second, MaximumCount - first);
}

} // namespace

RetentionHostBridge::~RetentionHostBridge()
{
    std::lock_guard lock(hostSync);
    // The process-wide hook remains pinned for other QML engines.
    (void)valmax_runtime_retention_ack(runtime_, nullptr);
}

bool RetentionHostBridge::profile_matches() const
{
    return passive::profile_matches(std::wstring_view(
        reinterpret_cast<const wchar_t *>(profile_.utf16()), size_t(profile_.size())));
}

bool RetentionHostBridge::read_saved(bool &enabled, QByteArray &revision) const
{
    constexpr char request[] = "{\"operation\":\"retention.get\"}";
    ValmaxCoreBuffer output{nullptr, 0, 0};
    const int code = valmax_runtime_dispatch(runtime_,
        reinterpret_cast<const uint8_t *>(request), sizeof(request) - 1, &output);
    QJsonDocument document;
    if (code == 0 && output.data && output.len <= 16384)
        document = QJsonDocument::fromJson(QByteArray(
            reinterpret_cast<const char *>(output.data), qsizetype(output.len)));
    valmax_core_buffer_free(&output);
    if (!document.isObject()) return false;
    const QJsonObject envelope = document.object();
    if (envelope.value(QStringLiteral("schema")) != QJsonValue(1)
        || envelope.value(QStringLiteral("ok")) != QJsonValue(true)) return false;
    const QJsonObject result = envelope.value(QStringLiteral("result")).toObject();
    if (result.value(QStringLiteral("schema")) != QJsonValue(2)
        || !result.value(QStringLiteral("enabled")).isBool()) return false;
    revision = result.value(QStringLiteral("revision")).toString().toLatin1();
    if (!lower_hex_revision(revision)) return false;
    enabled = result.value(QStringLiteral("enabled")).toBool();
    return true;
}

void RetentionHostBridge::publish() const
{
    if (!profile_matches()) {
        (void)valmax_runtime_retention_ack(runtime_, nullptr);
        return;
    }
    static_assert(sizeof(ValmaxRetentionNativeStatus) == 184);
    const passive::NativeStatus host = passive::status();
    ValmaxRetentionNativeStatus value{};
    value.size = sizeof(value); value.version = 1;
    value.error = native_error(host);
    value.installed = uint32_t(host.installed);
    value.images_exact = uint32_t(host.images_exact);
    value.listeners = host.listeners; value.gates = host.gates;
    value.owner_join = uint32_t(host.owner_join);
    value.store_ready = uint32_t(host.store_ready);
    value.worker_available = uint32_t(host.worker.available);
    value.worker_ready = uint32_t(host.worker.ready);
    value.preference_enabled = uint32_t(host.preference_enabled);
    value.applied = uint32_t(host.applied);
    value.generation = host.generation;
    std::memcpy(value.revision, host.revision.data(), sizeof(value.revision));
    value.captured = bounded(host.worker.captured);
    value.marked = bounded(host.worker.marked);
    value.missing = bounded(host.worker.missing);
    // Oversized borrowed-row callbacks are intentionally skipped without
    // revoking later capture. Include them in the count-only receipt so the
    // native status cannot silently report a loss-free run.
    value.missed = bounded_sum(host.worker.missed, host.bounded_skips);
    value.failures = bounded(host.worker.failures);
    value.queued = bounded(host.worker.queued);
    value.in_flight = bounded(host.worker.in_flight);
    value.pending = bounded(host.worker.pending);
    if (valmax_runtime_retention_ack(runtime_, &value) != 0)
        (void)valmax_runtime_retention_ack(runtime_, nullptr);
}

void RetentionHostBridge::initialize()
{
    std::lock_guard lock(hostSync);
    if (!profile_matches()) {
        (void)valmax_runtime_retention_ack(runtime_, nullptr);
        return;
    }
    if (!passive::preflight()) { publish(); return; }
    bool enabled = false;
    QByteArray revision;
    if (!read_saved(enabled, revision)) { publish(); return; }
    const passive::NativeStatus current = passive::status();
    if (!(current.applied && current.preference_enabled == enabled
        && std::equal(current.revision.begin(), current.revision.end(), revision.begin()))) {
        if (current.generation != std::numeric_limits<uint64_t>::max())
            (void)passive::set_enabled(enabled, view(revision), current.generation + 1);
    }
    publish();
}

void RetentionHostBridge::refresh()
{
    std::lock_guard lock(hostSync);
    publish();
}

QVariantMap RetentionHostBridge::diagnostics() const
{
    if (!profile_matches()) return {};
    const passive::NativeStatus host = passive::status();
    // Keep this separate from the Rust ACK and its readiness contract. It
    // contains counts only; no pointer, account, chat, message, path, or text.
    return {
        {QStringLiteral("schema"), 1},
        {QStringLiteral("listeners"), host.listeners},
        {QStringLiteral("activeOwners"), host.active_owners},
        {QStringLiteral("ownerConstructed"), qulonglong(host.owner_constructed)},
        {QStringLiteral("joinedUpserts"), qulonglong(host.joined_upserts)},
        {QStringLiteral("invalidHistory"), qulonglong(host.invalid_history_callbacks)},
        {QStringLiteral("noOwner"), qulonglong(host.no_owner_callbacks)},
        {QStringLiteral("invalidOwner"), qulonglong(host.invalid_owner_callbacks)},
        {QStringLiteral("unmatchedOwner"), qulonglong(host.unmatched_owner_callbacks)},
        {QStringLiteral("routeFailures"), qulonglong(host.route_failures)},
        {QStringLiteral("lastScopeFailureReason"),
            static_cast<quint32>(host.last_scope_failure_reason)},
        {QStringLiteral("boundedSkips"), qulonglong(host.bounded_skips)},
    };
}

RetentionHostBridge::Change RetentionHostBridge::before_dispatch(const QByteArray &request)
{
    Change change;
    QByteArray expected;
    if (!read_set_request(request, change.enabled, expected)) return change;
    change.lock = std::unique_lock<std::mutex>(hostSync);
    change.selected = true;
    if (!profile_matches()) {
        change.proceed = false;
        (void)valmax_runtime_retention_ack(runtime_, nullptr);
        return change;
    }
    if (!change.enabled) {
        const passive::NativeStatus current = passive::status();
        if (current.generation == std::numeric_limits<uint64_t>::max())
            change.proceed = false;
        else
            (void)passive::set_enabled(false, view(expected), current.generation + 1);
        publish();
        if (passive::status().capture_enabled()) change.proceed = false;
    }
    return change;
}

void RetentionHostBridge::after_dispatch(Change &change, const QByteArray &response)
{
    if (!change.selected) return;
    if (!profile_matches()) {
        (void)valmax_runtime_retention_ack(runtime_, nullptr);
        return;
    }
    QByteArray revision;
    if (change.proceed && read_set_reply(response, change.enabled, revision)) {
        const passive::NativeStatus current = passive::status();
        if (current.generation != std::numeric_limits<uint64_t>::max())
            (void)passive::set_enabled(change.enabled, view(revision), current.generation + 1);
    }
    publish();
}

} // namespace valmax
