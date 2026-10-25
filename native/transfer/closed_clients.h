#pragma once
#include <atomic>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace valmax::transfer {
inline constexpr uint32_t kMaximumProcessEntries=65536,kSettleMs=2000,kWaitMs=120000,kPollMs=50;
enum class ScanCode:uint32_t {Complete,Incomplete,Cancelled,Deadline};
struct ClientScan {uint32_t candidates=0;bool complete=false;ScanCode code=ScanCode::Incomplete;};
struct ScanControl {
    const std::atomic_bool *cancelled=nullptr;
    uint64_t deadline_ms=UINT64_MAX; // GetTickCount64 domain, exclusive deadline.
};
enum class CandidateDisposition:uint32_t {Original,PotentialClient,Unknown,Cancelled,Deadline};
struct ProcessEntry {uint32_t pid=0;std::wstring name;};
// Host/test-only seam. Production overload always uses Toolhelp and immutable
// official-pair pins. No caller-provided path/hash/policy comes from QML.
class ScanSource {
public:
    virtual ~ScanSource()=default;
    virtual ScanCode snapshot(std::vector<ProcessEntry>&,const ScanControl&)=0;
    virtual CandidateDisposition classify(const ProcessEntry&,const ScanControl&)=0;
};
bool max_process_name(std::wstring_view) noexcept;
ClientScan scan_closed_clients(ScanSource&,const ScanControl& = {}) noexcept;
ClientScan scan_closed_clients(const ScanControl& = {}) noexcept;
// Reads only the explicit EXE and sibling config.dll, pins immutable below.
// Useful for independent original-pair proof; never proves process absence.
CandidateDisposition original_image(std::wstring_view path,std::wstring_view name,const ScanControl& = {}) noexcept;

enum class WaitState:uint32_t {Waiting,Closed,Cancelled,Deadline,ScanFailed};
// Pure state machine: caller performs scans/sleeps on its worker and repeats a
// fresh scan immediately before mutation under the same transfer mutex.
// Sampled absence does not prove no process existed BETWEEN observations.
// Windows I/O cannot be preempted: 120s is a retry policy, not hard call latency.
class ClosedWait {
public:
    explicit ClosedWait(uint64_t start_ms) noexcept;
    WaitState observe(const ClientScan&,uint64_t now_ms,bool cancelled=false) noexcept;
    WaitState state() const noexcept {return state_;}
    uint64_t deadline_ms() const noexcept {return deadline_;}
    uint32_t next_delay_ms(uint64_t now_ms) const noexcept;
private:
    uint64_t last_=0,deadline_=0,empty_since_=0;
    bool empty_=false;
    WaitState state_=WaitState::Waiting;
};
}
