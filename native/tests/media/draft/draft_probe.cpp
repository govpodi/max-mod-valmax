// Development-only exact-version proof. Never part of the shipping plugin.
#include <Windows.h>
#include <QAbstractItemModel>
#include <QCoreApplication>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMetaMethod>
#include <QMetaProperty>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QThread>
#include <QUrl>
#include <atomic>
#include <cstring>
#include <memory>
#include <mutex>

namespace {
std::atomic_bool started{false};
std::mutex resultMutex;
QByteArray resultBytes = R"({"done":false})";
void finish(QJsonObject result) {
    result.insert("done", true);
    const auto bytes = QJsonDocument(result).toJson(QJsonDocument::Compact);
    const std::lock_guard lock(resultMutex);
    resultBytes = bytes;
}
void exercise(const QString &file) {
    QJsonObject result{{"acceptanceProven", false}, {"sendInvoked", false},
                       {"presenterInitialized", false}, {"fileOwnershipProven", false}};
    const auto fail = [&](const char *code) { result.insert("failure", QLatin1String(code)); finish(result); };
    if (QThread::currentThread() != QCoreApplication::instance()->thread()) {
        fail("wrong_thread"); return;
    }
    QQmlEngine engine;
    QQmlComponent component(&engine);
    component.setData(R"(
import OneMeUi
import QmlSingletonesModule
MessageSenderViewContext {
    chatId: QmlUtils.emptyChatId()
    isDelayedMode: false
    messageContextFactory: null
}
)", QUrl(QStringLiteral("qrc:/valmax-owned-draft-probe.qml")));
    if (component.status() != QQmlComponent::Ready) {
        QJsonArray errors;
        for (const auto &error : component.errors()) errors.append(error.description().left(256));
        result.insert("qmlErrors", errors); fail("component_not_ready"); return;
    }
    const std::unique_ptr<QObject> sender(component.create());
    if (!sender) { fail("create_failed"); return; }
    result.insert("senderClass", QLatin1String(sender->metaObject()->className()));
    QObject *view = sender->property("uploadView").value<QObject *>();
    if (!view || QByteArray(view->metaObject()->className()) != "Ui::History::AttachmentUpload::View") {
        fail("unexpected_view"); return;
    }
    result.insert("viewClass", QLatin1String(view->metaObject()->className()));
    auto *model = qobject_cast<QAbstractItemModel *>(view->property("uploadModel").value<QObject *>());
    if (!model || QByteArray(model->metaObject()->className()) != "Ui::History::AttachmentUpload::UploadModel") {
        fail("unexpected_model"); return;
    }
    result.insert("modelClass", QLatin1String(model->metaObject()->className()));
    const auto roles = model->roleNames();
    QJsonObject roleNames;
    for (auto it = roles.cbegin(); it != roles.cend(); ++it)
        roleNames.insert(QString::number(it.key()), QString::fromUtf8(it.value()));
    result.insert("roleNames", roleNames);
    if (roles.value(0x105) != "localFilePreview") { fail("role_mismatch"); return; }
    const auto *meta = view->metaObject();
    const int index = meta->indexOfMethod("uploadPhotoOrVideo(QList<QUrl>)");
    if (index < 0) { fail("method_missing"); return; }
    const QMetaMethod method = meta->method(index);
    result.insert("signature", QString::fromLatin1(method.methodSignature()));
    result.insert("returnType", QLatin1String(method.typeName()));
    if (method.returnMetaType().id() != QMetaType::Void || method.parameterCount() != 1) {
        fail("method_mismatch"); return;
    }
    QJsonArray ownSignals;
    for (int n = meta->methodOffset(); n < meta->methodCount(); ++n)
        if (meta->method(n).methodType() == QMetaMethod::Signal)
            ownSignals.append(QString::fromLatin1(meta->method(n).methodSignature()));
    result.insert("ownSignals", ownSignals);
    // Exact MAX 26.35.1 test seam, independently pinned/disassembled by the
    // runner. Never use this private offset to configure a production view.
    uintptr_t presenter = 1;
    SIZE_T read = 0;
    const bool readable = ReadProcessMemory(GetCurrentProcess(),
        reinterpret_cast<const char *>(view) + 0x18, &presenter, sizeof(presenter), &read) != FALSE;
    result.insert("nullPresenterVerified", readable && read == sizeof(presenter) && presenter == 0);
    if (!readable || read != sizeof(presenter) || presenter != 0) { fail("presenter_not_null"); return; }
    const int before = model->rowCount();
    result.insert("rowsBefore", before);
    if (before != 0) { fail("unexpected_existing_rows"); return; }
    const QList<QUrl> urls{QUrl::fromLocalFile(file)};
    const bool dispatched = method.invoke(view, Qt::DirectConnection, Q_ARG(QList<QUrl>, urls));
    result.insert("invokeReturnedTrue", dispatched);
    result.insert("rowsAfter", model->rowCount());
    result.insert("fileStillExists", QFileInfo::exists(file));
    result.insert("verified", dispatched && model->rowCount() == 0 && QFileInfo::exists(file));
    finish(result);
}
}

extern "C" __declspec(dllexport) int valmax_draft_probe_start(const wchar_t *path) {
    if (!path || !QCoreApplication::instance() || started.exchange(true)) return 1;
    const QString file = QString::fromWCharArray(path);
    if (!file.startsWith(QStringLiteral("C:/ValMAXBuild/build/max-engine-probes/")) ||
        !file.endsWith(QStringLiteral("/synthetic-draft.png")) || !QFileInfo(file).isFile()) return 2;
    return QMetaObject::invokeMethod(QCoreApplication::instance(), [file] {
        try { exercise(file); } catch (...) { finish(QJsonObject{{"failure", "exception"}}); }
    }, Qt::QueuedConnection) ? 0 : 3;
}
extern "C" __declspec(dllexport) int valmax_draft_probe_snapshot(char *output, unsigned capacity) {
    if (!output || capacity < 1) return -1;
    const std::lock_guard lock(resultMutex);
    if (resultBytes.size() >= capacity) return -1;
    std::memcpy(output, resultBytes.constData(), static_cast<size_t>(resultBytes.size()));
    output[resultBytes.size()] = '\0';
    return static_cast<int>(resultBytes.size());
}
