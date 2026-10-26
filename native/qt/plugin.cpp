#include "native_bridge.h"
#include "list_work_queue.h"
#include "history_model_cursor.h"
#include "transfer_launch.h"
#include "window_activation.h"
#include "../media/engine_context.h"
#include "../media/composer_adapter.h"
#if VALMAX_HAS_MEDIA_RUNTIME
#include "../media/runtime_host.h"
#endif
#include <QQmlEngine>
#include <QQmlExtensionPlugin>
#include <QJSEngine>
#include <memory>
#if VALMAX_HAS_RETENTION
#include "retention_passive_host.h"
#include <QCoreApplication>
#include <QDir>
#include <QJsonDocument>
#include <QJsonObject>
#endif

class ValMAXNativePlugin final : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)
public:
    void initializeEngine(QQmlEngine *engine, const char *) override
    {
        // The ordinary launcher asks this process to restore its own QWindow.
        // Win32 ShowWindowAsync leaves Qt's logical visible state stale after
        // the tray hides the window, disabling chat-row admission.
        if (!activationReceiver_) activationReceiver_ = std::make_unique<valmax::WindowActivationReceiver>();
#if VALMAX_HAS_RETENTION
        // MAX account owners are constructed after the QML plugin loads. Pin
        // the passive listeners here, before any engine-owned Bridge exists.
        // The only profile comes from the trusted native launcher config.
        const QByteArray config = qgetenv("VALMAX_NATIVE_RUNTIME_CONFIG");
        if (!config.isEmpty() && config.size() <= 16384) {
            const QJsonDocument document = QJsonDocument::fromJson(config);
            if (document.isObject()) {
                const QJsonObject object = document.object();
                const QString profile = object.value(QStringLiteral("profileDirectory")).toString();
                const QString runtime = object.value(QStringLiteral("runtimeDirectory")).toString();
                if (object.size() == 3 && object.value(QStringLiteral("existingClient")).isBool()
                    && !profile.isEmpty() && profile.size() < 32000
                    && !profile.contains(QChar(u'\0')) && QDir::isAbsolutePath(profile)
                    && !runtime.isEmpty() && QDir::isAbsolutePath(runtime)) {
                    const QString core = QDir::toNativeSeparators(
                        QCoreApplication::applicationDirPath() + QStringLiteral("/core.dll"));
                    const QString storage = QDir::toNativeSeparators(
                        QCoreApplication::applicationDirPath() + QStringLiteral("/storage.dll"));
                    const auto wide = [](const QString &value) {
                        return std::wstring_view(reinterpret_cast<const wchar_t *>(value.utf16()),
                                                 size_t(value.size()));
                    };
                    (void)valmax::retention::passive::install_early(
                        wide(profile), wide(core), wide(storage));
                }
            }
        }
#endif
        // Engine lifetime is independent of the Bridge singleton. A collision
        // leaves media unavailable; never replace another component's provider.
        (void)valmax::media::MediaEngineContext::install(engine);
#if VALMAX_HAS_MEDIA_RUNTIME
        (void)valmax::media::attach_media_runtime(engine,qgetenv("VALMAX_NATIVE_RUNTIME_CONFIG"));
#endif
    }
    void registerTypes(const char *uri) override
    {
        qmlRegisterSingletonType<valmax::TransferLaunch>(uri, 1, 0, "TransferLauncher",
            [](QQmlEngine *engine, QJSEngine *) -> QObject * { return new valmax::TransferLaunch(engine); });
        qmlRegisterType<valmax::ListWorkQueue>(uri, 1, 0, "ListWorkQueue");
        qmlRegisterType<valmax::HistoryModelCursor>(uri, 1, 0, "HistoryModelCursor");
        qmlRegisterType<valmax::media::MediaComposerAdapter>(uri, 1, 0, "MediaComposer");
        qmlRegisterSingletonType<valmax::NativeBridge>(uri, 1, 0, "Bridge",
            [](QQmlEngine *engine, QJSEngine *) -> QObject * {
                return new valmax::NativeBridge(engine);
            });
    }
private:
    std::unique_ptr<valmax::WindowActivationReceiver> activationReceiver_;
};

#include "plugin.moc"
