#include "staging_client.h"
#include <windows.h>
#include <atomic>
#include <cstddef>
#include <utility>

namespace valmax::media {
static_assert(sizeof(ValmaxMediaStageRequest)==32 && sizeof(ValmaxMediaStageResult)==40);
static_assert(offsetof(ValmaxMediaStageRequest,expected_bytes)==16 && offsetof(ValmaxMediaStageRequest,now_unix_seconds)==24);
static_assert(offsetof(ValmaxMediaStageResult,byte_length)==8 && offsetof(ValmaxMediaStageResult,file_url)==16);
namespace {
constexpr size_t MaxBytes = 25*1024*1024;
constexpr size_t MaxRoot = 31000;
constexpr size_t MaxUrl = 96000;
StagingCode decoded(uint32_t value) noexcept {
    return value <= VALMAX_MEDIA_INTERNAL_ERROR ? static_cast<StagingCode>(value) : StagingCode::AbiMismatch;
}
bool complete(const StagingApi& a) noexcept {
    return a.version && a.begin && a.stage && a.signal_cancel && a.cancel && a.retain && a.release && a.free_buffer;
}
// The Rust encoder emits only ASCII URI bytes; retain percent encoding rather
// than normalize or turn this into another filesystem path in the wrapper.
bool native_url(const ValmaxCoreBuffer& b) noexcept {
    if (!b.data || b.len < 12 || b.len > MaxUrl || b.capacity < b.len) return false;
    const std::string_view u(reinterpret_cast<const char*>(b.data), b.len);
    if (!u.starts_with("file:///")) return false;
    const auto alpha=[](char c){return (c>='A'&&c<='Z')||(c>='a'&&c<='z');};
    const auto hex=[](char c){return (c>='0'&&c<='9')||(c>='A'&&c<='F');};
    if (!alpha(u[8]) || u[9]!=':' || u[10]!='/') return false;
    for(size_t i=11; i<u.size(); ++i) {
        const char c=u[i];
        if(alpha(c)||(c>='0'&&c<='9')||std::string_view("-._~/").find(c)!=std::string_view::npos) continue;
        if(c=='%' && i+2<u.size() && hex(u[i+1]) && hex(u[i+2])) { i+=2; continue; }
        return false;
    }
    return true;
}
struct OwnedBuffer {
    StagingApi api;
    ValmaxCoreBuffer* value;
    ~OwnedBuffer() { try { api.free_buffer(value); } catch (...) {} }
};
}
namespace detail {
struct StagingControl final {
    StagingApi api;
    std::atomic<uint64_t> token;
    std::atomic<bool> cancelled{false};
    StagingControl(const StagingApi& a, uint64_t t) noexcept : api(a), token(t) {}
    ~StagingControl() { (void)release(); }
    StagingCode signal() noexcept {
        cancelled.store(true, std::memory_order_release);
        const auto t=token.load(std::memory_order_acquire);
        if(!t) return StagingCode::InvalidHandle;
        try { return decoded(api.signal_cancel(t)); } catch (...) { return StagingCode::InternalError; }
    }
    StagingCode release() noexcept {
        const auto t=token.exchange(0,std::memory_order_acq_rel);
        if(!t) return StagingCode::Ok;
        try { return decoded(api.release(t)); } catch (...) { return StagingCode::InternalError; }
    }
};
}
const char* staging_code(StagingCode value) noexcept {
    constexpr const char* names[]={"ok","invalid_request","unsupported_media","file_too_large","cache_full",
        "download_busy","unsafe_path","storage_unavailable","no_space","cancelled","cleanup_failed",
        "unsupported_platform","invalid_handle","invalid_state","handle_limit","internal_error",
        "core_unavailable","core_abi_mismatch"};
    const auto index=static_cast<uint32_t>(value);
    return index<std::size(names) ? names[index] : "internal_error";
}
StagingCode StagingCancellation::signal() const noexcept {
    if(const auto state=state_.lock()) return state->signal();
    return StagingCode::InvalidHandle;
}
StagingResult<StagingJob> StagingJob::begin(const StagingApi& api, std::wstring_view root) noexcept {
    if(!complete(api)) return {StagingCode::CoreUnavailable,{}};
    if(root.empty() || root.size()>MaxRoot || root.find(L'\0')!=root.npos) return {StagingCode::InvalidInput,{}};
    uint64_t token=0;
    try {
        if(api.version()!=1) return {StagingCode::AbiMismatch,{}};
        const auto n=static_cast<int>(root.size());
        const int size=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,root.data(),n,nullptr,0,nullptr,nullptr);
        if(size<=0 || static_cast<size_t>(size)>MaxRoot) return {StagingCode::InvalidInput,{}};
        std::string utf8(static_cast<size_t>(size),'\0');
        if(WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,root.data(),n,utf8.data(),size,nullptr,nullptr)!=size)
            return {StagingCode::InvalidInput,{}};
        const auto code=decoded(api.begin(reinterpret_cast<const uint8_t*>(utf8.data()),utf8.size(),&token));
        if(code!=StagingCode::Ok || !token) {
            if(token) { (void)api.release(token); return {StagingCode::AbiMismatch,{}}; }
            return {code==StagingCode::Ok ? StagingCode::AbiMismatch : code,{}};
        }
        auto control=std::make_shared<detail::StagingControl>(api,token);
        token=0;
        return {StagingCode::Ok,StagingJob(std::move(control))};
    } catch(...) {
        if(token) { try { (void)api.release(token); } catch(...) {} }
        return {StagingCode::InternalError,{}};
    }
}
StagingJob::StagingJob(StagingJob&& other) noexcept : state_(std::move(other.state_)),attempted_(other.attempted_) {}
StagingJob& StagingJob::operator=(StagingJob&& other) noexcept {
    if(this!=&other) { reset(); state_=std::move(other.state_); attempted_=other.attempted_; } return *this;
}
StagingJob::~StagingJob() { reset(); }
void StagingJob::reset() noexcept { if(state_) (void)state_->release(); state_.reset(); }
StagingCode StagingJob::cancel() noexcept {
    if(!state_) return StagingCode::Ok;
    state_->cancelled.store(true,std::memory_order_release);
    const auto token=state_->token.load(std::memory_order_acquire);
    StagingCode code=StagingCode::InvalidHandle;
    try { if(token) code=decoded(state_->api.cancel(token)); } catch(...) { code=StagingCode::InternalError; }
    const auto released=state_->release(); state_.reset();
    return code==StagingCode::Ok ? released : code;
}
StagingResult<PreparedFile> StagingJob::stage(std::span<const uint8_t> bytes, StagingMime mime,
    uint64_t expected, uint64_t now) noexcept {
    if(!state_) return {StagingCode::InvalidHandle,{}};
    if(attempted_) return {StagingCode::InvalidState,{}};
    if(state_->cancelled.load(std::memory_order_acquire)) return {StagingCode::Cancelled,{}};
    if(bytes.size()>MaxBytes || expected>MaxBytes) return {StagingCode::FileTooLarge,{}};
    if(bytes.empty() || expected!=bytes.size() || static_cast<uint32_t>(mime)<1 || static_cast<uint32_t>(mime)>6)
        return {StagingCode::InvalidInput,{}};
    ValmaxMediaStageRequest request{32,1,static_cast<uint32_t>(mime),0,expected,now};
    ValmaxMediaStageResult result{};
    OwnedBuffer buffer{state_->api,&result.file_url};
    attempted_=true;
    try {
        const auto code=decoded(state_->api.stage(state_->token.load(std::memory_order_acquire),&request,
            bytes.data(),bytes.size(),&result));
        if(result.size!=40 || result.version!=1) { reset(); return {StagingCode::AbiMismatch,{}}; }
        if(code!=StagingCode::Ok) {
            if(result.byte_length || result.file_url.data || result.file_url.len || result.file_url.capacity) {
                reset(); return {StagingCode::AbiMismatch,{}};
            }
            return {code,{}};
        }
        if(result.byte_length!=expected || !native_url(result.file_url)) { reset(); return {StagingCode::AbiMismatch,{}}; }
        if(state_->cancelled.load(std::memory_order_acquire)) {
            const auto cleaned=cancel();
            return {cleaned==StagingCode::Ok ? StagingCode::Cancelled : cleaned,{}};
        }
        std::string url(reinterpret_cast<const char*>(result.file_url.data),result.file_url.len);
        return {StagingCode::Ok,PreparedFile(std::move(state_),std::move(url),result.byte_length)};
    } catch(...) { reset(); return {StagingCode::InternalError,{}}; }
}
PreparedFile::PreparedFile(std::shared_ptr<detail::StagingControl> s,std::string url,uint64_t n) noexcept
    :state_(std::move(s)),url_(std::move(url)),bytes_(n) {}
PreparedFile::PreparedFile(PreparedFile&& other) noexcept
    :state_(std::move(other.state_)),url_(std::move(other.url_)),bytes_(std::exchange(other.bytes_,0)) { other.url_.clear(); }
PreparedFile& PreparedFile::operator=(PreparedFile&& other) noexcept {
    if(this!=&other) { reset(); state_=std::move(other.state_); url_=std::move(other.url_);
        bytes_=std::exchange(other.bytes_,0); other.url_.clear(); } return *this;
}
PreparedFile::~PreparedFile() { reset(); }
void PreparedFile::reset() noexcept { if(state_) (void)state_->release(); state_.reset(); url_.clear(); bytes_=0; }
bool PreparedFile::cancellation_requested() const noexcept {
    return !state_ || state_->cancelled.load(std::memory_order_acquire);
}
StagingCode PreparedFile::cancel() noexcept {
    if(!state_) return StagingCode::Ok;
    state_->cancelled.store(true,std::memory_order_release);
    StagingCode code=StagingCode::InvalidHandle;
    try { code=decoded(state_->api.cancel(state_->token.load(std::memory_order_acquire))); }
    catch(...) { code=StagingCode::InternalError; }
    const auto released=state_->release(); state_.reset(); url_.clear(); bytes_=0;
    return code==StagingCode::Ok ? released : code;
}
StagingCode PreparedFile::retain_for_handoff() noexcept {
    if(!state_) return StagingCode::InvalidState;
    if(cancellation_requested()) {
        const auto cleaned=cancel();
        return cleaned==StagingCode::Ok ? StagingCode::Cancelled : cleaned;
    }
    StagingCode code=StagingCode::InternalError;
    try { code=decoded(state_->api.retain(state_->token.load(std::memory_order_acquire))); }
    catch(...) {}
    const auto released=state_->release(); state_.reset(); url_.clear(); bytes_=0;
    return released==StagingCode::CleanupFailed ? released : code==StagingCode::Ok ? released : code;
}
} // namespace valmax::media
