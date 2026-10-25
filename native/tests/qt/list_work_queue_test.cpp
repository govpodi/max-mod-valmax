#include "list_work_queue.h"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QQmlComponent>
#include <QQmlContext>
#include <QQmlEngine>
#include <QtQml/qqml.h>
#include <QTextStream>
#include <QVariant>
#include <functional>
#include <memory>

namespace {
unsigned checks = 0;
bool verify(bool value, const char *description)
{
    ++checks;
    if (!value) QTextStream(stderr) << description << '\n';
    return value;
}
#define CHECK(value) do { if (!verify(bool(value), #value)) return 1; } while (false)

class Row final : public QObject {
    Q_OBJECT
public:
    explicit Row(QString identity = QStringLiteral("row"), double position = 0)
    {
        setProperty("identityKey", identity);
        setProperty("valmaxRequestSerial", 1);
        setProperty("valmaxCompleted", true);
        setProperty("valmaxClosing", false);
        setProperty("loadEligible", true);
        setProperty("eligible", true);
        setProperty("valmaxLoadPermitted", false);
        setProperty("valmaxLoadFailed", false);
        setProperty("valmaxBodyReady", false);
        setProperty("y", position);
        setProperty("height", 40);
    }
    Q_INVOKABLE QVariant valmaxGrantLoad(QVariant identity, QVariant serial)
    {
        ++attempts;
        if (!accept || identity != property("identityKey") || serial != property("valmaxRequestSerial")) return false;
        setProperty("valmaxLoadPermitted", true);
        if (onGrant) onGrant();
        return true;
    }
    Q_INVOKABLE void valmaxCancel() { ++cancels; setProperty("valmaxLoadPermitted", false); }
    Q_INVOKABLE void valmaxRefresh() { ++refreshes; if (onRefresh) onRefresh(); }
    int attempts = 0;
    int cancels = 0;
    int refreshes = 0;
    bool accept = true;
    std::function<void()> onGrant;
    std::function<void()> onRefresh;
};

// A synchronous QML-bound presentation update. The fixed integer work is a
// reproducible synthetic GUI cost, not a claim about MAX text or image rendering.
class BindingAudit final : public QObject {
    Q_OBJECT
public:
    Q_INVOKABLE int record(int pending, int inflight, int grants, int peakInflight, int peakPending)
    {
        ++evaluations;
        unsigned value = unsigned(pending + inflight + grants + peakInflight + peakPending);
        for (unsigned i = 0; i < 512; ++i) value = value * 1664525U + i + 1013904223U;
        checksum ^= value;
        return pending + inflight + grants + peakInflight + peakPending;
    }
    unsigned evaluations = 0;
    unsigned checksum = 0;
};

void configureView(QObject &view, valmax::ListWorkQueue &queue, bool active = true)
{
    view.setProperty("contentY", 0);
    view.setProperty("height", 240);
    queue.setView(&view);
    queue.setProperty("viewActive", active);
    queue.setProperty("admissionBudgetMs", 1000);
}

QJsonObject runBenchmark(QQmlEngine &engine)
{
    valmax::ListWorkQueue queue;
    QObject view;
    configureView(view, queue, false);
    Row row;
    BindingAudit audit;
    auto context = std::make_unique<QQmlContext>(engine.rootContext());
    context->setContextProperty(QStringLiteral("queue"), &queue);
    context->setContextProperty(QStringLiteral("audit"), &audit);
    QQmlComponent component(&engine);
    component.setData("import QueueTest 1.0\nRecord { property int presented: audit.record(queue.pendingCount, queue.inflightCount, queue.grants, queue.peakInflight, queue.peakPending) }", QUrl());
    std::unique_ptr<QObject> presentation(component.create(context.get()));
    if (!presentation) return {{QStringLiteral("error"), component.errorString()}};
    unsigned notifications = 0;
    QObject::connect(&queue, &valmax::ListWorkQueue::stateChanged, &queue, [&] { ++notifications; });
    queue.request(&row);
    const auto originalEvaluations = audit.evaluations;
    const auto originalNotifications = notifications;
    constexpr int iterations = 20000;
    QElapsedTimer timer;
    timer.start();
    for (int i = 0; i < iterations; ++i) {
        queue.request(&row, (i % 2) == 0); // Existing delegate updates priority.
        queue.cancel(nullptr);          // Nothing completed or destroyed.
    }
    const auto elapsed = timer.nsecsElapsed();
    const bool unchanged = queue.pendingCount() == 1 && queue.inflightCount() == 0 && queue.grants() == 0;
    const QJsonObject result{
        {QStringLiteral("iterations"), iterations},
        {QStringLiteral("queueCalls"), iterations * 2},
        {QStringLiteral("unchangedPublicCounters"), unchanged},
        {QStringLiteral("stateNotifications"), int(notifications - originalNotifications)},
        {QStringLiteral("qmlBindingEvaluations"), int(audit.evaluations - originalEvaluations)},
        {QStringLiteral("guiThreadNanoseconds"), double(elapsed)},
        {QStringLiteral("checksum"), double(audit.checksum)},
        {QStringLiteral("presented"), presentation->property("presented").toInt()}
    };
    return result;
}
}

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    const bool baseline = app.arguments().contains(QStringLiteral("--baseline"));
    {
        valmax::ListWorkQueue queue;
        QObject view;
        configureView(view, queue);
        unsigned notifications = 0;
        QObject::connect(&queue, &valmax::ListWorkQueue::stateChanged, &queue, [&] { ++notifications; });
        queue.cancel(nullptr);
        queue.clear();
        queue.pump();
        if (!baseline) CHECK(notifications == 0);
        Row row;
        queue.request(&row);
        CHECK(queue.pendingCount() == 1);
        CHECK(queue.peakPending() == 1);
        const auto first = notifications;
        for (int i = 0; i < 50; ++i) queue.request(&row, true);
        if (!baseline) CHECK(notifications == first);
        queue.pump();
        CHECK(row.attempts == 1);
        CHECK(queue.pendingCount() == 0);
        CHECK(queue.inflightCount() == 1);
        CHECK(queue.grants() == 1);
        CHECK(queue.peakInflight() == 1);
        const auto loading = notifications;
        for (int i = 0; i < 50; ++i) queue.pump();
        if (!baseline) CHECK(notifications == loading);
        row.setProperty("valmaxBodyReady", true);
        queue.pump();
        CHECK(queue.inflightCount() == 0);
        const auto completed = notifications;
        queue.complete(&row);
        if (!baseline) CHECK(notifications == completed);
    }
    {
        // A rejected grant must still settle its pending/inflight bindings.
        valmax::ListWorkQueue queue;
        QObject view;
        configureView(view, queue);
        Row row;
        row.accept = false;
        queue.request(&row);
        queue.pump();
        CHECK(row.attempts == 1);
        CHECK(queue.pendingCount() == 0);
        CHECK(queue.inflightCount() == 0);
        CHECK(queue.grants() == 0);
    }
    {
        // The same signal can synchronously enqueue another row. Publishing the
        // snapshot before emitting prevents stale bookkeeping after that call.
        valmax::ListWorkQueue queue;
        QObject view;
        configureView(view, queue);
        Row first(QStringLiteral("first"));
        Row second(QStringLiteral("second"), 40);
        int callbacks = 0;
        QObject::connect(&queue, &valmax::ListWorkQueue::stateChanged, &queue, [&] {
            ++callbacks;
            if (callbacks == 1) queue.request(&second);
        });
        queue.request(&first);
        CHECK(queue.pendingCount() == 2);
        CHECK(callbacks == 2);
        queue.pump();
        CHECK(first.attempts == 1 && second.attempts == 1);
        CHECK(queue.grants() == 2);
        CHECK(queue.inflightCount() == 2);
        const int before = callbacks;
        queue.pump();
        if (!baseline) CHECK(callbacks == before);
    }
    {
        valmax::ListWorkQueue queue;
        QObject view;
        configureView(view, queue);
        Row row;
        row.onGrant = [&] { queue.complete(&row); };
        queue.request(&row);
        queue.pump();
        CHECK(queue.grants() == 1 && queue.inflightCount() == 0 && queue.pendingCount() == 0);
    }
    {
        valmax::ListWorkQueue queue;
        QObject view;
        configureView(view, queue);
        Row row;
        row.onRefresh = [&] { queue.request(&row); };
        queue.request(&row);
        queue.pump();
        queue.clear(true);
        CHECK(row.cancels == 1 && row.refreshes == 1);
        CHECK(queue.pendingCount() == 1 && queue.inflightCount() == 0);
        queue.pump();
        CHECK(row.attempts == 2 && queue.grants() == 2);
    }
    {
        valmax::ListWorkQueue queue;
        QObject view;
        configureView(view, queue);
        auto row = std::make_unique<Row>();
        queue.request(row.get());
        row.reset();
        queue.pump();
        CHECK(queue.pendingCount() == 0 && queue.inflightCount() == 0 && queue.grants() == 0);
        Row reused;
        queue.request(&reused);
        reused.setProperty("valmaxRequestSerial", 2);
        queue.pump();
        CHECK(reused.attempts == 0 && queue.pendingCount() == 0);
        queue.request(&reused);
        queue.pump();
        CHECK(reused.attempts == 1 && queue.grants() == 1);
    }
    {
        valmax::ListWorkQueue queue;
        QObject view;
        configureView(view, queue, false);
        Row row;
        queue.request(&row);
        queue.pump();
        CHECK(row.attempts == 0 && queue.pendingCount() == 1);
        queue.setProperty("viewActive", true);
        view.setProperty("contentY", 600);
        queue.observeMotion();
        queue.pump();
        CHECK(row.attempts == 0);
        queue.request(&row, true); // Priority update is effective without count notification.
        queue.pump();
        CHECK(row.attempts == 1 && queue.grants() == 1);
    }
    qmlRegisterType<QObject>("QueueTest", 1, 0, "Record");
    QQmlEngine engine;
    QJsonArray benchmarks;
    for (int i = 0; i < 5; ++i) {
        const auto result = runBenchmark(engine);
        CHECK(!result.contains(QStringLiteral("error")));
        CHECK(result.value(QStringLiteral("unchangedPublicCounters")).toBool());
        CHECK(result.value(QStringLiteral("presented")).toInt() == 2);
        const int evaluations = result.value(QStringLiteral("qmlBindingEvaluations")).toInt();
        const int notifications = result.value(QStringLiteral("stateNotifications")).toInt();
        if (baseline) CHECK(evaluations >= 40000 && notifications == 40000);
        else CHECK(evaluations == 0 && notifications == 0);
        benchmarks.append(result);
    }
    QTextStream(stdout) << QJsonDocument(QJsonObject{
        {QStringLiteral("schema"), 1}, {QStringLiteral("ok"), true},
        {QStringLiteral("checks"), int(checks)}, {QStringLiteral("baseline"), baseline},
        {QStringLiteral("benchmarks"), benchmarks},
        {QStringLiteral("realClientStarted"), false}, {QStringLiteral("networkUsed"), false}
    }).toJson(QJsonDocument::Compact) << '\n';
    return 0;
}

#include "list_work_queue_test.moc"
