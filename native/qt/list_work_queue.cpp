#include "list_work_queue.h"
#include <QMetaObject>
#include <QScopedValueRollback>
#include <QVariant>
#include <algorithm>
#include <cmath>
#include <limits>

namespace valmax {
namespace {
bool flag(QObject *object, const char *name) { return object && object->property(name).toBool(); }
double number(QObject *object, const char *name) { return object ? object->property(name).toDouble() : 0; }
}

ListWorkQueue::ListWorkQueue(QObject *parent) : QObject(parent) { clock_.start(); }

void ListWorkQueue::setView(QObject *value)
{
    if (view_ == value) return;
    clear(false);
    view_ = value;
    lastMotion_ = -1;
    lastMovement_ = -1;
    observeMotion();
    emit viewChanged();
}

bool ListWorkQueue::matches(const Entry &entry) const
{
    return entry.row && flag(entry.row, "valmaxCompleted") && !flag(entry.row, "valmaxClosing")
        && entry.row->property("identityKey").toString() == entry.key
        && entry.row->property("valmaxRequestSerial").toInt() == entry.serial;
}
bool ListWorkQueue::waiting(const Entry &entry) const
{
    return matches(entry) && flag(entry.row, "loadEligible")
        && !flag(entry.row, "valmaxLoadPermitted") && !flag(entry.row, "valmaxLoadFailed");
}
bool ListWorkQueue::strictlyVisible(QObject *row) const
{
    return row && view_ && flag(row, "eligible")
        && number(row, "y") + number(row, "height") > number(view_, "contentY")
        && number(row, "y") < number(view_, "contentY") + number(view_, "height");
}

void ListWorkQueue::notifyStateChanged()
{
    const std::array<int, 5> next{pendingCount(), inflightCount(), grants(), peakInflight(), peakPending()};
    if (reportedState_ == next) return;
    // Publish before calling QML: a receiver may synchronously mutate the queue.
    // Repeated requests and idle frames must not reevaluate unchanged bindings.
    reportedState_ = next;
    emit stateChanged();
}

void ListWorkQueue::request(QObject *row, bool priority)
{
    if (!row || !flag(row, "loadEligible") || flag(row, "valmaxLoadPermitted") || flag(row, "valmaxLoadFailed")) return;
    bool existed = false;
    QVector<Entry> next;
    next.reserve(pending_.size() + 1);
    for (const Entry &entry : pending_) {
        if (entry.row == row) {
            if (!existed) {
                next.append({row, row->property("identityKey").toString(), row->property("valmaxRequestSerial").toInt(), priority || entry.priority});
                existed = true;
            }
        } else if (waiting(entry)) next.append(entry);
    }
    // ListView owns bounded live delegates. Refuse an unbounded external caller.
    if (!existed && next.size() < 4096)
        next.append({row, row->property("identityKey").toString(), row->property("valmaxRequestSerial").toInt(), priority});
    pending_ = std::move(next);
    peakPending_ = std::max(peakPending_, int(pending_.size()));
    notifyStateChanged();
}
void ListWorkQueue::cancel(QObject *row)
{
    pending_.removeIf([&](const Entry &entry) { return entry.row == row || !waiting(entry); });
    inflight_.removeIf([&](const Entry &entry) { return entry.row == row || !matches(entry); });
    notifyStateChanged();
}

void ListWorkQueue::observeMotion()
{
    if (!view_) return;
    const qint64 stamp = now();
    const double position = number(view_, "contentY");
    if (lastMotion_ >= 0) {
        const double distance = std::abs(position - lastY_);
        if (distance >= 2) {
            const int direction = position > lastY_ ? 1 : -1;
            if (direction_ != direction) { direction_ = direction; emit prefetchDirectionChanged(); }
            lastMovement_ = stamp;
            if (distance * 1000 / double(std::max<qint64>(1, stamp - lastMotion_)) >= fastPixelsPerSecond_)
                fastUntil_ = stamp + std::max(0, settleMs_);
        }
    }
    lastMotion_ = stamp;
    lastY_ = position;
}

void ListWorkQueue::pump()
{
    if (pumping_ || !viewActive_ || !view_) return;
    QScopedValueRollback<bool> guard(pumping_, true);
    const qint64 stamp = now();
    const bool quiet = lastMovement_ < 0 || stamp - lastMovement_ >= std::max(0, quietMs_);
    const int limit = quiet ? 5 : 1;
    const double center = number(view_, "contentY") + number(view_, "height") / 2;
    int starts = 0;
    int backgroundStarts = 0;
    while (starts < limit) {
        inflight_.removeIf([&](const Entry &entry) {
            return !matches(entry) || !flag(entry.row, "valmaxLoadPermitted") || flag(entry.row, "valmaxBodyReady");
        });
        pending_.removeIf([&](const Entry &entry) { return !waiting(entry); });
        bool visibleOutstanding = false;
        int backgroundLoading = 0;
        for (const Entry &entry : inflight_) {
            if (strictlyVisible(entry.row)) visibleOutstanding = true;
            else if (!entry.priority) ++backgroundLoading;
        }
        if (!visibleOutstanding)
            for (const Entry &entry : pending_)
                if (strictlyVisible(entry.row)) { visibleOutstanding = true; break; }
        qsizetype winner = -1;
        double best = std::numeric_limits<double>::infinity();
        for (qsizetype i = 0; i < pending_.size(); ++i) {
            const Entry &entry = pending_.at(i);
            const int capacity = entry.priority ? std::max(2, limit) : limit;
            if (inflight_.size() >= capacity || (!entry.priority && stamp < fastUntil_)) continue;
            const bool visible = strictlyVisible(entry.row);
            if (!entry.priority && visibleOutstanding && !visible) continue;
            if (!entry.priority && !visible && (!quiet || backgroundLoading >= 1 || backgroundStarts >= 1)) continue;
            const double score = (entry.priority ? -1000000000.0 : 0)
                + std::abs(number(entry.row, "y") + number(entry.row, "height") / 2 - center);
            if (score < best) { winner = i; best = score; }
        }
        if (winner < 0 || (starts > 0 && double(now() - stamp) >= std::max(0.0, admissionBudgetMs_))) break;
        // No references into either vector survive the re-entrant QML call.
        const Entry entry = pending_.takeAt(winner);
        inflight_.append(entry);
        peakInflight_ = std::max(peakInflight_, int(inflight_.size()));
        ++starts;
        if (!entry.priority && !strictlyVisible(entry.row)) ++backgroundStarts;
        QVariant accepted;
        const bool invoked = entry.row && QMetaObject::invokeMethod(entry.row, "valmaxGrantLoad", Qt::DirectConnection,
            Q_RETURN_ARG(QVariant, accepted), Q_ARG(QVariant, QVariant(entry.key)), Q_ARG(QVariant, QVariant(entry.serial)));
        if (invoked && accepted.toBool()) ++grants_;
        else cancel(entry.row);
    }
    notifyStateChanged();
}

void ListWorkQueue::clear(bool requeue)
{
    const auto active = inflight_;
    const auto queued = pending_;
    pending_.clear();
    inflight_.clear();
    fastUntil_ = 0;
    for (const Entry &entry : active) {
        if (!matches(entry)) continue;
        QMetaObject::invokeMethod(entry.row, "valmaxCancel", Qt::DirectConnection);
        if (requeue && entry.row) QMetaObject::invokeMethod(entry.row, "valmaxRefresh", Qt::DirectConnection);
    }
    if (requeue) for (const Entry &entry : queued)
        if (matches(entry)) QMetaObject::invokeMethod(entry.row, "valmaxRefresh", Qt::DirectConnection);
    notifyStateChanged();
}
}
