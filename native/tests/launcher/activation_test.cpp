#include "activation.h"
#include <algorithm>
#include <array>
#include <functional>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>
using namespace valmax::launcher;
namespace {
unsigned checks=0;
void check(bool value,const char *name) {if(!value){std::cerr<<"FAIL "<<name<<'\n';std::exit(1);}++checks;}
constexpr auto expected=L"C:\\Synthetic\\ValMAX\\runtime\\max.exe";
WindowInfo window(uint32_t pid=71) {return {pid,0,0x00cf0000,0x40000,L"Qt6112QWindowIcon",900,700,true,false};}
class Fake final:public WindowApi {
public:
    std::map<Window,WindowInfo> items{{101,window()}};
    std::map<uint32_t,std::wstring> images{{71,expected}};
    std::set<uint32_t> inaccessible,dead;
    std::vector<uint32_t> opened;
    std::vector<Process> closed;
    std::vector<std::pair<Window,int>> shown;
    std::vector<Window> focused;
    bool enum_ok=true,show_ok=true,apply_show=true,focus_ok=true,throw_image=false;
    unsigned info_calls=0,image_calls=0,alive_calls=0,enumerations=0;
    uint64_t time=0;
    std::function<void()> on_sleep;
    std::function<void(unsigned,Window)> on_info;
    std::function<void(unsigned,Process)> on_image,on_alive;
    bool windows(std::vector<Window> &out) override {
        ++enumerations;out.clear();for(const auto &[id,value]:items){(void)value;out.push_back(id);}return enum_ok;
    }
    bool info(Window id,WindowInfo &out) override {
        ++info_calls;if(on_info) on_info(info_calls,id);
        const auto it=items.find(id);if(it==items.end())return false;out=it->second;return true;
    }
    Process open_process(uint32_t pid) override {opened.push_back(pid);return inaccessible.contains(pid)?0:pid+10000;}
    bool image(Process process,std::wstring &out) override {
        ++image_calls;if(on_image)on_image(image_calls,process);
        if(throw_image)throw std::runtime_error("SYNTHETIC_PRIVATE");
        const auto it=images.find(static_cast<uint32_t>(process-10000));if(it==images.end())return false;out=it->second;return true;
    }
    bool alive(Process p) override {++alive_calls;if(on_alive)on_alive(alive_calls,p);return !dead.contains(static_cast<uint32_t>(p-10000));}
    void close(Process p) noexcept override {closed.push_back(p);}
    bool show(Window w,int command) override {
        shown.emplace_back(w,command);if(show_ok && apply_show){items[w].visible=true;items[w].iconic=false;}return show_ok;
    }
    bool foreground(Window w) override {focused.push_back(w);return focus_ok;}
    uint64_t now_ms() override {return time;}
    void sleep_ms(uint32_t ms) override {time+=ms;if(on_sleep)on_sleep();}
};
void paths() {
    for(const auto *path:{expected,L"c:/synthetic/valmax/runtime/MAX.EXE",L"\\\\?\\C:\\Synthetic\\ValMAX\\runtime\\max.exe",L"C:/Synthetic//ValMAX/runtime/max.exe"})
        check(same_activation_path(path,expected),"absolute path equivalent");
    for(const auto *bad:{L"",L"max.exe",L"C:max.exe",L"\\\\server\\share\\max.exe",L"1:/a/max.exe",L"C:/a/other.exe",L"C:/a/../max.exe",
        L"C:/a/./max.exe",L"C:/a./max.exe",L"C:/a /max.exe",L"C:/a:ads/max.exe",L"C:/a/max.exe/",L"C:/a/*/max.exe",L"C:/a\n/max.exe",L"\\\\.\\C:\\a\\max.exe"})
        check(!activation_path(bad),"unsafe path rejected");
    const std::wstring nul(L"C:/a/max.exe\0hidden",19);check(!activation_path(nul),"embedded NUL rejected");
    check(!activation_path(std::wstring(32768,L'a')),"path length bound");
    check(same_activation_path(L"C:/ТЕСТ/ValMAX/max.exe",L"c:/тест/valmax/MAX.EXE"),"Windows Unicode case match");
    Fake f;const auto r=activate_existing(L"relative/max.exe",f);
    check(!r.shown && r.outcome==ActivationOutcome::InvalidPath && f.enumerations==0,"invalid path no native activity");
}
void class_guards() {
    for(const auto *name:{L"Qt6QWindowIcon",L"Qt682QWindowIcon",L"Qt6112QWindowOwnDCIcon",L"Qt6112QWindowIcon{12345678-1234-1234-1234-123456789abc}"}) {
        auto i=window();i.window_class=name;check(eligible_main_window(i),"Qt main class accepted");
    }
    for(const auto *name:{L"Qt6112QWindowPopupDropShadowSaveBits",L"OtherQWindowIcon",L"Qt6123456QWindowIcon",L"Qt6QWindowIconSuffix",
        L"Qt6QWindowIcon{z2345678-1234-1234-1234-123456789abc}",L"Qt6QWindowIcon{12345678_1234-1234-1234-123456789abc}"}) {
        auto i=window();i.window_class=name;check(!eligible_main_window(i),"foreign/popup/malformed class rejected");
    }
    for(unsigned kind=0;kind<8;++kind) {
        Fake f;auto &i=f.items[101];
        switch(kind){case 0:i.owner=55;break;case 1:i.exstyle=0x80;break;case 2:i.exstyle=0x08000000;break;
            case 3:i.style=0x40000000;break;case 4:i.width=0;break;case 5:i.height=-1;break;case 6:i.pid=0;break;default:i.window_class=L"Other";}
        check(!activate_existing(expected,f).shown && f.opened.empty() && f.shown.empty() && f.focused.empty(),"ineligible window no process/UI access");
    }
}
void show_paths() {
    Fake hidden;hidden.items[101].visible=false;const auto h=activate_existing(expected,hidden);
    check(h.shown && h.foreground && hidden.shown==std::vector<std::pair<Window,int>>{{101,5}} && hidden.closed==std::vector<Process>{10071},"tray hidden shown and handle closed");
    Fake iconic;iconic.items[101].iconic=true;check(activate_existing(expected,iconic).shown && iconic.shown[0].second==9,"iconic restores");
    Fake visible;check(activate_existing(expected,visible).shown && visible.shown.empty() && visible.focused.size()==1,"visible geometry untouched");
    Fake denied;denied.items[101].visible=false;denied.focus_ok=false;
    const auto d=activate_existing(expected,denied);check(d.shown && !d.foreground,"OS foreground refusal best effort");
    Fake mixed;mixed.items[101]=window(72);mixed.items[102]=window(71);mixed.items[102].visible=false;mixed.images[72]=L"C:/Official/max.exe";
    check(activate_existing(expected,mixed).shown && mixed.shown==std::vector<std::pair<Window,int>>{{102,5}}
        && mixed.focused==std::vector<Window>{102} && mixed.closed.size()==2,"official MAX excluded by exact path");
}
void ambiguity_and_failures() {
    Fake two;two.items[102]=window();
    check(!activate_existing(expected,two).shown && two.shown.empty() && two.focused.empty() && two.opened.size()==two.enumerations
        && two.closed.size()==two.opened.size(),"same process independent main windows not guessed");
    Fake processes;processes.items[102]=window(72);processes.images[72]=expected;
    check(!activate_existing(expected,processes).shown && processes.focused.empty() && processes.closed.size()==processes.opened.size(),"two matching processes not guessed");
    Fake denied;denied.inaccessible.insert(71);
    check(!activate_existing(expected,denied).shown && denied.closed.empty() && denied.focused.empty(),"inaccessible process untouched");
    Fake dead;dead.dead.insert(71);
    check(!activate_existing(expected,dead).shown && dead.closed.size()==dead.opened.size() && dead.focused.empty(),"dead process handles closed");
    Fake missing;missing.items.clear();check(!activate_existing(expected,missing).shown && missing.time==2500 && missing.enumerations<=51,"missing window total deadline");
    Fake enumeration;enumeration.enum_ok=false;check(!activate_existing(expected,enumeration).shown && enumeration.opened.empty(),"enumeration failure safe");
    Fake overflow;for(unsigned i=0;i<4097;++i)overflow.items[i+1]=window();
    check(!activate_existing(expected,overflow).shown && overflow.opened.empty(),"4097 windows fail before inspecting processes");
    Fake exception;exception.throw_image=true;const auto e=activate_existing(expected,exception);
    check(!e.shown && e.outcome==ActivationOutcome::NativeFailure && exception.closed==std::vector<Process>{10071},"exception closes held process and returns bounded enum");
}
void races_and_timeouts() {
    Fake pid;pid.items[101].visible=false;pid.on_info=[&](unsigned count,Window id){if(count==2)pid.items[id].pid=72;};
    check(!activate_existing(expected,pid).shown && pid.shown.empty(),"HWND PID reuse rejected before show");
    Fake image;image.items[101].visible=false;image.on_image=[&](unsigned count,Process){if(count==2)image.images[71]=L"C:/Other/max.exe";};
    check(!activate_existing(expected,image).shown && image.shown.empty() && image.focused.empty(),"process image rechecked before UI");
    Fake exited;exited.items[101].visible=false;exited.on_alive=[&](unsigned count,Process){if(count==3)exited.dead.insert(71);};
    check(!activate_existing(expected,exited).shown && exited.shown.size()==1 && exited.focused.empty(),"exit during restore no foreground");
    Fake changed;changed.on_info=[&](unsigned count,Window id){if(count==3)changed.items[id].owner=99;};
    check(!activate_existing(expected,changed).shown && changed.focused.empty(),"ownership rechecked immediately before focus");
    Fake hung;hung.items[101].visible=false;hung.apply_show=false;
    check(!activate_existing(expected,hung).shown && hung.time<=2500 && hung.focused.empty() && hung.closed.size()==hung.opened.size(),"hung async restore bounded and handles closed");
    Fake show_failure;show_failure.items[101].visible=false;show_failure.show_ok=false;
    check(!activate_existing(expected,show_failure).shown && show_failure.focused.empty(),"failed ShowWindowAsync never success");
    Fake delayed;delayed.items.clear();delayed.on_sleep=[&]{delayed.items[101]=window();delayed.items[101].visible=false;};
    check(activate_existing(expected,delayed).shown && delayed.time==50 && delayed.shown.size()==1,"bounded retry awaits new native main window");
    Fake asynchronous;asynchronous.items[101].visible=false;asynchronous.apply_show=false;
    asynchronous.on_sleep=[&]{asynchronous.items[101].visible=true;};
    check(activate_existing(expected,asynchronous).shown && asynchronous.time==50,"asynchronous show confirmation polled");
    Fake fixed;fixed.items.clear();fixed.on_sleep=[&]{fixed.time=0;};
    check(!activate_existing(expected,fixed).shown && fixed.enumerations==51,"scan attempts bound broken/frozen clock");
    Fake clock_wrap;clock_wrap.time=UINT64_MAX-100;
    check(!activate_existing(expected,clock_wrap).shown && clock_wrap.enumerations==0,"clock overflow refused");
}
}
int main(){paths();class_guards();show_paths();ambiguity_and_failures();races_and_timeouts();
    std::cout<<"{\"schema\":1,\"checks\":"<<checks<<",\"passed\":true,\"windowApi\":\"fake\",\"realUserWindowsTouched\":false}\n";
}
