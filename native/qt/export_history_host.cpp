#include "export_history_host.h"
#include "valmax_core.h"
#pragma push_macro("slots")
#undef slots
#include "ghost_internal.h"
#pragma pop_macro("slots")
#include "frida-gum.h"
#ifndef VALMAX_EXPORT_HISTORY_TESTING
#include "export_history_pins.generated.h"
#endif
#include <QCoreApplication>
#include <QDir>
#include <QPointer>
#include <QThread>
#include <QTimer>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstring>
#include <mutex>

namespace valmax {
namespace {
constexpr size_t CacheLimit=256,PageLimit=4;
constexpr uint64_t CacheTtl=2000;
struct Page {uintptr_t view=0;uint64_t epoch=0,sequence=0,time=0;uint32_t direction=0,count=0,pending=0,backward=1,forward=1;};
struct Cache {uintptr_t view=0;uint64_t epoch=0,latest=0;std::array<Page,PageLimit> pages{};size_t count=0;bool overflow=false;};
struct ProbeState {
    // Separate from Qt and Runtime lifetime. If Gum cannot drain on shutdown,
    // this bounded state and pinned code stay alive instead of dangling.
    std::mutex mutex;
    std::atomic<bool> enabled{false};
    uint64_t epoch=1,sequence=0,prime_deadline=0;
    bool priming=false,dead=false;
    uintptr_t selected=0,vtable=0,terminal_return=0;
    std::array<Cache,CacheLimit> cache{};
    GumInterceptor* interceptor=nullptr;
    std::array<GumInvocationListener*,2> listeners{};
    ghost::VerifiedImage main,qt;
};
std::atomic<bool> owned{false};
template<class T> bool read(uintptr_t at,T& out){return ghost::read_memory(at,&out,sizeof(out));}
void wipeBinding(ValmaxExportHistoryBinding& binding){SecureZeroMemory(&binding,sizeof(binding));}
bool hex(const QByteArray& value,int length){return value.size()==length&&std::all_of(value.begin(),value.end(),[](char c){return(c>='0'&&c<='9')||(c>='a'&&c<='f');});}
Cache* cacheFor(ProbeState& s,uintptr_t view,bool create){
    Cache* empty=nullptr;const auto now=GetTickCount64();
    for(auto& entry:s.cache){
        if(entry.view&&entry.count&&now-entry.pages[entry.count-1].time>CacheTtl){
            if(s.selected==entry.view){s.dead=true;entry.overflow=true;return &entry;}
            entry={};
        }
        if(entry.view==view)return &entry;if(!entry.view&&!empty)empty=&entry;
    }
    if(create&&empty){empty->view=view;empty->epoch=s.epoch;return empty;}return nullptr;
}
void enterMessages(GumInvocationContext* context,gpointer raw){
    auto& s=*static_cast<ProbeState*>(raw);
    auto* page=static_cast<Page*>(gum_invocation_context_get_listener_invocation_data(context,sizeof(Page)));
    if(!page)return;*page={};if(!s.enabled.load(std::memory_order_acquire))return;
    try {
        std::lock_guard lock(s.mutex);
        if(s.priming&&GetTickCount64()>s.prime_deadline)s.priming=false;
        if(!s.enabled.load(std::memory_order_relaxed)||(!s.priming&&!s.selected))return;
        const auto sub=reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,0));
        if(sub<0x10)return;const auto self=sub-0x10;
        if(!s.priming&&self!=s.selected)return;
        uintptr_t vtable=0;if(!read(self,vtable)||vtable!=s.vtable)return;
        const auto direction=reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,2));if(direction>2)return;
        const auto header=reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,1));
        const auto pending=reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,3));
        struct Header{uintptr_t begin,end;} vector{};uint32_t done=0;uint8_t backward=2,forward=2;
        if(!read(header,vector)||!read(pending+8,done)||done>1
            ||!read(self+0x670,backward)||!read(self+0x680,forward)||backward>1||forward>1
            ||(vector.begin==0)!=(vector.end==0)||vector.end<vector.begin)return;
        const auto delta=vector.end-vector.begin;
        if(delta>100000ull*0x1a8||delta%0x1a8)return;
        bool provenance=false;
        if(reinterpret_cast<uintptr_t>(gum_invocation_context_get_return_address(context))==s.terminal_return&&context->cpu_context){
            struct Terminal{uint32_t state,direction;} terminal{};
            const auto capture=static_cast<uintptr_t>(context->cpu_context->rdi);
            provenance=capture<=UINTPTR_MAX-0x38&&read(capture+0x38,terminal)&&terminal.state==1&&terminal.direction==direction;
        }
        auto* cache=cacheFor(s,self,true);if(!cache||s.sequence==UINT64_MAX)return;
        *page={self,s.epoch,++s.sequence,GetTickCount64(),static_cast<uint32_t>(direction),static_cast<uint32_t>(delta/0x1a8),
            done&&provenance?1u:0u,backward,forward};cache->latest=page->sequence;
    }catch(...){*page={};}
}
void leaveMessages(GumInvocationContext* context,gpointer raw){
    auto& s=*static_cast<ProbeState*>(raw);
    const auto* page=static_cast<Page*>(gum_invocation_context_get_listener_invocation_data(context,sizeof(Page)));
    if(!page||!page->view||!s.enabled.load(std::memory_order_acquire))return;
    try{
        std::lock_guard lock(s.mutex);if(page->epoch!=s.epoch||(s.dead&&page->view==s.selected))return;
        auto* cache=cacheFor(s,page->view,false);if(!cache||cache->epoch!=s.epoch||cache->latest!=page->sequence)return;
        if(cache->overflow)return;
        if(cache->count==PageLimit){cache->overflow=true;if(s.selected==page->view)s.dead=true;return;}
        cache->pages[cache->count++]=*page;
    }catch(...){}
}
void enterDestruction(GumInvocationContext* context,gpointer raw){
    auto& s=*static_cast<ProbeState*>(raw);if(!s.enabled.load(std::memory_order_acquire))return;
    const auto self=reinterpret_cast<uintptr_t>(gum_invocation_context_get_nth_argument(context,0));
    try{std::lock_guard lock(s.mutex);if(auto* cache=cacheFor(s,self,false))*cache={};if(s.selected==self)s.dead=true;}catch(...){}
}
bool attach(ProbeState& s,void* messages,void* destruction){
    // Callback code must outlive a busy Gum detach even if the QQmlEngine is
    // destroyed. This is our already-loaded module, never a remote process.
    HMODULE pinned=nullptr;
    if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN|GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
        reinterpret_cast<const wchar_t*>(&enterMessages),&pinned))return false;
    gum_init_embedded();s.interceptor=gum_interceptor_obtain();if(!s.interceptor)return false;
    s.listeners[0]=gum_make_call_listener(enterMessages,leaveMessages,&s,nullptr);
    s.listeners[1]=gum_make_probe_listener(enterDestruction,&s,nullptr);
    if(!s.listeners[0]||!s.listeners[1])return false;
    GumAttachOptions options{};gum_interceptor_begin_transaction(s.interceptor);
    const bool first=gum_interceptor_attach(s.interceptor,messages,s.listeners[0],&options)==GUM_ATTACH_OK;
    const bool second=first&&gum_interceptor_attach(s.interceptor,destruction,s.listeners[1],&options)==GUM_ATTACH_OK;
    if(!second&&first)gum_interceptor_detach(s.interceptor,s.listeners[0]);
    gum_interceptor_end_transaction(s.interceptor);
    if(!second||!gum_interceptor_flush(s.interceptor))return false;
    s.enabled.store(true,std::memory_order_release);return true;
}
bool detach(ProbeState& s){
    s.enabled.store(false,std::memory_order_release);
    if(!s.interceptor)return true;
    gum_interceptor_begin_transaction(s.interceptor);
    for(auto* listener:s.listeners)if(listener)gum_interceptor_detach(s.interceptor,listener);
    gum_interceptor_end_transaction(s.interceptor);
    for(int attempt=0;attempt<20;++attempt){
        if(gum_interceptor_flush(s.interceptor)){
            for(auto*& listener:s.listeners){if(listener)g_object_unref(listener);listener=nullptr;}
            g_object_unref(s.interceptor);s.interceptor=nullptr;return true;
        }
        QThread::msleep(5);
    }
    return false;
}
}
struct ExportHistoryHost::Impl {
    ValmaxRuntime* runtime;
    std::unique_ptr<ProbeState> state;
    QObject lifetime;
    QTimer timer;
    QPointer<QObject> view;
    ValmaxExportHistoryBinding binding{};
    QByteArray job,token;
    uint64_t source=0;
    bool bound=false,lease=false;
    explicit Impl(ValmaxRuntime* value):runtime(value){
        bool expected=false;lease=owned.compare_exchange_strong(expected,true,std::memory_order_acq_rel);
        if(lease)state=std::make_unique<ProbeState>();
        timer.setInterval(50);QObject::connect(&timer,&QTimer::timeout,&lifetime,[this]{drain();});
    }
    ~Impl(){
        timer.stop();clear();
        if(state&&!detach(*state)){(void)state.release();return;}
        if(lease)owned.store(false,std::memory_order_release);
    }
    bool onThread()const{return lifetime.thread()==QThread::currentThread();}
    bool authorized(const QString& id,const QString& secret)const{return id.toLatin1()==job&&secret.toLatin1()==token&&!job.isEmpty();}
    void clear(){
        if(bound)valmax_runtime_export_history_invalidate(runtime,&binding);
        bound=false;view=nullptr;wipeBinding(binding);
        job.fill(0);token.fill(0);job.clear();token.clear();
        if(state){std::lock_guard lock(state->mutex);++state->epoch;state->priming=false;state->selected=0;state->dead=false;state->cache={};}
    }
    bool begin(const QString& id,const QString& secret){
        if(!onThread()||!runtime||!state||!state->enabled.load(std::memory_order_acquire))return false;
        const auto nextJob=id.toLatin1(),nextToken=secret.toLatin1();if(!hex(nextJob,32)||!hex(nextToken,64))return false;
        // One selected export per Rust Runtime. A different call cannot steal
        // a live view or silently revoke another job.
        if(!job.isEmpty())return authorized(id,secret)&&!bound;
        clear();job=nextJob;token=nextToken;
        {std::lock_guard lock(state->mutex);state->priming=true;state->prime_deadline=GetTickCount64()+CacheTtl;}
        timer.start();
        return true;
    }
    bool bind(QObject* selected,uintptr_t raw,const QString& id,const QString& secret,bool synthetic=false){
        if(!onThread()||!selected||selected->thread()!=lifetime.thread()||!raw||!authorized(id,secret)||!state||bound)return false;
        if(!synthetic&&std::strcmp(selected->metaObject()->className(),"Ui::History::Chat::View")!=0)return false;
        uintptr_t vtable=0;if(!read(raw,vtable)||vtable!=state->vtable)return false;
        {std::lock_guard lock(state->mutex);if(auto* cache=cacheFor(*state,raw,false);cache&&cache->overflow)return false;}
        if(source==UINT64_MAX)return false;
        binding={};binding.size=sizeof(binding);binding.version=1;binding.source=++source;
        std::memcpy(binding.job_id,job.constData(),32);std::memcpy(binding.job_token,token.constData(),64);
        if(valmax_runtime_export_history_bind(runtime,&binding)!=0){wipeBinding(binding);return false;}
        bound=true;view=selected;
        {std::lock_guard lock(state->mutex);state->selected=raw;state->priming=false;}
        QObject::connect(selected,&QObject::destroyed,&lifetime,[this,raw]{
            if(state){std::lock_guard lock(state->mutex);if(state->selected==raw)state->dead=true;}
            drain();
        });
        drain();timer.start();return true;
    }
    void drain(){
        if(!onThread()||!state)return;
        if(!bound){bool expired=false;{std::lock_guard lock(state->mutex);expired=state->priming&&GetTickCount64()>state->prime_deadline;}
            if(expired){timer.stop();clear();}return;}
        std::array<Page,PageLimit> pages{};size_t count=0;bool dead=false;
        {std::lock_guard lock(state->mutex);dead=state->dead||view.isNull();
            if(!dead){if(auto* cache=cacheFor(*state,state->selected,false)){count=cache->count;pages=cache->pages;cache->count=0;}}}
        if(dead){timer.stop();clear();return;}
        for(size_t i=0;i<count;++i){
            const auto& page=pages[i];ValmaxExportHistoryObservation value{};value.binding=binding;
            value.direction=page.direction;value.count=page.count;value.pending_done=page.pending;
            value.more_backward=page.backward;value.more_forward=page.forward;
            const auto result=valmax_runtime_export_history_observe(runtime,&value);
            if(result==3){
                bool overflow=false;
                {std::lock_guard lock(state->mutex);auto* cache=cacheFor(*state,state->selected,true);
                    const size_t remaining=count-i;
                    if(!cache||remaining+cache->count>PageLimit)overflow=true;
                    else {std::move_backward(cache->pages.begin(),cache->pages.begin()+static_cast<ptrdiff_t>(cache->count),cache->pages.begin()+static_cast<ptrdiff_t>(cache->count+remaining));
                        std::copy(pages.begin()+static_cast<ptrdiff_t>(i),pages.begin()+static_cast<ptrdiff_t>(count),cache->pages.begin());cache->count+=remaining;}}
                if(overflow)clear();break;
            }
            if(result!=0){clear();break;}
        }
    }
};
ExportHistoryHost::ExportHistoryHost(ValmaxRuntime* runtime):impl_(std::make_unique<Impl>(runtime)){
#ifndef VALMAX_EXPORT_HISTORY_TESTING
    auto& p=*impl_;if(!p.state)return;auto& s=*p.state;
    const auto main=QDir::toNativeSeparators(QCoreApplication::applicationFilePath());
    const auto qt=QDir::toNativeSeparators(QCoreApplication::applicationDirPath()+QStringLiteral("/Qt6Core.dll"));
    if(!ghost::validate_image(reinterpret_cast<const wchar_t*>(main.utf16()),export_history_pins::kMain,s.main)
        ||!ghost::validate_image(reinterpret_cast<const wchar_t*>(qt.utf16()),export_history_pins::kQt,s.qt))return;
    s.vtable=s.main.base+0x376b768;s.terminal_return=s.main.base+0x11e185a;
    attach(s,reinterpret_cast<void*>(s.main.base+0xe27a00),reinterpret_cast<void*>(s.qt.base+0xeabd0));
#endif
}
ExportHistoryHost::~ExportHistoryHost()=default;
bool ExportHistoryHost::begin(const QString& job,const QString& token){return impl_->begin(job,token);}
bool ExportHistoryHost::bind(QObject* view,const QString& job,const QString& token){return impl_->bind(view,reinterpret_cast<uintptr_t>(view),job,token);}
void ExportHistoryHost::release(const QString& job,const QString& token){if(impl_->onThread()&&impl_->authorized(job,token)){impl_->timer.stop();impl_->clear();}}
#ifdef VALMAX_EXPORT_HISTORY_TESTING
ExportHistoryHost::ExportHistoryHost(ValmaxRuntime* runtime,const TestTargets& targets):impl_(std::make_unique<Impl>(runtime)){
    if(!impl_->state)return;auto& s=*impl_->state;s.vtable=targets.vtable;s.terminal_return=targets.terminal_return;
    attach(s,targets.messages,targets.destruction);
}
bool ExportHistoryHost::bindSynthetic(QObject* view,uintptr_t raw,const QString& job,const QString& token){return impl_->bind(view,raw,job,token,true);}
void ExportHistoryHost::drainSynthetic(){impl_->drain();}
#endif
}
