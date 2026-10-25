#include "markers.h"
#include <QCoreApplication>
#include <QGuiApplication>
#include <QDir>
#include <QPluginLoader>
#include <QStringList>
#include <cstdlib>

namespace {
struct StaticMarker {StaticMarker(){marker("executable_static_constructor");}} static_marker;
void core_startup(){marker("qt_core_startup_routine");}
Q_COREAPP_STARTUP_FUNCTION(core_startup)
QString executable_directory(){
    wchar_t path[32768]{};const DWORD size=GetModuleFileNameW(nullptr,path,32768);
    if(!size || size>=32768)return {};
    const QString file=QString::fromWCharArray(path,static_cast<int>(size));
    return QDir::fromNativeSeparators(file).section(u'/',0,-2);
}
}
int main(int argc,char **argv){
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    marker("main_entered");
    if(argc!=1 || qEnvironmentVariable("QT_QPA_PLATFORM")!=QStringLiteral("windows"))return 2;
    qInstallMessageHandler([](QtMsgType type,const QMessageLogContext &,const QString &){
        if(type==QtWarningMsg)marker("qt_warning");
        if(type==QtCriticalMsg)marker("qt_critical");
        if(type==QtFatalMsg){marker("qt_fatal");std::_Exit(10);}
    });
    const QString directory=executable_directory();if(directory.isEmpty())return 3;
    QCoreApplication::setLibraryPaths({directory+QStringLiteral("/plugins")});
    // An invalid QPA can show a Windows popup before Qt's fatal handler.
    // Test only a preloaded exact owned qwindows.dll, never invalid QPA.
    QPluginLoader platform(directory+QStringLiteral("/plugins/platforms/qwindows.dll"));
    if(!platform.load()){marker("platform_preflight_failed");return 4;}
    marker("before_gui_constructor");
    {
        QGuiApplication app(argc,argv);
        marker("gui_constructor_returned");
        if(QString::fromLatin1(qVersion())!=QStringLiteral("6.11.2"))return 5;
        // No window, event loop, native runtime, MAX or user action follows.
        marker("synthetic_client_continuation");
    }
    marker("gui_destructor_returned");
    return 0;
}
