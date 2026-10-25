#include "controller.h"
#include "../../common/startup_context.h"
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickStyle>
#include <windows.h>

int main(int argc, char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    if(valmax::startup::classify(valmax::startup::observe_current())!=valmax::startup::Context::Desktop){
        MessageBoxW(nullptr,valmax::startup::refusal_message(),L"ValMAX",MB_OK|MB_ICONERROR);
        return 2;
    }
    QGuiApplication application(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("ValMAX — перенос аккаунтов"));
    QCoreApplication::setOrganizationName(QStringLiteral("ValMAX"));
    QQuickStyle::setStyle(QStringLiteral("Fusion"));
    const auto mode = QCoreApplication::arguments().contains(QStringLiteral("--recover"))
        ? valmax::transfer::app::Mode::Recovery : valmax::transfer::app::Mode::Import;
    valmax::transfer::app::Controller controller(mode);
    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty(QStringLiteral("transferController"), &controller);
    QObject::connect(&engine, &QQmlApplicationEngine::objectCreationFailed,
        &application, [] { QCoreApplication::exit(1); }, Qt::QueuedConnection);
    engine.load(QUrl(QStringLiteral("qrc:/valmax-transfer/Main.qml")));
    return application.exec();
}
