#include <QGuiApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLibrary>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QTextStream>
#include <QTimer>
#include <QPluginLoader>
#include <QFileInfo>
#include <windows.h>
#include <cstdlib>

int main(int argc, char **argv)
{
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    qInstallMessageHandler([](QtMsgType type, const QMessageLogContext &, const QString &message) {
        QTextStream(stderr) << message << '\n';
        if (type == QtFatalMsg) std::_Exit(6);
    });
    if (argc != 2 || qEnvironmentVariable("QT_QPA_PLATFORM") != QStringLiteral("windows")
        || qEnvironmentVariable("QT_WEBVIEW_PLUGIN") != QStringLiteral("webview2")) return 2;
    const auto directory = QFileInfo(QString::fromLocal8Bit(argv[0])).absolutePath();
    QPluginLoader platform(directory + QStringLiteral("/plugins/platforms/qwindows.dll"));
    if (!platform.load()) return 6;
    // Resolve the actual MAX Qt DLL export, not a replacement SDK runtime.
    QLibrary webview(directory + QStringLiteral("/Qt6WebView.dll"));
    if (!webview.load()) return 7;
    const auto initialize = webview.resolve("?initialize@QtWebView@@YAXXZ");
    if (!initialize) return 8;
    initialize();
    QGuiApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("ValMAXWebViewBlankTest"));
    app.setOrganizationName(QStringLiteral("ValMAXSynthetic"));
    QQmlEngine engine;
    engine.addImportPath(directory + QStringLiteral("/qml"));
    QObject::connect(&engine, &QQmlEngine::exit, &app, &QCoreApplication::exit);
    QQmlComponent component(&engine, QUrl::fromLocalFile(QString::fromLocal8Bit(argv[1])), QQmlComponent::PreferSynchronous);
    if (component.isError()) { QTextStream(stderr) << component.errorString(); return 3; }
    QScopedPointer<QObject> root(component.create());
    if (!root) { QTextStream(stderr) << component.errorString(); return 4; }
    QTimer::singleShot(20000, &app, [&] { app.exit(5); });
    const int code = app.exec();
    const QJsonObject result{{QStringLiteral("schema"), 1}, {QStringLiteral("ok"), code == 0},
        {QStringLiteral("qtVersion"), QString::fromLatin1(qVersion())},
        {QStringLiteral("url"), root->property("verifiedUrl").toString()},
        {QStringLiteral("userAgent"), root->property("verifiedUserAgent").toString()},
        {QStringLiteral("checks"), root->property("checks").toInt()},
        {QStringLiteral("failure"), root->property("failure").toString()}};
    QTextStream(stdout) << QJsonDocument(result).toJson(QJsonDocument::Compact) << '\n';
    return code;
}
