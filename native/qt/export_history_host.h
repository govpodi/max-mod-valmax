#pragma once
#include <QObject>
#include <QString>
#include <memory>
struct ValmaxRuntime;
namespace valmax {
// Export-only metadata owner, created on the QQmlEngine thread after ordinary
// plugin load. Never discovers accounts or reads native message items.
class ExportHistoryHost final {
public:
    explicit ExportHistoryHost(ValmaxRuntime*);
    ~ExportHistoryHost();
    ExportHistoryHost(const ExportHistoryHost&)=delete;
    ExportHistoryHost& operator=(const ExportHistoryHost&)=delete;
    bool begin(const QString& job,const QString& token);
    bool bind(QObject* selectedView,const QString& job,const QString& token);
    void release(const QString& job,const QString& token);
#ifdef VALMAX_EXPORT_HISTORY_TESTING
    struct TestTargets {void* messages;void* destruction;uintptr_t vtable,terminal_return;};
    ExportHistoryHost(ValmaxRuntime*,const TestTargets&);
    bool bindSynthetic(QObject*,uintptr_t,const QString&,const QString&);
    void drainSynthetic();
#endif
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
