#pragma once
#include <QVariantMap>
#include <chrono>

namespace valmax {
// Program-byte verifier, not a packet monitor. QML cannot select a path/role.
class PrivacyHost final {
public:
    PrivacyHost() = default;
    ~PrivacyHost();
    QVariantMap refresh();
    static QVariantMap unavailable(const QString &error = QStringLiteral("verifier_unavailable"));
    PrivacyHost(const PrivacyHost &) = delete;
    PrivacyHost &operator=(const PrivacyHost &) = delete;
private:
    bool owned_ = false;
    bool attempted_ = false;
    bool verified_ = false;
    unsigned attempts_ = 0;
    std::chrono::steady_clock::time_point retryAt_{};
    QString failure_ = QStringLiteral("verifier_unavailable");
};
}
