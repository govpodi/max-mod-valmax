#include "runtime_host.h"
#include "runtime_prepare.h"
#include "composer_adapter.h"
#include <QCoreApplication>
#include <QDeadlineTimer>
#include <QEvent>
#include <QEventLoop>
#include <QPointer>
#include <QThread>
#include <QTimer>
#include <algorithm>
#include <memory>
#include <vector>
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

namespace valmax::media {
namespace {
class RuntimeOwner;
RuntimeOwner* processOwner = nullptr; // GUI-only, reset by Qt's cleanup routine.
void cleanup_runtime();
bool gui() {
    const auto app=QCoreApplication::instance();
    return app && QThread::currentThread()==app->thread();
}
bool pin_this_module() {
    // Workers/Qt cleanup callbacks can outlive the importing QQmlEngine. Keep
    // the plugin code mapped until process exit, rather than relying on a QML
    // loader's unloading policy. This does not load another module.
    HMODULE module=nullptr;
    return GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,
        reinterpret_cast<LPCWSTR>(&cleanup_runtime),&module)!=FALSE;
}
class RuntimeOwner final : public QObject {
public:
    explicit RuntimeOwner(QByteArray config):config_(std::move(config)) {
        timer_.setInterval(5);
        connect(&timer_,&QTimer::timeout,this,[this]{finish_if_drained();});
        auto* app=QCoreApplication::instance();
        app->installEventFilter(this);
        connect(app,&QCoreApplication::aboutToQuit,this,[this]{stop_and_drain();},Qt::DirectConnection);
    }
    ~RuntimeOwner() override {
        // cleanup_runtime has already drained; no worker may post into this
        // object or execute a function from the retained Rust module now.
        timer_.stop();
        if(auto* app=QCoreApplication::instance())app->removeEventFilter(this);
        delete service_;
        delete preparing_;
    }
    bool attach(QQmlEngine* engine,const QByteArray& config) {
        if(config!=config_ || state_==MediaRuntimeState::Stopping || state_==MediaRuntimeState::Stopped
            || state_==MediaRuntimeState::Failed)return false;
        std::erase_if(engines_,[](const auto& item){return item.isNull();});
        if(std::find(engines_.begin(),engines_.end(),engine)!=engines_.end())return true;
        if(engines_.size()>=32)return false;
        engines_.push_back(engine);
        if(state_==MediaRuntimeState::Ready)return configure_media_composer_engine(engine,service_);
        return true;
    }
    void start() {
        state_=MediaRuntimeState::Preparing;
        // No GUI QObject is captured/read by the worker. Qt's finished delivery
        // publishes this result only after prepare and all stack cleanup finish.
        pending_=std::make_shared<MediaPrepareResult>();
        const auto pending=pending_;const auto config=config_;
        preparing_=QThread::create([pending,config]{*pending=prepare_media_runtime(config);});
        connect(preparing_,&QThread::finished,this,[this]{prepared();},Qt::QueuedConnection);
        preparing_->start();
    }
    MediaRuntimeSnapshot snapshot() const {
        return {state_,int(std::count_if(engines_.begin(),engines_.end(),[](const auto& e){return !e.isNull();})),
                preparing_!=nullptr,state_==MediaRuntimeState::Failed||state_==MediaRuntimeState::Stopped};
    }
    void stop() {
        if(state_==MediaRuntimeState::Stopped || state_==MediaRuntimeState::Stopping)return;
        state_=MediaRuntimeState::Stopping;
        engines_.clear();
        if(service_)service_->shutdown(); // revokes callbacks before pumping Qt.
        timer_.start();finish_if_drained();
    }
    void stop_and_drain() {
        if(draining_)return;
        draining_=true;
        stop();
        // Normal Quit uses the asynchronous filter below. This fallback covers
        // direct exit() and destruction without exec(), while Qt's dispatcher
        // and application singleton still exist (Qt 6.11.2 cleanup ordering).
        while(state_!=MediaRuntimeState::Stopped) {
            QCoreApplication::processEvents(QEventLoop::ExcludeUserInputEvents,QDeadlineTimer(10));
            QCoreApplication::sendPostedEvents(nullptr,QEvent::DeferredDelete);
            finish_if_drained();
            if(state_!=MediaRuntimeState::Stopped)QThread::msleep(1);
        }
        draining_=false;
    }
protected:
    bool eventFilter(QObject* watched,QEvent* event) override {
        if(watched==QCoreApplication::instance() && event->type()==QEvent::Quit) {
            if(state_==MediaRuntimeState::Stopped)return false;
            quitPending_=true;stop();
            return true;
        }
        return QObject::eventFilter(watched,event);
    }
private:
    void prepared() {
        if(!preparing_ || !preparing_->isFinished())return;
        // QThread::finished precedes final TLS cleanup; wait is only on this
        // already-finished preparation thread, never a network/service worker.
        preparing_->wait();delete preparing_;preparing_=nullptr;
        auto result=std::move(*pending_);pending_.reset();
        if(state_==MediaRuntimeState::Stopping) {finish_if_drained();return;}
        if(!result) {state_=MediaRuntimeState::Failed;return;}
        resources_=result.value->resources;
        const auto created=MediaServiceHost::create(std::move(result.value->service),this);
        if(!created) {resources_.reset();state_=MediaRuntimeState::Failed;return;}
        service_=*created.value;
        ownedRun_=true;
        state_=MediaRuntimeState::Ready;
        // Configuration may synchronously complete a QML object; take a copy
        // so engine destruction/reentrant registration cannot invalidate it.
        const auto engines=engines_;
        for(const auto& engine:engines) {
            if(state_!=MediaRuntimeState::Ready)break;
            if(engine)(void)configure_media_composer_engine(engine,service_);
        }
    }
    void finish_if_drained() {
        if(state_!=MediaRuntimeState::Stopping || preparing_)return;
        if(service_) {
            const auto current=service_->snapshot();
            if(!current || !current.value->drained)return;
            auto* finished=service_;service_=nullptr;delete finished;
        }
        // The self-owned Run is deleted after drained; retain the core and
        // directory leases until its process reservation is actually released.
        if(ownedRun_&&!MediaServiceHost::process_available())return;
        ownedRun_=false;
        resources_.reset();state_=MediaRuntimeState::Stopped;timer_.stop();
        if(quitPending_ && !draining_)QTimer::singleShot(0,QCoreApplication::instance(),&QCoreApplication::quit);
    }
    QByteArray config_;
    MediaRuntimeState state_=MediaRuntimeState::Absent;
    std::vector<QPointer<QQmlEngine>> engines_;
    QThread* preparing_=nullptr;
    std::shared_ptr<MediaPrepareResult> pending_;
    std::shared_ptr<const MediaRuntimeResources> resources_;
    MediaServiceHost* service_=nullptr;
    QTimer timer_;
    bool quitPending_=false,draining_=false,ownedRun_=false;
};
void cleanup_runtime() {
    if(!processOwner)return;
    processOwner->stop_and_drain();
    auto* old=processOwner;processOwner=nullptr;delete old;
}
}
bool attach_media_runtime(QQmlEngine* engine,const QByteArray& config) {
    if(!gui() || !engine || engine->thread()!=QThread::currentThread() || config.isEmpty() || config.size()>16384)return false;
    if(processOwner)return processOwner->attach(engine,config);
    if(!pin_this_module())return false;
    // qgetenv owns its storage, but the native API may also receive fromRawData.
    // Take a bounded deep copy before the caller can release that backing store.
    auto* owner=new RuntimeOwner(QByteArray(config.constData(),config.size()));
    processOwner=owner;qAddPostRoutine(&cleanup_runtime);
    if(!owner->attach(engine,config))return false;
    owner->start();return true;
}
MediaRuntimeSnapshot media_runtime_snapshot() {
    return gui() && processOwner?processOwner->snapshot():MediaRuntimeSnapshot{};
}
void shutdown_media_runtime() {if(gui()&&processOwner)processOwner->stop();}
} // namespace valmax::media
