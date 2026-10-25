#include "retention_passive_host.h"

#include "ghost_internal.h"
#include "retention_store_api.h"
#include "frida-gum.h"
#ifndef VALMAX_RETENTION_HOST_TESTING
#include "retention_pins.generated.h"
#endif

#include <windows.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace valmax::retention::passive {
namespace {
constexpr size_t kOwners = 128;
constexpr uintptr_t kCoreOwnerVtable = 0x18de708;
constexpr uintptr_t kStorageDataVtable = 0x983ab8;
constexpr uintptr_t kStorageHistoryOwnerVtable = 0x985058;
constexpr uintptr_t kStorageFactoryVtable = 0x9849c8;
constexpr uintptr_t kStorageChatHistoryVtable = 0x9895d8;

struct Runtime final {
    // Static process lifetime is intentional: Gum callbacks and worker data
    // must outlive a failed detach or a second QML engine.
    std::mutex lifecycle, scope, events;
    std::atomic<bool> callbacks_active{false};
    bool attempted = false, installed = false, images_exact = false;
    bool owner_join = false, store_ready = false, preference_enabled = false;
    bool applied = false, closing = false;
    uint32_t listeners_attached = 0;
    uint64_t generation = 0, owner_constructed = 0, joined_upserts = 0, route_failures = 0;
    std::array<char,64> revision{};
    HostError error = HostError::NotInstalled;
    std::array<uintptr_t,kOwners> owners{};
    size_t owner_count = 0;
    std::wstring profile_key, core_path, storage_path;
    std::vector<uint16_t> profile_units;
    struct Layout final {
        uintptr_t owner=0,data=0,history_owner=0,factory=0,chat_history=0;
    } layout;
    ghost::VerifiedImage core, storage;
    GumInterceptor *interceptor = nullptr;
    std::array<GumInvocationListener *,3> listeners{};
    std::unique_ptr<pilot::AsyncWorker> worker;
};
Runtime &runtime() { static Runtime *value = new Runtime; return *value; }
bool install_error(Runtime &r,HostError error) noexcept {
    std::lock_guard lock(r.scope);
    r.error=error;
    return false;
}

void increment(uint64_t &value) noexcept { if (value < 1000000) ++value; }
template<class T> bool read_at(uintptr_t base, size_t offset, T &value) noexcept {
    return base != 0 && base <= UINTPTR_MAX-offset && ghost::read(base+offset,value);
}
bool valid_path(std::wstring_view value) noexcept {
    return value.size() >= 4 && value.size() <= 32768 && value[1] == L':'
        && (value[2] == L'\\' || value[2] == L'/')
        && value.find(L'\0') == std::wstring_view::npos;
}
bool valid_revision(std::string_view value) noexcept {
    return value.size() == 64 && std::all_of(value.begin(),value.end(),[](char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
    });
}
bool owner_scope(const Runtime &r, uintptr_t owner, rows::OwnerScope &out) noexcept {
    uintptr_t vtable=0, data=0, history_owner=0, factory=0, database=0, control=0;
    int64_t account=0;
    if (!read_at(owner,0,vtable) || vtable != r.layout.owner
        || !read_at(owner,0xd8,account) || account <= 0
        || !read_at(owner,0x30,data) || !read_at(data,0,vtable)
        || vtable != r.layout.data
        || !read_at(data,0x90,history_owner) || !read_at(history_owner,0,vtable)
        || vtable != r.layout.history_owner
        || !read_at(history_owner,0x88,factory) || !read_at(factory,0,vtable)
        || vtable != r.layout.factory
        || !read_at(factory,0x48,database) || !read_at(factory,0x50,control)
        || !database || !control) return false;
    out={account,database,control};return true;
}
bool route_for(Runtime &r, uintptr_t history, rows::Route &route) noexcept {
    uintptr_t vtable=0,database=0,control=0;
    int64_t chat=0;
    if (!read_at(history,0,vtable) || vtable != r.layout.chat_history
        || !read_at(history,0x30,database) || !read_at(history,0x38,control)
        || !read_at(history,0xd8,chat) || !database || !control || chat == 0) return false;
    std::array<rows::OwnerScope,kOwners> candidates{};
    size_t valid=0;
    // The destructor probe removes an owner under this same lock before its
    // body can free it. Keep the lock while reading the bounded owner chain.
    std::lock_guard lock(r.scope);
    for (size_t i=0;i<r.owner_count;++i) {
        rows::OwnerScope candidate{};
        if (owner_scope(r,r.owners[i],candidate)) candidates[valid++]=candidate;
    }
    return rows::unique_route(std::span(candidates.data(),valid),database,control,chat,route);
}
bool capture_batch(void *raw, rows::Route route,
                   std::span<const pilot::CapturedText> messages) noexcept {
    auto &r=*static_cast<Runtime *>(raw);
    if (messages.empty() || messages.size() > rows::kMaxBatchRows) return false;
    std::array<ValmaxRetentionCaptureRowV2,rows::kMaxBatchRows> batch{};
    for (size_t i=0;i<messages.size();++i) {
        const auto text=messages[i].text.bytes();
        if (text.empty() || text.size() > rows::kMaxTextBytes) return false;
        batch[i]={messages[i].id,messages[i].received_at_ms,text.data(),text.size(),
            messages[i].order_key};
    }
    return valmax_core_retention_capture_text_batch_v2(r.profile_units.data(),
        r.profile_units.size(),route.owner,route.chat,batch.data(),messages.size()) == 0;
}
bool mark_batch(void *raw, rows::Route route, std::span<const int64_t> ids,
                std::span<uint8_t> bits) noexcept {
    auto &r=*static_cast<Runtime *>(raw);
    if (ids.empty() || ids.size() > rows::kMaxBatchRows
        || bits.size() != (ids.size()+7)/8) return false;
    return valmax_core_retention_mark_delete_batch(r.profile_units.data(),
        r.profile_units.size(),route.owner,route.chat,ids.data(),ids.size(),
        bits.data(),bits.size()) == 0;
}
bool activate_locked(Runtime &r) noexcept {
    if (!r.installed || !r.images_exact || r.listeners_attached != 3
        || !r.owner_join || !r.store_ready || !r.applied || r.closing
        || r.error != HostError::None || !r.worker) return false;
    const pilot::PassivePreflight proof{r.generation,3,0,r.preference_enabled,
        true,true,true};
    if (r.worker->activate(proof)) return true;
    r.error=HostError::Worker;
    return false;
}
void fail_scope(Runtime &r) noexcept {
    std::lock_guard lock(r.scope);
    increment(r.route_failures);
    if (r.owner_count != 0 && r.error == HostError::None) {
        r.error=HostError::OwnerScope;
        if (r.worker) r.worker->revoke(pilot::AsyncError::InvalidRoute);
    }
}
void owner_enter(GumInvocationContext *context,gpointer) {
    auto *slot=static_cast<uintptr_t *>(gum_invocation_context_get_listener_invocation_data(
        context,sizeof(uintptr_t)));
    if (slot) *slot=reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,0));
}
void owner_leave(GumInvocationContext *context,gpointer raw) {
    auto &r=*static_cast<Runtime *>(raw);
    if (!r.callbacks_active.load(std::memory_order_acquire)) return;
    const auto *slot=static_cast<const uintptr_t *>(gum_invocation_context_get_listener_invocation_data(
        context,sizeof(uintptr_t)));
    if (!slot || !*slot) return;
    try {
        uintptr_t vtable=0;
        if (!read_at(*slot,0,vtable) || vtable != r.layout.owner) return;
        std::lock_guard lock(r.scope);
        if (std::find(r.owners.begin(),r.owners.begin()+static_cast<ptrdiff_t>(r.owner_count),*slot)
            != r.owners.begin()+static_cast<ptrdiff_t>(r.owner_count)) return;
        if (r.owner_count == kOwners) {
            r.error=HostError::OwnerScope;
            if (r.worker) r.worker->revoke(pilot::AsyncError::InvalidRoute);
            return;
        }
        r.owners[r.owner_count++]=*slot;
        increment(r.owner_constructed);
    } catch (...) { fail_scope(r); }
}
void owner_destroyed(GumInvocationContext *context,gpointer raw) {
    auto &r=*static_cast<Runtime *>(raw);
    if (!r.callbacks_active.load(std::memory_order_acquire)) return;
    const auto pointer=reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,0));
    try {
        std::lock_guard lock(r.scope);
        for (size_t i=0;i<r.owner_count;++i) if (r.owners[i]==pointer) {
            r.owners[i]=r.owners[--r.owner_count];
            r.owners[r.owner_count]=0;
            if (r.owner_count == 0) r.owner_join=false;
            break;
        }
    } catch (...) { fail_scope(r); }
}
bool read_rows_vector(uintptr_t pointer, uintptr_t &begin,size_t &count) noexcept {
    struct Vector { uintptr_t begin,end,cap; } value{};
    if (!ghost::read(pointer,value)
        || (value.begin==0)!=(value.end==0)
        || (value.begin==0)!=(value.cap==0)
        || value.end<value.begin || value.cap<value.end) return false;
    if (value.begin==value.end) { begin=value.begin;count=0;return true; }
    if (!value.begin || (value.begin&7)) return false;
    const auto bytes=value.end-value.begin;
    if (bytes%rows::kMessageBytes || bytes/rows::kMessageBytes>rows::kMaxBatchRows)
        return false;
    begin=value.begin;count=static_cast<size_t>(bytes/rows::kMessageBytes);
    return true;
}
bool read_row(uintptr_t address, void *output,size_t size,void *) noexcept {
    return ghost::read_memory(address,output,size);
}
void rows_enter(GumInvocationContext *context,gpointer raw) {
    auto &r=*static_cast<Runtime *>(raw);
    if (!r.callbacks_active.load(std::memory_order_acquire)) return;
    try {
        // Lock only callback parsing/enqueue ordering. No filesystem or Rust
        // call runs on a MAX thread; MAX's original write always proceeds.
        std::lock_guard event_lock(r.events);
        if (!context->cpu_context) { fail_scope(r); return; }
        const auto sp=static_cast<uintptr_t>(context->cpu_context->rsp);
        int32_t mode=-1;
        if (!read_at(sp,0x28,mode) || mode != 0) return;
        const auto history=reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,0));
        const auto vector=reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,2));
        rows::Route route{};
        if (!route_for(r,history,route)) { fail_scope(r); return; }
        bool enabled=false;
        {
            std::lock_guard lock(r.scope);
            r.owner_join=true;increment(r.joined_upserts);
            const bool activated=activate_locked(r);
            enabled=r.installed && r.store_ready && r.preference_enabled && r.applied
                && r.error==HostError::None && activated;
        }
        if (!enabled) return;
        uintptr_t begin=0;size_t count=0;
        if (!read_rows_vector(vector,begin,count)) { fail_scope(r); return; }
        if (count==0) return;
        std::vector<rows::Snapshot> captured;
        std::vector<int64_t> removed;
        captured.reserve(count);removed.reserve(count);
        for (size_t i=0;i<count;++i) {
            const auto row=begin+i*rows::kMessageBytes;
            uint32_t status_code=0;
            int64_t sender=0;
            if (!read_at(row,0x128,status_code) || !read_at(row,0x80,sender)) {
                fail_scope(r);return;
            }
            if (status_code<=1) {
                if (sender<=0) { fail_scope(r);return; }
                // The selected account's own text is never copied into an
                // owned SecureText buffer or submitted to the store.
                if (sender==route.owner) continue;
            }
            rows::Snapshot message;
            if (!rows::decode(row,read_row,nullptr,message)) {
                fail_scope(r);return;
            }
            if (message.kind==rows::Kind::Text) captured.push_back(std::move(message));
            else if (message.kind==rows::Kind::Removed) removed.push_back(message.id);
        }
        const auto now=std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
        if (now<0 || !r.worker) { fail_scope(r); return; }
        if (!captured.empty() && !r.worker->submit_capture(route,std::move(captured),now)) return;
        if (!removed.empty()) (void)r.worker->submit_removed(route,removed);
    } catch (...) { fail_scope(r); }
}
bool pin_callback_module() noexcept {
    HMODULE pinned=nullptr;
    return GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN|GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
        reinterpret_cast<const wchar_t *>(&rows_enter),&pinned)!=0;
}
bool attach(Runtime &r,void *create,void *destroy,void *rows) noexcept {
    if (!pin_callback_module()) return false;
    gum_init_embedded();
    r.interceptor=gum_interceptor_obtain();
    if (!r.interceptor) return false;
    r.listeners[0]=gum_make_call_listener(owner_enter,owner_leave,&r,nullptr);
    r.listeners[1]=gum_make_probe_listener(owner_destroyed,&r,nullptr);
    r.listeners[2]=gum_make_probe_listener(rows_enter,&r,nullptr);
    if (std::any_of(r.listeners.begin(),r.listeners.end(),[](auto *listener){return !listener;}))
        return false;
    GumAttachOptions options{};
    std::array<bool,3> attached{};
    gum_interceptor_begin_transaction(r.interceptor);
    attached[0]=gum_interceptor_attach(r.interceptor,create,r.listeners[0],&options)==GUM_ATTACH_OK;
    attached[1]=attached[0]&&gum_interceptor_attach(r.interceptor,destroy,r.listeners[1],&options)==GUM_ATTACH_OK;
    attached[2]=attached[1]&&gum_interceptor_attach(r.interceptor,rows,r.listeners[2],&options)==GUM_ATTACH_OK;
    const bool complete=attached[0]&&attached[1]&&attached[2];
    if (!complete) for (size_t i=0;i<attached.size();++i)
        if (attached[i]) gum_interceptor_detach(r.interceptor,r.listeners[i]);
    if (complete) {
        // A callback can run as soon as the transaction is committed. Publish
        // its fail-closed initial state before allowing any callback to enter.
        {
            std::lock_guard lock(r.scope);
            r.error=HostError::None;
        }
        r.callbacks_active.store(true,std::memory_order_release);
    }
    gum_interceptor_end_transaction(r.interceptor);
    if (!complete || !gum_interceptor_flush(r.interceptor)) {
        r.callbacks_active.store(false,std::memory_order_release);
        {
            std::lock_guard lock(r.scope);
            r.installed=false;
            r.listeners_attached=0;
            r.error=HostError::HookFailed;
        }
        if (complete) {
            gum_interceptor_begin_transaction(r.interceptor);
            for (auto *listener:r.listeners) gum_interceptor_detach(r.interceptor,listener);
            gum_interceptor_end_transaction(r.interceptor);
            (void)gum_interceptor_flush(r.interceptor);
        }
        return false;
    }
    {
        std::lock_guard lock(r.scope);
        r.installed=true;
        r.listeners_attached=3;
        // A scope failure from an early callback must remain visible.
    }
    return true;
}
} // namespace

bool install_early(std::wstring_view profile,std::wstring_view core_path,
                   std::wstring_view storage_path) noexcept {
    auto &r=runtime();
    std::lock_guard lifecycle(r.lifecycle);
    if (r.attempted) return r.installed && r.profile_key==profile
        && r.core_path==core_path && r.storage_path==storage_path;
    r.attempted=true;
    if (!valid_path(profile) || !valid_path(core_path) || !valid_path(storage_path))
        return install_error(r,HostError::InvalidConfig);
    try {
        r.profile_key=profile;r.core_path=core_path;r.storage_path=storage_path;
        r.profile_units.assign(profile.begin(),profile.end());
#ifndef VALMAX_RETENTION_HOST_TESTING
        if (!ghost::validate_image(r.core_path.c_str(),pins::kCore,r.core)
            || !ghost::validate_image(r.storage_path.c_str(),pins::kStorage,r.storage)) {
            return install_error(r,HostError::ImageMismatch);
        }
        HMODULE pinned=nullptr;
        if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN|GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                reinterpret_cast<const wchar_t *>(r.core.base),&pinned)
            || !GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN|GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                reinterpret_cast<const wchar_t *>(r.storage.base),&pinned)) {
            return install_error(r,HostError::ImageMismatch);
        }
        r.layout={r.core.base+kCoreOwnerVtable,r.storage.base+kStorageDataVtable,
            r.storage.base+kStorageHistoryOwnerVtable,r.storage.base+kStorageFactoryVtable,
            r.storage.base+kStorageChatHistoryVtable};
        auto worker=std::make_unique<pilot::AsyncWorker>(pilot::AsyncBackend{&r,capture_batch,mark_batch});
        {
            std::lock_guard lock(r.scope);
            r.images_exact=true;
            r.worker=std::move(worker);
        }
        if (!attach(r,reinterpret_cast<void *>(r.core.base+pins::kOwnerCreateRva),
                reinterpret_cast<void *>(r.core.base+pins::kOwnerDestroyRva),
                reinterpret_cast<void *>(r.storage.base+pins::kRowsRva))) {
            return install_error(r,HostError::HookFailed);
        }
        std::lock_guard lock(r.scope);
        return r.error==HostError::None;
#else
        return install_error(r,HostError::NotInstalled);
#endif
    } catch (...) { return install_error(r,HostError::HookFailed); }
}
bool profile_matches(std::wstring_view profile) noexcept {
    auto &r=runtime();
    std::lock_guard lifecycle(r.lifecycle);
    return r.attempted && r.profile_key==profile;
}
#ifdef VALMAX_RETENTION_HOST_TESTING
bool install_synthetic(std::wstring_view profile,const TestTargets &targets,
                       pilot::AsyncBackend backend) noexcept {
    auto &r=runtime();
    std::lock_guard lifecycle(r.lifecycle);
    if (r.attempted || !valid_path(profile) || !targets.owner_create
        || !targets.owner_destroy || !targets.rows || !targets.owner_vtable
        || !targets.data_vtable || !targets.history_owner_vtable
        || !targets.factory_vtable || !targets.chat_history_vtable) return false;
    r.attempted=true;
    try {
        r.profile_key=profile;
        r.profile_units.assign(profile.begin(),profile.end());
        r.layout={targets.owner_vtable,targets.data_vtable,
            targets.history_owner_vtable,targets.factory_vtable,
            targets.chat_history_vtable};
        auto worker=std::make_unique<pilot::AsyncWorker>(backend);
        {
            std::lock_guard lock(r.scope);
            r.images_exact=true;
            r.worker=std::move(worker);
        }
        if (!attach(r,targets.owner_create,targets.owner_destroy,targets.rows)) {
            return install_error(r,HostError::HookFailed);
        }
        std::lock_guard lock(r.scope);
        return r.error==HostError::None;
    } catch (...) { return install_error(r,HostError::HookFailed); }
}
bool synthetic_store_ready() noexcept {
    auto &r=runtime();
    std::lock_guard lock(r.scope);
    if (!r.installed || r.closing) return false;
    r.store_ready=true;
    if (r.owner_join && r.applied) (void)activate_locked(r);
    return r.error==HostError::None;
}
#endif
bool preflight() noexcept {
    auto &r=runtime();
    std::lock_guard lifecycle(r.lifecycle);
    if (!r.installed || r.closing || r.profile_units.empty()) return false;
    const bool okay=valmax_core_retention_preflight(r.profile_units.data(),r.profile_units.size())==0;
    std::lock_guard scope(r.scope);
    if (!okay) {
        r.error=HostError::Store;
        if (r.worker) r.worker->revoke(pilot::AsyncError::Storage);
        return false;
    }
    r.store_ready=true;
    if (r.owner_join && r.applied) (void)activate_locked(r);
    return r.error==HostError::None;
}
bool set_enabled(bool enabled,std::string_view revision,uint64_t generation) noexcept {
    if (!valid_revision(revision) || generation==0) return false;
    auto &r=runtime();
    std::lock_guard lock(r.scope);
    if (!r.installed || r.closing || generation<r.generation) return false;
    if (generation==r.generation) return r.applied && r.preference_enabled==enabled
        && std::equal(r.revision.begin(),r.revision.end(),revision.begin());
    r.generation=generation;r.preference_enabled=enabled;r.applied=true;
    std::copy(revision.begin(),revision.end(),r.revision.begin());
    if (r.owner_join && r.store_ready) (void)activate_locked(r);
    return r.error==HostError::None;
}
NativeStatus status() noexcept {
    auto &r=runtime();
    NativeStatus value;
    std::lock_guard lock(r.scope);
    value.installed=r.installed;value.images_exact=r.images_exact;
    value.owner_join=r.owner_join;value.store_ready=r.store_ready;
    value.preference_enabled=r.preference_enabled;value.applied=r.applied;
    value.listeners=r.listeners_attached;value.generation=r.generation;
    value.owner_constructed=r.owner_constructed;value.joined_upserts=r.joined_upserts;
    value.route_failures=r.route_failures;value.revision=r.revision;value.error=r.error;
    if (r.worker) value.worker=r.worker->status();
    return value;
}
void shutdown() noexcept {
    auto &r=runtime();
    std::lock_guard lifecycle(r.lifecycle);
    if (r.closing) return;
    r.callbacks_active.store(false,std::memory_order_release);
    {
        std::lock_guard lock(r.scope);
        r.closing=true;r.error=HostError::Shutdown;r.installed=false;r.owner_join=false;
    }
    if (r.interceptor) {
        gum_interceptor_begin_transaction(r.interceptor);
        for (auto *listener:r.listeners) if (listener) gum_interceptor_detach(r.interceptor,listener);
        gum_interceptor_end_transaction(r.interceptor);
        bool drained=false;
        for (int attempt=0;attempt<20;++attempt) {
            if (gum_interceptor_flush(r.interceptor)) { drained=true;break; }
            Sleep(5);
        }
        if (drained) {
            for (auto *&listener:r.listeners) { if (listener) g_object_unref(listener);listener=nullptr; }
            g_object_unref(r.interceptor);r.interceptor=nullptr;
        }
    }
    if (r.worker) r.worker->shutdown();
    std::lock_guard lock(r.scope);
    r.listeners_attached=0;
}

} // namespace valmax::retention::passive
