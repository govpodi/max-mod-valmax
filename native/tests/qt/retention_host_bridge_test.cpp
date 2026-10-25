#include "retention_host_bridge.h"
#include "retention_passive_host.h"
#include "valmax_core.h"

#include <QByteArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QString>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

struct ValmaxRuntime { int synthetic = 1; };

namespace {
using valmax::retention::passive::NativeStatus;
NativeStatus host;
const std::wstring profile = L"C:\\synthetic\\retention-profile";
const QByteArray offRevision(64, 'a');
const QByteArray onRevision(64, 'b');
bool saved = false;
std::vector<ValmaxRetentionNativeStatus> acknowledgements;
int revocations = 0;
int preflights = 0;
int applied = 0;

QByteArray reply(bool enabled, bool okay = true)
{
    if (!okay) return QByteArrayLiteral("{\"schema\":1,\"ok\":false,\"error\":\"revision_conflict\"}");
    const QJsonObject result{{QStringLiteral("schema"), 2},
        {QStringLiteral("revision"), QString::fromLatin1(enabled ? onRevision : offRevision)},
        {QStringLiteral("enabled"), enabled}};
    return QJsonDocument(QJsonObject{{QStringLiteral("schema"), 1},
        {QStringLiteral("ok"), true}, {QStringLiteral("result"), result}})
        .toJson(QJsonDocument::Compact);
}

QByteArray request(bool enabled)
{
    return QJsonDocument(QJsonObject{{QStringLiteral("operation"), QStringLiteral("retention.set")},
        {QStringLiteral("revision"), QString::fromLatin1(enabled ? offRevision : onRevision)},
        {QStringLiteral("enabled"), enabled}}).toJson(QJsonDocument::Compact);
}

bool check(bool condition, const char *label)
{
    if (!condition) std::cerr << "retention host bridge fixture failed: " << label << '\n';
    return condition;
}
}

namespace valmax::retention::passive {
bool profile_matches(std::wstring_view selected) noexcept { return selected == profile; }
bool preflight() noexcept {
    ++preflights;
    host.store_ready = true;
    return true;
}
bool set_enabled(bool enabled, std::string_view revision, uint64_t generation) noexcept {
    if (revision.size() != 64 || generation <= host.generation) return false;
    ++applied;
    host.generation = generation;
    host.preference_enabled = enabled;
    host.applied = true;
    std::memcpy(host.revision.data(), revision.data(), 64);
    host.worker.available = true;
    host.worker.ready = enabled;
    host.worker.error = pilot::AsyncError::None;
    return true;
}
NativeStatus status() noexcept { return host; }
}

extern "C" int32_t valmax_runtime_dispatch(const ValmaxRuntime *runtime,
    const uint8_t *input, size_t input_len, ValmaxCoreBuffer *output)
{
    if (!runtime || !input || !output || QByteArray(reinterpret_cast<const char *>(input),
        qsizetype(input_len)) != QByteArrayLiteral("{\"operation\":\"retention.get\"}")) return 1;
    const QByteArray bytes = reply(saved);
    output->data = new uint8_t[size_t(bytes.size())];
    output->len = size_t(bytes.size()); output->capacity = output->len;
    std::memcpy(output->data, bytes.constData(), output->len);
    return 0;
}
extern "C" void valmax_core_buffer_free(ValmaxCoreBuffer *value)
{
    if (!value) return;
    delete[] value->data; *value = {nullptr, 0, 0};
}
extern "C" int32_t valmax_runtime_retention_ack(const ValmaxRuntime *runtime,
    const ValmaxRetentionNativeStatus *value)
{
    if (!runtime) return 1;
    if (!value) { ++revocations; return 0; }
    acknowledgements.push_back(*value);
    return 0;
}

int main()
{
    host.installed = true;
    host.images_exact = true;
    host.owner_join = true;
    host.listeners = 3;
    host.error = valmax::retention::passive::HostError::None;
    host.worker.error = valmax::retention::pilot::AsyncError::Preflight;
    ValmaxRuntime runtime;
    {
        valmax::RetentionHostBridge bridge(&runtime, QString::fromStdWString(profile));
        bridge.initialize();
        if (!check(preflights == 1 && applied == 1 && host.generation == 1,
            "initial saved-off preference applied")) return 1;
        if (!check(acknowledgements.back().worker_available == 1
            && acknowledgements.back().worker_ready == 0
            && acknowledgements.back().error == 0, "ready infrastructure is separate from capture")) return 1;
        auto enable = bridge.before_dispatch(request(true));
        if (!check(enable.selected && enable.proceed && applied == 1,
            "enable waits for Rust CAS")) return 1;
        saved = true;
        bridge.after_dispatch(enable, reply(true));
        enable.lock.unlock();
        if (!check(applied == 2 && host.generation == 2
            && acknowledgements.back().worker_ready == 1,
            "successful CAS enables capture with new generation")) return 1;
        auto disable = bridge.before_dispatch(request(false));
        if (!check(disable.selected && disable.proceed && applied == 3
            && !host.worker.ready && host.revision.front() == 'b'
            && host.revision.back() == 'b',
            "native capture stops before Rust disable CAS")) return 1;
        bridge.after_dispatch(disable, reply(false, false));
        disable.lock.unlock();
        if (!check(applied == 3 && !host.worker.ready && saved,
            "CAS conflict leaves capture stopped")) return 1;
        auto retry = bridge.before_dispatch(request(false));
        saved = false;
        bridge.after_dispatch(retry, reply(false));
        retry.lock.unlock();
        if (!check(applied == 5 && host.generation == 5
            && acknowledgements.back().worker_ready == 0
            && acknowledgements.back().revision[0] == uint8_t('a'),
            "successful disable binds saved revision")) return 1;
        host.bounded_skips = 2;
        host.worker.missed = 3;
        bridge.refresh();
        if (!check(acknowledgements.back().missed == 5,
            "bounded callback skips reach the native status receipt")) return 1;
        host.worker.missed = std::numeric_limits<uint64_t>::max();
        host.bounded_skips = 2;
        bridge.refresh();
        if (!check(acknowledgements.back().missed == 1'000'000'000,
            "worker counter overflow saturates before addition")) return 1;
        host.worker.missed = 2;
        host.bounded_skips = std::numeric_limits<uint64_t>::max();
        bridge.refresh();
        if (!check(acknowledgements.back().missed == 1'000'000'000,
            "callback counter overflow saturates before addition")) return 1;
        host.worker.error = valmax::retention::pilot::AsyncError::Storage;
        host.worker.ready = false;
        bridge.refresh();
        if (!check(acknowledgements.back().error == 5,
            "storage failure revokes readiness")) return 1;
    }
    const int before = applied;
    {
        ValmaxRuntime other;
        valmax::RetentionHostBridge wrong(&other,
            QStringLiteral("C:/synthetic/another-profile"));
        wrong.initialize();
        auto change = wrong.before_dispatch(request(true));
        if (!check(change.selected && !change.proceed && applied == before,
            "other profile cannot apply native preference")) return 1;
        wrong.after_dispatch(change, reply(true));
        change.lock.unlock();
        wrong.refresh();
        if (!check(applied == before && revocations >= 3,
            "other profile receives only ACK revocation")) return 1;
    }
    std::cout << "{\"ok\":true,\"checks\":1,\"retentionHostBridgeSyntheticLifecycle\":true,\"realProfilesTouched\":false}\n";
    return 0;
}
