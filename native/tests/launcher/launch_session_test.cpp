#include "launch_session.h"
#include <algorithm>
#include <iostream>
#include <stdexcept>
#include <vector>
using namespace valmax::launcher;
namespace {
int checks=0;
void check(bool value){++checks;if(!value)throw std::runtime_error("check_failed");}
struct Fake final:LaunchDriver {
    std::vector<std::string> calls;
    std::string fail;
    ClientPresence first=ClientPresence::Absent,second=ClientPresence::Absent;
    int scans=0,profiles=0,guards=0,recovery_guard=0;
    bool recovery=false,throw_runtime=false;
    LaunchResult activated{LaunchCode::Activated,false,true,false};
    LaunchCode step(const char* name){calls.emplace_back(name);return fail==name?LaunchCode::ProfileFailed:LaunchCode::Launched;}
    LaunchCode verify()override{return step("verify");}
    LaunchCode acquire_transfer()override{return step("lock");}
    LaunchCode check_profile()override {++guards;auto value=step("check");return guards==recovery_guard?LaunchCode::RecoveryRequired:value;}
    LaunchCode check_webview2_runtime()override{
        calls.emplace_back("webview2");
        return fail=="webview2"?LaunchCode::WebView2Unavailable:LaunchCode::Launched;
    }
    LaunchCode prepare_profile()override {++profiles;return step("profile");}
    ClientPresence scan()override{calls.emplace_back("scan");return scans++==0?first:second;}
    LaunchResult activate()override{calls.emplace_back("activate");return activated;}
    LaunchCode prepare_runtime()override{if(throw_runtime)throw 1;return step("runtime");}
    LaunchCode prepare_environment()override{return step("environment");}
    LaunchResult spawn()override{calls.emplace_back("spawn");return {LaunchCode::Launched,true};}
    LaunchResult spawn_transfer(bool value)override{calls.emplace_back("helper");recovery=value;return {LaunchCode::HelperLaunched,true};}
};
bool contains(const Fake& f,const char* value){return std::find(f.calls.begin(),f.calls.end(),value)!=f.calls.end();}
}
int main(){
    try {
        Fake ordinary;auto result=run_launch(ordinary);check(result.success()&&result.process_created&&!result.window_shown);
        check(ordinary.calls==std::vector<std::string>{"verify","lock","scan","check","webview2","profile","runtime","environment","check","scan","spawn"});
        check(ordinary.profiles==1&&ordinary.guards==2);
        for(auto step:{"verify","lock","check","webview2","profile","runtime","environment"}){Fake f;f.fail=step;check(!run_launch(f).success());check(!contains(f,"spawn"));}
        Fake missing_webview2;missing_webview2.fail="webview2";
        check(run_launch(missing_webview2).code==LaunchCode::WebView2Unavailable);
        check(missing_webview2.profiles==0&&!contains(missing_webview2,"runtime")
            &&!contains(missing_webview2,"environment")&&!contains(missing_webview2,"spawn"));
        check(missing_webview2.calls==std::vector<std::string>{"verify","lock","scan","check","webview2"});
        for(auto presence:{ClientPresence::Unknown,ClientPresence::Unavailable,ClientPresence::ProfileInUse}){
            Fake initial;initial.first=presence;check(!run_launch(initial).success());check(!contains(initial,"runtime")&&!contains(initial,"spawn"));
            check(initial.calls==std::vector<std::string>{"verify","lock","scan"});
            Fake later;later.second=presence;check(!run_launch(later).success());check(!contains(later,"spawn"));
        }
        Fake existing;existing.first=ClientPresence::Running;result=run_launch(existing);
        check(result.code==LaunchCode::Activated&&!result.process_created&&result.window_shown);check(!contains(existing,"runtime")&&!contains(existing,"spawn"));
        check(existing.calls==std::vector<std::string>{"verify","lock","scan","check","activate"}&&existing.profiles==0);
        Fake failed;failed.first=ClientPresence::Running;failed.activated={LaunchCode::ActivationFailed};check(!run_launch(failed).success());check(!contains(failed,"spawn"));
        Fake appeared;appeared.second=ClientPresence::Running;check(run_launch(appeared).code==LaunchCode::Activated);check(!contains(appeared,"spawn"));
        Fake marker;marker.recovery_guard=2;check(run_launch(marker).code==LaunchCode::RecoveryRequired);check(!contains(marker,"spawn"));
        Fake early_marker;early_marker.recovery_guard=1;check(run_launch(early_marker).code==LaunchCode::RecoveryRequired);check(early_marker.profiles==0);
        Fake running_marker;running_marker.first=ClientPresence::Running;running_marker.recovery_guard=1;
        check(run_launch(running_marker).code==LaunchCode::RecoveryRequired&&!contains(running_marker,"activate")&&running_marker.profiles==0);
        Fake foreign;foreign.first=ClientPresence::ProfileInUse;check(run_launch(foreign).code==LaunchCode::ProfileInUse&&foreign.profiles==0);
        check(static_cast<uint32_t>(LaunchCode::InternalError)==16&&static_cast<uint32_t>(LaunchCode::ProfileInUse)==17);
        check(std::string(launch_code(LaunchCode::ProfileInUse))=="profile_in_use");
        Fake exception;exception.throw_runtime=true;check(run_launch(exception).code==LaunchCode::InternalError);check(!contains(exception,"spawn"));
        for(auto action:{LaunchAction::Import,LaunchAction::Recover}){Fake helper;result=run_launch(helper,action);check(result.code==LaunchCode::HelperLaunched&&result.process_created);
            check(helper.calls==std::vector<std::string>{"verify","helper"});check(helper.recovery==(action==LaunchAction::Recover));}
        LaunchAction action;check(parse_action({},action)&&action==LaunchAction::Client);
        for(auto command:{L"mdata",L"import"}){const std::wstring_view args[]={command};check(parse_action(args,action)&&action==LaunchAction::Import);}
        const std::wstring_view recover[]={L"recover"};check(parse_action(recover,action)&&action==LaunchAction::Recover);
        for(auto bad:{L"observe",L"--without-diagnostics",L"--runtime",L"--password=secret"}){const std::wstring_view args[]={bad};check(!parse_action(args,action));}
        const std::wstring_view extras[]={L"recover",L"anything"};check(!parse_action(extras,action));
        const auto config=runtime_config(L"D:\\ValMAXBuild\\fixtures\\native-launcher\\local\\MXMOD",L"D:\\App\\runtime\\mod",true);
        check(config.find("\"existingClient\":true")!=config.npos);check(config.find("D:\\\\App")!=config.npos);
        const wchar_t inherited[]=L"PATH=ordinary\0QT_PLUGIN_PATH=untrusted\0VALMAX_NATIVE_RUNTIME_CONFIG=untrusted\0Z_LAST=kept\0\0";
        auto env=configured_environment(std::span(inherited,std::size(inherited)-1),{L"D:\\App\\runtime\\webview2",L"D:\\Local\\MXMOD"},config);
        check(env.ok());const std::wstring block(env.block.begin(),env.block.end());
        check(block.find(L"untrusted")==block.npos);check(block.find(L"PATH=ordinary")!=block.npos);check(block.find(L"VALMAX_NATIVE_RUNTIME_CONFIG={")!=block.npos);
        check(block.find(L"VALMAX_NATIVE_RUNTIME_CONFIG=")<block.find(L"WEBVIEW2_BROWSER_EXECUTABLE_FOLDER="));
        check(env.block.size()>2&&env.block.back()==0&&env.block[env.block.size()-2]==0);
        // Exercise the nested client -> launcher -> helper environment contract,
        // rather than feeding the helper only a sterile host environment. The
        // client has a freshly inserted runtime config; the import helper must
        // remove that client-only config and preserve ordinary render settings.
        const wchar_t nested_input[]=L"PATH=C:\\Synthetic\\bin\0QSG_RHI_BACKEND=d3d11\0QT_SCALE_FACTOR=1.5\0"
            L"QT_PLUGIN_PATH=untrusted\0VALMAX_NATIVE_RUNTIME_CONFIG=stale\0MAX_MOD_STARTUP_OPERATION=stale\0\0";
        const auto nested_paths=LexicalEnvironmentPaths{L"D:\\App\\runtime\\webview2",L"D:\\Local\\MXMOD"};
        const auto nested_original=std::span(nested_input,std::size(nested_input)-1);
        const auto nested_client=configured_environment(nested_original,nested_paths,config);
        check(nested_client.ok());
        const auto client_before=nested_client.block;
        const auto nested_helper=configured_environment(nested_client.block,nested_paths,{});
        const auto expected_helper=build_child_environment(nested_original,nested_paths);
        check(nested_helper.ok()&&expected_helper.ok()&&nested_helper.block==expected_helper.block);
        check(nested_client.block==client_before);
        const std::wstring helper_block(nested_helper.block.begin(),nested_helper.block.end());
        check(helper_block.find(L"VALMAX_NATIVE_RUNTIME_CONFIG=")==helper_block.npos);
        check(helper_block.find(L"MAX_MOD_STARTUP_OPERATION=")==helper_block.npos);
        check(helper_block.find(L"untrusted")==helper_block.npos);
        check(helper_block.find(L"PATH=C:\\Synthetic\\bin")!=helper_block.npos);
        check(helper_block.find(L"QSG_RHI_BACKEND=d3d11")!=helper_block.npos);
        check(helper_block.find(L"QT_SCALE_FACTOR=1.5")!=helper_block.npos);
        const auto repeated_helper=configured_environment(nested_helper.block,nested_paths,{});
        check(repeated_helper.ok()&&repeated_helper.block==nested_helper.block);
        std::cout<<"{\"ok\":true,\"checks\":"<<checks<<",\"scope\":\"synthetic order and child environment\"}\n";return 0;
    }catch(...){std::cerr<<"launcher_policy_failed\n";return 1;}
}
