#include "result_cache.h"
#include <QRandomGenerator>
#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <map>
#include <mutex>
#include <utility>

namespace valmax::media {
namespace {
template<class T> CacheResult<T> success(T value) { return {std::move(value),CacheCode::Ok}; }
template<class T> CacheResult<T> failure(CacheCode code) { return {{},code}; }
bool valid_id(const QString& s) {
    if(s.size()!=32)return false;
    return std::all_of(s.begin(),s.end(),[](QChar c){return (c>=u'0'&&c<=u'9')||(c>=u'a'&&c<=u'f');});
}
bool text_valid(const QString& s,qsizetype maxPoints,bool nonempty=false) {
    if(s.size()>maxPoints*2 || (nonempty&&s.isEmpty()))return false;
    qsizetype count=0;
    for(qsizetype i=0;i<s.size();++i) {
        const auto u=s[i].unicode();++count;
        if(u<32||u==127||u==0x202a||u==0x202b||u==0x202d||u==0x202e||(u>=0x2066&&u<=0x2069))return false;
        if(s[i].isHighSurrogate()){if(++i>=s.size()||!s[i].isLowSurrogate())return false;}
        else if(s[i].isLowSurrogate())return false;
    }
    return count<=maxPoints;
}
QString compact(const QString& s){return QString(s.constData(),s.size());}
std::optional<Candidate> prepare(const Candidate& c) {
    const auto& m=c.metadata;
    if(!text_valid(m.title,160)||!text_valid(m.author,240,true)||!text_valid(m.license,240,true)
        ||media_suffix(m.mime).isEmpty()||m.bytes<1||m.bytes>MaxFileBytes||m.width<1||m.width>16000
        ||m.height<1||m.height>16000||qint64(m.width)*m.height>80'000'000)return {};
    const auto original=canonical_media_url(c.originalUrl),source=validate_url(m.source,UrlKind::Source);
    if(!original||!source||m.preview!=!c.previewUrl.isEmpty())return {};
    QString preview;
    if(!c.previewUrl.isEmpty()){const auto p=canonical_media_url(c.previewUrl);if(!p)return {};preview=*p.value;}
    // Deep compact every string. A short QString retaining a giant allocation
    // must not bypass the cache budget through shared backing capacity.
    return Candidate{{compact(m.title),compact(m.author),compact(m.license),compact(*source.value),compact(m.mime),m.bytes,m.width,m.height,m.preview},compact(*original.value),compact(preview)};
}
qsizetype charge(const Candidate& c) {
    const auto& m=c.metadata;
    // Conservative fixed node/key/string overhead; payload charged as UTF16.
    // This is a deterministic logical budget, not an allocator/RSS measurement.
    return 1024+2*(m.title.size()+m.author.size()+m.license.size()+m.source.size()+m.mime.size()+c.originalUrl.size()+c.previewUrl.size());
}
QByteArray random_bytes() {
    QByteArray b(16,Qt::Uninitialized);
    for(int n=0;n<4;++n){const quint32 v=QRandomGenerator::system()->generate();for(int j=0;j<4;++j)b[n*4+j]=char(v>>(j*8));}
    return b;
}
}
struct ResultCache::Impl {
    struct Scope {qint64 account;quint64 page;};
    struct Entry {quint64 scope;Candidate candidate;double expires;quint64 serial;qsizetype bytes;};
    mutable std::mutex mutex;
    CacheLimits limits;
    std::function<QByteArray()> entropy;
    std::map<quint64,Scope> scopes;
    std::map<QString,Entry> entries;
    quint64 nextScope=1,nextEntry=1;
    qsizetype bytes=0;
    double lastNow=0;
    bool valid=false;
    explicit Impl(CacheLimits l,std::function<QByteArray()> e):limits(l),entropy(std::move(e)) {
        valid=l.entries>=1&&l.entries<=48&&l.bytes>=1024&&l.bytes<=2*1024*1024&&l.scopes>=1&&l.scopes<=32;
    }
    void revoke(quint64 token){
        for(auto it=entries.begin();it!=entries.end();){if(it->second.scope==token){bytes-=it->second.bytes;it=entries.erase(it);}else ++it;}
        scopes.erase(token);
    }
    bool time_valid(double now)const{return std::isfinite(now)&&now>=lastNow&&now<=double(quint64(1)<<40);}
};
ResultCache::ResultCache(CacheLimits l,std::function<QByteArray()> e):d_(std::make_unique<Impl>(l,std::move(e))){}
ResultCache::~ResultCache()=default;
CacheResult<CacheScope> ResultCache::open_scope(qint64 account,quint64 page) {
    if(account<=0||page==0)return failure<CacheScope>(CacheCode::InvalidInput);
    std::lock_guard lock(d_->mutex);
    if(!d_->valid)return failure<CacheScope>(CacheCode::InvalidInput);
    if(d_->nextScope==std::numeric_limits<quint64>::max())return failure<CacheScope>(CacheCode::SequenceExhausted);
    quint64 old=0;
    for(const auto& [id,s]:d_->scopes)if(s.account==account&&s.page==page){old=id;break;}
    if(!old&&d_->scopes.size()>=size_t(d_->limits.scopes))return failure<CacheScope>(CacheCode::Capacity);
    const auto token=d_->nextScope;
    try {d_->scopes.emplace(token,Impl::Scope{account,page});}
    catch(...){return failure<CacheScope>(CacheCode::Unavailable);}
    ++d_->nextScope;
    if(old)d_->revoke(old);
    return success(CacheScope{token});
}
bool ResultCache::close_scope(CacheScope scope) {
    std::lock_guard lock(d_->mutex);
    if(!d_->scopes.contains(scope.token))return false;
    d_->revoke(scope.token);return true;
}
bool ResultCache::current(CacheScope scope)const{std::lock_guard lock(d_->mutex);return d_->scopes.contains(scope.token);}
void ResultCache::clear(){std::lock_guard lock(d_->mutex);d_->entries.clear();d_->scopes.clear();d_->bytes=0;}
CacheResult<QVector<QJsonObject>> ResultCache::publish_batch(CacheScope scope,const QVector<Candidate>& candidates,double now) {
    using Return=QVector<QJsonObject>;
    if(candidates.size()>ResultLimit)return failure<Return>(CacheCode::InvalidInput);
    // Bound work even for a revoked ticket, but never keep mutex across entropy
    // or any caller-provided callback. Recheck current scope at commit below.
    {std::lock_guard lock(d_->mutex);if(!d_->scopes.contains(scope.token))return failure<Return>(CacheCode::StaleScope);if(!d_->time_valid(now))return failure<Return>(CacheCode::InvalidInput);}
    struct Prepared {Candidate candidate;qsizetype bytes;std::array<QString,IdAttempts> ids;};
    QVector<Prepared> prepared;
    qsizetype total=0;
    try {
        for(const auto& candidate:candidates) {
            auto c=prepare(candidate);if(!c)return failure<Return>(CacheCode::InvalidInput);
            Prepared p{std::move(*c),0,{}};p.bytes=charge(p.candidate);total+=p.bytes;
            for(auto& id:p.ids){const auto bytes=d_->entropy?d_->entropy():random_bytes();if(bytes.size()!=16)return failure<Return>(CacheCode::IdExhausted);id=QString::fromLatin1(bytes.toHex());}
            prepared.push_back(std::move(p));
        }
        std::lock_guard lock(d_->mutex);
        if(!d_->scopes.contains(scope.token))return failure<Return>(CacheCode::StaleScope);
        if(!d_->time_valid(now))return failure<Return>(CacheCode::InvalidInput);
        if(candidates.size()>d_->limits.entries||total>d_->limits.bytes)return failure<Return>(CacheCode::Capacity);
        if(quint64(candidates.size())>std::numeric_limits<quint64>::max()-d_->nextEntry)return failure<Return>(CacheCode::SequenceExhausted);
        // Work on a private bounded copy. Failure leaves entries, counters and
        // all published IDs unchanged; swap is the sole commit point.
        auto next=d_->entries;
        qsizetype nextBytes=d_->bytes;
        for(auto it=next.begin();it!=next.end();){if(it->second.expires<=now){nextBytes-=it->second.bytes;it=next.erase(it);}else ++it;}
        Return rows;quint64 serial=d_->nextEntry;
        for(auto& p:prepared) {
            QString id;
            for(const auto& option:p.ids)if(!next.contains(option)){id=option;break;}
            if(id.isEmpty())return failure<Return>(CacheCode::IdExhausted);
            auto row=p.candidate.metadata.with_id(id);if(!row)return failure<Return>(CacheCode::Unavailable);
            rows.push_back(*row.value);
            next.emplace(id,Impl::Entry{scope.token,std::move(p.candidate),now+TtlSeconds,serial++,p.bytes});nextBytes+=p.bytes;
        }
        while(next.size()>size_t(d_->limits.entries)||nextBytes>d_->limits.bytes) {
            const auto oldest=std::min_element(next.begin(),next.end(),[](const auto& a,const auto& b){return a.second.serial<b.second.serial;});
            if(oldest==next.end()||oldest->second.serial>=d_->nextEntry)return failure<Return>(CacheCode::Capacity);
            nextBytes-=oldest->second.bytes;next.erase(oldest);
        }
        d_->entries.swap(next);d_->bytes=nextBytes;d_->nextEntry=serial;d_->lastNow=now;
        return success(std::move(rows));
    }catch(...){return failure<Return>(CacheCode::Unavailable);}
}
CacheResult<Candidate> ResultCache::resolve(CacheScope scope,const QString& id,double now) {
    if(!valid_id(id))return failure<Candidate>(CacheCode::InvalidInput);
    std::lock_guard lock(d_->mutex);
    if(!d_->scopes.contains(scope.token))return failure<Candidate>(CacheCode::StaleScope);
    if(!d_->time_valid(now))return failure<Candidate>(CacheCode::InvalidInput);
    d_->lastNow=now;
    const auto it=d_->entries.find(id);
    if(it==d_->entries.end()||it->second.scope!=scope.token)return failure<Candidate>(CacheCode::ResultExpired);
    if(it->second.expires<=now){d_->bytes-=it->second.bytes;d_->entries.erase(it);return failure<Candidate>(CacheCode::ResultExpired);}
    try{return success(it->second.candidate);}catch(...){return failure<Candidate>(CacheCode::Unavailable);}
}
CacheSnapshot ResultCache::snapshot()const{std::lock_guard lock(d_->mutex);return {int(d_->entries.size()),int(d_->scopes.size()),d_->bytes};}
} // namespace valmax::media
