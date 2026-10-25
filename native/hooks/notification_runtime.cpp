#include "notification_internal.h"
#include "notification_pins.generated.h"
#include "hook_set.h"
#include "frida-gum.h"
#include <algorithm>
#include <cstring>
#include <filesystem>
namespace valmax::notifications {
namespace {
struct Marker { uintptr_t context = 0; int property = -1; bool counted = false; };
struct Late { uintptr_t context = 0; uint8_t properties = 0; };
struct Runtime {
    std::mutex lifecycle, mutex;
    PolicyState policy;
    Targets targets;
    std::array<void *,6> originals{};
    std::array<GumInvocationListener *,2> listeners{};
    std::array<Late,kOwners> late{};
    std::array<uint64_t,4> counters{};
    uint32_t error = VALMAX_NOTIFICATION_NOT_READY, replaced = 0, attached = 0, in_flight = 0;
    bool installed = false, draining = false, pinned = false, fatal = false;
    GumInterceptor *interceptor = nullptr;
    VerifiedImage main, backend;
    Asset asset;
#ifdef VALMAX_NOTIFICATION_TESTING
    std::atomic<uint64_t> clock{1};
#endif
    uint64_t now() const {
#ifdef VALMAX_NOTIFICATION_TESTING
        return clock.load();
#else
        return GetTickCount64();
#endif
    }
};
Runtime &runtime() { static Runtime *value = new Runtime; return *value; }
#ifdef VALMAX_NOTIFICATION_TESTING
void (*afterFlushProbe)() = nullptr;
#endif
void increment(uint64_t &value) { if (value < kCounterLimit) ++value; }
void finish(Runtime &r) { std::lock_guard guard(r.mutex); if (r.in_flight) --r.in_flight; }
bool scope(Runtime &r, void *data, uint64_t &native, int64_t &chat) {
    const auto pointer = reinterpret_cast<uintptr_t>(data);
    uintptr_t marker = 0; uint64_t duplicate = 0;
    std::array<uint8_t,kDataSize> snapshot{};
    if (!read_memory(pointer,snapshot.data(),snapshot.size())) return false;
    std::memcpy(&marker,snapshot.data()+pins::kKeyMarkerOffset,sizeof(marker));
    std::memcpy(&native,snapshot.data()+pins::kAccountOffset,sizeof(native));
    std::memcpy(&duplicate,snapshot.data()+pins::kAccountDuplicateOffset,sizeof(duplicate));
    std::memcpy(&chat,snapshot.data()+pins::kChatOffset,sizeof(chat));
    SecureZeroMemory(snapshot.data(),snapshot.size());
    return marker == r.targets.marker && native == duplicate;
}
using Display = void (*)(void *, void *);
using DisplayFlag = void (*)(void *, void *, uint8_t);
void dispatch(size_t index, void *self, void *data, uint8_t flag = 0) noexcept {
    auto &r = runtime();
    bool admitted = false;
    try {
        Decision decision = Decision::Suppress;
        {
            std::lock_guard guard(r.mutex);
            // Count entry and decide atomically with updates. ACK can only be
            // published after every callback admitted under an older policy
            // completes, including intermediate backend wrappers.
            if (r.in_flight >= 64) { increment(r.counters[2]); increment(r.counters[3]); r.fatal = true; r.error = VALMAX_NOTIFICATION_BUSY; return; }
            ++r.in_flight; admitted = true;
            uint64_t native = 0; int64_t chat = 0;
            const bool valid = scope(r,data,native,chat);
            if (!valid || !r.policy.owners.resolve(native)) increment(r.counters[1]);
            decision = r.policy.select(valid,native,chat);
            if (r.fatal && decision == Decision::Mask) decision = Decision::Suppress;
            if (decision == Decision::Suppress) increment(r.counters[2]);
        }
        // Do not hold the policy mutex across any foreign MAX/Qt call.
        if (decision == Decision::Pass) {
            if (index >= 4) reinterpret_cast<DisplayFlag>(r.originals[index])(self,data,flag);
            else reinterpret_cast<Display>(r.originals[index])(self,data);
        } else if (decision == Decision::Mask) {
            bool cleanup_failed = false;
            const bool ok = clone_call(r.targets,r.asset.utf8.c_str(),r.originals[index],self,data,index>=4,flag,cleanup_failed);
            std::lock_guard guard(r.mutex);
            if (ok) increment(r.counters[0]);
            else { increment(r.counters[2]); increment(r.counters[3]); r.fatal = true;
                r.error = cleanup_failed ? VALMAX_NOTIFICATION_CLEANUP_FAILED : VALMAX_NOTIFICATION_CLONE_FAILED; }
        }
    } catch (...) { std::lock_guard guard(r.mutex); increment(r.counters[3]); r.fatal = true; r.error = VALMAX_NOTIFICATION_CLONE_FAILED; }
    if (admitted) finish(r);
}
void display(void *self,void *data) { dispatch(0,self,data); }
void edit(void *self,void *data) { dispatch(1,self,data); }
void native_display(void *self,void *data) { dispatch(2,self,data); }
void native_edit(void *self,void *data) { dispatch(3,self,data); }
void qt_display(void *self,void *data,uint8_t flag) { dispatch(4,self,data,flag); }
void qt_edit(void *self,void *data,uint8_t flag) { dispatch(5,self,data,flag); }
void remember(Runtime &r, uintptr_t context) {
    uintptr_t vtable = 0; int64_t owner = 0; uint64_t native = 0;
    if (!read(context,vtable) || vtable != r.targets.identity_vtable
        || !read(context+pins::kOwnerOffset,owner) || owner <= 0
        || !read(context+pins::kNativeAccountOffset,native)) return;
    r.policy.owners.remember(native,owner);
}
void identity(Runtime &r, uintptr_t context, int property) {
    if (property < 0) { remember(r,context); return; }
    Late *empty = nullptr;
    for (auto &entry : r.late) {
        if (entry.context == context) {
            entry.properties |= static_cast<uint8_t>(1u << property);
            if (entry.properties == 3) { remember(r,context); entry = {}; }
            return;
        }
        if (!entry.context && !empty) empty = &entry;
    }
    if (empty) *empty = {context,static_cast<uint8_t>(1u << property)};
    else { increment(r.counters[3]); } // no unbounded pointer cache or guessed owner
}
void identity_enter(GumInvocationContext *context, gpointer user) {
    auto &r = runtime();
    auto *marker = static_cast<Marker *>(gum_invocation_context_get_listener_invocation_data(context,sizeof(Marker)));
    if (!marker) return;
    *marker = {};
    std::lock_guard guard(r.mutex);
    if (r.in_flight >= 64) { r.fatal=true; r.error=VALMAX_NOTIFICATION_SCOPE_GUARD; increment(r.counters[3]); return; }
    ++r.in_flight; marker->counted = true;
    const auto self = reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,0));
    if (reinterpret_cast<uintptr_t>(user) == 0) {
        if (self >= pins::kIdentityInterfaceOffset) marker->context = self-pins::kIdentityInterfaceOffset;
    } else {
        const auto call = reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,1));
        const auto index = reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,2));
        if ((call == 1 || call == 8) && index <= 1) { marker->context = self; marker->property = static_cast<int>(index); }
    }
}
void identity_leave(GumInvocationContext *context, gpointer) {
    auto &r = runtime();
    auto *marker = static_cast<Marker *>(gum_invocation_context_get_listener_invocation_data(context,sizeof(Marker)));
    if (!marker) return;
    std::lock_guard guard(r.mutex);
    if (marker->context) identity(r,marker->context,marker->property);
    if (marker->counted && r.in_flight) --r.in_flight;
    *marker = {};
}
bool detach(Runtime &r) {
    if (!r.interceptor) return true;
    if (!r.draining) {
        gum_interceptor_begin_transaction(r.interceptor);
        for (size_t i = 0; i < r.attached; ++i) gum_interceptor_detach(r.interceptor,r.listeners[i]);
        for (size_t i = 0; i < r.replaced; ++i) gum_interceptor_revert(r.interceptor,r.targets.replacements[i]);
        gum_interceptor_end_transaction(r.interceptor); r.draining = true;
    }
    if (!gum_interceptor_flush(r.interceptor)) return false;
    { std::lock_guard guard(r.mutex); if (r.in_flight) return false; }
    for (auto &value : r.listeners) { if (value) g_object_unref(value); value = nullptr; }
    std::lock_guard guard(r.mutex);
    r.originals = {}; r.replaced = r.attached = 0; r.installed = r.draining = false;
    return true;
}
uint32_t install(Runtime &r,const Targets &targets) {
    if (r.draining) return VALMAX_NOTIFICATION_BUSY;
    if (r.installed) return VALMAX_NOTIFICATION_INVALID_ARGUMENT;
    r.targets = targets;
    static std::once_flag initialized;
    std::call_once(initialized,[]{gum_init_embedded();});
    if (!r.interceptor) r.interceptor = gum_interceptor_obtain();
    const std::array<void *,6> callbacks{reinterpret_cast<void *>(&display),reinterpret_cast<void *>(&edit),
        reinterpret_cast<void *>(&native_display),reinterpret_cast<void *>(&native_edit),reinterpret_cast<void *>(&qt_display),reinterpret_cast<void *>(&qt_edit)};
    bool ok = true;
    gum_interceptor_begin_transaction(r.interceptor);
    for (size_t i = 0; i < callbacks.size(); ++i) {
        GumReplaceOptions options{};
        if (gum_interceptor_replace(r.interceptor,targets.replacements[i],callbacks[i],&r.originals[i],&options) != GUM_REPLACE_OK) { ok = false; break; }
        ++r.replaced;
    }
    if (ok) for (size_t i = 0; i < r.listeners.size(); ++i) {
        r.listeners[i] = gum_make_call_listener(&identity_enter,&identity_leave,reinterpret_cast<void *>(i),nullptr);
        GumAttachOptions options{};
        if (!r.listeners[i] || gum_interceptor_attach(r.interceptor,targets.listeners[i],r.listeners[i],&options) != GUM_ATTACH_OK) { ok = false; break; }
        ++r.attached;
    }
    if (!ok) {
        for (size_t i=0;i<r.attached;++i) gum_interceptor_detach(r.interceptor,r.listeners[i]);
        for (size_t i=0;i<r.replaced;++i) gum_interceptor_revert(r.interceptor,r.targets.replacements[i]);
        r.draining=true;
    }
    gum_interceptor_end_transaction(r.interceptor);
    if (!ok || !gum_interceptor_flush(r.interceptor)) {
        { std::lock_guard guard(r.mutex); r.error = VALMAX_NOTIFICATION_HOOK_FAILED; }
        return detach(r) ? VALMAX_NOTIFICATION_HOOK_FAILED : VALMAX_NOTIFICATION_BUSY;
    }
#ifdef VALMAX_NOTIFICATION_TESTING
    // Exercise a live replacement before the external ready result is set.
    if (afterFlushProbe) afterFlushProbe();
#endif
    std::lock_guard guard(r.mutex);
    r.installed = true; r.fatal = false; r.error = VALMAX_NOTIFICATION_OK;
    return VALMAX_NOTIFICATION_OK;
}
bool safe_path(const wchar_t *input,std::wstring &output) {
    if (!input) return false;
    for (size_t i=0;i<32768;++i) { wchar_t ch=0; if (!read(reinterpret_cast<uintptr_t>(input)+i*sizeof(wchar_t),ch)) return false;
        if (!ch) return output.size()>=4; output.push_back(ch); }
    return false;
}
bool same_parent(const std::filesystem::path &a,const std::filesystem::path &b) {
    return CompareStringOrdinal(a.parent_path().c_str(),-1,b.parent_path().c_str(),-1,TRUE) == CSTR_EQUAL;
}
}
#ifdef VALMAX_NOTIFICATION_TESTING
uint32_t install_synthetic(const Targets &targets,const char *image) {
    auto &r=runtime(); std::lock_guard lifecycle(r.lifecycle);
    if (r.installed || r.draining) return VALMAX_NOTIFICATION_BUSY;
    r.asset.utf8=image; return install(r,targets);
}
void set_synthetic_after_flush_probe(void (*probe)()) noexcept { afterFlushProbe = probe; }
void synthetic_remember(uint64_t native,int64_t owner) { auto &r=runtime(); std::lock_guard guard(r.mutex); r.policy.owners.remember(native,owner); }
void synthetic_clock(uint64_t now) { runtime().clock.store(now); }
void synthetic_identity(void *context,int property) { auto &r=runtime(); std::lock_guard guard(r.mutex); identity(r,reinterpret_cast<uintptr_t>(context),property); }
#endif
}
using namespace valmax::notifications;
namespace ghost = valmax::ghost;
uint32_t valmax_notifications_set_policy(const ValmaxNotificationPolicy *input,const ValmaxNotificationScope *scopes) {
    auto &r=runtime();
    try {
        ValmaxNotificationPolicy copy{}; std::array<ValmaxNotificationScope,kScopes> items{};
        const bool valid=read(reinterpret_cast<uintptr_t>(input),copy) && copy.count<=items.size()
            && (!copy.count || read_memory(reinterpret_cast<uintptr_t>(scopes),items.data(),copy.count*sizeof(items[0])));
        std::lock_guard guard(r.mutex);
        if (!valid) { r.policy.fresh=false; return VALMAX_NOTIFICATION_INVALID_ARGUMENT; }
        const auto error=r.policy.update(copy,items.data(),r.now());
        if (!r.fatal) r.error=error;
        return error;
    } catch (...) { std::lock_guard guard(r.mutex); r.policy.fresh=false; return VALMAX_NOTIFICATION_INVALID_POLICY; }
}
uint32_t valmax_notifications_status(ValmaxNotificationStatus *output) {
    if (!output) return VALMAX_NOTIFICATION_INVALID_ARGUMENT;
    ValmaxNotificationStatus request{};
    if (!read(reinterpret_cast<uintptr_t>(output),request) || request.size!=sizeof(request) || request.version!=1) return VALMAX_NOTIFICATION_INVALID_ARGUMENT;
    auto &r=runtime();
    std::lock_guard lifecycle(r.lifecycle); std::lock_guard guard(r.mutex);
    ValmaxNotificationStatus result{}; result.size=sizeof(result); result.version=1;
    result.ready=r.installed && !r.draining && r.replaced==6 && r.attached==2;
    result.applied=result.ready && !r.fatal && r.policy.leased(r.now()) && !r.in_flight;
    result.error=r.fatal?r.error:!result.ready?VALMAX_NOTIFICATION_NOT_READY:!r.policy.known?VALMAX_NOTIFICATION_CATALOG_UNAVAILABLE:
        !r.policy.leased(r.now())?VALMAX_NOTIFICATION_LEASE_EXPIRED:r.error;
    result.generation=r.policy.generation; std::memcpy(result.revision,r.policy.policy.revision,sizeof(result.revision));
    result.replacements=r.replaced; result.listeners=r.attached; result.in_flight=r.in_flight;
    result.masked=r.counters[0];result.scope_unknown=r.counters[1];result.suppressed=r.counters[2];result.faults=r.counters[3];
    SIZE_T written=0;
    return WriteProcessMemory(GetCurrentProcess(),output,&result,sizeof(result),&written) && written==sizeof(result)
        ? VALMAX_NOTIFICATION_OK:VALMAX_NOTIFICATION_INVALID_ARGUMENT;
}
uint32_t valmax_notifications_install(const wchar_t *main_path,const wchar_t *backend_path,const wchar_t *asset_path) {
    auto &r=runtime(); std::lock_guard lifecycle(r.lifecycle);
    try {
        if (r.draining || r.installed) return VALMAX_NOTIFICATION_BUSY;
        std::wstring main_value,backend_value,asset_value;
        if (!safe_path(main_path,main_value) || !safe_path(backend_path,backend_value) || !safe_path(asset_path,asset_value)) return VALMAX_NOTIFICATION_INVALID_ARGUMENT;
        std::filesystem::path main(main_value),backend(backend_value),asset(asset_value);
        if (main.filename()!=L"max.exe" || backend.filename()!=L"notifications.dll" || !same_parent(main,backend) || !same_parent(main,asset)) return VALMAX_NOTIFICATION_INVALID_ARGUMENT;
        for (const auto &path : {main,backend}) {
            if (!GetModuleHandleW(path.c_str()))
                return GetModuleHandleW(path.filename().c_str()) ? VALMAX_NOTIFICATION_MODULE_MISMATCH : VALMAX_NOTIFICATION_NOT_READY;
        }
        if (!ghost::validate_image(main_value.c_str(),pins::kMain,r.main)
            || !ghost::validate_image(backend_value.c_str(),pins::kBackend,r.backend)) {
            r.main.release(); r.backend.release(); return VALMAX_NOTIFICATION_MODULE_MISMATCH;
        }
        if (!validate_asset(asset_value.c_str(),main_value.c_str(),r.asset)) { r.asset.release(); r.main.release(); r.backend.release(); return VALMAX_NOTIFICATION_INVALID_ASSET; }
        if (!r.pinned) { HMODULE module=nullptr;
            if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,reinterpret_cast<LPCWSTR>(&valmax_notifications_install),&module)) return VALMAX_NOTIFICATION_MODULE_MISMATCH;
            r.pinned=true;
        }
        Targets targets;
        targets.replacements={reinterpret_cast<void *>(r.backend.base+pins::kDisplayRva),reinterpret_cast<void *>(r.backend.base+pins::kEditRva),
            reinterpret_cast<void *>(r.backend.base+pins::kNativeDisplayRva),reinterpret_cast<void *>(r.backend.base+pins::kNativeEditRva),
            reinterpret_cast<void *>(r.main.base+pins::kQtDisplayRva),reinterpret_cast<void *>(r.main.base+pins::kQtEditRva)};
        targets.listeners={reinterpret_cast<void *>(r.main.base+pins::kIdentitySetterRva),reinterpret_cast<void *>(r.main.base+pins::kIdentityMetaCallRva)};
        targets.data_copy=reinterpret_cast<void *>(r.backend.base+pins::kDataCopyRva);targets.string_copy=reinterpret_cast<void *>(r.backend.base+pins::kStringCopyRva);
        targets.string_destroy=reinterpret_cast<void *>(r.backend.base+pins::kStringDestroyRva);targets.header_destroy=reinterpret_cast<void *>(r.backend.base+pins::kHeaderDestroyRva);
        targets.marker=r.main.base+pins::kKeyMarkerRva;targets.identity_vtable=r.main.base+pins::kIdentityVtable;
        return install(r,targets);
    } catch (...) { return VALMAX_NOTIFICATION_HOOK_FAILED; }
}
uint32_t valmax_notifications_shutdown() {
    auto &r=runtime();std::lock_guard lifecycle(r.lifecycle);
    { std::lock_guard guard(r.mutex); r.policy.fresh=false; }
    return detach(r)?VALMAX_NOTIFICATION_OK:VALMAX_NOTIFICATION_BUSY;
}
