#pragma once
#include "../core/valmax_core.h"
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>

namespace valmax::media {
// Host-only function table, resolved from one verified, already loaded DLL.
// The owner must keep that DLL loaded until all jobs, buffers and concurrent
// signal() calls have finished. This wrapper performs no module/path discovery.
struct StagingApi {
    decltype(&valmax_media_staging_abi_version) version = nullptr;
    decltype(&valmax_media_staging_begin) begin = nullptr;
    decltype(&valmax_media_staging_stage) stage = nullptr;
    decltype(&valmax_media_staging_signal_cancel) signal_cancel = nullptr;
    decltype(&valmax_media_staging_cancel) cancel = nullptr;
    decltype(&valmax_media_staging_retain) retain = nullptr;
    decltype(&valmax_media_staging_release) release = nullptr;
    decltype(&valmax_core_buffer_free) free_buffer = nullptr;
};
enum class StagingCode : uint32_t {
    Ok=0, InvalidInput=1, UnsupportedMedia=2, FileTooLarge=3, CacheFull=4,
    Busy=5, UnsafePath=6, Unavailable=7, NoSpace=8, Cancelled=9,
    CleanupFailed=10, UnsupportedPlatform=11, InvalidHandle=12,
    InvalidState=13, HandleLimit=14, InternalError=15,
    CoreUnavailable=16, AbiMismatch=17
};
enum class StagingMime : uint32_t { Jpeg=1, Png=2, Gif=3, Webp=4, Webm=5, Mp4=6 };
const char *staging_code(StagingCode) noexcept;
template<class T> struct StagingResult {
    StagingCode code = StagingCode::InternalError;
    std::optional<T> value;
    explicit operator bool() const noexcept { return code == StagingCode::Ok && value.has_value(); }
};
namespace detail { struct StagingControl; }
class StagingJob;

// Copies may signal from another thread, including concurrently with an I/O
// operation or destruction of their owner. This is cancellation only: it does
// not do filesystem cleanup, does not expose a token, and never owns a lease.
class StagingCancellation final {
public:
    StagingCode signal() const noexcept;
private:
    friend class StagingJob;
    explicit StagingCancellation(std::weak_ptr<detail::StagingControl> state) noexcept : state_(std::move(state)) {}
    std::weak_ptr<detail::StagingControl> state_;
};

// Move-only native draft lease. All methods/destruction run serially on an I/O
// thread; only the separate StagingCancellation may be used concurrently.
// The URL stays native: never publish it to QML, JSON, logs or diagnostics.
// A signal can revoke its usability while this metadata copy still exists.
// Before handoff: copy the URL into the native-only delivery DTO, recheck the
// controller account/page/search stamp, retain on the I/O thread, then recheck
// the GUI generation before native dispatch. Retain clears this object's URL.
// Retain is terminal ownership transfer to the TTL cache, not an attachment
// ACK. Failed/suppressed dispatch after retain leaves a quota-charged TTL file.
// Retain neither attaches nor sends anything.
class PreparedFile final {
public:
    PreparedFile(PreparedFile&&) noexcept;
    PreparedFile& operator=(PreparedFile&&) noexcept;
    ~PreparedFile();
    PreparedFile(const PreparedFile&) = delete;
    PreparedFile& operator=(const PreparedFile&) = delete;
    std::string_view native_file_url() const noexcept { return url_; }
    uint64_t byte_length() const noexcept { return bytes_; }
    bool cancellation_requested() const noexcept;
    StagingCode cancel() noexcept;
    StagingCode retain_for_handoff() noexcept;
private:
    friend class StagingJob;
    PreparedFile(std::shared_ptr<detail::StagingControl>, std::string, uint64_t) noexcept;
    void reset() noexcept;
    std::shared_ptr<detail::StagingControl> state_;
    std::string url_;
    uint64_t bytes_ = 0;
};

// Explicit host-configured existing staging root; never a path supplied by UI.
// begin/stage/cancel/destruction may block in filesystem syscalls: the caller
// MUST use its I/O worker, not the GUI thread. No callback or worker is created.
// stage borrows bytes until its synchronous return, even after cancellation.
// Failed native stage is terminal; create a fresh job to retry. A local input
// validation failure does not consume it. Success transfers ownership to the
// PreparedFile. Destructor cleanup is best-effort; explicit cancel reports it.
// The same C++ object must not be called/moved/destroyed concurrently.
class StagingJob final {
public:
    static StagingResult<StagingJob> begin(const StagingApi&, std::wstring_view explicit_root) noexcept;
    StagingJob(StagingJob&&) noexcept;
    StagingJob& operator=(StagingJob&&) noexcept;
    ~StagingJob();
    StagingJob(const StagingJob&) = delete;
    StagingJob& operator=(const StagingJob&) = delete;
    StagingCancellation cancellation() const noexcept { return StagingCancellation(state_); }
    StagingResult<PreparedFile> stage(std::span<const uint8_t> bytes, StagingMime,
        uint64_t expected_bytes, uint64_t now_unix_seconds) noexcept;
    StagingCode cancel() noexcept;
private:
    explicit StagingJob(std::shared_ptr<detail::StagingControl> state) noexcept : state_(std::move(state)) {}
    void reset() noexcept;
    std::shared_ptr<detail::StagingControl> state_;
    bool attempted_ = false;
};
} // namespace valmax::media
