#pragma once
#include <windows.h>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>
namespace valmax::launcher {
struct PayloadPin {const wchar_t *relative;uint64_t size;const char *sha256;};
enum class PreflightError:uint32_t {Ok,InvalidRoot,InvalidPin,MissingFile,UnsafePath,FileMismatch,IoError,UnexpectedEntry,Capacity};
const char *preflight_code(PreflightError) noexcept;
class VerifiedPayload {
public:
    VerifiedPayload()=default;
    ~VerifiedPayload();
    VerifiedPayload(const VerifiedPayload &)=delete;
    VerifiedPayload &operator=(const VerifiedPayload &)=delete;
    VerifiedPayload(VerifiedPayload &&) noexcept;
    VerifiedPayload &operator=(VerifiedPayload &&) noexcept;
    void release() noexcept;
    bool verified() const noexcept {return verified_;}
    uint32_t file_count() const noexcept {return static_cast<uint32_t>(files_.size());}
    const std::wstring &root() const noexcept {return root_;}
private:
    friend PreflightError verify_payload(std::wstring_view,std::span<const PayloadPin>,VerifiedPayload &) noexcept;
    friend PreflightError verify_native_payload(std::wstring_view,std::span<const PayloadPin>,VerifiedPayload &) noexcept;
    friend PreflightError verify_payload_impl(std::wstring_view,std::span<const PayloadPin>,VerifiedPayload &,bool) noexcept;
    std::wstring root_;
    std::vector<HANDLE> files_,directories_;
    bool verified_=false;
};
// The span must come from an immutable build-generated header, never a runtime
// user-supplied manifest. No file is created, loaded or executed. Successful
// output owns no-write/no-delete handles for files and no-delete directory
// handles for the future caller's entire critical startup interval.
// This verifies one fixed inventory, not runtime activation/protection readiness.
PreflightError verify_payload(std::wstring_view explicit_root,std::span<const PayloadPin>,VerifiedPayload &) noexcept;
// Production runtime/ tree: the required image is mod/max.exe. The ONLY
// unpinned mutable entry is mod/optimization-active.json (optional, regular,
// one link, <=4096 bytes). It is revalidated on each launch, never held across
// the trusted Rust atomic replacement. No directory or code-file exemptions.
PreflightError verify_native_payload(std::wstring_view explicit_root,std::span<const PayloadPin>,VerifiedPayload &) noexcept;
}
