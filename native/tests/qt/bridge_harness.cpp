#include <QGuiApplication>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QTextStream>
#include <QTimer>
#include <QPluginLoader>
#include <QFileInfo>
#include <windows.h>
#include <cstdlib>

QString modulePath(const wchar_t *name)
{
    const HMODULE module = GetModuleHandleW(name);
    if (!module) return {};
    wchar_t path[32768];
    const DWORD length = GetModuleFileNameW(module, path, 32768);
    return length > 0 && length < 32768 ? QString::fromWCharArray(path, int(length)) : QString();
}

int main(int argc, char **argv)
{
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    qInstallMessageHandler([](QtMsgType type, const QMessageLogContext &, const QString &message) {
        QTextStream(stderr) << message << '\n';
        if (type == QtFatalMsg) std::_Exit(6);
    });
    // Qt's Windows QPA failure path can show MessageBoxW before qFatal, so
    // validate the one supported platform before constructing QGuiApplication.
    if (qEnvironmentVariable("QT_QPA_PLATFORM") != QStringLiteral("windows")) {
        QTextStream(stderr) << "Unsupported synthetic platform plugin\n";
        return 6;
    }
    const QString platforms = QFileInfo(QString::fromLocal8Bit(argv[0])).absolutePath()
        + QStringLiteral("/plugins/platforms/qwindows.dll");
    QPluginLoader platformCheck(platforms);
    if (!platformCheck.load()) {
        QTextStream(stderr) << "Synthetic platform plugin preflight failed: " << platformCheck.errorString() << '\n';
        return 6;
    }
    QGuiApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("ValMAXNativeBridgeSyntheticTest"));
    app.setOrganizationName(QStringLiteral("ValMAXSynthetic"));
    if (argc != 3) {
        QTextStream(stderr) << "usage: harness <native-qml-root> <test.qml>\n";
        return 2;
    }
    QQmlEngine engine;
    engine.addImportPath(QString::fromLocal8Bit(argv[1]));
    QQmlComponent component(&engine, QUrl::fromLocalFile(QString::fromLocal8Bit(argv[2])),
                            QQmlComponent::PreferSynchronous);
    if (component.isError()) {
        QTextStream(stderr) << component.errorString();
        return 3;
    }
    QScopedPointer<QObject> root(component.create());
    if (!root) {
        QTextStream(stderr) << component.errorString();
        return 4;
    }
    QObject::connect(&engine, &QQmlEngine::exit, &app, &QCoreApplication::exit);
    QTimer deadline;
    deadline.setSingleShot(true);
    QObject::connect(&deadline, &QTimer::timeout, &app, [&]() { app.exit(5); });
    deadline.start(10000);
    const int code = app.exec();
    const QJsonObject proof{{QStringLiteral("schema"), 1},
                           {QStringLiteral("qtVersion"), QString::fromLatin1(qVersion())},
                           {QStringLiteral("ok"), code == 0},
                           {QStringLiteral("checks"), root->property("checks").toInt()},
                           {QStringLiteral("error"), root->property("failure").toString()},
                           {QStringLiteral("syntheticOnly"), true},
                           {QStringLiteral("loadedQtCore"), modulePath(L"Qt6Core.dll")},
                           {QStringLiteral("loadedQtQml"), modulePath(L"Qt6Qml.dll")},
                           {QStringLiteral("loadedQtQuick"), modulePath(L"Qt6Quick.dll")},
                           {QStringLiteral("loadedNativePlugin"), modulePath(L"valmaxnativeplugin.dll")},
                           {QStringLiteral("loadedRustCore"), modulePath(L"valmax_core.dll")}};
    QTextStream(stdout) << QJsonDocument(proof).toJson(QJsonDocument::Compact) << '\n';
    return code;
}
