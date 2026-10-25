#include "transfer_launch.h"
#include <QCoreApplication>
#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QThread>
#include <iostream>
#include <memory>
#include <stdexcept>
namespace {
int checks = 0;
void check(bool value) { ++checks; if (!value) throw std::runtime_error("transfer_launch_check"); }
void pump(int ms = 20) {
    QElapsedTimer timer; timer.start();
    while (timer.elapsed() < ms) { QCoreApplication::processEvents(); QThread::msleep(1); }
}
void write(const QString& path, const QByteArray& data) {
    QFile file(path); check(file.open(QIODevice::WriteOnly | QIODevice::NewOnly)); check(file.write(data) == data.size());
}
}
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    try {
        check(argc == 3);
        const auto fixture = QDir::cleanPath(QString::fromLocal8Bit(argv[1]));
        check(fixture.startsWith(QStringLiteral("D:/ValMAXBuild/build/qt-mdata-launch-")));
        const auto fake = QString::fromLocal8Bit(argv[2]);
        for (const auto& mode : {QStringLiteral("success"), QStringLiteral("confirmed"), QStringLiteral("page_destroyed"),
                                QStringLiteral("failure"), QStringLiteral("timeout"), QStringLiteral("missing")}) {
            const auto root = fixture + QLatin1Char('/') + mode;
            check(QDir().mkpath(root + QStringLiteral("/runtime/mod")));
            if (mode != QStringLiteral("missing")) check(QFile::copy(fake, root + QStringLiteral("/ValMAX.exe")));
            write(root + QStringLiteral("/mode.txt"), mode.toUtf8());
            int quits = 0, completions = 0, uiDeliveries = 0, reentrantAttempts = 0; bool launched = false;
            valmax::TransferLaunch transfer(root + QStringLiteral("/runtime/mod/max.exe"),
                mode == QStringLiteral("timeout") ? 50 : 2000, [&] { ++quits; });
            QObject::connect(&transfer, &valmax::TransferLaunch::handoffFinished, [&](bool ok, const QString&) { ++completions; launched = ok; });
            auto page = std::make_unique<QObject>();
            QObject::connect(&transfer, &valmax::TransferLaunch::handoffFinished, page.get(), [&] { ++uiDeliveries; });
            QObject::connect(&transfer, &valmax::TransferLaunch::stateChanged, [&] {
                if (!transfer.busy()) { ++reentrantAttempts; check(!transfer.confirmAndStartImport()); }
            });
            check(transfer.available()); check(!transfer.completeHandoff());
            check(mode == QStringLiteral("success") ? transfer.startImport() : transfer.confirmAndStartImport());
            check(!transfer.startImport()); check(!transfer.confirmAndStartImport()); check(!transfer.completeHandoff());
            if (mode == QStringLiteral("page_destroyed")) page.reset();
            QElapsedTimer timer; timer.start(); while (transfer.busy() && timer.elapsed() < 4000) pump(2);
            check(!transfer.busy() && completions == 1);
            check(reentrantAttempts == 1);
            check(uiDeliveries == (mode == QStringLiteral("page_destroyed") ? 0 : 1));
            if (mode == QStringLiteral("success") || mode == QStringLiteral("confirmed") || mode == QStringLiteral("page_destroyed")) {
                check(launched && transfer.state() == QStringLiteral("helper_launched"));
                if (mode == QStringLiteral("success")) { check(quits == 0); check(transfer.completeHandoff()); }
                pump(); check(quits == 1); check(!transfer.completeHandoff());
            } else {
                check(!launched && transfer.state() == QStringLiteral("error"));
                check(!transfer.completeHandoff()); pump(); check(quits == 0);
                check(transfer.error() == (mode == QStringLiteral("timeout") ? QStringLiteral("handoff_timeout")
                    : mode == QStringLiteral("missing") ? QStringLiteral("launcher_unavailable") : QStringLiteral("handoff_failed")));
            }
            if (mode != QStringLiteral("missing")) {
                QFile marker(root + QStringLiteral("/called.txt")); check(marker.open(QIODevice::ReadOnly)); check(marker.readAll() == "mdata");
            }
            pump(300); // The owned fake timeout child exits naturally; never kill it.
        }
        int quits = 0;
        valmax::TransferLaunch invalid(fixture + QStringLiteral("/unrelated/max.exe"), 100, [&] { ++quits; });
        check(!invalid.available()); check(!invalid.startImport()); check(!invalid.completeHandoff()); check(quits == 0);
        std::cout << QJsonDocument(QJsonObject{{QStringLiteral("ok"), true}, {QStringLiteral("checks"), checks},
            {QStringLiteral("realClientStarted"), false}, {QStringLiteral("realCredentialsUsed"), false},
            {QStringLiteral("importSuccessTested"), false}}).toJson(QJsonDocument::Compact).constData() << std::endl;
        return 0;
    } catch (...) { std::cout << "{\"ok\":false,\"error\":\"transfer_launch_test_failed\"}" << std::endl; return 1; }
}
