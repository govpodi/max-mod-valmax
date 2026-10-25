#include "export_selection_host.h"
#include "valmax_core.h"

#include <QCoreApplication>
#include <QObject>
#include <QString>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>

namespace Ui { namespace History { namespace Chat {
class View final : public QObject {
    Q_OBJECT
public:
    using QObject::QObject;
};
}}}

namespace {
constexpr auto kJob = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
constexpr auto kToken = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
int checks = 0;
int binds = 0;
int invalidates = 0;
bool busyOnce = false;
uint64_t lastSource = 0;
const ValmaxRuntime *expectedRuntime = reinterpret_cast<const ValmaxRuntime *>(0x1230);

void check(bool condition)
{
    ++checks;
    if (!condition) { std::cerr << "export selection fixture failed at check " << checks << '\n'; std::exit(1); }
}
bool canonical(const ValmaxExportHistoryBinding *value)
{
    return value && value->size == sizeof(*value) && value->version == 1
        && value->source != 0 && std::memcmp(value->job_id, kJob, 32) == 0
        && std::memcmp(value->job_token, kToken, 64) == 0;
}
} // namespace

extern "C" int32_t valmax_runtime_export_history_bind(
    const ValmaxRuntime *runtime, const ValmaxExportHistoryBinding *value)
{
    if (runtime != expectedRuntime || !canonical(value)) return 1;
    ++binds;
    if (busyOnce) { busyOnce = false; return 3; }
    if (value->source <= lastSource) return 2;
    lastSource = value->source;
    return 0;
}

extern "C" int32_t valmax_runtime_export_history_invalidate(
    const ValmaxRuntime *runtime, const ValmaxExportHistoryBinding *value)
{
    if (runtime != expectedRuntime || !canonical(value) || value->source != lastSource) return 1;
    ++invalidates;
    return 0;
}

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    Q_UNUSED(app);
    const QString job = QString::fromLatin1(kJob);
    const QString token = QString::fromLatin1(kToken);
    valmax::ExportSelectionHost absent(nullptr);
    check(!absent.begin(job, token));
    valmax::ExportSelectionHost host(const_cast<ValmaxRuntime *>(expectedRuntime));
    check(!host.begin(QStringLiteral("A"), token));
    check(!host.begin(job, QStringLiteral("B")));
    check(host.begin(job, token));
    check(!host.begin(QStringLiteral("cccccccccccccccccccccccccccccccc"), token));
    check(host.begin(job, token));
    QObject plain;
    plain.setObjectName(QStringLiteral("valmax-export-") + job);
    check(!host.bind(&plain, job, token));
    auto view = std::make_unique<Ui::History::Chat::View>();
    check(std::strcmp(view->metaObject()->className(), "Ui::History::Chat::View") == 0);
    check(!host.bind(view.get(), job, token));
    view->setObjectName(QStringLiteral("valmax-export-") + job);
    check(!host.bind(view.get(), job, QStringLiteral("wrong")));
    busyOnce = true;
    check(!host.bind(view.get(), job, token));
    check(host.bind(view.get(), job, token));
    check(!host.bind(view.get(), job, token));
    host.release(job, QStringLiteral("wrong"));
    check(invalidates == 0);
    host.release(job, token);
    check(invalidates == 1);
    check(host.begin(job, token));
    auto second = std::make_unique<Ui::History::Chat::View>();
    second->setObjectName(QStringLiteral("valmax-export-") + job);
    check(host.bind(second.get(), job, token));
    view.reset();
    check(invalidates == 1);
    second.reset();
    check(invalidates == 2);
    check(host.begin(job, token));
    host.release(job, token);
    check(invalidates == 2);
    check(binds == 3);
    std::cout << "{\"ok\":true,\"checks\":" << checks
              << ",\"bindAttempts\":" << binds << ",\"invalidations\":"
              << invalidates << ",\"eofObserved\":false,\"realProfilesTouched\":false}\n";
    return 0;
}

#include "export_selection_host_test.moc"
