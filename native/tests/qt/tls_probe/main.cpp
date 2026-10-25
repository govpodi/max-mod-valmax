#include <QCoreApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSslSocket>
#include <iostream>
#include <windows.h>
int main(int argc, char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    QCoreApplication application(argc, argv);
    const auto backends = QSslSocket::availableBackends();
    const auto backend = QSslSocket::activeBackend();
    const auto supported = QSslSocket::supportsSsl();
    wchar_t path[32768]{};
    const auto module = GetModuleHandleW(L"qschannelbackend.dll");
    const auto length = module ? GetModuleFileNameW(module, path, 32768) : 0;
    const bool ok = supported && backend == QStringLiteral("schannel") && backends.contains(QStringLiteral("schannel"))
        && length > 0 && length < 32768;
    const QJsonObject proof{{QStringLiteral("ok"), ok}, {QStringLiteral("checks"), 4},
        {QStringLiteral("supportsSsl"), supported}, {QStringLiteral("activeBackend"), backend},
        {QStringLiteral("availableBackends"), QJsonArray::fromStringList(backends)},
        {QStringLiteral("loadedSchannel"), length > 0 && length < 32768 ? QString::fromWCharArray(path, int(length)) : QString()},
        {QStringLiteral("networkRequestsSent"), false}};
    std::cout << QJsonDocument(proof).toJson(QJsonDocument::Compact).constData() << std::endl;
    return ok ? 0 : 1;
}
