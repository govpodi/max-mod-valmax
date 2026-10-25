#include "history_model_cursor.h"

#include <QCoreApplication>
#include <QElapsedTimer>
#include <QJsonDocument>
#include <QJsonObject>
#include <QQmlComponent>
#include <QQmlContext>
#include <QQmlEngine>
#include <QTextStream>
#include <QThread>
#include <qqml.h>
#include <algorithm>
#include <functional>
#include <memory>
#include <thread>

class Context final : public QObject {
    Q_OBJECT
    Q_PROPERTY(int identity READ identity CONSTANT)
public:
    explicit Context(int value) : value_(value) {}
    int identity() const { return value_; }
private:
    int value_;
};

class Wrapper {
    Q_GADGET
public:
    Wrapper() = default;
    explicit Wrapper(QObject *object) : object_(object) {}
    Q_INVOKABLE QObject *get() const { return object_; }
private:
    QObject *object_ = nullptr;
};
Q_DECLARE_METATYPE(Wrapper)

class Model final : public QAbstractListModel {
    Q_OBJECT
public:
    static constexpr int ContextRole = Qt::UserRole + 9;
    explicit Model(int count = 0) { for (int i = 0; i < count; ++i) values.append(i); }
    int rowCount(const QModelIndex &parent = QModelIndex()) const override { return parent.isValid() ? 0 : int(values.size()); }
    QHash<int, QByteArray> roleNames() const override { return {{ContextRole, "messageContext"}, {ContextRole + 1, "other"}}; }
    QVariant data(const QModelIndex &index, int role) const override
    {
        if (!index.isValid() || role != ContextRole || index.row() < 0 || index.row() >= values.size()) return {};
        ++reads;
        const auto result = values[index.row()];
        if (onRead) { auto callback = std::move(onRead); callback(); }
        return result;
    }
    void insertAt(int position, const QVariantList &items)
    {
        beginInsertRows({}, position, position + int(items.size()) - 1);
        for (int i = 0; i < items.size(); ++i) values.insert(position + i, items.at(i));
        endInsertRows();
    }
    void removeAt(int position, int count)
    {
        beginRemoveRows({}, position, position + count - 1);
        values.remove(position, count);
        endRemoveRows();
    }
    void update(int first, int last, QList<int> roles = {ContextRole}) { emit dataChanged(index(first), index(last), roles); }
    void replace(const QVariantList &items) { beginResetModel(); values = items; endResetModel(); }
    void reverseLayout()
    {
        emit layoutAboutToBeChanged();
        const auto old = persistentIndexList();
        QModelIndexList replacement;
        replacement.reserve(old.size());
        for (const auto &index : old) replacement.append(this->index(rowCount() - 1 - index.row()));
        std::reverse(values.begin(), values.end());
        changePersistentIndexList(old, replacement);
        emit layoutChanged();
    }
    void firstToLast()
    {
        beginMoveRows({}, 0, 0, {}, rowCount());
        values.append(values.takeFirst());
        endMoveRows();
    }
    QVariantList values;
    mutable quint64 reads = 0;
    mutable std::function<void()> onRead;
};

class LargeModel final : public QAbstractListModel {
public:
    explicit LargeModel(int count) : count_(count) {}
    int rowCount(const QModelIndex &parent = QModelIndex()) const override { return parent.isValid() ? 0 : count_; }
    QHash<int, QByteArray> roleNames() const override { return {{Model::ContextRole, "messageContext"}}; }
    QVariant data(const QModelIndex &, int) const override { ++reads; return 0; }
    int count_;
    mutable int reads = 0;
};

namespace {
unsigned checks = 0;
bool verify(bool value, const char *description)
{
    ++checks;
    if (!value) QTextStream(stderr) << description << '\n';
    return value;
}
#define CHECK(value) do { if (!verify(bool(value), #value)) return 1; } while (false)

QString ticket(const QVariantMap &batch) { return batch.value(QStringLiteral("ticket")).toString(); }
QVariantList contexts(const QVariantMap &batch) { return batch.value(QStringLiteral("contexts")).toList(); }
bool okay(const QVariantMap &batch) { return batch.value(QStringLiteral("ok")).toBool(); }
QString error(const QVariantMap &batch) { return batch.value(QStringLiteral("error")).toString(); }
QVariantList drain(valmax::HistoryModelCursor &cursor)
{
    QVariantList result;
    for (int iterations = 0; iterations < 1000; ++iterations) {
        const auto batch = cursor.reserve(200);
        if (!okay(batch)) return {};
        const auto items = contexts(batch);
        if (items.empty()) return result;
        result.append(items);
        if (!cursor.commit(ticket(batch), int(items.size()))) return {};
    }
    return {};
}
}

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    qRegisterMetaType<Wrapper>();
    qmlRegisterType<valmax::HistoryModelCursor>("ValMAX.CursorFixture", 1, 0, "HistoryModelCursor");
    {
        valmax::HistoryModelCursor cursor;
        CHECK(error(cursor.reserve()) == QStringLiteral("no_model"));
        Model model(10);
        cursor.setModel(&model);
        CHECK(model.reads == 0 && cursor.rowCount() == 10 && cursor.pendingCount() == 10);
        CHECK(error(cursor.reserve(0)) == QStringLiteral("limit_invalid"));
        CHECK(error(cursor.reserve(201)) == QStringLiteral("limit_invalid"));
        auto batch = cursor.reserve(3);
        CHECK(okay(batch) && contexts(batch) == QVariantList({0, 1, 2}));
        CHECK(cursor.pendingCount() == 7 && cursor.leasedCount() == 3);
        CHECK(!batch.value(QStringLiteral("exhausted")).toBool());
        CHECK(error(cursor.reserve()) == QStringLiteral("lease_active"));
        CHECK(!cursor.commit(ticket(batch), -1) && !cursor.commit(ticket(batch), 4));
        CHECK(cursor.commit(ticket(batch), 2));
        CHECK(cursor.pendingCount() == 8 && cursor.leasedCount() == 0);
        auto second = cursor.reserve(3);
        CHECK(contexts(second) == QVariantList({2, 3, 4}));
        CHECK(!cursor.commit(ticket(batch), 1));
        CHECK(cursor.release(ticket(second)));
        CHECK(!cursor.release(ticket(second)));
        CHECK(drain(cursor) == QVariantList({2, 3, 4, 5, 6, 7, 8, 9}));
        batch = cursor.reserve();
        CHECK(okay(batch) && ticket(batch).isEmpty() && contexts(batch).isEmpty());
        CHECK(batch.value(QStringLiteral("exhausted")).toBool());
        cursor.setRoleName(QStringLiteral("unknown"));
        CHECK(cursor.error() == QStringLiteral("missing_role"));
        CHECK(error(cursor.reserve()) == QStringLiteral("missing_role"));
        cursor.setRoleName(QStringLiteral("messageContext"));
        CHECK(cursor.error().isEmpty() && cursor.pendingCount() == 10);
    }
    {
        Model model(10);
        valmax::HistoryModelCursor cursor;
        cursor.setModel(&model);
        const auto batch = cursor.reserve(4);
        model.insertAt(0, {100, 101});
        CHECK(cursor.rowCount() == 12 && cursor.leasedCount() == 4);
        CHECK(cursor.commit(ticket(batch), 2));
        CHECK(drain(cursor) == QVariantList({100, 101, 2, 3, 4, 5, 6, 7, 8, 9}));
        model.insertAt(6, {102});
        CHECK(drain(cursor) == QVariantList({QVariant(102)}));
        model.insertAt(model.rowCount(), {103, 104});
        CHECK(drain(cursor) == QVariantList({103, 104}));
    }
    {
        Model model(10);
        valmax::HistoryModelCursor cursor;
        cursor.setModel(&model);
        const auto batch = cursor.reserve(5);
        model.removeAt(1, 2);
        CHECK(cursor.commit(ticket(batch), 1));
        CHECK(drain(cursor) == QVariantList({3, 4, 5, 6, 7, 8, 9}));
        cursor.reset();
        model.removeAt(2, 3);
        CHECK(drain(cursor) == QVariantList({0, 3, 7, 8, 9}));
        cursor.reset();
        model.removeAt(0, model.rowCount());
        CHECK(cursor.pendingCount() == 0 && cursor.rowCount() == 0);
        CHECK(cursor.reserve().value(QStringLiteral("exhausted")).toBool());
    }
    {
        Model model(10);
        valmax::HistoryModelCursor cursor;
        cursor.setModel(&model);
        CHECK(drain(cursor).size() == 10);
        model.update(2, 4, {Model::ContextRole + 1});
        CHECK(cursor.pendingCount() == 0);
        model.update(2, 4);
        model.update(3, 5);
        CHECK(cursor.pendingCount() == 4);
        CHECK(drain(cursor) == QVariantList({2, 3, 4, 5}));
        model.update(0, 1, {});
        auto batch = cursor.reserve(1);
        model.update(0, 0);
        CHECK(cursor.commit(ticket(batch), 1));
        CHECK(drain(cursor) == QVariantList({0, 1}));
    }
    {
        Model model(4);
        valmax::HistoryModelCursor cursor;
        cursor.setModel(&model);
        auto batch = cursor.reserve(2);
        const auto oldGeneration = cursor.generation();
        model.replace({10, 11, 12});
        CHECK(oldGeneration != cursor.generation());
        CHECK(!cursor.commit(ticket(batch), 2));
        CHECK(drain(cursor) == QVariantList({10, 11, 12}));
        cursor.reset();
        batch = cursor.reserve(1);
        model.reverseLayout();
        CHECK(!cursor.commit(ticket(batch), 1));
        CHECK(drain(cursor) == QVariantList({12, 11, 10}));
        cursor.reset();
        batch = cursor.reserve(1);
        model.firstToLast();
        CHECK(!cursor.release(ticket(batch)));
        CHECK(drain(cursor) == QVariantList({11, 10, 12}));
        Model replacement(2);
        cursor.setModel(&replacement);
        model.insertAt(0, {99});
        CHECK(cursor.rowCount() == 2 && drain(cursor) == QVariantList({0, 1}));
    }
    {
        valmax::HistoryModelCursor cursor;
        auto model = std::make_unique<Model>(3);
        cursor.setModel(model.get());
        const auto batch = cursor.reserve(2);
        model.reset();
        CHECK(!cursor.model() && cursor.pendingCount() == 0 && cursor.leasedCount() == 0);
        CHECK(!cursor.commit(ticket(batch), 2));
        CHECK(error(cursor.reserve()) == QStringLiteral("no_model"));
    }
    {
        Model model(3);
        valmax::HistoryModelCursor cursor;
        cursor.setModel(&model);
        const auto batch = cursor.reserve(1);
        bool accepted = true;
        QString result;
        std::thread foreign([&] {
            result = error(cursor.reserve());
            accepted = cursor.commit(ticket(batch), 1);
            cursor.setModel(nullptr);
            cursor.reset();
        });
        foreign.join();
        CHECK(result == QStringLiteral("wrong_thread") && !accepted);
        CHECK(cursor.model() == &model && cursor.leasedCount() == 1);
        CHECK(cursor.commit(ticket(batch), 1));
        QThread thread;
        Model foreignModel;
        foreignModel.moveToThread(&thread);
        valmax::HistoryModelCursor separate;
        separate.setModel(&model);
        const auto detachedBatch = separate.reserve(1);
        const auto oldReads = model.reads;
        separate.setModel(&foreignModel);
        CHECK(!separate.model() && separate.pendingCount() == 0 && separate.leasedCount() == 0);
        CHECK(error(separate.reserve()) == QStringLiteral("wrong_thread"));
        CHECK(!separate.commit(ticket(detachedBatch), 1) && model.reads == oldReads);
        // Move back from the owning thread before this stack object is destroyed.
        thread.start();
        QMetaObject::invokeMethod(&foreignModel, [&] { foreignModel.moveToThread(app.thread()); }, Qt::BlockingQueuedConnection);
        thread.quit();
        thread.wait();
        Model moving(2);
        separate.setModel(&moving);
        const auto movingBatch = separate.reserve(1);
        moving.moveToThread(&thread);
        CHECK(!separate.model() && error(separate.reserve()) == QStringLiteral("wrong_thread"));
        CHECK(!separate.release(ticket(movingBatch)));
        thread.start();
        QMetaObject::invokeMethod(&moving, [&] { moving.update(0, 1); moving.moveToThread(app.thread()); }, Qt::BlockingQueuedConnection);
        QCoreApplication::processEvents();
        CHECK(!separate.model() && separate.pendingCount() == 0);
        thread.quit();
        thread.wait();
    }
    {
        Model model(3);
        valmax::HistoryModelCursor cursor;
        cursor.setModel(&model);
        model.onRead = [&] { model.replace({9, 10}); };
        CHECK(error(cursor.reserve(2)) == QStringLiteral("model_changed"));
        CHECK(cursor.leasedCount() == 0 && drain(cursor) == QVariantList({9, 10}));
        bool busyRejected = false;
        QObject::connect(&model, &QAbstractItemModel::rowsAboutToBeInserted, &cursor,
            [&] { busyRejected = error(cursor.reserve()) == QStringLiteral("model_busy"); });
        model.insertAt(0, {8});
        CHECK(busyRejected && drain(cursor) == QVariantList({QVariant(8)}));
        cursor.reset();
        const auto connection = QObject::connect(&cursor, &valmax::HistoryModelCursor::stateChanged, &cursor, [&] {
            if (cursor.leasedCount()) cursor.reset();
        });
        CHECK(error(cursor.reserve(1)) == QStringLiteral("model_changed"));
        QObject::disconnect(connection);
        CHECK(cursor.pendingCount() == 3);
    }
    {
        LargeModel model(100001);
        valmax::HistoryModelCursor cursor;
        cursor.setModel(&model);
        CHECK(cursor.error() == QStringLiteral("row_limit") && model.reads == 0);
        CHECK(error(cursor.reserve()) == QStringLiteral("row_limit"));
        Model fragmented(5000);
        cursor.setModel(&fragmented);
        CHECK(drain(cursor).size() == 5000);
        for (int i = 0; i < 2048; ++i) fragmented.update(i * 2, i * 2);
        CHECK(cursor.error().isEmpty() && cursor.pendingCount() == 2048);
        fragmented.update(4096, 4096);
        CHECK(cursor.error() == QStringLiteral("range_limit") && cursor.pendingCount() == 0);
        CHECK(error(cursor.reserve()) == QStringLiteral("range_limit"));
        cursor.reset();
        CHECK(cursor.error().isEmpty() && cursor.pendingCount() == 5000);
    }
    unsigned qmlChecks = 0;
    {
        Context object(42);
        QQmlEngine::setObjectOwnership(&object, QQmlEngine::CppOwnership);
        Model model;
        Model control;
        auto parentDirect = new Context(43);
        auto parentWrapper = new Context(44);
        auto controlWrapper = new Context(45);
        auto controlDirect = new Context(46);
        for (auto item : {parentDirect, parentWrapper}) item->setParent(&model);
        for (auto item : {controlWrapper, controlDirect}) item->setParent(&control);
        const QPointer<Context> explicitGuard(&object), directGuard(parentDirect), wrapperGuard(parentWrapper);
        const QPointer<Context> controlWrapperGuard(controlWrapper), controlDirectGuard(controlDirect);
        const auto explicitOwnership = QQmlEngine::objectOwnership(&object);
        model.values = {QVariant::fromValue(static_cast<QObject *>(&object)), QVariant::fromValue(Wrapper(&object)),
            QVariant::fromValue(static_cast<QObject *>(parentDirect)), QVariant::fromValue(Wrapper(parentWrapper))};
        control.values = {QVariant::fromValue(Wrapper(controlWrapper)), QVariant::fromValue(static_cast<QObject *>(controlDirect))};
        valmax::HistoryModelCursor cursor;
        cursor.setModel(&model);
        QQmlEngine engine;
        engine.rootContext()->setContextProperty(QStringLiteral("fixtureModel"), &model);
        engine.rootContext()->setContextProperty(QStringLiteral("controlModel"), &control);
        QQmlComponent component(&engine);
        component.setData(R"(import QtQml
import QtQml.Models
import ValMAX.CursorFixture 1.0
QtObject {
    property HistoryModelCursor cursor: HistoryModelCursor { model: fixtureModel }
    property DelegateModel original: DelegateModel { model: controlModel; delegate: QtObject {} }
    property int verified: 0
    property string failure: ""
    Component.onCompleted: {
        var batch = cursor.reserve(4)
        if (cursor.rowCount !== 4 || cursor.roleName !== "messageContext") { failure = "registration"; return }
        if (!batch.ok || batch.contexts.length !== 4) { failure = "batch"; return }
        if (batch.contexts[0].identity !== 42) { failure = "QObject role"; return }
        if (typeof batch.contexts[1].get !== "function") { failure = "gadget method"; return }
        if (batch.contexts[1].get().identity !== 42) { failure = "gadget value"; return }
        if (batch.contexts[2].identity !== 43 || batch.contexts[3].get().identity !== 44) { failure = "parent-owned role"; return }
        if (!cursor.commit(batch.ticket, 4)) { failure = "commit"; return }
        if (original.items.get(0).model.messageContext.get().identity !== 45) { failure = "original wrapper"; return }
        if (original.items.get(1).model.messageContext.identity !== 46) { failure = "original direct"; return }
        if (!cursor.reserve(2).exhausted) { failure = "local queue"; return }
        verified = 10
    }
})", QUrl());
        std::unique_ptr<QObject> view(component.create());
        if (!view) QTextStream(stderr) << component.errorString();
        CHECK(view != nullptr);
        if (!view->property("failure").toString().isEmpty()) QTextStream(stderr) << view->property("failure").toString() << '\n';
        CHECK(view->property("failure").toString().isEmpty());
        qmlChecks = view->property("verified").toUInt();
        CHECK(qmlChecks == 10);
        view.reset();
        engine.collectGarbage();
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        CHECK(explicitGuard && directGuard && wrapperGuard && controlWrapperGuard && controlDirectGuard);
        CHECK(QQmlEngine::objectOwnership(&object) == explicitOwnership);
        CHECK(QQmlEngine::objectOwnership(parentDirect) == QQmlEngine::objectOwnership(controlDirect));
        CHECK(QQmlEngine::objectOwnership(parentWrapper) == QQmlEngine::objectOwnership(controlWrapper));
    }
    QJsonObject benchmark;
    {
        constexpr int count = 10000;
        Model model(count);
        valmax::HistoryModelCursor cursor;
        QElapsedTimer timer;
        timer.start();
        cursor.setModel(&model);
        CHECK(model.reads == 0);
        const auto result = drain(cursor);
        const auto elapsed = timer.nsecsElapsed();
        CHECK(result.size() == count && model.reads == count);
        bool ordered = true;
        for (int i = 0; i < count; ++i) if (result[i].toInt() != i) ordered = false;
        CHECK(ordered);
        const auto cursorReads = model.reads;
        model.reads = 0;
        timer.restart();
        for (int end = 200; end <= count; end += 200)
            for (int row = 0; row < end; ++row) model.data(model.index(row), Model::ContextRole);
        const auto baselineElapsed = timer.nsecsElapsed();
        CHECK(model.reads == 255000);
        benchmark = {{QStringLiteral("rows"), count}, {QStringLiteral("batchLimit"), 200},
            {QStringLiteral("cursorRoleReads"), double(cursorReads)},
            {QStringLiteral("prefixRescanRoleReads"), double(model.reads)},
            {QStringLiteral("cursorNanoseconds"), double(elapsed)},
            {QStringLiteral("prefixRescanNanoseconds"), double(baselineElapsed)},
            {QStringLiteral("scope"), QStringLiteral("Synthetic already-loaded model; not MAX rendering or server pagination")}};
    }
    const QJsonObject result{{QStringLiteral("ok"), true}, {QStringLiteral("checks"), int(checks)},
        {QStringLiteral("qmlConversionChecks"), int(qmlChecks)}, {QStringLiteral("benchmark"), benchmark},
        {QStringLiteral("actualMaxStarted"), false}, {QStringLiteral("profilesTouched"), false},
        {QStringLiteral("networkRequestsSent"), false}, {QStringLiteral("remoteHistoryCompletenessProven"), false}};
    QTextStream(stdout) << QJsonDocument(result).toJson(QJsonDocument::Compact) << '\n';
    return 0;
}

#include "history_model_cursor_test.moc"
