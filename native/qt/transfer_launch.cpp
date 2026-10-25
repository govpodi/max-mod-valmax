#include "transfer_launch.h"
#include <QCoreApplication>
#include <QDir>
#include <QEvent>
#include <QFileInfo>
#include <QPointer>
#include <QRunnable>
#include <QThread>
#include <QTimer>
#include <windows.h>
#include <array>
#include <string>
#include <vector>
#include <memory>

namespace valmax {
namespace {
struct Handle {
    HANDLE value = INVALID_HANDLE_VALUE;
    explicit Handle(HANDLE input = INVALID_HANDLE_VALUE) : value(input) {}
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
};
bool same(const std::wstring& a, const std::wstring& b) {
    return a.size() < 32768 && b.size() < 32768
        && CompareStringOrdinal(a.c_str(), static_cast<int>(a.size()), b.c_str(), static_cast<int>(b.size()), TRUE) == CSTR_EQUAL;
}
bool ordinary(HANDLE file, const std::wstring& expected, bool directory) {
    BY_HANDLE_FILE_INFORMATION info{};
    if (!GetFileInformationByHandle(file, &info) || GetFileType(file) != FILE_TYPE_DISK
        || (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)
        || bool(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != directory
        || (!directory && info.nNumberOfLinks != 1)) return false;
    std::array<wchar_t, 32768> final{};
    const auto length = GetFinalPathNameByHandleW(file, final.data(), static_cast<DWORD>(final.size()), 0);
    return length && length < final.size() && same(std::wstring(final.data(), length), L"\\\\?\\" + expected);
}
QString launch(const QString& name, int timeoutMs) {
    // Pin every ancestor and this exact launcher across creation and its exit.
    // The launcher then verifies its own immutable payload, including the helper.
    const auto path = QDir::toNativeSeparators(name).toStdWString();
    std::vector<std::unique_ptr<Handle>> parents;
    for (size_t at = 3; at < path.size(); ++at) {
        if (path[at] != L'\\') continue;
        const auto part = path.substr(0, at);
        auto pin = std::make_unique<Handle>(CreateFileW(part.c_str(), FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
        if (!ordinary(pin->value, part, true)) return QStringLiteral("launcher_unavailable");
        parents.push_back(std::move(pin));
    }
    Handle pin(CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                           FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
    if (!ordinary(pin.value, path, false)) return QStringLiteral("launcher_unavailable");
    std::wstring command = L"\"" + path + L"\" mdata";
    const auto directory = QFileInfo(name).absolutePath().toStdWString();
    STARTUPINFOW startup{}; startup.cb = sizeof(startup); PROCESS_INFORMATION process{};
    if (!CreateProcessW(path.c_str(), command.data(), nullptr, nullptr, FALSE,
                        CREATE_DEFAULT_ERROR_MODE, nullptr, directory.c_str(), &startup, &process))
        return QStringLiteral("spawn_failed");
    Handle child(process.hProcess), thread(process.hThread);
    // No kill on failure/timeout: a launcher may already have opened the helper.
    // The existing client is closed only after a normal successful exit.
    const auto waited = WaitForSingleObject(child.value, static_cast<DWORD>(timeoutMs));
    if (waited == WAIT_TIMEOUT) return QStringLiteral("handoff_timeout");
    DWORD code = 0;
    if (waited != WAIT_OBJECT_0 || !GetExitCodeProcess(child.value, &code)) return QStringLiteral("handoff_failed");
    return code == 0 ? QString() : QStringLiteral("handoff_failed");
}
}
QString TransferLaunch::launcherForImage(const QString& image) {
    const auto normalized = QDir::cleanPath(QDir::fromNativeSeparators(image));
    const QFileInfo executable(normalized);
    if (!executable.isAbsolute() || executable.fileName().compare(QStringLiteral("max.exe"), Qt::CaseInsensitive) != 0)
        return {};
    QDir mod = executable.absoluteDir();
    if (mod.dirName().compare(QStringLiteral("mod"), Qt::CaseInsensitive) != 0 || !mod.cdUp()
        || mod.dirName().compare(QStringLiteral("runtime"), Qt::CaseInsensitive) != 0 || !mod.cdUp()) return {};
    const auto path = mod.absoluteFilePath(QStringLiteral("ValMAX.exe"));
    return path.size() < 32000 && path.size() > 3 && path[1] == QLatin1Char(':')
        && !path.contains(QChar(0)) ? path : QString();
}
TransferLaunch::TransferLaunch(QObject* parent) : QObject(parent), launcher_(launcherForImage(QCoreApplication::applicationFilePath())),
    quit_([] { QCoreApplication::quit(); }) {
    workers_.setMaxThreadCount(1);
}
#ifdef VALMAX_TRANSFER_LAUNCH_TESTING
TransferLaunch::TransferLaunch(const QString& image, int timeoutMs, std::function<void()> quit, QObject* parent)
    : QObject(parent), launcher_(launcherForImage(image)), timeoutMs_(timeoutMs), quit_(std::move(quit)) { workers_.setMaxThreadCount(1); }
#endif
TransferLaunch::~TransferLaunch() {
    workers_.waitForDone();
    QCoreApplication::removePostedEvents(this, QEvent::MetaCall);
}
bool TransferLaunch::startImport() { return begin(false); }
bool TransferLaunch::confirmAndStartImport() { return begin(true); }
bool TransferLaunch::begin(bool quitAfterSuccess) {
    if (QThread::currentThread() != thread() || busy_ || completing_ || state_ == QStringLiteral("helper_launched")) return false;
    if (!available()) {
        state_ = QStringLiteral("error"); error_ = QStringLiteral("launcher_unavailable");
        const auto result = error_;
        const QPointer<TransferLaunch> alive(this); emit stateChanged();
        if (alive) emit handoffFinished(false, result);
        return false;
    }
    quitAfterSuccess_ = quitAfterSuccess;
    busy_ = true; state_ = QStringLiteral("launching"); error_.clear();
    const QPointer<TransferLaunch> alive(this); emit stateChanged(); if (!alive) return false;
    const auto executable = launcher_; const auto timeout = timeoutMs_;
    workers_.start(QRunnable::create([this, executable, timeout]() {
        QString result;
        try { result = launch(executable, timeout); }
        catch (...) { result = QStringLiteral("handoff_failed"); }
        QMetaObject::invokeMethod(this, [this, result]() {
            const bool quitIntent = quitAfterSuccess_; quitAfterSuccess_ = false; completing_ = true;
            busy_ = false; error_ = result;
            state_ = result.isEmpty() ? QStringLiteral("helper_launched") : QStringLiteral("error");
            const QPointer<TransferLaunch> alive(this); emit stateChanged();
            if (alive) emit handoffFinished(result.isEmpty(), result);
            // The user's confirmation belongs to this engine-owned C++ object,
            // not the originating settings/login page, which may be gone now.
            if (alive) {
                completing_ = false;
                if (result.isEmpty() && quitIntent) completeHandoff();
            }
        }, Qt::QueuedConnection);
    }));
    return true;
}
bool TransferLaunch::completeHandoff() {
    if (QThread::currentThread() != thread() || busy_ || state_ != QStringLiteral("helper_launched")
        || quitQueued_ || !QCoreApplication::instance()) return false;
    quitQueued_ = true;
    // Queued graceful shutdown of this client only, after explicit confirmation
    // and a successful helper-launch acknowledgement. No tray-close shortcut.
    QTimer::singleShot(0, QCoreApplication::instance(), [quit = quit_]() { quit(); });
    return true;
}
}
