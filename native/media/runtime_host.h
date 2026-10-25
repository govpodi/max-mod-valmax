#pragma once
#include <QByteArray>
#include <QQmlEngine>

namespace valmax::media {
enum class MediaRuntimeState { Absent, Preparing, Ready, Failed, Stopping, Stopped };
struct MediaRuntimeSnapshot {
    MediaRuntimeState state = MediaRuntimeState::Absent;
    int engines = 0;
    bool preparing = false;
    bool drained = true;
};
// Native host only; never registered with QML. The first nonempty launcher
// config is immutable for this QApplication lifetime. Hashing and directory
// preparation run on a worker. There is one service across all QML engines.
bool attach_media_runtime(QQmlEngine*, const QByteArray& exactHostConfig);
MediaRuntimeSnapshot media_runtime_snapshot();
// Normal shutdown is asynchronous. Quit events are delayed until the service
// has drained; explicit QCoreApplication::exit and application destruction use
// a final event-pumping drain. OS/PAC/filesystem calls have no hard time bound.
void shutdown_media_runtime();
} // namespace valmax::media
