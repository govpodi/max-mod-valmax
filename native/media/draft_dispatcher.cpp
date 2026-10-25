#include "draft_dispatcher.h"
#include <QCoreApplication>
#include <QGuiApplication>
#include <QMetaMethod>
#include <QMetaProperty>
#include <QThread>
#include <QVariant>

namespace valmax::media {
namespace {
bool gui_thread() noexcept {
    const auto app=QCoreApplication::instance();
    // Check the stable application thread before touching any caller QPointer.
    return app && QThread::currentThread()==app->thread() && qobject_cast<QGuiApplication*>(app);
}
bool local_file(const QUrl& url) {
    if(!url.isValid() || !url.isLocalFile() || url.scheme()!=QStringLiteral("file")
        || !url.authority().isEmpty() || url.hasQuery() || url.hasFragment()) return false;
    const auto encoded=url.toEncoded(QUrl::FullyEncoded);
    if(encoded.isEmpty() || encoded.size()>96000) return false;
    const auto path=url.path(QUrl::FullyDecoded);
    if(path.size()<4 || path[0]!=u'/' || !((path[1]>=u'A'&&path[1]<=u'Z')||(path[1]>=u'a'&&path[1]<=u'z'))
        || path[2]!=u':' || path[3]!=u'/') return false;
    for(const auto c:path) if(c.unicode()<32 || c.unicode()==127 || c==u'\\') return false;
    const auto parts=path.mid(4).split(u'/',Qt::KeepEmptyParts);
    for(const auto& part:parts) if(part.isEmpty() || part==QStringLiteral(".") || part==QStringLiteral("..") || part.contains(u':')) return false;
    return true;
}
}

std::optional<DraftBinding> DraftBinding::create(MediaEngineContext* context,
    const EngineMediaLease& lease,QPointer<QObject> sender,QPointer<QObject> upload,
    QPointer<QObject> model,const SearchGeneration& search,std::function<bool()> stillCurrent) {
    if(!gui_thread()) return std::nullopt;
    if(!context || context->thread()!=QThread::currentThread() || !context->current(lease)
        || !sender || !upload || !model || !search.valid() || !stillCurrent) return std::nullopt;
    if(sender->thread()!=QThread::currentThread() || upload->thread()!=QThread::currentThread()
        || model->thread()!=QThread::currentThread()) return std::nullopt;
    DraftBinding binding;
    binding.context_=context; binding.lease_=lease; binding.sender_=sender; binding.upload_=upload;
    binding.model_=model; binding.generation_=search.current;
    binding.stillCurrent_=std::move(stillCurrent);
    return binding;
}

DraftDispatchResult dispatch_retained(const DraftBinding& source,const RetainedDraftFile& input) noexcept {
    if(!gui_thread()) return DraftDispatchResult::Unavailable;
    try {
        // Caller-owned binding/DTO may be destroyed by a property getter or
        // synchronous upload callback. Make all snapshots before calling either.
        const auto binding=source;
        const auto file=input;
        const auto current=[&]() {
            if(!binding.context_ || !file.search.valid() || file.search.current!=binding.generation_
                || !binding.context_->current(binding.lease_) || !binding.context_->current(file.lease))
                return DraftDispatchResult::Stale;
            if(!binding.sender_ || !binding.upload_ || !binding.model_)
                return DraftDispatchResult::Unavailable;
            const auto thread=QThread::currentThread();
            if(binding.context_->thread()!=thread || binding.sender_->thread()!=thread
                || binding.upload_->thread()!=thread || binding.model_->thread()!=thread)
                return DraftDispatchResult::Unavailable;
            return DraftDispatchResult::Dispatched; // admissible, not yet invoked
        };
        auto status=current();
        if(status!=DraftDispatchResult::Dispatched) return status;
        if(!local_file(file.local_url)) return DraftDispatchResult::Unavailable;

        const auto meta=binding.upload_->metaObject();
        const auto index=meta->indexOfMethod("uploadPhotoOrVideo(QList<QUrl>)");
        if(index<0) return DraftDispatchResult::Unavailable;
        const auto method=meta->method(index);
        if(method.access()!=QMetaMethod::Public
            || (method.methodType()!=QMetaMethod::Method && method.methodType()!=QMetaMethod::Slot)
            || method.returnMetaType()!=QMetaType::fromType<void>() || method.parameterCount()!=1
            || method.parameterMetaType(0)!=QMetaType::fromType<QList<QUrl>>()
            || method.methodSignature()!=QByteArrayLiteral("uploadPhotoOrVideo(QList<QUrl>)"))
            return DraftDispatchResult::Unavailable;
        const auto modelMeta=binding.model_->metaObject();
        const auto countIndex=modelMeta->indexOfProperty("attachmentsCount");
        if(countIndex<0) return DraftDispatchResult::Unavailable;
        const auto countProperty=modelMeta->property(countIndex);
        if(!countProperty.isReadable() || countProperty.metaType()!=QMetaType::fromType<qsizetype>())
            return DraftDispatchResult::Unavailable;
        // This getter is third-party code and can synchronously reenter/delete
        // any bound object. Never dereference them before the complete recheck.
        const auto value=countProperty.read(binding.model_.data());
        status=current();
        if(status!=DraftDispatchResult::Dispatched) return status;
        if(value.metaType()!=QMetaType::fromType<qsizetype>()) return DraftDispatchResult::Unavailable;
        const auto count=value.value<qsizetype>();
        if(count<0) return DraftDispatchResult::Unavailable;
        if(count>=10) return DraftDispatchResult::Limit;
        // A native getter can change the actual chat/account without first
        // changing our lease. Query the host's live identity after that getter.
        const auto identityCurrent=binding.stillCurrent_();
        status=current();
        if(status!=DraftDispatchResult::Dispatched) return status;
        if(!identityCurrent) return DraftDispatchResult::Stale;
        const QList<QUrl> urls{file.local_url};
        // No callbacks or event pumping between the final guard and this direct
        // call. Once invoked successfully, do not inspect possibly deleted QObjects.
        return method.invoke(binding.upload_.data(),Qt::DirectConnection,Q_ARG(QList<QUrl>,urls))
            ? DraftDispatchResult::Dispatched : DraftDispatchResult::Unavailable;
    } catch(...) { return DraftDispatchResult::Unavailable; }
}
} // namespace valmax::media
