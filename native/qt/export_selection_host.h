#pragma once

#include <memory>

class QObject;
class QString;
struct ValmaxRuntime;

namespace valmax {
// Binds one export-owned native history view to a selected API2 job. This
// deliberately observes no EOF callback: archives remain labelled partial.
class ExportSelectionHost final {
public:
    explicit ExportSelectionHost(ValmaxRuntime *runtime);
    ~ExportSelectionHost();
    ExportSelectionHost(const ExportSelectionHost &) = delete;
    ExportSelectionHost &operator=(const ExportSelectionHost &) = delete;

    bool begin(const QString &jobId, const QString &jobToken);
    bool bind(QObject *view, const QString &jobId, const QString &jobToken);
    void release(const QString &jobId, const QString &jobToken);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace valmax
