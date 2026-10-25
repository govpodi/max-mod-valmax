#include "export_history_host.h"
#include "valmax_core.h"
#include <QCoreApplication>
#include <QObject>
#include <array>
#include <atomic>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <source_location>
namespace {
int checks=0,binds=0,observations=0,invalidations=0;
bool busyOnce=false;
std::atomic<int> calls{0};
ValmaxExportHistoryObservation last{};
void check(bool value,std::source_location at=std::source_location::current()){++checks;if(!value){std::cerr<<"line="<<at.line()<<"\n";throw std::runtime_error("history_host_failed");}}
struct Header{uintptr_t begin,end;};
struct Pending{uint64_t unused;uint32_t done;};
constexpr uintptr_t Vtable=0x123456789ull;
alignas(16) std::array<uint8_t,0x690> raw{},other{};
alignas(16) std::array<uint8_t,0x40> capture{};
void setup(){std::memcpy(raw.data(),&Vtable,8);std::memcpy(other.data(),&Vtable,8);raw[0x670]=raw[0x680]=0;other[0x670]=other[0x680]=0;}
void terminal(uint32_t state,uint32_t direction){std::memcpy(capture.data()+0x38,&state,4);std::memcpy(capture.data()+0x3c,&direction,4);}
}
extern "C" __declspec(noinline) void OwnedHistoryMessages(void*,Header*,uint32_t,Pending*){calls.fetch_add(1,std::memory_order_relaxed);}
extern "C" __declspec(noinline) void OwnedHistoryDestruction(void*){calls.fetch_add(1,std::memory_order_relaxed);}
extern "C" void OwnedHistoryCall(void*,Header*,uint32_t,Pending*,void*);
extern "C" void OwnedHistoryReturn();
extern "C" int32_t valmax_runtime_export_history_bind(const ValmaxRuntime*,const ValmaxExportHistoryBinding* value){check(value&&value->size==112&&value->version==1&&value->source);++binds;return 0;}
extern "C" int32_t valmax_runtime_export_history_observe(const ValmaxRuntime*,const ValmaxExportHistoryObservation* value){check(value&&value->binding.size==112&&value->reserved==0);if(busyOnce){busyOnce=false;return 3;}last=*value;++observations;return 0;}
extern "C" int32_t valmax_runtime_export_history_invalidate(const ValmaxRuntime*,const ValmaxExportHistoryBinding*){++invalidations;return 0;}
int main(int argc,char** argv){
    QCoreApplication app(argc,argv);
    try{
        static_assert(sizeof(ValmaxExportHistoryBinding)==112&&sizeof(ValmaxExportHistoryObservation)==136);
        setup();const QString id(32,QChar(u'a')),token(64,QChar(u'b'));
        const valmax::ExportHistoryHost::TestTargets targets{reinterpret_cast<void*>(&OwnedHistoryMessages),reinterpret_cast<void*>(&OwnedHistoryDestruction),Vtable,reinterpret_cast<uintptr_t>(&OwnedHistoryReturn)};
        Header header{0x10000000,0x10000000+3*0x1a8};Pending pending{0,1};terminal(1,1);
        valmax::ExportHistoryHost host(reinterpret_cast<ValmaxRuntime*>(1),targets);
        check(!host.begin(QStringLiteral("bad"),token));
        OwnedHistoryCall(raw.data()+0x10,&header,1,&pending,capture.data());host.drainSynthetic();check(observations==0);
        check(host.begin(id,token));
        // The first page arrives synchronously before QML receives the View.
        OwnedHistoryCall(raw.data()+0x10,&header,1,&pending,capture.data());
        QObject view;
        check(!host.bindSynthetic(&view,reinterpret_cast<uintptr_t>(raw.data()),id,QString(64,QChar(u'c'))));
        check(host.bindSynthetic(&view,reinterpret_cast<uintptr_t>(raw.data()),id,token));
        check(binds==1&&observations==1&&last.count==3&&last.direction==1&&last.pending_done==1&&last.more_backward==0&&last.more_forward==0);
        terminal(0,1);OwnedHistoryCall(raw.data()+0x10,&header,1,&pending,capture.data());host.drainSynthetic();check(observations==2&&last.pending_done==0);
        busyOnce=true;OwnedHistoryCall(raw.data()+0x10,&header,1,&pending,capture.data());host.drainSynthetic();check(observations==2&&invalidations==0);
        host.drainSynthetic();check(observations==3&&invalidations==0);
        terminal(1,1);OwnedHistoryMessages(raw.data()+0x10,&header,1,&pending);host.drainSynthetic();check(observations==4&&last.pending_done==0);
        OwnedHistoryCall(other.data()+0x10,&header,1,&pending,capture.data());host.drainSynthetic();check(observations==4);
        Header oversized{0x10000000,0x10000000+100001ull*0x1a8};
        OwnedHistoryCall(raw.data()+0x10,&oversized,1,&pending,capture.data());host.drainSynthetic();check(observations==4);
        host.release(id,QString(64,QChar(u'c')));check(invalidations==0);
        Header empty{0,0};OwnedHistoryCall(raw.data()+0x10,&empty,1,&pending,capture.data());host.drainSynthetic();check(observations==5&&last.count==0);
        OwnedHistoryDestruction(raw.data());host.drainSynthetic();check(invalidations==1);
        OwnedHistoryCall(raw.data()+0x10,&header,1,&pending,capture.data());host.drainSynthetic();check(observations==5);
        check(host.begin(id,token));check(host.bindSynthetic(&view,reinterpret_cast<uintptr_t>(raw.data()),id,token));
        for(int i=0;i<5;++i)OwnedHistoryCall(raw.data()+0x10,&header,1,&pending,capture.data());
        host.drainSynthetic();check(invalidations==2&&observations==5);
        check(calls.load()>=9);
        std::cout<<"{\"ok\":true,\"checks\":"<<checks<<",\"ownGumCallbackOnly\":true,\"win64RdiAndReturnAddressTested\":true,\"realProfilesTouched\":false,\"realMaxStarted\":false}\n";return 0;
    }catch(...){std::cerr<<"history_host_failed\n";return 1;}
}
