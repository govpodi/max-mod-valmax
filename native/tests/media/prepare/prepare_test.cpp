#include "runtime_prepare.h"
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>
#include <QTextStream>
#include <thread>
#include <windows.h>
using namespace valmax::media;
namespace {
int checks=0;
void check(bool v,const char* name){++checks;if(!v){QTextStream(stderr)<<"FAIL: "<<name<<'\n';std::_Exit(1);}}
QByteArray config(const QString& profile,const QString& runtime){return QJsonDocument(QJsonObject{{"profileDirectory",profile},{"runtimeDirectory",runtime},{"existingClient",false}}).toJson(QJsonDocument::Compact);}
MediaPrepareResult prepare(const QByteArray& bytes){MediaPrepareResult result;std::thread worker([&]{result=prepare_media_runtime(bytes);});worker.join();return result;}
}
int main(int argc,char** argv){
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    QCoreApplication app(argc,argv);const auto root=qEnvironmentVariable("VALMAX_SYNTHETIC_PREPARE_ROOT");check(!root.isEmpty()&&QFileInfo(root).isDir(),"explicit fixture exists");
    const auto profile=root+QStringLiteral("/profile space-Ю-😀");const auto runtime=root+QStringLiteral("/runtime-do-not-create");check(QDir().mkdir(profile),"create synthetic profile");
    const auto cfg=config(profile,runtime);
    check(prepare_media_runtime(cfg).code==MediaPrepareCode::WrongThread,"GUI/main call rejected before IO");
    const auto child=profile+QStringLiteral("/inline-media-native-v1");
    const QList<QByteArray> malformed={QByteArray(),"null","[]","{}",cfg+"x",QByteArray::fromHex("efbbbf")+cfg,QByteArray::fromHex("ff")+cfg,cfg+QByteArray::fromHex("c2"),cfg+QByteArray::fromHex("e282"),
        R"({"profileDirectory":"C:/x","profileDirectory":"C:/x","existingClient":false})",
        R"({"profileDirectory":"C:/x","profile\u0044irectory":"C:/x","runtimeDirectory":"C:/y","existingClient":false})",
        R"({"profileDirectory":"C:/x","runtimeDirectory":"C:/y","existingClient":0})",
        R"({"profileDirectory":"C:/x","runtimeDirectory":"C:/y","existingClient":false,"extra":0})",
        R"({"profileDirectory":"C:/x","runtimeDirectory":"C:/y","existingClient":null})",
        R"({"profileDirectory":"\uD800","runtimeDirectory":"C:/y","existingClient":false})",
        R"({"profileDirectory":"\uDC00","runtimeDirectory":"C:/y","existingClient":false})",
        R"({"profileDirectory":"\uD800\u0041","runtimeDirectory":"C:/y","existingClient":false})",
        R"({"profileDirectory":"C:/x","runtimeDirectory":"C:/y","existingClient":false,})",
        QByteArray(16385,' ')};
    for(const auto& bytes:malformed)check(prepare(bytes).code==MediaPrepareCode::InvalidConfig,"strict malformed JSON rejected");
    check(!QFile::exists(child)&&!QFile::exists(runtime),"bad config has no directory side effects");
    for(const auto& value:{QStringLiteral("relative"),QStringLiteral("//server/share"),QStringLiteral("C:/x/../y"),QStringLiteral("C:/x/"),QStringLiteral("C:/x//y"),QStringLiteral("C:/CON/a"),QStringLiteral("C:/x:ads"),QStringLiteral("C:/x. ")})check(prepare(config(value,runtime)).code==MediaPrepareCode::UnsafePath,"unsafe profile rejected");
    check(prepare(config(profile,QStringLiteral("relative"))).code==MediaPrepareCode::UnsafePath,"unused runtime path still validated");
    check(!QFile::exists(child),"unsafe config did not create staging directory");
    if(qEnvironmentVariableIsSet("VALMAX_SYNTHETIC_BAD_CORE")){
        check(prepare(cfg).code==MediaPrepareCode::CoreMismatch,"loaded wrong hash rejected");check(!QFile::exists(child),"core mismatch has no staging creation");
        QTextStream(stdout)<<QJsonDocument(QJsonObject{{"passed",true},{"checks",checks},{"badCoreRejected",true}}).toJson(QJsonDocument::Compact)<<'\n';return 0;
    }
    auto result=prepare(cfg);check(bool(result),"exact core preparation success");
    check(result.value->resources&&result.value->service.explicitStagingRoot==QDir::toNativeSeparators(child).toStdWString(),"exact fixed root and shared lease");
    check(!QFile::exists(runtime)&&!QFile::exists(profile+QStringLiteral("/config.json")),"runtime and unrelated settings untouched");
    check(QDir(child).entryList(QDir::AllEntries|QDir::NoDotAndDotDot).isEmpty(),"prepare writes no media or lock file");
    const auto exe=QCoreApplication::applicationDirPath()+QStringLiteral("/valmax_core.dll");
    HANDLE writer=CreateFileW(QDir::toNativeSeparators(exe).toStdWString().c_str(),GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,nullptr,OPEN_EXISTING,0,nullptr);
    check(writer==INVALID_HANDLE_VALUE&&GetLastError()==ERROR_SHARING_VIOLATION,"core file remains immutable while resource lease exists");
    check(!MoveFileW(QDir::toNativeSeparators(profile).toStdWString().c_str(),QDir::toNativeSeparators(profile+QStringLiteral("-moved")).toStdWString().c_str()),"profile ancestors pinned against rename");
    auto keep=result.value->resources;result.value->resources.reset();
    check(!RemoveDirectoryW(QDir::toNativeSeparators(child).toStdWString().c_str()),"copied resource lease retains staging directory pin");
    auto& api=result.value->service.staging;check(api.version()==1,"validated ABI table callable");
    StagingResult<StagingJob> job;
    std::thread io([&]{job=StagingJob::begin(api,result.value->service.explicitStagingRoot);});io.join();check(bool(job),"actual frozen Rust accepts prepared root");
    std::thread cleanup([&]{job.value.reset();});cleanup.join();
    keep.reset();result.value.reset();
    check(QDir(child).entryList(QDir::AllEntries|QDir::NoDotAndDotDot).isEmpty(),"begin and release do not stage media or create lock file");
    check(QDir().rmdir(child),"directory pin released with final resource owner");
    check(QDir().rename(profile,profile+QStringLiteral("-moved")),"ancestor pin released with final owner");
    check(QDir().rename(profile+QStringLiteral("-moved"),profile),"synthetic fixture restored");
    auto again=prepare(cfg);check(bool(again),"repeat preparation recreates only fixed child");again.value.reset();
    const auto fileProfile=root+QStringLiteral("/profile-with-file");check(QDir().mkdir(fileProfile),"file collision profile");QFile collision(fileProfile+QStringLiteral("/inline-media-native-v1"));check(collision.open(QIODevice::WriteOnly)&&collision.write("owned")>0,"synthetic child file collision created");collision.close();
    const auto rejected=prepare(config(fileProfile,runtime));check(rejected.code==MediaPrepareCode::StorageUnavailable||rejected.code==MediaPrepareCode::UnsafePath,"existing child file not replaced");check(collision.open(QIODevice::ReadOnly)&&collision.readAll()=="owned","existing child file preserved");collision.close();
    const auto missing=root+QStringLiteral("/must-not-create/parent");check(prepare(config(missing,runtime)).code==MediaPrepareCode::StorageUnavailable&&!QFile::exists(root+QStringLiteral("/must-not-create")),"missing parents never created");
    QByteArray reordered="{\"existingClient\":true,\"runtimeDirectory\":"+QJsonDocument(QJsonArray{runtime}).toJson(QJsonDocument::Compact).mid(1).chopped(1)+",\"profileDirectory\":"+QJsonDocument(QJsonArray{profile}).toJson(QJsonDocument::Compact).mid(1).chopped(1)+"}";
    auto order=prepare(reordered);check(bool(order),"key order arbitrary and existingClient boolean retained");order.value.reset();
    auto escaped=cfg;escaped.replace(QByteArray::fromHex("f09f9880"),"\\ud83d\\ude00");auto paired=prepare(escaped);check(bool(paired),"escaped valid surrogate pair accepted");paired.value.reset();
    QTextStream(stdout)<<QJsonDocument(QJsonObject{{"passed",true},{"checks",checks},{"actualFrozenCore",true},{"noRuntimeOpen",true},{"noRealProfiles",true}}).toJson(QJsonDocument::Compact)<<'\n';return 0;
}
