// Production bridge scheduling, fake synchronous hosts and fake Rust ABI.
// No hook DLL, profile, network or MAX application is used in this test.
#include "native_bridge.h"
#include "ghost_host.h"
#include "notification_host.h"
#include "valmax_core.h"
#include <QCoreApplication>
#include <QEvent>
#include <QPointer>
#include <QThread>
#include <QTextStream>
#include <atomic>
#include <chrono>
#include <thread>

namespace {
std::atomic<unsigned> ghostCalls{0}, notificationCalls{0};
std::atomic<unsigned> startupOpenDelayMs{0}, startupGhostDelayMs{0}, startupNotificationDelayMs{0};
std::atomic<unsigned> dispatchCalls{0};
std::atomic<bool> ghostReady{true}, notificationReady{true};
std::atomic<unsigned> openEntered{0}, openActive{0}, runtimeDispatchEntered{0};
std::atomic<unsigned> runtimeDispatchActive{0}, cancelCalls{0}, closeCalls{0};
std::atomic<unsigned> unsafeClose{0}, gateTimeouts{0}, cancelledDispatches{0};
std::atomic<bool> holdOpen{false}, dispatchWaitsForCancel{false}, cancelSignal{false};
unsigned checks = 0;
bool check(bool value, const char *name) {++checks; if(!value) QTextStream(stderr)<<name<<'\n'; return value;}
#define CHECK(value) do {if(!check(value,#value))return 1;} while(0)
bool waitFor(const std::atomic<unsigned> &counter, unsigned count) {
    for(unsigned i=0;i<100;++i){if(counter.load()>=count)return true;QThread::msleep(2);}return false;
}
// A broken shutdown must fail the check, not leave the test hung indefinitely.
void boundedGate(const std::atomic<bool> &flag, bool waitWhile) {
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(1);
    while(flag.load()==waitWhile) {
        if(std::chrono::steady_clock::now()>=deadline){++gateTimeouts;return;}
        QThread::msleep(1);
    }
}
}
namespace valmax {
GhostHost::GhostHost(ValmaxRuntime *runtime):runtime_(runtime) {}
GhostHost::~GhostHost() = default;
QString GhostHost::refresh() {
    ++ghostCalls;
    if (const unsigned delay = startupGhostDelayMs.exchange(0); delay) QThread::msleep(delay);
    return ghostReady ? QStringLiteral("ready") : QStringLiteral("unavailable");
}
NotificationHost::NotificationHost(ValmaxRuntime *runtime):runtime_(runtime) {}
NotificationHost::~NotificationHost() = default;
QString NotificationHost::refresh() {
    ++notificationCalls;
    if (const unsigned delay = startupNotificationDelayMs.exchange(0); delay) QThread::msleep(delay);
    return notificationReady ? QStringLiteral("ready") : QStringLiteral("unavailable");
}
class NativeBridgeDeliveryTest {
public:
    static void ghost(NativeBridge &bridge) {bridge.refreshGhost();}
    static void notification(NativeBridge &bridge) {bridge.refreshNotifications();}
    static void stopTimers(NativeBridge &bridge) {bridge.ghostTimer_.stop();bridge.notificationTimer_.stop();}
    static void waitWorkers(NativeBridge &bridge) {bridge.workers_.waitForDone();}
    static void drain(NativeBridge &bridge) {bridge.drain();}
};
}
extern "C" uint32_t valmax_core_abi_version() {return 1;}
extern "C" int32_t valmax_core_dispatch(const uint8_t *,size_t,ValmaxCoreBuffer *) {++dispatchCalls;return 1;}
extern "C" void valmax_core_buffer_free(ValmaxCoreBuffer *out) {*out={};}
extern "C" int32_t valmax_runtime_open(const uint8_t *,size_t,ValmaxRuntime **runtime,ValmaxCoreBuffer *out) {
    ++openActive;++openEntered;
    if(holdOpen.load())boundedGate(holdOpen,true);
    if (const unsigned delay = startupOpenDelayMs.exchange(0); delay) QThread::msleep(delay);
    *runtime=reinterpret_cast<ValmaxRuntime *>(uintptr_t(1));*out={};
    --openActive;return 0;
}
extern "C" int32_t valmax_runtime_dispatch(const ValmaxRuntime *,const uint8_t *,size_t,ValmaxCoreBuffer *) {
    ++runtimeDispatchActive;++runtimeDispatchEntered;
    if(dispatchWaitsForCancel.load()) {
        boundedGate(cancelSignal,false);
        if(cancelSignal.load())++cancelledDispatches;
    }
    --runtimeDispatchActive;return 1;
}
extern "C" void valmax_runtime_close(ValmaxRuntime **runtime) {
    if(*runtime) {
        ++closeCalls;
        if(openActive.load()||runtimeDispatchActive.load())++unsafeClose;
    }
    *runtime=nullptr;
}
extern "C" int32_t valmax_runtime_cancel_pending(const ValmaxRuntime *) {
    ++cancelCalls;cancelSignal.store(true);return 0;
}

int shutdownChecks() {
    using valmax::NativeBridge;
    using valmax::NativeBridgeDeliveryTest;
    qputenv("VALMAX_NATIVE_RUNTIME_CONFIG",QByteArrayLiteral("{}"));
    {
        const auto entered=openEntered.load(), cancelled=cancelCalls.load(), closed=closeCalls.load();
        holdOpen.store(true);
        QPointer<NativeBridge> weak=new NativeBridge;auto* raw=weak.data();int emitted=0;
        QObject::connect(raw,&NativeBridge::runtimeStateChanged,[&]{++emitted;});
        QObject::connect(raw,&QObject::destroyed,[&](QObject* object){QCoreApplication::sendPostedEvents(object,QEvent::MetaCall);});
        CHECK(waitFor(openEntered,entered+1));
        CHECK(openActive.load()==1);
        CHECK(raw->runtimeState()==QStringLiteral("opening")&&!raw->runtimeAvailable());
        // The destructor starts with an unpublished handle. A separate finite
        // release lets its waitForDone complete; the GUI never pumps ready.
        std::thread release([]{QThread::msleep(20);holdOpen.store(false);});
        delete raw;release.join();QCoreApplication::processEvents();
        CHECK(weak.isNull());CHECK(openActive.load()==0);
        CHECK(cancelCalls.load()==cancelled);
        CHECK(closeCalls.load()==closed+1);CHECK(emitted==0);
    }
    {
        const auto cancelled=cancelCalls.load(), closed=closeCalls.load();
        QPointer<NativeBridge> weak=new NativeBridge;auto* raw=weak.data();int emitted=0;
        QObject::connect(raw,&NativeBridge::runtimeStateChanged,[&]{++emitted;});
        QObject::connect(raw,&QObject::destroyed,[&](QObject* object){QCoreApplication::sendPostedEvents(object,QEvent::MetaCall);});
        NativeBridgeDeliveryTest::waitWorkers(*raw);
        CHECK(raw->runtimeState()==QStringLiteral("opening")&&!raw->runtimeAvailable());
        delete raw;QCoreApplication::processEvents();
        CHECK(weak.isNull());CHECK(closeCalls.load()==closed+1);
        CHECK(cancelCalls.load()==cancelled);CHECK(emitted==0);
    }
    {
        QPointer<NativeBridge> weak=new NativeBridge;auto* raw=weak.data();int finished=0;
        NativeBridgeDeliveryTest::waitWorkers(*raw);QCoreApplication::processEvents();
        CHECK(raw->runtimeAvailable());NativeBridgeDeliveryTest::stopTimers(*raw);
        QObject::connect(raw,&NativeBridge::operationFinished,[&]{++finished;});
        QObject::connect(raw,&QObject::destroyed,[&](QObject* object){QCoreApplication::sendPostedEvents(object,QEvent::MetaCall);QCoreApplication::processEvents();});
        const auto entered=runtimeDispatchEntered.load(), cancelled=cancelCalls.load();
        const auto closed=closeCalls.load(), completed=cancelledDispatches.load();
        cancelSignal.store(false);dispatchWaitsForCancel.store(true);
        CHECK(raw->invoke(QStringLiteral("{\"operation\":\"export.finish\",\"value\":{}}")).value(QStringLiteral("accepted")).toBool());
        CHECK(waitFor(runtimeDispatchEntered,entered+1));CHECK(runtimeDispatchActive.load()==1);
        // Only cancel_pending can release this worker normally. If the bridge
        // joins first, the one-second test gate expires and fails below.
        delete raw;dispatchWaitsForCancel.store(false);QCoreApplication::processEvents();
        CHECK(weak.isNull());CHECK(runtimeDispatchActive.load()==0);
        CHECK(cancelCalls.load()==cancelled+1);CHECK(cancelledDispatches.load()==completed+1);
        CHECK(closeCalls.load()==closed+1);CHECK(finished==0);
    }
    CHECK(gateTimeouts.load()==0);CHECK(unsafeClose.load()==0);
    qunsetenv("VALMAX_NATIVE_RUNTIME_CONFIG");
    return 0;
}
int lifetimeChecks() {
    using valmax::NativeBridge;
    using valmax::NativeBridgeDeliveryTest;
    qunsetenv("VALMAX_NATIVE_RUNTIME_CONFIG");
    const QString request=QStringLiteral("{\"operation\":\"version\"}");
    {
        QPointer<NativeBridge> weak=new NativeBridge;auto* raw=weak.data();
        const auto before=dispatchCalls.load();
        QObject::connect(raw,&NativeBridge::pendingOperationsChanged,[&]{delete weak.data();});
        const auto value=raw->invoke(request);
        CHECK(weak.isNull());CHECK(!value.value(QStringLiteral("accepted")).toBool());CHECK(dispatchCalls==before);
    }
    for(int stage=0;stage<2;++stage) {
        QPointer<NativeBridge> weak=new NativeBridge;auto* raw=weak.data();int finished=0;
        QObject::connect(raw,&NativeBridge::pendingOperationsChanged,[&]{if(stage==0&&weak&&weak->pendingOperations()==0)delete weak.data();});
        QObject::connect(raw,&NativeBridge::operationFinished,[&]{++finished;if(stage==1)delete weak.data();});
        CHECK(raw->invoke(request).value(QStringLiteral("accepted")).toBool());
        NativeBridgeDeliveryTest::waitWorkers(*raw);QCoreApplication::processEvents();
        CHECK(weak.isNull());CHECK(finished==stage);
    }
    for(int stage=0;stage<3;++stage) {
        qputenv("VALMAX_NATIVE_RUNTIME_CONFIG",QByteArrayLiteral("{}"));
        QPointer<NativeBridge> weak=new NativeBridge;auto* raw=weak.data();int emittedCount=0;
        const auto delivered=[&](int which){++emittedCount;if(stage==which)delete weak.data();};
        QObject::connect(raw,&NativeBridge::runtimeStateChanged,[&]{delivered(0);});
        QObject::connect(raw,&NativeBridge::ghostRuntimeStateChanged,[&]{delivered(1);});
        QObject::connect(raw,&NativeBridge::notificationRuntimeStateChanged,[&]{delivered(2);});
        NativeBridgeDeliveryTest::waitWorkers(*raw);QCoreApplication::processEvents();
        CHECK(weak.isNull());CHECK(emittedCount==stage+1);
    }
    qunsetenv("VALMAX_NATIVE_RUNTIME_CONFIG");
    {
        QPointer<NativeBridge> weak=new NativeBridge;auto* raw=weak.data();
        QObject::connect(raw,&NativeBridge::pendingRequestsChanged,[&]{delete weak.data();});
        const auto value=raw->enqueueRows({1,2});
        CHECK(weak.isNull());CHECK(!value.value(QStringLiteral("accepted")).toBool());
    }
    {
        QPointer<NativeBridge> weak=new NativeBridge;auto* raw=weak.data();int cancelled=0;
        const auto id=raw->enqueueRows({1,2}).value(QStringLiteral("requestId")).toString();
        QObject::connect(raw,&NativeBridge::pendingRequestsChanged,[&]{delete weak.data();});
        QObject::connect(raw,&NativeBridge::cancelled,[&]{++cancelled;});
        CHECK(raw->cancel(id));CHECK(weak.isNull());CHECK(cancelled==0);
    }
    for(int stage=0;stage<2;++stage) {
        QPointer<NativeBridge> weak=new NativeBridge;auto* raw=weak.data();int cancelled=0;
        raw->enqueueRows({1});raw->enqueueRows({2});
        QObject::connect(raw,&NativeBridge::pendingRequestsChanged,[&]{if(stage==0)delete weak.data();});
        QObject::connect(raw,&NativeBridge::cancelled,[&]{++cancelled;if(stage==1)delete weak.data();});
        raw->cancelAll();CHECK(weak.isNull());CHECK(cancelled==stage);
    }
    for(int stage=0;stage<3;++stage) {
        QPointer<NativeBridge> weak=new NativeBridge;auto* raw=weak.data();int completed=0;
        raw->enqueueRows({1,2});
        QObject::connect(raw,&NativeBridge::rowsReady,[&]{if(stage==0)delete weak.data();});
        QObject::connect(raw,&NativeBridge::pendingRequestsChanged,[&]{if(stage==1)delete weak.data();});
        QObject::connect(raw,&NativeBridge::completed,[&]{++completed;if(stage==2)delete weak.data();});
        NativeBridgeDeliveryTest::drain(*raw);CHECK(weak.isNull());CHECK(completed==(stage==2?1:0));
    }
    for(int runtime=0;runtime<2;++runtime) {
        if(runtime)qputenv("VALMAX_NATIVE_RUNTIME_CONFIG",QByteArrayLiteral("{}"));
        QPointer<NativeBridge> weak=new NativeBridge;auto* raw=weak.data();int emittedCount=0,destroyed=0;
        QObject::connect(raw,&NativeBridge::runtimeStateChanged,[&]{++emittedCount;});
        QObject::connect(raw,&NativeBridge::operationFinished,[&]{++emittedCount;});
        QObject::connect(raw,&QObject::destroyed,[&](QObject* object){++destroyed;QCoreApplication::sendPostedEvents(object,QEvent::MetaCall);QCoreApplication::processEvents();});
        if(!runtime)CHECK(raw->invoke(request).value(QStringLiteral("accepted")).toBool());
        NativeBridgeDeliveryTest::waitWorkers(*raw);delete raw;QCoreApplication::processEvents();
        CHECK(weak.isNull());CHECK(destroyed==1&&emittedCount==0);
        qunsetenv("VALMAX_NATIVE_RUNTIME_CONFIG");
    }
    {
        NativeBridge bridge;int batches=0,cancelled=0,completed=0;
        bridge.enqueueRows({1,2});
        QObject::connect(&bridge,&NativeBridge::rowsReady,[&]{if(++batches==1){bridge.cancelAll();bridge.enqueueRows({3});}});
        QObject::connect(&bridge,&NativeBridge::cancelled,[&]{++cancelled;});
        QObject::connect(&bridge,&NativeBridge::completed,[&]{++completed;});
        NativeBridgeDeliveryTest::drain(bridge);NativeBridgeDeliveryTest::drain(bridge);
        CHECK(batches==2&&cancelled==1&&completed==1&&bridge.pendingRequests()==0);
    }
    return 0;
}
int main(int argc,char **argv) {
    QCoreApplication app(argc,argv);
    qputenv("VALMAX_NATIVE_RUNTIME_CONFIG",QByteArrayLiteral("{}"));
    startupOpenDelayMs = 20;
    startupNotificationDelayMs = 30;
    startupGhostDelayMs = 40;
    valmax::NativeBridge bridge;
    int timingSignals = 0;
    QObject::connect(&bridge, &valmax::NativeBridge::startupTimingsChanged, [&] { ++timingSignals; });
    CHECK(waitFor(ghostCalls,1));CHECK(waitFor(notificationCalls,1));
    valmax::NativeBridgeDeliveryTest::waitWorkers(bridge);
    QCoreApplication::processEvents();
    CHECK(bridge.runtimeAvailable());
    const QVariantMap timing = bridge.startupTimings();
    CHECK(timingSignals == 1 && timing.size() == 8 && timing.value(QStringLiteral("schema")).toInt() == 1);
    const qint64 rustMs = timing.value(QStringLiteral("rustOpenMs")).toLongLong();
    const qint64 notificationMs = timing.value(QStringLiteral("notificationRefreshMs")).toLongLong();
    const qint64 ghostMs = timing.value(QStringLiteral("ghostRefreshMs")).toLongLong();
    const qint64 queueMs = timing.value(QStringLiteral("workerQueueMs")).toLongLong();
    const qint64 workerMs = timing.value(QStringLiteral("workerMs")).toLongLong();
    const qint64 totalMs = timing.value(QStringLiteral("totalMs")).toLongLong();
    CHECK(rustMs >= 15 && notificationMs >= 25 && ghostMs >= 35);
    CHECK(timing.contains(QStringLiteral("retentionInitMs")) && timing.value(QStringLiteral("retentionInitMs")).isNull());
    CHECK(workerMs >= rustMs + notificationMs + ghostMs);
    CHECK(totalMs >= queueMs + workerMs);
    valmax::NativeBridgeDeliveryTest::stopTimers(bridge);
    for (int kind=0;kind<2;++kind) {
        auto &calls=kind==0?ghostCalls:notificationCalls;
        auto &ready=kind==0?ghostReady:notificationReady;
        auto refresh=[&]{if(kind==0)valmax::NativeBridgeDeliveryTest::ghost(bridge);else valmax::NativeBridgeDeliveryTest::notification(bridge);};
        auto state=[&]{return kind==0?bridge.ghostRuntimeState():bridge.notificationRuntimeState();};
        CHECK(state()==QStringLiteral("ready"));
        refresh();CHECK(waitFor(calls,2));QThread::msleep(30);
        ready=false;
        for(unsigned i=0;i<20;++i)refresh();
        QThread::msleep(50);
        CHECK(calls==2); // Prior delivery still queued; newer read not admitted.
        QCoreApplication::processEvents();CHECK(state()==QStringLiteral("ready"));
        refresh();CHECK(waitFor(calls,3));QThread::msleep(30);
        QCoreApplication::processEvents();CHECK(state()==QStringLiteral("unavailable"));
        QCoreApplication::processEvents();CHECK(calls==3);
    }
    CHECK(lifetimeChecks()==0);
    CHECK(shutdownChecks()==0);
    QTextStream(stdout)<<"{\"schema\":1,\"ok\":true,\"checks\":"<<checks<<",\"ghostAndNotificationDelayedDelivery\":true,\"reentrantDeletion\":true,\"nestedDestroyedEventLoop\":true,\"openingShutdown\":true,\"cancelBeforeWorkerJoin\":true,\"hostsMocked\":true}\n";
    return 0;
}
