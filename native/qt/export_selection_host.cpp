#include "export_selection_host.h"
#include "valmax_core.h"

#include <QByteArray>
#include <QObject>
#include <QPointer>
#include <QString>
#include <QThread>
#include <atomic>
#include <cstring>

namespace valmax {
namespace {
std::atomic<uint64_t> nextSource{0};

bool canonicalHex(const QByteArray &text, int length)
{
    if (text.size() != length) return false;
    for (const char ch : text) {
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) return false;
    }
    return true;
}

void wipe(ValmaxExportHistoryBinding &binding)
{
    volatile auto *bytes = reinterpret_cast<volatile unsigned char *>(&binding);
    for (size_t index = 0; index < sizeof(binding); ++index) bytes[index] = 0;
}
} // namespace

struct ExportSelectionHost::Impl {
    explicit Impl(ValmaxRuntime *value) : runtime(value) {}
    ~Impl() { clear(); }

    ValmaxRuntime *runtime = nullptr;
    QObject lifetime;
    QPointer<QObject> view;
    ValmaxExportHistoryBinding binding{};
    QByteArray job;
    QByteArray token;
    bool bound = false;

    bool onThread() const { return lifetime.thread() == QThread::currentThread(); }
    bool authorized(const QString &id, const QString &secret) const
    {
        return !job.isEmpty() && id.toLatin1() == job && secret.toLatin1() == token;
    }
    void clear()
    {
        if (bound && runtime) (void)valmax_runtime_export_history_invalidate(runtime, &binding);
        bound = false;
        view = nullptr;
        wipe(binding);
        job.fill(0);
        token.fill(0);
        job.clear();
        token.clear();
    }
    bool begin(const QString &id, const QString &secret)
    {
        if (!onThread() || !runtime) return false;
        auto nextJob = id.toLatin1();
        auto nextToken = secret.toLatin1();
        const bool valid = canonicalHex(nextJob, 32) && canonicalHex(nextToken, 64);
        if (!valid) { nextToken.fill(0); return false; }
        if (!job.isEmpty()) {
            const bool same = nextJob == job && nextToken == token && !bound;
            nextToken.fill(0);
            return same;
        }
        job = nextJob;
        token = nextToken;
        nextToken.fill(0);
        return true;
    }
    bool bind(QObject *selected, const QString &id, const QString &secret)
    {
        if (!onThread() || !runtime || !selected || selected->thread() != lifetime.thread()
                || bound || !authorized(id, secret)) return false;
        const char *type = selected->metaObject()->className();
        if (!type || std::strcmp(type, "Ui::History::Chat::View") != 0
                || selected->objectName() != QStringLiteral("valmax-export-") + id) return false;
        const auto source = nextSource.fetch_add(1, std::memory_order_relaxed) + 1;
        if (source == 0) return false;
        ValmaxExportHistoryBinding candidate{};
        candidate.size = sizeof(candidate);
        candidate.version = 1;
        candidate.source = source;
        std::memcpy(candidate.job_id, job.constData(), sizeof(candidate.job_id));
        std::memcpy(candidate.job_token, token.constData(), sizeof(candidate.job_token));
        const auto result = valmax_runtime_export_history_bind(runtime, &candidate);
        if (result != 0) { wipe(candidate); return false; }
        binding = candidate;
        wipe(candidate);
        bound = true;
        view = selected;
        QObject::connect(selected, &QObject::destroyed, &lifetime, [this, source] {
            // A released View can outlive its job. Its late destruction must
            // never invalidate a newer binding on the same Runtime.
            if (bound && binding.source == source) clear();
        });
        return true;
    }
    void release(const QString &id, const QString &secret)
    {
        if (onThread() && authorized(id, secret)) clear();
    }
};

ExportSelectionHost::ExportSelectionHost(ValmaxRuntime *runtime)
    : impl_(std::make_unique<Impl>(runtime)) {}
ExportSelectionHost::~ExportSelectionHost() = default;
bool ExportSelectionHost::begin(const QString &id, const QString &token)
{
    return impl_->begin(id, token);
}
bool ExportSelectionHost::bind(QObject *view, const QString &id, const QString &token)
{
    return impl_->bind(view, id, token);
}
void ExportSelectionHost::release(const QString &id, const QString &token)
{
    impl_->release(id, token);
}
} // namespace valmax
