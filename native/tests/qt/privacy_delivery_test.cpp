#include "native_bridge.h"
#include "privacy_api.h"
#include <QCoreApplication>
#include <QThread>
#include <QTextStream>
#include <atomic>
#include <cstring>

namespace {
std::atomic<unsigned> calls{0};
std::atomic<bool> ready{true};
unsigned checks=0;
bool check(bool value,const char *name) {++checks;if(!value)QTextStream(stderr)<<name<<'\n';return value;}
#define CHECK(value) do {if(!check(value,#value))return 1;} while(0)
bool waitFor(unsigned count) {
    for(unsigned i=0;i<100;++i){if(calls.load()>=count)return true;QThread::msleep(2);}return false;
}
}
namespace valmax {
class NativeBridgePrivacyTest {
public: static void refresh(NativeBridge &bridge) {bridge.refreshPrivacy();}
};
}
extern "C" uint32_t valmax_privacy_verify(const wchar_t *,uint32_t) {return 0;}
extern "C" uint32_t valmax_privacy_status(ValmaxPrivacyStatus *out) {
    *out={};out->size=sizeof(*out);out->version=1;
    if(ready){out->ready=1;out->role=1;out->disk_rules=63;out->loaded_rules=53;out->files_verified=6;
        out->modules_verified=3;out->patches_verified=31;out->required_loaded_rules=53;std::memset(out->manifest_sha256,'a',64);}
    else out->error=VALMAX_PRIVACY_NOT_VERIFIED;
    ++calls;return 0;
}
extern "C" uint32_t valmax_privacy_verify_service() {return VALMAX_PRIVACY_MODULE_NOT_LOADED;}
extern "C" uint32_t valmax_privacy_shutdown() {return 0;}
int main(int argc,char **argv) {
    QCoreApplication app(argc,argv);valmax::NativeBridge bridge;
    CHECK(waitFor(2));QThread::msleep(30); // First worker queued result; GUI has not applied it.
    CHECK(bridge.privacyReport().value(QStringLiteral("state")).toString()==QStringLiteral("unavailable"));
    ready=false;
    for(unsigned i=0;i<20;++i)valmax::NativeBridgePrivacyTest::refresh(bridge);
    QThread::msleep(50);
    CHECK(calls==2); // Cannot start a newer read before applying the queued one.
    QCoreApplication::processEvents();
    CHECK(bridge.privacyReport().value(QStringLiteral("state")).toString()==QStringLiteral("verified"));
    valmax::NativeBridgePrivacyTest::refresh(bridge);CHECK(waitFor(3));QThread::msleep(30);
    QCoreApplication::processEvents();
    CHECK(bridge.privacyReport().value(QStringLiteral("state")).toString()==QStringLiteral("unavailable"));
    QCoreApplication::processEvents();CHECK(calls==3);
    QTextStream(stdout)<<"{\"schema\":1,\"ok\":true,\"checks\":"<<checks<<",\"delayedGuiDeliveryTested\":true}\n";
    return 0;
}
