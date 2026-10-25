#include "export_downloader.h"
#include "valmax_core.h"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QHostAddress>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTcpServer>
#include <QTcpSocket>
#include <QThread>
#include <QTimer>
#include <atomic>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <source_location>

struct ValmaxExportDownload { QByteArray bytes; };
namespace {
int checks = 0;
std::atomic<int> opened{0}, closed{0}, rejectWrites{0};
std::mutex dataMutex;
QByteArray written;
void require(bool condition, const std::source_location location = std::source_location::current()) {
    ++checks;
    if (!condition) throw std::runtime_error("failed assertion at line " + std::to_string(location.line()));
}
template<class F> void until(F test) {
    QElapsedTimer clock; clock.start();
    while (!test() && clock.elapsed() < 6000) {
        QCoreApplication::processEvents(); QThread::msleep(1);
    }
    require(test());
}
QString job() {
    return QString::fromUtf8(QJsonDocument(QJsonObject{{QStringLiteral("jobId"), QString(32, QLatin1Char('a'))},
        {QStringLiteral("jobToken"), QString(64, QLatin1Char('b'))},
        {QStringLiteral("messageId"), QStringLiteral("m1")}, {QStringLiteral("attachmentId"), QStringLiteral("a1")}}).toJson(QJsonDocument::Compact));
}
}
extern "C" uint32_t valmax_export_download_open(const ValmaxRuntime* runtime, const uint8_t* json, size_t length, ValmaxExportDownload** output) {
    const auto value = QJsonDocument::fromJson(QByteArray(reinterpret_cast<const char*>(json), static_cast<qsizetype>(length)));
    if (!runtime || !output || *output || !value.isObject() || value.object().size() != 4) return 1;
    *output = new ValmaxExportDownload; ++opened; return 0;
}
extern "C" uint32_t valmax_export_download_write(ValmaxExportDownload* sink, const uint8_t* data, size_t length) {
    if (!sink || !data || length == 0 || length > 1024 * 1024) return 1;
    if (rejectWrites) return 7;
    sink->bytes.append(reinterpret_cast<const char*>(data), static_cast<qsizetype>(length)); return 0;
}
extern "C" uint32_t valmax_export_download_close(ValmaxExportDownload** sink) {
    if (sink && *sink) {
        { const std::lock_guard lock(dataMutex); written = (*sink)->bytes; }
        delete *sink; *sink = nullptr; ++closed;
    }
    return 0;
}

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    try {
        QTcpServer server;
        require(server.listen(QHostAddress::LocalHost));
        QByteArray response;
        int requests = 0;
        QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
            while (auto* socket = server.nextPendingConnection()) {
                QObject::connect(socket, &QIODevice::readyRead, socket, [&, socket] {
                    const auto data = socket->readAll();
                    if (!data.startsWith("GET ")) return;
                    ++requests;
                    socket->write(response); socket->flush();
                });
                QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
            }
        });
        const auto url = QStringLiteral("http://127.0.0.1:") + QString::number(server.serverPort()) + QStringLiteral("/synthetic");
        valmax::ExportDownloader downloader;
        require(!downloader.available());
        require(!downloader.start(job(), url).value(QStringLiteral("accepted")).toBool());
        downloader.setRuntime(reinterpret_cast<void*>(1), std::make_shared<int>(0));
        require(downloader.available());
        for (const auto& bad : {QStringLiteral("file:///D:/private"), QStringLiteral("http://example.com/a"),
                               QStringLiteral("https://user:pass@example.com/a"), QStringLiteral("https://example.com:444/a")})
            require(!downloader.start(job(), bad).value(QStringLiteral("accepted")).toBool());
        require(!downloader.start(job(), url).value(QStringLiteral("accepted")).toBool());
        downloader.allowLoopbackForTest();
        bool done = false, success = false;
        QString error, requestId;
        QObject::connect(&downloader, &valmax::ExportDownloader::completed, &app,
            [&](const QString& id, bool ok, const QString& code) {
                require(id == requestId); require(opened == closed);
                done = true; success = ok; error = code;
            });
        auto start = [&] {
            done = false; success = false; error.clear();
            const auto admitted = downloader.start(job(), url);
            require(admitted.value(QStringLiteral("accepted")).toBool());
            requestId = admitted.value(QStringLiteral("requestId")).toString(); require(requestId.size() == 32);
        };
        response = QByteArrayLiteral("HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nhello!");
        start();
        require(!downloader.start(job(), url).value(QStringLiteral("accepted")).toBool());
        until([&] { return done; }); require(success && error.isEmpty());
        { const std::lock_guard lock(dataMutex); require(written == QByteArrayLiteral("hello!")); }
        require(!downloader.busy());
        response = QByteArrayLiteral("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n");
        start(); until([&] { return done; }); require(!success && error == QStringLiteral("not_available"));
        response = QByteArrayLiteral("HTTP/1.1 200 OK\r\nContent-Length: 17179869185\r\n\r\n");
        start(); until([&] { return done; }); require(!success && error == QStringLiteral("too_large"));
        response = QByteArrayLiteral("HTTP/1.1 302 Found\r\nLocation: file:///D:/private\r\nContent-Length: 0\r\n\r\n");
        start(); until([&] { return done; }); require(!success && error == QStringLiteral("invalid_url"));
        response = QByteArrayLiteral("HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nhello!");
        rejectWrites = 1;
        start(); until([&] { return done; }); require(!success && error == QStringLiteral("no_space")); rejectWrites = 0;
        response = QByteArrayLiteral("HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\npartial");
        start();
        require(!downloader.cancel(QStringLiteral("other-task")));
        QTimer::singleShot(30, &app, [&] { require(downloader.cancel(requestId)); });
        until([&] { return done; }); require(!success && error == QStringLiteral("cancelled"));
        require(opened == 6 && closed == 6 && requests == 6);
        server.close();
        std::cout << "{\"ok\":true,\"checks\":" << checks << ",\"syntheticLoopbackOnly\":true,\"realFilesWritten\":false}" << std::endl;
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl; return 1;
    }
}
