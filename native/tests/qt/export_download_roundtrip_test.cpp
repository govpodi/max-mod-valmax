#include "export_downloader.h"
#include "valmax_core.h"
#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QHostAddress>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QTcpServer>
#include <QTcpSocket>
#include <QThread>
#include <QTimer>
#include <QUrl>
#include <iostream>
#include <memory>
#include <source_location>
#include <stdexcept>

namespace {
int checks=0;
void require(bool value,const std::source_location location=std::source_location::current()) {
    ++checks;if(!value) throw std::runtime_error("synthetic assertion line "+std::to_string(location.line()));
}
template<class Predicate> void until(Predicate ready) {
    QElapsedTimer clock;clock.start();
    while(!ready() && clock.elapsed()<10000) {QCoreApplication::processEvents();QThread::msleep(1);}
    require(ready());
}
QJsonObject decode(ValmaxCoreBuffer& output) {
    require(output.data && output.len>0 && output.len<8*1024*1024);
    const QByteArray bytes(reinterpret_cast<const char*>(output.data),static_cast<qsizetype>(output.len));
    valmax_core_buffer_free(&output);require(!output.data && output.len==0 && output.capacity==0);
    QJsonParseError error{};const auto document=QJsonDocument::fromJson(bytes,&error);
    require(error.error==QJsonParseError::NoError && document.isObject());
    const auto envelope=document.object();require(envelope.value(QStringLiteral("schema")).toInt()==1);
    require(envelope.value(QStringLiteral("ok")).toBool());
    require(envelope.value(QStringLiteral("result")).isObject());return envelope.value(QStringLiteral("result")).toObject();
}
QString localUrl(const QString& path) {return QUrl::fromLocalFile(path).toString(QUrl::FullyEncoded);}
QByteArray readFile(const QString& path) {QFile file(path);require(file.open(QIODevice::ReadOnly));return file.readAll();}
struct Core {
    ValmaxRuntime* runtime=nullptr;
    explicit Core(const QString& root) {
        const auto body=QJsonDocument(QJsonObject{{QStringLiteral("profileDirectory"),root+QStringLiteral("/synthetic-profile")},
            {QStringLiteral("runtimeDirectory"),root+QStringLiteral("/synthetic-runtime")},{QStringLiteral("existingClient"),true}}).toJson(QJsonDocument::Compact);
        ValmaxCoreBuffer output{};require(valmax_runtime_open(reinterpret_cast<const uint8_t*>(body.constData()),
            static_cast<size_t>(body.size()),&runtime,&output)==0);decode(output);require(runtime);
        require(!QFileInfo::exists(root+QStringLiteral("/synthetic-profile")));
    }
    ~Core(){if(runtime) {valmax_runtime_cancel_pending(runtime);valmax_runtime_close(&runtime);}}
    QJsonObject call(const QString& operation,const QJsonObject& value) {
        const auto bytes=QJsonDocument(QJsonObject{{QStringLiteral("operation"),operation},{QStringLiteral("value"),value}}).toJson(QJsonDocument::Compact);
        ValmaxCoreBuffer output{};require(valmax_runtime_dispatch(runtime,reinterpret_cast<const uint8_t*>(bytes.constData()),
            static_cast<size_t>(bytes.size()),&output)==0);return decode(output);
    }
};
struct Job {
    Core& core;QJsonObject identity;
    Job(Core& value,const QString& selected):core(value) {
        identity=core.call(QStringLiteral("export.start"),QJsonObject{{QStringLiteral("schema"),1},{QStringLiteral("accountId"),QStringLiteral("99")},
            {QStringLiteral("chat"),QJsonObject{{QStringLiteral("id"),QStringLiteral("42")},{QStringLiteral("title"),QStringLiteral("Synthetic roundtrip")},{QStringLiteral("timezoneOffsetMinutes"),0}}},
            {QStringLiteral("directory"),localUrl(selected)}});
        const QJsonObject attachment{{QStringLiteral("id"),QStringLiteral("asset")},{QStringLiteral("type"),QStringLiteral("file")},
            {QStringLiteral("name"),QStringLiteral("synthetic.bin")},{QStringLiteral("mime"),QStringLiteral("application/octet-stream")},
            {QStringLiteral("status"),QStringLiteral("pending")},{QStringLiteral("reason"),QJsonValue::Null}};
        const QJsonObject row{{QStringLiteral("id"),QStringLiteral("1")},
            {QStringLiteral("author"),QJsonObject{{QStringLiteral("id"),QStringLiteral("123")},{QStringLiteral("name"),QStringLiteral("Synthetic sender")}}},
            {QStringLiteral("timestampMs"),1},{QStringLiteral("text"),QStringLiteral("Synthetic text only")},{QStringLiteral("outgoing"),false},
            {QStringLiteral("reply"),QJsonValue::Null},{QStringLiteral("attachments"),QJsonArray{attachment}}};
        auto page=body();page.insert(QStringLiteral("sequence"),0);page.insert(QStringLiteral("messages"),QJsonArray{row});
        core.call(QStringLiteral("export.page"),page);
        const auto lease=core.call(QStringLiteral("export.media-start"),media());
        require(lease.value(QStringLiteral("nativePathFileURL")).toString().startsWith(QStringLiteral("file:///D:/ValMAXBuild/fixtures/export-download-roundtrip/")));
    }
    QJsonObject body() const {return {{QStringLiteral("schema"),1},{QStringLiteral("jobId"),identity.value(QStringLiteral("jobId"))},{QStringLiteral("jobToken"),identity.value(QStringLiteral("jobToken"))}};}
    QJsonObject media() const {auto value=body();value.insert(QStringLiteral("messageId"),QStringLiteral("1"));value.insert(QStringLiteral("attachmentId"),QStringLiteral("asset"));return value;}
    QString downloadRequest() const {auto value=media();value.remove(QStringLiteral("schema"));return QString::fromUtf8(QJsonDocument(value).toJson(QJsonDocument::Compact));}
    QJsonObject status() {return core.call(QStringLiteral("export.status"),body());}
    QJsonObject acknowledge(const QString& status,const QJsonValue& reason) {auto value=media();value.insert(QStringLiteral("status"),status);value.insert(QStringLiteral("reason"),reason);return core.call(QStringLiteral("export.media"),value);}
    QJsonObject finish() {auto value=body();value.insert(QStringLiteral("completion"),QJsonObject{{QStringLiteral("historyComplete"),false},{QStringLiteral("stopReason"),QStringLiteral("unsupported_history")},{QStringLiteral("pagesFetched"),1}});return core.call(QStringLiteral("export.finish"),value);}
};
}
int main(int argc,char** argv) {
    QCoreApplication app(argc,argv);
    try {
        require(argc==2);
        const QString root=QDir::cleanPath(QString::fromLocal8Bit(argv[1]));
        const QDir base(QStringLiteral("D:/ValMAXBuild/fixtures/export-download-roundtrip"));
        require(QFileInfo(root).absolutePath().compare(base.absolutePath(),Qt::CaseInsensitive)==0);
        require(QRegularExpression(QStringLiteral("^[0-9a-f]{32}$")).match(QFileInfo(root).fileName()).hasMatch());
        require(!QFileInfo::exists(root));require(QDir().mkpath(root));
        const auto selected=root+QStringLiteral("/selected-export");require(QDir().mkpath(selected));
        auto core=std::make_shared<Core>(root);
        QString successfulMedia;QByteArray expected(262151,'x');expected.replace(0,20,QByteArrayLiteral("synthetic-owned-data"));
        int requests=0;
        {
            QTcpServer server;require(server.listen(QHostAddress::LocalHost));QByteArray response;
            QObject::connect(&server,&QTcpServer::newConnection,&server,[&]{while(auto* socket=server.nextPendingConnection()){
                QObject::connect(socket,&QIODevice::readyRead,socket,[&,socket]{const auto request=socket->readAll();if(!request.startsWith("GET "))return;
                    ++requests;socket->write(response);socket->flush();});
                QObject::connect(socket,&QTcpSocket::disconnected,socket,&QObject::deleteLater);
            }});
            const auto url=QStringLiteral("http://127.0.0.1:")+QString::number(server.serverPort())+QStringLiteral("/synthetic");
            valmax::ExportDownloader downloader;downloader.allowLoopbackForTest();downloader.setRuntime(core->runtime,core);
            bool done=false,success=false;QString requestId,error;
            QObject::connect(&downloader,&valmax::ExportDownloader::completed,&app,[&](const QString& id,bool ok,const QString& code){
                require(id==requestId);done=true;success=ok;error=code;});
            auto start=[&](const Job& job){done=false;success=false;error.clear();const auto admitted=downloader.start(job.downloadRequest(),url);
                require(admitted.value(QStringLiteral("accepted")).toBool());requestId=admitted.value(QStringLiteral("requestId")).toString();require(requestId.size()==32);};
            Job first(*core,selected);
            response=QByteArrayLiteral("HTTP/1.1 200 OK\r\nContent-Length: ")+QByteArray::number(expected.size())+QByteArrayLiteral("\r\nConnection: close\r\n\r\n")+expected;
            start(first);until([&]{return done;});require(success && error.isEmpty());
            require(first.status().value(QStringLiteral("downloadsPending")).toBool());
            require(first.status().value(QStringLiteral("counts")).toObject().value(QStringLiteral("ready")).toInt()==0);
            const auto imported=first.acknowledge(QStringLiteral("ready"),QJsonValue::Null);
            require(!imported.value(QStringLiteral("downloadsPending")).toBool());
            require(imported.value(QStringLiteral("counts")).toObject().value(QStringLiteral("mediaBytes")).toInteger()==expected.size());
            const auto finished=first.finish();require(finished.value(QStringLiteral("state")).toString()==QStringLiteral("partial"));
            const auto html=finished.value(QStringLiteral("htmlPath")).toString();require(QFileInfo(html).canonicalFilePath().startsWith(QDir(selected).canonicalPath()+QLatin1Char('/')));
            QDir media(QFileInfo(html).absolutePath()+QStringLiteral("/media"));const auto files=media.entryList(QDir::Files);require(files.size()==1);
            successfulMedia=media.absoluteFilePath(files.first());require(readFile(successfulMedia)==expected);
            require(readFile(html).contains("Synthetic text only"));
            Job missing(*core,selected);response=QByteArrayLiteral("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n");
            start(missing);until([&]{return done;});require(!success && error==QStringLiteral("not_available"));
            const auto absent=missing.acknowledge(QStringLiteral("error"),QStringLiteral("not_available"));require(!absent.value(QStringLiteral("downloadsPending")).toBool());
            require(absent.value(QStringLiteral("counts")).toObject().value(QStringLiteral("unavailable")).toInt()==1);
            require(missing.finish().value(QStringLiteral("state")).toString()==QStringLiteral("partial"));require(readFile(successfulMedia)==expected);
            Job cancelled(*core,selected);response=QByteArrayLiteral("HTTP/1.1 200 OK\r\nContent-Length: 100000000\r\n\r\npartial");
            start(cancelled);until([&]{return requests==3;});
            require(core->call(QStringLiteral("export.cancel"),cancelled.body()).value(QStringLiteral("downloadsPending")).toBool());
            require(downloader.cancel(requestId));until([&]{return done;});require(!success && error==QStringLiteral("cancelled"));
            const auto stopped=cancelled.acknowledge(QStringLiteral("cancelled"),QStringLiteral("cancelled"));
            require(stopped.value(QStringLiteral("state")).toString()==QStringLiteral("cancelled"));require(!stopped.value(QStringLiteral("downloadsPending")).toBool());
            require(!downloader.busy());require(readFile(successfulMedia)==expected);require(requests==3);
            require(QDir(selected).entryList(QDir::Dirs|QDir::NoDotAndDotDot).size()==2);
            server.close();
        }
        core.reset();require(!QFileInfo::exists(root+QStringLiteral("/synthetic-profile")));
        QJsonObject proof{{QStringLiteral("ok"),true},{QStringLiteral("checks"),checks},{QStringLiteral("syntheticLoopbackOnly"),true},
            {QStringLiteral("realRustSink"),true},{QStringLiteral("requests"),requests},{QStringLiteral("bodyBytes"),expected.size()},
            {QStringLiteral("bodySha256"),QString::fromLatin1(QCryptographicHash::hash(expected,QCryptographicHash::Sha256).toHex())},
            {QStringLiteral("historyComplete"),false},{QStringLiteral("realProfilesRead"),false},{QStringLiteral("windowsCredentialsUsed"),false}};
        std::cout<<QJsonDocument(proof).toJson(QJsonDocument::Compact).constData()<<std::endl;
    } catch(const std::exception& error) {std::cerr<<error.what()<<std::endl;return 1;}
}
