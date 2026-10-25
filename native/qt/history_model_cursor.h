#pragma once

#include <QAbstractItemModel>
#include <QPersistentModelIndex>
#include <QPointer>
#include <QVariantMap>
#include <QVector>

namespace valmax {
// A cursor over rows already exposed by an explicitly selected client model.
// Local exhaustion says nothing about server history or remote pagination.
class HistoryModelCursor : public QObject {
    Q_OBJECT
    Q_PROPERTY(QAbstractItemModel *model READ model WRITE setModel NOTIFY modelChanged)
    Q_PROPERTY(QString roleName READ roleName WRITE setRoleName NOTIFY roleNameChanged)
    Q_PROPERTY(QString generation READ generation NOTIFY stateChanged)
    Q_PROPERTY(QString error READ error NOTIFY stateChanged)
    Q_PROPERTY(int pendingCount READ pendingCount NOTIFY stateChanged)
    Q_PROPERTY(int leasedCount READ leasedCount NOTIFY stateChanged)
    Q_PROPERTY(int rowCount READ rowCount NOTIFY stateChanged)
public:
    explicit HistoryModelCursor(QObject *parent = nullptr);
    QAbstractItemModel *model() const { return model_; }
    void setModel(QAbstractItemModel *value);
    QString roleName() const { return roleName_; }
    void setRoleName(const QString &value);
    QString generation() const { return QString::number(generation_); }
    QString error() const { return error_; }
    int pendingCount() const;
    int leasedCount() const { return int(lease_.size()); }
    int rowCount() const { return rowCount_; }
    Q_INVOKABLE QVariantMap reserve(int limit = 200);
    Q_INVOKABLE bool commit(const QString &ticket, int consumed);
    Q_INVOKABLE bool release(const QString &ticket);
    Q_INVOKABLE void reset();
signals:
    void modelChanged();
    void roleNameChanged();
    void stateChanged();
protected:
    bool eventFilter(QObject *watched, QEvent *event) override;
private:
    struct Range { int first; int last; }; // Inclusive, sorted and disjoint.
    static constexpr int MaxRows = 100000;
    static constexpr int MaxRanges = 2048;
    bool onThread() const;
    void rescan();
    void invalidate();
    void fail(const QString &error);
    void addRange(int first, int last);
    void inserted(int first, int last);
    void removed(int first, int last);
    void changed(int first, int last);
    bool reentrantMutation();
    QVariantMap rejected(const QString &error) const;
    QPointer<QAbstractItemModel> model_;
    QVector<QMetaObject::Connection> connections_;
    QVector<Range> pending_;
    QVector<QPersistentModelIndex> lease_;
    QString roleName_ = QStringLiteral("messageContext");
    QString ticket_;
    QString error_;
    quint64 generation_ = 0;
    quint64 ticketSerial_ = 0;
    int role_ = -1;
    int rowCount_ = 0;
    bool structuralChange_ = false;
    bool reserving_ = false;
};
}
