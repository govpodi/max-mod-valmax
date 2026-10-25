#include "retention_queue.h"
#include <windows.h>
#include <bcrypt.h>
#include <algorithm>
#include <bit>
#include <chrono>
#include <cstring>
#include <limits>

namespace valmax::retention {
namespace {
bool hash(std::span<const uint8_t> first,std::span<const uint8_t> second,
          std::span<const uint8_t> third,char (&output)[64]) {
    BCRYPT_ALG_HANDLE algorithm=nullptr;
    BCRYPT_HASH_HANDLE handle=nullptr;
    if(BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0) return false;
    bool ok=BCryptCreateHash(algorithm,&handle,nullptr,0,nullptr,0,0)>=0;
    for(const auto part:{first,second,third}) if(ok && !part.empty())
        ok=BCryptHashData(handle,const_cast<PUCHAR>(part.data()),static_cast<ULONG>(part.size()),0)>=0;
    std::array<uint8_t,32> result{};
    if(ok) ok=BCryptFinishHash(handle,result.data(),static_cast<ULONG>(result.size()),0)>=0;
    if(handle) BCryptDestroyHash(handle);
    BCryptCloseAlgorithmProvider(algorithm,0);
    if(ok) {
        constexpr char hex[]="0123456789abcdef";
        for(size_t i=0;i<result.size();++i) { output[2*i]=hex[result[i]>>4]; output[2*i+1]=hex[result[i]&15]; }
    }
    return ok;
}
std::span<const uint8_t> bytes(const void *pointer,size_t count) {
    return {static_cast<const uint8_t *>(pointer),count};
}
bool valid_ids(int64_t owner,int64_t chat,std::span<const int64_t> ids) {
    if(owner<=0 || chat==0 || ids.empty() || ids.size()>VALMAX_RETENTION_MAX_IDS) return false;
    std::array<int64_t,VALMAX_RETENTION_MAX_IDS> sorted{};
    for(size_t i=0;i<ids.size();++i) { if(ids[i]<=0) return false; sorted[i]=ids[i]; }
    std::sort(sorted.begin(),sorted.begin()+ids.size());
    const bool unique=std::adjacent_find(sorted.begin(),sorted.begin()+ids.size())==sorted.begin()+ids.size();
    SecureZeroMemory(sorted.data(),sizeof(sorted));
    return unique;
}
bool valid_bitmap(const ValmaxRetentionAck &ack) {
    for(size_t i=0;i<VALMAX_RETENTION_MAX_IDS;++i) {
        const bool bit=(ack.retained[i/8] & (1u<<(i%8)))!=0;
        if(bit && (ack.result!=VALMAX_RETENTION_DURABLE || ack.request.kind!=VALMAX_RETENTION_PREPARE_DELETE
            || i>=ack.request.count)) return false;
    }
    return true;
}
}
bool digest_ids(std::span<const int64_t> ids,char (&output)[64]) {
    static_assert(std::endian::native==std::endian::little);
    if(ids.empty() || ids.size()>VALMAX_RETENTION_MAX_IDS) return false;
    constexpr char prefix[]="valmax-retention-ids-v1"; // sizeof includes required NUL
    const auto count=static_cast<uint32_t>(ids.size());
    return hash(bytes(prefix,sizeof(prefix)),bytes(&count,sizeof(count)),bytes(ids.data(),ids.size_bytes()),output);
}
bool policy_revision(bool enabled,char (&output)[64]) {
    constexpr char on[]="{\"enabled\":true,\"schema\":1}\n", off[]="{\"enabled\":false,\"schema\":1}\n";
    return hash(enabled?bytes(on,sizeof(on)-1):bytes(off,sizeof(off)-1),{},{},output);
}
bool Queue::exhausted_locked() const {
    return nonce_==UINT64_MAX || epoch_==UINT64_MAX || policy_.generation==UINT64_MAX;
}
bool Queue::active_locked() const {
    return !stopped_ && !storage_failed_ && policy_.enabled==1 && policy_.storage_ready==1
        && policy_.generation!=0 && !exhausted_locked();
}
void Queue::release_locked(Slot &slot) {
    SecureZeroMemory(&slot,sizeof(slot));
    slot.state=State::Free;
    changed_.notify_all();
}
void Queue::cancel_locked(uint32_t reason) {
    if(epoch_!=UINT64_MAX) ++epoch_;
    for(auto &slot:slots_) {
        if(slot.state==State::Free) continue;
        slot.result=reason;
        slot.retained.fill(0);
        if(slot.state==State::Taken || slot.state==State::Abandoned) slot.state=State::Abandoned;
        else if(slot.waiter) slot.state=State::Completed;
        else release_locked(slot);
    }
    changed_.notify_all();
}
uint32_t Queue::set_policy(const ValmaxRetentionPolicy &policy) {
    char expected[64]{};
    const bool valid=policy.size==sizeof(policy) && policy.version==1 && policy.enabled<=1
        && policy.storage_ready<=1 && policy.reserved==0 && policy.wait_ms>=1
        && policy.wait_ms<=VALMAX_RETENTION_MAX_WAIT_MS && policy.generation!=0
        && policy_revision(policy.enabled==1,expected) && std::memcmp(expected,policy.revision,64)==0;
    std::lock_guard guard(mutex_);
    if(stopped_) return VALMAX_RETENTION_NOT_READY;
    if(!valid || policy.generation<=policy_.generation) {
        policy_.storage_ready=0; error_=VALMAX_RETENTION_POLICY_ERROR;
        cancel_locked(error_); return error_;
    }
    if(exhausted_locked() || epoch_>=UINT64_MAX-1 || policy.generation==UINT64_MAX) {
        error_=VALMAX_RETENTION_EXHAUSTED; storage_failed_=true;
        cancel_locked(error_); return error_;
    }
    cancel_locked(VALMAX_RETENTION_STALE);
    policy_=policy;
    storage_failed_=false;
    error_=policy.storage_ready?VALMAX_RETENTION_OK:VALMAX_RETENTION_NOT_READY;
    return VALMAX_RETENTION_OK;
}
uint32_t Queue::enqueue(int64_t owner,int64_t chat,std::span<const int64_t> ids,uint32_t kind,size_t &index) {
    if(!valid_ids(owner,chat,ids)) return VALMAX_RETENTION_ARGUMENT;
    if(exhausted_locked() || nonce_>=UINT64_MAX-1) { error_=VALMAX_RETENTION_EXHAUSTED; return error_; }
    if(!active_locked()) return VALMAX_RETENTION_NOT_READY;
    for(index=0;index<slots_.size();++index) if(slots_[index].state==State::Free) break;
    if(index==slots_.size()) { error_=VALMAX_RETENTION_QUEUE_FULL; return error_; }
    auto &slot=slots_[index];
    slot.request={sizeof(ValmaxRetentionRequest),1,kind,static_cast<uint32_t>(ids.size()),
        ++nonce_,epoch_,policy_.generation,owner,chat,kind==VALMAX_RETENTION_PREPARE_DELETE?GetTickCount64()+policy_.wait_ms:0,{}};
    if(!digest_ids(ids,slot.request.ids_digest)) { release_locked(slot); error_=VALMAX_RETENTION_STORAGE_ERROR; return error_; }
    std::copy(ids.begin(),ids.end(),slot.ids.begin());
    slot.waiter=kind==VALMAX_RETENTION_PREPARE_DELETE;
    slot.result=VALMAX_RETENTION_NOT_READY;
    slot.state=State::Queued;
    increment(counters_[kind==VALMAX_RETENTION_RECEIVED?0:1]);
    changed_.notify_all();
    return VALMAX_RETENTION_OK;
}
uint32_t Queue::received(int64_t owner,int64_t chat,std::span<const int64_t> ids) {
    std::lock_guard guard(mutex_);
    size_t index=0;
    const auto result=enqueue(owner,chat,ids,VALMAX_RETENTION_RECEIVED,index);
    if(result!=VALMAX_RETENTION_OK && result!=VALMAX_RETENTION_NOT_READY) increment(counters_[3]);
    return result;
}
uint32_t Queue::prepare(int64_t owner,int64_t chat,std::span<const int64_t> ids,Bitmap &retained) {
    retained.fill(0);
    std::unique_lock lock(mutex_);
    size_t index=0;
    auto result=enqueue(owner,chat,ids,VALMAX_RETENTION_PREPARE_DELETE,index);
    if(result!=VALMAX_RETENTION_OK) { if(result!=VALMAX_RETENTION_NOT_READY) increment(counters_[3]); return result; }
    auto &slot=slots_[index]; // waiter prevents reuse until this function exits
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(policy_.wait_ms);
    bool finished=false;
    try {
        finished=changed_.wait_until(lock,deadline,[&slot]{return slot.state==State::Completed || slot.state==State::Abandoned;});
    } catch(...) {
        error_=VALMAX_RETENTION_STORAGE_ERROR;
        storage_failed_=true;
        cancel_locked(error_);
        finished=true;
    }
    if(!finished) {
        result=VALMAX_RETENTION_TIMEOUT;
        if(slot.state==State::Taken) slot.state=State::Abandoned;
    } else result=slot.result;
    if(result==VALMAX_RETENTION_OK) retained=slot.retained;
    else increment(counters_[3]);
    slot.waiter=false;
    if(slot.state!=State::Abandoned) release_locked(slot);
    else { slot.result=result; slot.retained.fill(0); }
    return result;
}
uint32_t Queue::take(ValmaxRetentionRequest &request,int64_t *ids,uint32_t capacity,uint32_t wait_ms) {
    if(request.size!=sizeof(request) || request.version!=1 || ids==nullptr
        || capacity!=VALMAX_RETENTION_MAX_IDS || wait_ms>VALMAX_RETENTION_MAX_WAIT_MS) return VALMAX_RETENTION_ARGUMENT;
    std::unique_lock lock(mutex_);
    auto find=[this]() -> Slot * {
        Slot *first=nullptr;
        for(auto &slot:slots_) {
            if(slot.state==State::Taken || slot.state==State::Abandoned) return nullptr;
            if(slot.state==State::Queued && (!first || slot.request.nonce<first->request.nonce)) first=&slot;
        }
        return first;
    };
    if(wait_ms) changed_.wait_for(lock,std::chrono::milliseconds(wait_ms),[&]{return stopped_ || find()!=nullptr;});
    if(stopped_) return VALMAX_RETENTION_NOT_READY;
    auto *slot=find();
    if(slot==nullptr) {
        for(const auto &item:slots_) if(item.state==State::Taken || item.state==State::Abandoned) return VALMAX_RETENTION_BUSY;
        return VALMAX_RETENTION_NOT_READY;
    }
    if(slot->request.kind==VALMAX_RETENTION_PREPARE_DELETE && GetTickCount64()>=slot->request.deadline_tick) {
        slot->result=VALMAX_RETENTION_TIMEOUT; slot->state=State::Completed;
        changed_.notify_all(); return VALMAX_RETENTION_NOT_READY;
    }
    request=slot->request;
    std::copy_n(slot->ids.begin(),request.count,ids);
    slot->state=State::Taken;
    return VALMAX_RETENTION_OK;
}
uint32_t Queue::ack(const ValmaxRetentionAck &ack) {
    const bool valid=ack.size==sizeof(ack) && ack.version==1 && ack.reserved==0
        && ack.result<=VALMAX_RETENTION_STORAGE_FAILED && ack.request.size==sizeof(ack.request)
        && ack.request.version==1 && ack.request.count>=1 && ack.request.count<=VALMAX_RETENTION_MAX_IDS
        && valid_bitmap(ack);
    std::lock_guard guard(mutex_);
    Slot *slot=nullptr;
    for(auto &item:slots_) if((item.state==State::Taken || item.state==State::Abandoned)
        && item.request.nonce==ack.request.nonce) {slot=&item; break;}
    if(!valid || slot==nullptr || std::memcmp(&ack.request,&slot->request,sizeof(ack.request))!=0) {
        increment(counters_[4]); return VALMAX_RETENTION_ARGUMENT;
    }
    const bool stale=slot->state==State::Abandoned || stopped_ || slot->request.epoch!=epoch_
        || slot->request.generation!=policy_.generation || !active_locked()
        || (slot->request.kind==VALMAX_RETENTION_PREPARE_DELETE && GetTickCount64()>=slot->request.deadline_tick);
    if(stale) {
        increment(counters_[4]); slot->result=VALMAX_RETENTION_STALE;
        if(slot->waiter) {slot->state=State::Completed; changed_.notify_all();}
        else release_locked(*slot);
        return VALMAX_RETENTION_STALE;
    }
    if(ack.result!=VALMAX_RETENTION_DURABLE) {
        increment(counters_[5]); storage_failed_=true; error_=VALMAX_RETENTION_STORAGE_ERROR;
        // The consumer has finished this request before all others are cancelled.
        slot->state=State::Completed; slot->result=error_;
        if(!slot->waiter) release_locked(*slot);
        cancel_locked(error_);
        return VALMAX_RETENTION_STORAGE_ERROR;
    }
    increment(counters_[2]);
    slot->result=VALMAX_RETENTION_OK;
    std::copy(std::begin(ack.retained),std::end(ack.retained),slot->retained.begin());
    if(slot->waiter) {slot->state=State::Completed; changed_.notify_all();}
    else release_locked(*slot);
    return VALMAX_RETENTION_OK;
}
void Queue::status(ValmaxRetentionStatus &status) {
    std::lock_guard guard(mutex_);
    status={}; status.size=sizeof(status); status.version=1; status.error=error_;
    status.enabled=policy_.enabled; status.generation=policy_.generation;
    std::memcpy(status.revision,policy_.revision,64); status.epoch=epoch_;
    for(const auto &slot:slots_) {
        if(slot.state==State::Queued) ++status.queued;
        if(slot.state==State::Taken || slot.state==State::Abandoned) ++status.in_flight;
    }
    status.received_batches=counters_[0]; status.deletion_requests=counters_[1]; status.acknowledged=counters_[2];
    status.missed=counters_[3]; status.rejected_ack=counters_[4]; status.storage_failures=counters_[5];
}
uint32_t Queue::shutdown() {
    std::lock_guard guard(mutex_);
    if(!stopped_) { stopped_=true; error_=VALMAX_RETENTION_NOT_READY; cancel_locked(error_); }
    for(const auto &slot:slots_) if(slot.state!=State::Free) return VALMAX_RETENTION_BUSY;
    return VALMAX_RETENTION_OK;
}
#ifdef VALMAX_RETENTION_TESTING
void Queue::exhaust_for_test(bool nonce,bool epoch) {
    std::lock_guard guard(mutex_);
    if(nonce) nonce_=UINT64_MAX;
    if(epoch) epoch_=UINT64_MAX;
}
#endif
}
namespace {
valmax::retention::Queue &queue() { static auto *value=new valmax::retention::Queue; return *value; }
}
extern "C" uint32_t valmax_retention_set_policy(const ValmaxRetentionPolicy *value) {
    if(!value) return VALMAX_RETENTION_ARGUMENT;
    try {return queue().set_policy(*value);} catch(...) {return VALMAX_RETENTION_STORAGE_ERROR;}
}
extern "C" uint32_t valmax_retention_take(ValmaxRetentionRequest *request,int64_t *ids,uint32_t capacity,uint32_t wait_ms) {
    if(!request) return VALMAX_RETENTION_ARGUMENT;
    try {return queue().take(*request,ids,capacity,wait_ms);} catch(...) {return VALMAX_RETENTION_STORAGE_ERROR;}
}
extern "C" uint32_t valmax_retention_ack(const ValmaxRetentionAck *value) {
    if(!value) return VALMAX_RETENTION_ARGUMENT;
    try {return queue().ack(*value);} catch(...) {return VALMAX_RETENTION_STORAGE_ERROR;}
}
extern "C" uint32_t valmax_retention_status(ValmaxRetentionStatus *value) {
    if(!value || value->size!=sizeof(*value) || value->version!=1) return VALMAX_RETENTION_ARGUMENT;
    try {queue().status(*value); return VALMAX_RETENTION_OK;} catch(...) {return VALMAX_RETENTION_STORAGE_ERROR;}
}
extern "C" uint32_t valmax_retention_shutdown(void) {
    try {return queue().shutdown();} catch(...) {return VALMAX_RETENTION_STORAGE_ERROR;}
}
