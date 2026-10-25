#pragma once
#include "service_host.h"
#include <QByteArray>
#include <memory>
#include <optional>

namespace valmax::media {
enum class MediaPrepareCode { Ok, InvalidConfig, WrongThread, CoreUnavailable,
    CoreMismatch, AbiMismatch, UnsafePath, StorageUnavailable, InternalError };
const char* media_prepare_code(MediaPrepareCode) noexcept;
class MediaRuntimeResources;
struct PreparedMediaRuntime {
    ServiceConfig service;
    // Keep this shared lease alive until the service's asynchronous run has
    // fully stopped (including after facade destruction). It pins the loaded
    // core module, its immutable disk file, and the staging-root ancestors.
    std::shared_ptr<const MediaRuntimeResources> resources;
};
struct MediaPrepareResult {
    MediaPrepareCode code = MediaPrepareCode::InternalError;
    std::optional<PreparedMediaRuntime> value;
    explicit operator bool() const noexcept { return code==MediaPrepareCode::Ok&&value.has_value(); }
};
// Native host only. Run on a non-GUI worker; filesystem/hash calls can block.
// Input is the launcher's exact <=16KiB JSON with precisely profileDirectory,
// runtimeDirectory, existingClient. No environment/profile discovery or QML
// setter; no runtime_open/settings mutation. Creates only one fixed child of
// an already existing explicit profile: inline-media-native-v1.
// Checks the already imported core's identity; this is not initial-load safety
// or a claim to detect arbitrary modifications of executable memory.
MediaPrepareResult prepare_media_runtime(const QByteArray& exactHostConfig) noexcept;
} // namespace valmax::media
