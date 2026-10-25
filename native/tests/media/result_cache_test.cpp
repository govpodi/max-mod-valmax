#include "result_cache.h"
#include <QJsonDocument>
#include <atomic>
#include <barrier>
#include <iostream>
#include <limits>
#include <thread>
#include <vector>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
#endif
using namespace valmax::media;
namespace {
int checks=0;
void check(bool condition){++checks;if(!condition){std::cerr<<"failed check "<<checks<<'\n';std::exit(1);}}
Candidate candidate(){return {{"Synthetic title","Test author","CC0","https://commons.wikimedia.org/wiki/File:Synthetic.png","image/png",17,320,240,true},"https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic.png","https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Synthetic.png/240px-Synthetic.png"};}
qsizetype charge(const Candidate& c){const auto& m=c.metadata;return 1024+2*(m.title.size()+m.author.size()+m.license.size()+m.source.size()+m.mime.size()+c.originalUrl.size()+c.previewUrl.size());}
auto entropy(){return [counter=std::make_shared<std::atomic<quint64>>(1)]{const auto v=counter->fetch_add(1);QByteArray b(16,'x');for(int i=0;i<8;++i)b[i]=char(v>>(8*i));return b;};}
QString id(const CacheResult<QVector<QJsonObject>>& result,int row=0){return result.value->at(row)["id"].toString();}
void basic(){
    ResultCache c({},entropy());const auto s=*c.open_scope(1,1).value;
    check(c.current(s));check(!c.current({0}));check(!c.open_scope(0,1));check(!c.open_scope(1,0));
    auto r=c.publish_batch(s,{candidate()},10);check(bool(r));check(id(r).size()==32);
    const auto wire=QJsonDocument(r.value->at(0)).toJson();
    check(!wire.contains("upload.wikimedia.org"));check(!wire.contains("pageId"));check(!wire.contains("accountId"));
    check(c.snapshot().entries==1);check(c.snapshot().chargedBytes==charge(candidate()));
    auto found=c.resolve(s,id(r),10);check(bool(found));check(found.value->originalUrl==candidate().originalUrl);
    check(c.resolve(s,id(r),9).error==CacheCode::InvalidInput);check(c.resolve(s,"../path",10).error==CacheCode::InvalidInput);
    check(c.resolve(s,id(r),1209.999).value.has_value());
    check(c.resolve(s,id(r),1210).error==CacheCode::ResultExpired);check(c.snapshot().entries==0);check(c.snapshot().chargedBytes==0);
    check(!c.publish_batch(s,{candidate()},std::numeric_limits<double>::quiet_NaN()));
    check(!c.publish_batch(s,{candidate()},std::numeric_limits<double>::infinity()));
    check(!c.publish_batch(s,{candidate()},-1));check(!c.publish_batch(s,{candidate()},double(quint64(1)<<41)));
    check(c.close_scope(s));check(!c.close_scope(s));check(!c.current(s));
    check(c.publish_batch(s,{candidate()},1300).error==CacheCode::StaleScope);
}
void scopes(){
    ResultCache c({},entropy());auto a=*c.open_scope(10,20).value;auto b=*c.open_scope(11,20).value;auto d=*c.open_scope(10,21).value;
    check(a.token!=b.token&&a.token!=d.token&&b.token!=d.token);
    auto result=c.publish_batch(a,{candidate()},0);check(bool(result));
    check(c.resolve(b,id(result),0).error==CacheCode::ResultExpired);check(c.resolve(d,id(result),0).error==CacheCode::ResultExpired);
    const auto oldCopy=*c.resolve(a,id(result),0).value;
    const auto replacement=*c.open_scope(10,20).value;check(replacement.token!=a.token);check(!c.current(a));check(c.snapshot().entries==0);
    check(c.current(b)&&c.current(d));check(!oldCopy.originalUrl.isEmpty()); // copies aren't physically revoked; scope is.
    check(c.resolve(a,id(result),0).error==CacheCode::StaleScope);check(c.resolve(replacement,id(result),0).error==CacheCode::ResultExpired);
    const auto huge=*c.open_scope(9'007'199'254'740'992LL,std::numeric_limits<quint64>::max()).value;
    const auto adjacent=*c.open_scope(9'007'199'254'740'993LL,std::numeric_limits<quint64>::max()).value;
    check(huge.token!=adjacent.token);
    const auto largeIdResult=c.publish_batch(huge,{candidate()},0);check(bool(largeIdResult));
    check(c.resolve(adjacent,id(largeIdResult),0).error==CacheCode::ResultExpired);
    c.clear();check(c.snapshot().entries==0&&c.snapshot().scopes==0&&c.snapshot().chargedBytes==0);check(!c.current(b));
    auto fresh=*c.open_scope(10,20).value;check(fresh.token>replacement.token);
    for(int i=1;i<32;++i)check(bool(c.open_scope(i,100)));
    check(c.open_scope(500,1).error==CacheCode::Capacity);
    check(bool(c.open_scope(10,20))); // replacing at capacity remains possible
    ResultCache bad({0,1024,1});check(bad.open_scope(1,1).error==CacheCode::InvalidInput);
    ResultCache bad2({49,1024,1});check(!bad2.open_scope(1,1));
    ResultCache bad3({1,2*1024*1024+1,1});check(!bad3.open_scope(1,1));
}
void atomic_and_budget(){
    ResultCache c({},entropy());const auto s=*c.open_scope(1,1).value;
    QVector<Candidate> batch(6,candidate());auto r=c.publish_batch(s,batch,0);check(bool(r));check(r.value->size()==6);check(c.snapshot().chargedBytes==6*charge(candidate()));
    batch[3].originalUrl="https://invalid.example/private";auto failed=c.publish_batch(s,batch,1);check(failed.error==CacheCode::InvalidInput);check(c.snapshot().entries==6);
    for(int i=0;i<6;++i)check(bool(c.resolve(s,id(r,i),1)));
    check(!c.publish_batch(s,QVector<Candidate>(7,candidate()),1));check(c.snapshot().entries==6);
    auto altered=candidate();altered.metadata.preview=false;check(!c.publish_batch(s,{altered},1));
    altered=candidate();altered.metadata.author="";check(!c.publish_batch(s,{altered},1));
    altered=candidate();altered.metadata.width=16001;check(!c.publish_batch(s,{altered},1));
    altered=candidate();altered.metadata.title=QString(161,'x');check(!c.publish_batch(s,{altered},1));
    altered=candidate();altered.metadata.author="bad\u202etext";check(!c.publish_batch(s,{altered},1));
    check(bool(c.publish_batch(s,{},1)));check(c.snapshot().entries==6);
    auto tracked=candidate();tracked.originalUrl+="?utm_source=synthetic";auto stripped=c.publish_batch(s,{tracked},1);check(bool(stripped));check(c.resolve(s,id(stripped),1).value->originalUrl==candidate().originalUrl);
    ResultCache countLimited({2,2*1024*1024,32},entropy());auto p=*countLimited.open_scope(1,1).value;
    auto first=countLimited.publish_batch(p,{candidate()},0);auto second=countLimited.publish_batch(p,{candidate()},0);auto third=countLimited.publish_batch(p,{candidate()},0);
    check(bool(third));check(countLimited.resolve(p,id(first),0).error==CacheCode::ResultExpired);check(bool(countLimited.resolve(p,id(second),0)));check(countLimited.snapshot().entries==2);
    ResultCache bytesLimited({48,charge(candidate())*2,32},entropy());p=*bytesLimited.open_scope(1,1).value;
    first=bytesLimited.publish_batch(p,{candidate()},0);second=bytesLimited.publish_batch(p,{candidate()},0);third=bytesLimited.publish_batch(p,{candidate()},0);
    check(bool(third));check(bytesLimited.resolve(p,id(first),0).error==CacheCode::ResultExpired);check(bytesLimited.snapshot().chargedBytes==2*charge(candidate()));
    check(bytesLimited.publish_batch(p,QVector<Candidate>(3,candidate()),0).error==CacheCode::Capacity);check(bytesLimited.snapshot().entries==2);
    ResultCache tooSmall({48,1024,32},entropy());p=*tooSmall.open_scope(1,1).value;check(tooSmall.publish_batch(p,{candidate()},0).error==CacheCode::Capacity);check(tooSmall.snapshot().entries==0);
    // Source backing capacity is not retained, including oversized QStrings
    // narrowed by callers before admission. Values, not reserved capacity, copy.
    auto large=candidate();large.metadata.title.reserve(2'000'000);check(large.metadata.title.capacity()>1'000'000);
    auto compacted=c.publish_batch(s,{large},2);check(bool(compacted));auto copied=c.resolve(s,id(compacted),2);check(copied.value->metadata.title.capacity()<1000);
}
void collisions(){
    ResultCache c({},[]{return QByteArray(16,'x');});const auto s=*c.open_scope(1,1).value;
    auto first=c.publish_batch(s,{candidate()},0);check(bool(first));
    check(c.publish_batch(s,{candidate()},0).error==CacheCode::IdExhausted);check(c.snapshot().entries==1);check(bool(c.resolve(s,id(first),0)));
    ResultCache duplicate({},[]{return QByteArray(16,'y');});auto p=*duplicate.open_scope(1,1).value;
    check(duplicate.publish_batch(p,QVector<Candidate>(2,candidate()),0).error==CacheCode::IdExhausted);check(duplicate.snapshot().entries==0);check(duplicate.snapshot().chargedBytes==0);
    ResultCache bad({},[]{return QByteArray(15,'x');});p=*bad.open_scope(1,1).value;check(bad.publish_batch(p,{candidate()},0).error==CacheCode::IdExhausted);
    ResultCache throwing({},[]()->QByteArray{throw 1;});p=*throwing.open_scope(1,1).value;check(throwing.publish_batch(p,{candidate()},0).error==CacheCode::Unavailable);check(throwing.snapshot().entries==0);
    ResultCache random;const auto rs=*random.open_scope(1,1).value;auto r=random.publish_batch(rs,QVector<Candidate>(6,candidate()),0);check(bool(r));
    for(const auto& row:*r.value){const auto rid=row["id"].toString();check(rid.size()==32);check(bool(random.resolve(rs,rid,0)));}
}
void race(){
    // Pause the untrusted entropy callback OUTSIDE lock; close/reopen must
    // complete, and the delayed batch must fail its second scope gate.
    std::barrier gate(2);std::atomic<bool> first=true;
    ResultCache c({},[&]{if(first.exchange(false)){gate.arrive_and_wait();gate.arrive_and_wait();}return QByteArray(16,'x');});
    const auto s=*c.open_scope(1,1).value;CacheCode code=CacheCode::Ok;
    std::thread worker([&]{code=c.publish_batch(s,{candidate()},0).error;});
    gate.arrive_and_wait();check(c.close_scope(s));auto newScope=*c.open_scope(1,1).value;gate.arrive_and_wait();worker.join();
    check(code==CacheCode::StaleScope);check(c.snapshot().entries==0);check(c.current(newScope));
    ResultCache stress({},entropy());std::atomic<int> committed=0;
    std::vector<std::thread> workers;
    for(int t=0;t<8;++t)workers.emplace_back([&,t]{for(int i=0;i<80;++i){auto scope=stress.open_scope(t+1,1);if(!scope)continue;auto r=stress.publish_batch(*scope.value,{candidate()},0);if(r)++committed;stress.close_scope(*scope.value);}});
    for(auto& w:workers)w.join();check(committed==640);check(stress.snapshot().entries==0);check(stress.snapshot().scopes==0);check(stress.snapshot().chargedBytes==0);
}
}
int main(){
#ifdef _WIN32
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
#endif
    basic();scopes();atomic_and_budget();collisions();race();
    std::cout<<"{\"passed\":true,\"checks\":"<<checks<<"}\n";return 0;
}
