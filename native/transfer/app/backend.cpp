#include "backend.h"
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <windows.h>
#include <shlobj.h>
#include <stdexcept>

namespace valmax::transfer::app {
void erase(QByteArray& bytes) noexcept {
    // Best effort for our owned UTF-8 buffers; Qt/input/allocator copies cannot
    // be promised erased. Password arrays are never deliberately shared.
    if (!bytes.isEmpty()) SecureZeroMemory(bytes.data(), static_cast<SIZE_T>(bytes.size()));
    bytes.clear();
    bytes.squeeze();
}
QString nativeLocalDirectory() {
    PWSTR path = nullptr;
    const auto hr = SHGetKnownFolderPath(FOLDERID_LocalAppData,
        KF_FLAG_DONT_VERIFY|KF_FLAG_NO_PACKAGE_REDIRECTION, nullptr, &path);
    QString value;
    if (SUCCEEDED(hr) && path) value = QString::fromWCharArray(path);
    if (path) CoTaskMemFree(path);
    return value;
}
namespace {
constexpr qsizetype maximum = 128 * 1024;
struct SecretBytes {
    QByteArray value;
    SecretBytes() { value.reserve(maximum); }
    ~SecretBytes() { erase(value); }
};
struct Buffer {
    ValmaxCoreBuffer value{};
    ~Buffer() { valmax_core_buffer_free(&value); }
};
bool quoted(QByteArray& out, const QByteArray& input) {
    static constexpr char hex[] = "0123456789abcdef";
    if (out.size() >= maximum) return false;
    out.append('"');
    for (char raw : input) {
        const auto c = static_cast<unsigned char>(raw);
        if (out.size() > maximum - 7) return false;
        if (c == '"' || c == '\\') { out.append('\\'); out.append(raw); }
        else if (c < 0x20) {
            out.append("\\u00", 4); out.append(hex[c >> 4]); out.append(hex[c & 15]);
        } else out.append(raw);
    }
    out.append('"');
    return true;
}
bool request(SecretBytes& result, const QUrl& file, const QByteArray& password,
    const QString* fingerprint = nullptr) {
    if (password.size() < 12 || password.size() > 1024 || password.contains('\0')) return false;
    const auto url = file.toEncoded(QUrl::FullyEncoded);
    if (url.isEmpty() || url.size() > 32000) return false;
    auto& out = result.value;
    out.append("{\"file\":", 8);
    if (!quoted(out, url)) return false;
    out.append(",\"password\":", 12);
    if (!quoted(out, password)) return false;
    if (fingerprint) {
        out.append(",\"fingerprint\":", 15);
        if (!quoted(out, fingerprint->toLatin1())) return false;
    }
    out.append('}');
    return out.size() <= maximum;
}
QJsonObject response(int status, const Buffer& buffer) {
    if (status || !buffer.value.data || buffer.value.len > static_cast<size_t>(maximum)) return {};
    QJsonParseError error;
    const auto bytes = QByteArray::fromRawData(reinterpret_cast<const char*>(buffer.value.data),
        static_cast<qsizetype>(buffer.value.len));
    const auto document = QJsonDocument::fromJson(bytes, &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) return {};
    const auto object = document.object();
    if (object.value(QStringLiteral("schema")).toInt(-1) != 1
        || !object.value(QStringLiteral("ok")).isBool()) return {};
    return object;
}
QString failure(const QJsonObject& object) {
    const auto code = object.value(QStringLiteral("error")).toString();
    if (code.isEmpty() || code.size() > 64) return QStringLiteral("internal_error");
    for (const auto c : code) if (!(c >= u'a' && c <= u'z') && c != u'_')
        return QStringLiteral("internal_error");
    return code;
}
bool fingerprintValid(const QString& value) {
    if (value.size() != 64) return false;
    for (const auto c : value) if (!(c >= u'0' && c <= u'9') && !(c >= u'a' && c <= u'f')) return false;
    return true;
}
class NativeBackend final : public Backend {
public:
    RecoveryStatusReply recoveryStatus(const QString& local) override {
        const auto bytes = local.toUtf8();
        Buffer output;
        const auto status = valmax_transfer_recovery_status(reinterpret_cast<const uint8_t*>(bytes.constData()),
            static_cast<size_t>(bytes.size()), &output.value);
        const auto body = response(status, output);
        if (!body.value(QStringLiteral("ok")).toBool()) return {false, false, failure(body), {}};
        const auto result = body.value(QStringLiteral("result")).toObject();
        const auto required = result.value(QStringLiteral("required"));
        const auto fingerprint = result.value(QStringLiteral("fingerprint")).toString();
        if (result.size() != 2 || !required.isBool()
            || (required.toBool() ? !fingerprintValid(fingerprint) : !fingerprint.isEmpty()))
            return {false, false, QStringLiteral("internal_error"), {}};
        return {true, required.toBool(), {}, fingerprint};
    }
    RecoveryReply recover(const QString& local, const QByteArray& password,
        const QString& fingerprint, NativeTransferHost& host) override {
        if (password.size() < 12 || password.size() > 1024 || password.contains('\0')
            || !fingerprintValid(fingerprint)) return {false, QStringLiteral("invalid_request"), 0};
        SecretBytes input;
        input.value.append("{\"password\":");
        if (!quoted(input.value, password)) return {false, QStringLiteral("invalid_request"), 0};
        input.value.append(",\"fingerprint\":");
        if (!quoted(input.value, fingerprint.toLatin1())) return {false, QStringLiteral("invalid_request"), 0};
        input.value.append('}');
        const auto bytes = local.toUtf8();
        auto callbacks = host.callbacks();
        Buffer output;
        const auto status = valmax_transfer_recover(reinterpret_cast<const uint8_t*>(bytes.constData()),
            static_cast<size_t>(bytes.size()), reinterpret_cast<const uint8_t*>(input.value.constData()),
            static_cast<size_t>(input.value.size()), &callbacks, &output.value);
        const auto body = response(status, output);
        if (!body.value(QStringLiteral("ok")).toBool()) return {false, failure(body), 0};
        const auto result = body.value(QStringLiteral("result")).toObject();
        const auto files = result.value(QStringLiteral("files")).toInt(-1);
        if (result.size() != 3 || result.value(QStringLiteral("schema")).toInt(-1) != 1
            || result.value(QStringLiteral("result")).toString() != QStringLiteral("restored")
            || files < 0 || files > 10001) return {false, QStringLiteral("internal_error"), 0};
        return {true, {}, files};
    }
    PreviewReply inspect(const QUrl& file, const QByteArray& password) override {
        SecretBytes input;
        if (!request(input, file, password)) return {false, QStringLiteral("invalid_request"), {}, {}};
        Buffer output;
        const auto status = valmax_transfer_preview(reinterpret_cast<const uint8_t*>(input.value.constData()),
            static_cast<size_t>(input.value.size()), &output.value);
        const auto body = response(status, output);
        if (!body.value(QStringLiteral("ok")).toBool()) return {false, failure(body), {}, {}};
        const auto result = body.value(QStringLiteral("result")).toObject();
        const auto accounts = result.value(QStringLiteral("accounts")).toArray();
        const auto fingerprint = result.value(QStringLiteral("fingerprint")).toString();
        if (result.value(QStringLiteral("schema")).toInt(-1) != 1
            || result.value(QStringLiteral("kind")).toString() != QStringLiteral("login")
            || accounts.isEmpty() || accounts.size() > 3
            || result.value(QStringLiteral("profiles")).toInt(-1) != accounts.size()
            || !fingerprintValid(fingerprint)) return {false, QStringLiteral("internal_error"), {}, {}};
        PreviewReply reply{true, {}, fingerprint, {}};
        for (const auto& item : accounts) {
            const auto row = item.toObject();
            Account account{row.value(QStringLiteral("accountId")).toString(),
                row.value(QStringLiteral("displayName")).toString(), row.value(QStringLiteral("shortName")).toString()};
            bool digits = !account.id.isEmpty() && account.id.size() <= 19 && account.id.front() != u'0';
            for (const auto c : account.id) digits &= c >= u'0' && c <= u'9';
            if (!digits || account.name.size() > 512 || account.shortName.size() > 64)
                return {false, QStringLiteral("internal_error"), {}, {}};
            reply.accounts.push_back(std::move(account));
        }
        return reply;
    }
    ImportReply importLogin(const QString& local, const QUrl& file, const QByteArray& password,
        const QString& fingerprint, NativeTransferHost& host) override {
        SecretBytes input;
        if (!request(input, file, password, &fingerprint)) return {false, QStringLiteral("invalid_request"), {}, 0};
        const auto localUtf8 = local.toUtf8();
        auto callbacks = host.callbacks();
        Buffer output;
        const auto status = valmax_transfer_import(reinterpret_cast<const uint8_t*>(localUtf8.constData()),
            static_cast<size_t>(localUtf8.size()), reinterpret_cast<const uint8_t*>(input.value.constData()),
            static_cast<size_t>(input.value.size()), &callbacks, &output.value);
        const auto body = response(status, output);
        if (!body.value(QStringLiteral("ok")).toBool()) return {false, failure(body), {}, 0};
        const auto result = body.value(QStringLiteral("result")).toObject();
        const auto state = result.value(QStringLiteral("result")).toString();
        const auto profiles = result.value(QStringLiteral("profiles")).toInt(-1);
        if (result.value(QStringLiteral("schema")).toInt(-1) != 1 || profiles < 0 || profiles > 3
            || (state != QStringLiteral("added") && state != QStringLiteral("already_present")))
            return {false, QStringLiteral("internal_error"), {}, 0};
        return {true, {}, state, profiles};
    }
};
}
std::shared_ptr<Backend> makeProductionBackend() { return std::make_shared<NativeBackend>(); }
}
