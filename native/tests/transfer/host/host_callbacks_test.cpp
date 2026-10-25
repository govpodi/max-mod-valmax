#include "host_callbacks.h"
#include <windows.h>
#include <algorithm>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <type_traits>
#include <vector>

using namespace valmax::transfer;
namespace {
uint32_t checks=0;
void check(bool value,const char *description){++checks;if(!value)throw std::runtime_error(description);}
constexpr ClientScan empty{0,true,ScanCode::Complete},active{1,true,ScanCode::Complete},unknown{0,false,ScanCode::Incomplete};
struct Fake {
    uint64_t time=1000;
    uint32_t scans=0,sleeps=0,max_delay=0;
    std::vector<uint64_t> deadlines;
    std::function<ClientScan(Fake&,const ScanControl&)> scanner;
    std::function<void(Fake&,uint32_t)> waiter;
    std::function<uint64_t(Fake&)> clock;
    NativeTransferHost::TestApi api() noexcept {return {this,&now,&scan,&sleep};}
    static uint64_t now(void *context){auto &f=*static_cast<Fake*>(context);return f.clock?f.clock(f):f.time;}
    static ClientScan scan(void *context,const ScanControl &control){
        auto &f=*static_cast<Fake*>(context);++f.scans;f.deadlines.push_back(control.deadline_ms);
        if(!control.cancelled)throw std::runtime_error("cancel flag not passed to scanner");
        return f.scanner?f.scanner(f,control):empty;
    }
    static void sleep(void *context,uint32_t delay){
        auto &f=*static_cast<Fake*>(context);++f.sleeps;f.max_delay=std::max(f.max_delay,delay);
        if(delay==0||delay>50)throw std::runtime_error("unbounded synthetic sleep");
        if(f.waiter)f.waiter(f,delay);else f.time+=delay;
    }
};
void abi_and_precancel(){
    static_assert(!std::is_copy_constructible_v<NativeTransferHost> && !std::is_move_constructible_v<NativeTransferHost>);
    NativeTransferHost host;auto api=host.callbacks();
    check(api.size==40&&api.version==1&&api.context==&host,"exact C ABI context");
    check(api.wait_closed&&api.check_closed&&api.cancelled,"all callbacks present");
    check(api.wait_closed(nullptr)==2&&api.check_closed(nullptr)==2&&api.cancelled(nullptr)==1,"null contexts fail closed");
    check(api.cancelled(api.context)==0,"fresh host not cancelled");
    check(api.check_closed(api.context)==2,"no scan or permission before successful wait");
    host.request_cancel();host.request_cancel();
    check(api.cancelled(api.context)==1,"monotonic atomic cancellation");
    check(api.wait_closed(api.context)==3&&api.check_closed(api.context)==3,"production host pre-cancelled without scan");
}
void empty_then_fresh_changes(){
    Fake f;NativeTransferHost host(f.api());auto api=host.callbacks();
    check(api.check_closed(api.context)==2&&f.scans==0,"check cannot skip initial wait");
    check(api.wait_closed(api.context)==0,"complete two second sampled absence accepted");
    check(f.time==3000&&f.scans==41&&f.sleeps==40&&f.max_delay==50,"settle duration and poll budget exact");
    check(std::all_of(f.deadlines.begin(),f.deadlines.end(),[](auto d){return d==121000;}),"every wait scan uses one deadline");
    f.time+=1000000;const auto sleeps=f.sleeps,scans=f.scans;
    check(api.check_closed(api.context)==0&&f.scans==scans+1&&f.sleeps==sleeps,"fresh check one scan without settle after slow decode");
    check(f.deadlines.back()==f.time+kWaitMs,"fresh scan gets its own finite deadline");
    check(api.check_closed(api.context)==0&&f.scans==scans+2,"second fresh check rescans");
    f.scanner=[](Fake&,const ScanControl&){return active;};
    check(api.check_closed(api.context)==1,"new client after successful wait blocks commit");
    const auto after=f.scans;f.scanner={};
    check(api.check_closed(api.context)==2&&f.scans==after,"failed fresh check revokes successful wait");
    check(api.wait_closed(api.context)==2&&f.scans==after,"one-shot wait cannot reset deadline");
}
void reset_and_deadline(){
    Fake f;f.scanner=[](Fake &v,const ScanControl&){
        if(v.scans==40)return active;
        if(v.scans==80)return unknown;
        return empty;
    };
    NativeTransferHost host(f.api());auto api=host.callbacks();
    check(api.wait_closed(api.context)==0,"candidate and incomplete scan each restart settle");
    check(f.scans==121&&f.time==7000,"only full final absence interval counts");
    for(const auto value:{active,unknown,ClientScan{0,true,ScanCode::Incomplete}}){
        Fake blocked;blocked.scanner=[value](Fake&,const ScanControl&){return value;};
        NativeTransferHost waiting(blocked.api());auto call=waiting.callbacks();
        check(call.wait_closed(call.context)==1,"persistent candidate/unknown reaches finite deadline");
        check(blocked.time==121000&&blocked.scans==2400&&blocked.max_delay==50,"deadline never silently becomes closed");
        check(call.check_closed(call.context)==2,"deadline cannot authorize check");
    }
    Fake last;last.scanner=[](Fake &v,const ScanControl&){return v.time<119050?active:empty;};
    NativeTransferHost late(last.api());auto call=late.callbacks();
    check(call.wait_closed(call.context)==1,"deadline wins before too-late settle");
    Fake residual;residual.scanner=[](Fake &v,const ScanControl&){v.time+=17;return active;};
    NativeTransferHost bounded(residual.api());call=bounded.callbacks();
    check(call.wait_closed(call.context)==1&&residual.time>=121000&&residual.time<=121017&&residual.max_delay<=50,"sleep bounded; already-running scan may cross deadline");
}
void cancellation(){
    Fake f;NativeTransferHost host(f.api());auto api=host.callbacks();
    f.waiter=[&](Fake &v,uint32_t delay){v.time+=delay;host.request_cancel();};
    check(api.wait_closed(api.context)==3&&f.scans==1,"cancel after sleep skips next scan");
    check(api.check_closed(api.context)==3,"cancel persists after failed wait");
    Fake concurrent;NativeTransferHost owner(concurrent.api());auto call=owner.callbacks();
    concurrent.scanner=[&](Fake&,const ScanControl &control){
        uint32_t wrong_wait=99,wrong_check=99,cancel_flag=99;
        std::thread other([&]{wrong_wait=call.wait_closed(call.context);wrong_check=call.check_closed(call.context);
            owner.request_cancel();cancel_flag=call.cancelled(call.context);});
        other.join();
        if(wrong_wait!=2||wrong_check!=2||cancel_flag!=1||!control.cancelled->load())
            throw std::runtime_error("cross-thread contract failed");
        return empty;
    };
    check(call.wait_closed(call.context)==3&&concurrent.scans==1,"cross-thread cancel during scan overrides complete result");
    Fake success;NativeTransferHost after(success.api());call=after.callbacks();
    check(call.wait_closed(call.context)==0,"precondition for post-wait cancel");
    after.request_cancel();const auto scans=success.scans;
    check(call.check_closed(call.context)==3&&success.scans==scans,"cancel after wait does no fresh I/O");
    Fake scanner_cancel;scanner_cancel.scanner=[](Fake&,const ScanControl&){return ClientScan{0,false,ScanCode::Cancelled};};
    NativeTransferHost reported(scanner_cancel.api());call=reported.callbacks();
    check(call.wait_closed(call.context)==3,"scanner cancellation propagated");
}
void finite_failures(){
    Fake reentrant;NativeTransferHost host(reentrant.api());auto api=host.callbacks();bool guarded=false;
    reentrant.scanner=[&](Fake&,const ScanControl&){
        guarded=api.wait_closed(api.context)==2&&api.check_closed(api.context)==2;return empty;
    };
    check(api.wait_closed(api.context)==0&&guarded,"reentry is rejected without corrupting outer callback");
    check(api.wait_closed(api.context)==2&&api.check_closed(api.context)==2,"repeated wait revokes previous success");
    for(int where=0;where<3;++where){
        Fake f;
        if(where==0)f.clock=[](Fake&)->uint64_t{throw std::runtime_error("synthetic clock failure");};
        if(where==1)f.scanner=[](Fake&,const ScanControl&)->ClientScan{throw std::runtime_error("synthetic scan failure");};
        if(where==2)f.waiter=[](Fake&,uint32_t){throw std::runtime_error("synthetic waiter failure");};
        NativeTransferHost failed(f.api());auto call=failed.callbacks();
        check(call.wait_closed(call.context)==2&&call.check_closed(call.context)==2,"exception caught before C ABI return");
    }
    Fake overflow;overflow.time=UINT64_MAX-1;NativeTransferHost huge(overflow.api());api=huge.callbacks();
    check(api.wait_closed(api.context)==2&&overflow.scans==0,"overflow clock fails without scan");
    Fake rollback;rollback.scanner=[](Fake &v,const ScanControl&){v.time-=1;return empty;};
    NativeTransferHost backwards(rollback.api());api=backwards.callbacks();
    check(api.wait_closed(api.context)==2,"clock rollback fails closed");
    Fake deadline;deadline.scanner=[](Fake&,const ScanControl&){return ClientScan{0,false,ScanCode::Deadline};};
    NativeTransferHost timed(deadline.api());api=timed.callbacks();
    check(api.wait_closed(api.context)==1&&deadline.sleeps==0,"scanner deadline needs no retry");
}
void fresh_scan_failures(){
    const std::vector<ClientScan> values={unknown,{0,true,ScanCode::Incomplete},{0,true,static_cast<ScanCode>(99)},
        {0,false,ScanCode::Cancelled},{0,false,ScanCode::Deadline},active};
    const uint32_t codes[]={2,2,2,3,1,1};
    for(size_t i=0;i<values.size();++i){
        Fake f;NativeTransferHost host(f.api());auto api=host.callbacks();
        if(api.wait_closed(api.context)!=0)throw std::runtime_error("fresh scan test setup failed");
        const auto old=f.scans,sleeps=f.sleeps;f.scanner=[&,i](Fake&,const ScanControl&){return values[i];};
        check(api.check_closed(api.context)==codes[i]&&f.scans==old+1&&f.sleeps==sleeps,"fresh error maps exactly without retries");
        check(api.check_closed(api.context)==2&&f.scans==old+1,"unsuccessful fresh result revokes initial grant");
    }
    for(int edge=0;edge<4;++edge){
        Fake f;NativeTransferHost host(f.api());auto api=host.callbacks();
        if(api.wait_closed(api.context)!=0)throw std::runtime_error("fresh timing test setup failed");
        if(edge==0)f.time=UINT64_MAX;
        else f.scanner=[edge](Fake &v,const ScanControl &control)->ClientScan{
            if(edge==1)v.time=control.deadline_ms;
            if(edge==2)--v.time;
            if(edge==3)throw std::runtime_error("synthetic fresh scan failure");
            return empty;
        };
        check(api.check_closed(api.context)==(edge==1?1u:2u),"fresh clock/exception guard finite");
    }
    Fake f;NativeTransferHost host(f.api());auto api=host.callbacks();
    if(api.wait_closed(api.context)!=0)throw std::runtime_error("fresh cancel setup failed");
    f.scanner=[&](Fake&,const ScanControl&){host.request_cancel();return empty;};
    check(api.check_closed(api.context)==3,"fresh scan cancellation beats empty result");
}
}
int main(){
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    try {
        abi_and_precancel();empty_then_fresh_changes();reset_and_deadline();cancellation();finite_failures();fresh_scan_failures();
        std::cout<<"{\"verified\":true,\"checks\":"<<checks<<",\"syntheticScansOnly\":true,\"realEnumeration\":false,\"profilesRead\":false,\"credentialsRead\":false,\"processControl\":false}\n";
        return 0;
    }catch(const std::exception &error){std::cerr<<"transfer host test failed: "<<error.what()<<"\n";return 1;}
    catch(...){std::cerr<<"transfer host test failed\n";return 1;}
}
