#include <QGuiApplication>
#include <QWebView>
#include <QtWebView/QtWebView>
#include <QPluginLoader>
#include <QFileInfo>
#include <QPointer>
#include <QTimer>
#include <QTextStream>
#include <windows.h>
#include <cstdlib>

int main(int argc,char **argv)
{
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    qInstallMessageHandler([](QtMsgType type,const QMessageLogContext &,const QString &message){
        QTextStream(stderr)<<message<<'\n';if(type==QtFatalMsg)std::_Exit(6);
    });
    QPluginLoader platform(QFileInfo(QString::fromLocal8Bit(argv[0])).absolutePath()+QStringLiteral("/plugins/platforms/qwindows.dll"));
    if(!platform.load())return 6;
    QtWebView::initialize();
    QGuiApplication app(argc,argv);
    if (qgetenv("VALMAX_WEBVIEW_FAULT").isEmpty())
        qputenv("VALMAX_WEBVIEW_FAULT",QByteArrayLiteral("sync-error"));
    QPointer<QWebView> view = new QWebView;
    unsigned deleted=0;
    QObject::connect(view,&QWebView::loadProgressChanged,&app,[&](int){
        // Direct reentrant deletion from the first failure signal.
        delete view.data();++deleted;
        QTimer::singleShot(0,&app,[&]{app.exit(view.isNull()&&deleted==1?0:10);});
    },Qt::DirectConnection);
    view->setUrl(QUrl(QStringLiteral("about:blank")));
    QTimer::singleShot(3000,&app,[&]{app.exit(5);});
    const int code=app.exec();
    QTextStream(stdout)<<"{\"schema\":1,\"ok\":"<<(code==0?"true":"false")<<",\"checks\":"<<deleted<<",\"failure\":\"\"}\n";
    delete view.data();return code;
}
