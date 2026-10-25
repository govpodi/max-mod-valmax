#include "history_model_cursor.h"

#include <QEvent>
#include <QScopedValueRollback>
#include <QThread>
#include <algorithm>

namespace valmax {
HistoryModelCursor::HistoryModelCursor(QObject *parent) : QObject(parent) {}

bool HistoryModelCursor::onThread() const
{
    return QThread::currentThread() == thread() && (!model_ || model_->thread() == thread());
}

int HistoryModelCursor::pendingCount() const
{
    int count = 0;
    for (const auto &range : pending_) count += range.last - range.first + 1;
    return count;
}

void HistoryModelCursor::invalidate()
{
    ++generation_;
    pending_.clear();
    lease_.clear();
    ticket_.clear();
}

void HistoryModelCursor::fail(const QString &error)
{
    invalidate();
    error_ = error;
}

void HistoryModelCursor::rescan()
{
    invalidate();
    error_.clear();
    role_ = -1;
    rowCount_ = 0;
    if (!model_) return;
    if (model_->thread() != thread()) { error_ = QStringLiteral("wrong_thread"); return; }
    rowCount_ = model_->rowCount();
    const auto roles = model_->roleNames();
    const auto name = roleName_.toUtf8();
    for (auto it = roles.cbegin(); it != roles.cend(); ++it)
        if (it.value() == name) { role_ = it.key(); break; }
    if (role_ < 0) { error_ = QStringLiteral("missing_role"); return; }
    const int count = rowCount_;
    if (count < 0 || count > MaxRows) { error_ = QStringLiteral("row_limit"); return; }
    if (count) pending_.append({0, count - 1});
}

void HistoryModelCursor::setModel(QAbstractItemModel *value)
{
    if (QThread::currentThread() != thread()) return;
    if (value && value->thread() != thread()) {
        setModel(nullptr);
        fail(QStringLiteral("wrong_thread"));
        emit stateChanged();
        return;
    }
    if (model_ == value) return;
    if (model_) model_->removeEventFilter(this);
    for (const auto &connection : connections_) disconnect(connection);
    connections_.clear();
    model_ = value;
    structuralChange_ = false;
    if (model_) {
        model_->installEventFilter(this);
        auto before = [this] { structuralChange_ = true; };
        connections_.append(connect(model_, &QAbstractItemModel::rowsAboutToBeInserted, this,
            [before](const QModelIndex &parent, int, int) { if (!parent.isValid()) before(); }));
        connections_.append(connect(model_, &QAbstractItemModel::rowsAboutToBeRemoved, this,
            [before](const QModelIndex &parent, int, int) { if (!parent.isValid()) before(); }));
        connections_.append(connect(model_, &QAbstractItemModel::rowsInserted, this,
            [this](const QModelIndex &parent, int first, int last) {
                if (parent.isValid()) return;
                structuralChange_ = false;
                inserted(first, last);
                emit stateChanged();
            }));
        connections_.append(connect(model_, &QAbstractItemModel::rowsRemoved, this,
            [this](const QModelIndex &parent, int first, int last) {
                if (parent.isValid()) return;
                structuralChange_ = false;
                removed(first, last);
                emit stateChanged();
            }));
        connections_.append(connect(model_, &QAbstractItemModel::dataChanged, this,
            [this](const QModelIndex &first, const QModelIndex &last, const QList<int> &roles) {
                if (first.parent().isValid() || last.parent().isValid() ||
                    (!roles.empty() && !roles.contains(role_))) return;
                changed(first.row(), last.row());
                emit stateChanged();
            }));
        connections_.append(connect(model_, &QAbstractItemModel::modelAboutToBeReset, this, before));
        connections_.append(connect(model_, &QAbstractItemModel::layoutAboutToBeChanged, this, before));
        connections_.append(connect(model_, &QAbstractItemModel::rowsAboutToBeMoved, this, before));
        connections_.append(connect(model_, &QAbstractItemModel::columnsAboutToBeInserted, this, before));
        connections_.append(connect(model_, &QAbstractItemModel::columnsAboutToBeRemoved, this, before));
        connections_.append(connect(model_, &QAbstractItemModel::columnsAboutToBeMoved, this, before));
        auto rescanAfter = [this] { structuralChange_ = false; rescan(); emit stateChanged(); };
        connections_.append(connect(model_, &QAbstractItemModel::modelReset, this, rescanAfter));
        connections_.append(connect(model_, &QAbstractItemModel::layoutChanged, this, rescanAfter));
        connections_.append(connect(model_, &QAbstractItemModel::rowsMoved, this, rescanAfter));
        connections_.append(connect(model_, &QAbstractItemModel::columnsInserted, this, rescanAfter));
        connections_.append(connect(model_, &QAbstractItemModel::columnsRemoved, this, rescanAfter));
        connections_.append(connect(model_, &QAbstractItemModel::columnsMoved, this, rescanAfter));
        connections_.append(connect(model_, &QObject::destroyed, this, [this] {
            model_ = nullptr;
            structuralChange_ = false;
            rescan();
            emit modelChanged();
            emit stateChanged();
        }));
    }
    rescan();
    emit modelChanged();
    emit stateChanged();
}

bool HistoryModelCursor::eventFilter(QObject *watched, QEvent *event)
{
    if (watched == model_ && event->type() == QEvent::ThreadChange) {
        // This event is delivered in the old owning thread, before the move.
        // Foreign models are unsupported; never retain their old account rows.
        setModel(nullptr);
        fail(QStringLiteral("wrong_thread"));
        emit stateChanged();
    }
    return QObject::eventFilter(watched, event);
}

void HistoryModelCursor::setRoleName(const QString &value)
{
    if (!onThread() || roleName_ == value) return;
    roleName_ = value;
    rescan();
    emit roleNameChanged();
    emit stateChanged();
}

void HistoryModelCursor::reset()
{
    if (!onThread() || structuralChange_) return;
    rescan();
    emit stateChanged();
}

void HistoryModelCursor::addRange(int first, int last)
{
    if (!error_.isEmpty() || !model_) return;
    first = std::max(0, first);
    last = std::min(rowCount_ - 1, last);
    if (first > last) return;
    Range merged{first, last};
    auto it = pending_.begin();
    while (it != pending_.end() && it->last + 1 < merged.first) ++it;
    while (it != pending_.end() && it->first <= merged.last + 1) {
        merged.first = std::min(merged.first, it->first);
        merged.last = std::max(merged.last, it->last);
        it = pending_.erase(it);
    }
    pending_.insert(it, merged);
    if (pending_.size() > MaxRanges) fail(QStringLiteral("range_limit"));
}

bool HistoryModelCursor::reentrantMutation()
{
    if (!reserving_ && error_.isEmpty()) return false;
    // data() must be read-only. A model mutating during data() invalidates the
    // entire returned batch rather than pairing stale values with new indices.
    rescan();
    return true;
}

void HistoryModelCursor::inserted(int first, int last)
{
    if (!onThread()) { fail(QStringLiteral("wrong_thread")); return; }
    rowCount_ = model_ ? model_->rowCount() : 0;
    if (reentrantMutation()) return;
    if (!model_ || rowCount_ > MaxRows) { fail(QStringLiteral("row_limit")); return; }
    const int count = last - first + 1;
    for (auto &range : pending_) {
        if (range.first >= first) { range.first += count; range.last += count; }
        else if (range.last >= first) range.last += count;
    }
    addRange(first, last);
}

void HistoryModelCursor::removed(int first, int last)
{
    if (!onThread()) { fail(QStringLiteral("wrong_thread")); return; }
    rowCount_ = model_ ? model_->rowCount() : 0;
    if (reentrantMutation()) return;
    const int count = last - first + 1;
    QVector<Range> next;
    next.reserve(pending_.size());
    for (const auto &range : pending_) {
        if (range.last < first) next.append(range);
        else if (range.first > last) next.append({range.first - count, range.last - count});
        else {
            if (range.first < first) next.append({range.first, first - 1});
            if (range.last > last) next.append({first, range.last - count});
        }
    }
    pending_.clear();
    for (const auto &range : next) addRange(range.first, range.last);
}

void HistoryModelCursor::changed(int first, int last)
{
    if (!onThread()) { fail(QStringLiteral("wrong_thread")); return; }
    if (reentrantMutation()) return;
    addRange(first, last);
}

QVariantMap HistoryModelCursor::rejected(const QString &error) const
{
    return {{QStringLiteral("ok"), false}, {QStringLiteral("error"), error}};
}

QVariantMap HistoryModelCursor::reserve(int limit)
{
    if (!onThread()) return rejected(QStringLiteral("wrong_thread"));
    if (reserving_ || structuralChange_) return rejected(QStringLiteral("model_busy"));
    if (!error_.isEmpty()) return rejected(error_);
    if (!model_) return rejected(QStringLiteral("no_model"));
    if (!ticket_.isEmpty()) return rejected(QStringLiteral("lease_active"));
    if (limit < 1 || limit > 200) return rejected(QStringLiteral("limit_invalid"));
    const auto version = generation_;
    QVariantList contexts;
    contexts.reserve(limit);
    lease_.reserve(limit);
    while (!pending_.isEmpty() && lease_.size() < limit) {
        auto &range = pending_.first();
        const int row = range.first++;
        if (range.first > range.last) pending_.removeFirst();
        const auto index = model_->index(row, 0);
        if (!index.isValid()) { fail(QStringLiteral("invalid_index")); return rejected(error_); }
        lease_.append(QPersistentModelIndex(index));
    }
    if (!lease_.isEmpty()) {
        ticket_ = QString::number(++ticketSerial_);
        const auto ticket = ticket_;
        const auto indices = lease_; // At most 200 persistent indices.
        QScopedValueRollback<bool> guard(reserving_, true);
        for (const auto &index : indices) {
            const QVariant value = model_->data(index, role_);
            if (version != generation_ || ticket != ticket_)
                return rejected(QStringLiteral("model_changed"));
            contexts.append(value); // Preserve QObject/value-wrapper metatype.
        }
    }
    const auto issuedTicket = ticket_;
    if (!issuedTicket.isEmpty()) emit stateChanged();
    // A synchronous binding may reset the model from stateChanged().
    if (version != generation_ || issuedTicket != ticket_) return rejected(QStringLiteral("model_changed"));
    return {{QStringLiteral("ok"), true}, {QStringLiteral("ticket"), ticket_},
        {QStringLiteral("generation"), generation()}, {QStringLiteral("contexts"), contexts},
        {QStringLiteral("pending"), pendingCount()},
        {QStringLiteral("exhausted"), pending_.isEmpty() && lease_.isEmpty()}};
}

bool HistoryModelCursor::commit(const QString &ticket, int consumed)
{
    if (!onThread() || reserving_ || structuralChange_ || ticket.isEmpty() || ticket != ticket_ ||
        consumed < 0 || consumed > lease_.size()) return false;
    const auto remainder = lease_.mid(consumed);
    lease_.clear();
    ticket_.clear();
    for (const auto &index : remainder) if (index.isValid()) addRange(index.row(), index.row());
    emit stateChanged();
    return true;
}

bool HistoryModelCursor::release(const QString &ticket) { return commit(ticket, 0); }
}
