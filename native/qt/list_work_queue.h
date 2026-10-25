#pragma once

#include <QObject>
#include <QPointer>
#include <QElapsedTimer>
#include <QVector>
#include <array>

namespace valmax {
class ListWorkQueue : public QObject {
    Q_OBJECT
    Q_PROPERTY(QObject *view READ view WRITE setView NOTIFY viewChanged)
    Q_PROPERTY(bool viewActive MEMBER viewActive_)
    Q_PROPERTY(int settleMs MEMBER settleMs_)
    Q_PROPERTY(int quietMs MEMBER quietMs_)
    Q_PROPERTY(double admissionBudgetMs MEMBER admissionBudgetMs_)
    Q_PROPERTY(double fastPixelsPerSecond MEMBER fastPixelsPerSecond_)
    Q_PROPERTY(int prefetchDirection READ prefetchDirection NOTIFY prefetchDirectionChanged)
    Q_PROPERTY(int pendingCount READ pendingCount NOTIFY stateChanged)
    Q_PROPERTY(int inflightCount READ inflightCount NOTIFY stateChanged)
    Q_PROPERTY(int grants READ grants NOTIFY stateChanged)
    Q_PROPERTY(int peakInflight READ peakInflight NOTIFY stateChanged)
    Q_PROPERTY(int peakPending READ peakPending NOTIFY stateChanged)
public:
    explicit ListWorkQueue(QObject *parent = nullptr);
    QObject *view() const { return view_; }
    void setView(QObject *value);
    int prefetchDirection() const { return direction_; }
    int pendingCount() const { return int(pending_.size()); }
    int inflightCount() const { return int(inflight_.size()); }
    int grants() const { return grants_; }
    int peakInflight() const { return peakInflight_; }
    int peakPending() const { return peakPending_; }
    Q_INVOKABLE void request(QObject *row, bool priority = false);
    Q_INVOKABLE void cancel(QObject *row);
    Q_INVOKABLE void complete(QObject *row) { cancel(row); }
    Q_INVOKABLE void observeMotion();
    Q_INVOKABLE void pump();
    Q_INVOKABLE void clear(bool requeue = false);
signals:
    void viewChanged();
    void prefetchDirectionChanged();
    void stateChanged();
private:
    struct Entry {
        QPointer<QObject> row;
        QString key;
        int serial = 0;
        bool priority = false;
    };
    bool matches(const Entry &entry) const;
    bool waiting(const Entry &entry) const;
    bool strictlyVisible(QObject *row) const;
    void notifyStateChanged();
    qint64 now() const { return clock_.elapsed(); }
    QPointer<QObject> view_;
    QVector<Entry> pending_;
    QVector<Entry> inflight_;
    QElapsedTimer clock_;
    bool viewActive_ = false;
    bool pumping_ = false;
    int settleMs_ = 80;
    int quietMs_ = 32;
    double admissionBudgetMs_ = 2;
    double fastPixelsPerSecond_ = 1800;
    qint64 lastMotion_ = -1;
    qint64 lastMovement_ = -1;
    qint64 fastUntil_ = 0;
    double lastY_ = 0;
    int direction_ = 1;
    int grants_ = 0;
    int peakInflight_ = 0;
    int peakPending_ = 0;
    std::array<int, 5> reportedState_{};
};
}
