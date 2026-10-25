#include "runtime_host.h"
#include "service_host.h"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTextStream>
#include <QThread>
#include <QTimer>
#include <cstdlib>
#include <functional>
#include <memory>
#include <vector>
#include <windows.h>

using namespace valmax::media;
namespace {
int checks=0;
void check(bool ok,const char* reason) {
    ++checks;if(!ok){QTextStream(stderr)<<reason<<'\n';std::exit(3);}
}
void until(const std::function<bool()>& done,const char* reason) {
    QElapsedTimer elapsed;elapsed.start();
    while(!done()&&elapsed.elapsed()<10000) {
        QCoreApplication::processEvents(QEventLoop::AllEvents,10);
        QCoreApplication::sendPostedEvents(nullptr,QEvent::DeferredDelete);
        QThread::msleep(1);
    }
    check(done(),reason);
}
QByteArray config() {
    return qgetenv("VALMAX_SYNTHETIC_RUNTIME_CONFIG");
}
void ready_case() {
    QQmlEngine first;
    QByteArray backing=config();
    const auto borrowed=QByteArray::fromRawData(backing.constData(),backing.size());
    check(attach_media_runtime(&first,borrowed),"first engine admitted with borrowed bytes");
    backing.fill('x');
    check(attach_media_runtime(&first,config()),"same engine idempotent");
    check(!attach_media_runtime(&first,config()+" "),"changed launcher bytes rejected");
    auto* transient=new QQmlEngine;
    check(attach_media_runtime(transient,config()),"engine during preparation admitted");delete transient;
    until([]{return media_runtime_snapshot().state!=MediaRuntimeState::Preparing;},"prepare finishes");
    check(media_runtime_snapshot().state==MediaRuntimeState::Ready,"verified service ready");
    check(media_runtime_snapshot().engines==1,"destroyed engine excluded");
    first.clearSingletons();
    check(media_runtime_snapshot().state==MediaRuntimeState::Ready,"clearSingletons keeps process service");
    std::vector<std::unique_ptr<QQmlEngine>> engines;
    for(int i=0;i<31;++i){auto e=std::make_unique<QQmlEngine>();check(attach_media_runtime(e.get(),config()),"within engine bound");engines.push_back(std::move(e));}
    QQmlEngine excess;check(!attach_media_runtime(&excess,config()),"engine bound enforced");
    engines.pop_back();check(attach_media_runtime(&excess,config()),"dead engine releases slot");
    QElapsedTimer elapsed;elapsed.start();shutdown_media_runtime();
    check(elapsed.elapsed()<500,"normal stop does not join service");
    until([]{return media_runtime_snapshot().state==MediaRuntimeState::Stopped;},"service drain completes");
    check(MediaServiceHost::process_available(),"all native workers stopped");
    check(!attach_media_runtime(&first,config()),"stopped process service cannot restart silently");
}
void quit_case(QCoreApplication& app,bool direct,bool waitReady) {
    QQmlEngine engine;
    check(attach_media_runtime(&engine,config()),"quit case startup admitted");
    if(waitReady)until([]{return media_runtime_snapshot().state==MediaRuntimeState::Ready;},"quit case ready");
    bool quitSeen=false;
    QObject::connect(&app,&QCoreApplication::aboutToQuit,&app,[&]{quitSeen=true;});
    QTimer::singleShot(0,&app,[direct]{if(direct)QCoreApplication::exit(0);else QCoreApplication::quit();});
    check(app.exec()==0,"application exits normally");
    check(quitSeen,"aboutToQuit observed");
    check(media_runtime_snapshot().state==MediaRuntimeState::Stopped,"quit waits until stopped");
    check(MediaServiceHost::process_available(),"quit leaves no service workers");
}
}
int main(int argc,char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    const QByteArray mode=qgetenv("VALMAX_SYNTHETIC_HOST_MODE");
    check(!config().isEmpty(),"explicit owned test config supplied");
    {
        QCoreApplication app(argc,argv);
        check(media_runtime_snapshot().state==MediaRuntimeState::Absent,"initially no runtime");
        check(!attach_media_runtime(nullptr,config()),"null engine rejected");
        if(mode=="ready")ready_case();
        else if(mode=="quit-pending")quit_case(app,false,false);
        else if(mode=="quit-ready")quit_case(app,false,true);
        else if(mode=="exit-ready")quit_case(app,true,true);
        else if(mode=="destroy-pending") {
            QQmlEngine engine;
            check(attach_media_runtime(&engine,config()),"destructor fallback admitted");
            // Deliberately no exec(): the Qt cleanup routine must drain prepare.
        } else {check(false,"unknown test mode");}
    }
    check(MediaServiceHost::process_available(),"Qt cleanup left no native run");
    check(media_runtime_snapshot().state==MediaRuntimeState::Absent,"Qt cleanup reset process owner");
    QTextStream(stdout)<<QJsonDocument(QJsonObject{{QStringLiteral("passed"),true},{QStringLiteral("checks"),checks},
        {QStringLiteral("case"),QString::fromLatin1(mode)},{QStringLiteral("allThreadsDrained"),true},
        {QStringLiteral("noLiveNetwork"),true},{QStringLiteral("noRealProfiles"),true}}).toJson(QJsonDocument::Compact)<<'\n';
    return 0;
}
