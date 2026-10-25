#include "protocol.h"
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QString>
#include <cmath>
#include <iostream>
#include <limits>
#include <string>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>
#endif
using namespace valmax::media;
namespace {
int checks = 0;
void check(bool value) { ++checks; if (!value) { std::cerr << "FAILED check " << checks << '\n'; std::exit(1); } }
QJsonObject error_json(const Error& e) { return {{"ok",false},{"error",code_name(e.code)},{"status",e.status},{"retry",e.retryAfterSeconds ? QJsonValue(*e.retryAfterSeconds) : QJsonValue()}}; }
template<class T> QJsonObject unit(const Result<T>& r) { return r ? QJsonObject{{"ok",true}} : error_json(r.error); }
UrlKind kind(const QString& s) { return s == "api" ? UrlKind::Api : s == "source" ? UrlKind::Source : UrlKind::Media; }
QJsonObject dispatch(const QJsonObject& o) {
    const auto op=o["op"].toString();
    const auto s=o["text"].toString();
    if (op=="url" || op=="canonical" || op=="redirect") {
        const auto r=op=="canonical" ? canonical_media_url(s) : op=="redirect" ? validate_redirect(o["current"].toString(),s,kind(o["kind"].toString())) : validate_url(s,kind(o["kind"].toString()));
        return r ? QJsonObject{{"ok",true},{"value",*r.value}} : error_json(r.error);
    }
    if (op=="plain") return {{"value",plain_metadata(s,o.contains("limit") ? o["limit"].toInt() : 240)}};
    if (op=="retry") return {{"value",retry_delay(s,o["wall"].toDouble())}};
    if (op=="signature" || op=="preview") {
        const auto data=QByteArray::fromBase64(o["data"].toString().toLatin1());
        if (op=="preview") return unit(validate_preview(data,o["mime"].toString()));
        return {{"value",valid_content(data,o["mime"].toString())}};
    }
    if (op=="request") {
        const auto r=parse_search_request(QByteArray::fromBase64(o["json"].toString().toLatin1()));
        return r ? QJsonObject{{"ok",true},{"query",r.value->query},{"url",r.value->apiUrl}} : error_json(r.error);
    }
    if (op=="selection") { const auto r=parse_selection_request(s.toUtf8()); return r ? QJsonObject{{"ok",true},{"id",*r.value}} : error_json(r.error); }
    if (op=="response") {
        const auto r=parse_search_response(QByteArray::fromBase64(o["json"].toString().toLatin1()),o["mime"].toString(),o["mode"].toString()=="vid" ? Mode::Video : Mode::Picture);
        if (!r) return error_json(r.error);
        QJsonArray rows;
        for (const auto& c : *r.value) { auto row=*c.metadata.with_id(QString(32,u'a')).value; row.remove("id"); rows.append(row); }
        return {{"ok",true},{"results",rows}};
    }
    if (op=="headers") { const auto r=validate_response_headers(o["status"].toInt(),o["mime"].toString(),o["encoding"].toString(),o["length"].toString(),o["limit"].toInt()); return r ? QJsonObject{{"ok",true},{"mime",*r.value}} : error_json(r.error); }
    return {{"error","unknown_test_operation"}};
}
int selftest() {
    const QString url="https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic.png";
    check(bool(validate_url(url,UrlKind::Media)));
    for (const auto& bad : {url+"?auth=secret",url+"#",url+"?",url+"/%2e%2e/x",url+"/%00",url+"/%5c",url+"/%ff",url+"/%",QString("http://upload.wikimedia.org/wikipedia/commons/a.png")}) check(!validate_url(bad,UrlKind::Media));
    check(*canonical_media_url(url+"?utm_source=a&utm_campaign=b&utm_content=c").value==url);
    check(!canonical_media_url(url+"?utm_term=a"));
    check(bool(canonical_media_url(url+"?%75tm_source=a")));
    check(!validate_redirect(url,"https://127.0.0.1/a",UrlKind::Media));
    check(!validate_redirect(QString::fromLatin1(Api),url,UrlKind::Api));
    check(!validate_redirect(url,QString::fromLatin1(Api),UrlKind::Media));
    check(plain_metadata("<a href='https://invalid.example'>Name &amp; author</a>")=="Name & author");
    check(plain_metadata("<img src='file:///never-read'>A<b>B</b>")=="AB");
    check(plain_metadata("<script>never executes</script>").isEmpty());
    check(plain_metadata("a <b").isEmpty());
    check(plain_metadata("&NotEqualTilde;")==QString::fromUtf8("≂̸"));
    check(plain_metadata("&#x1f642;")==QString::fromUtf8("🙂"));
    check(plain_metadata("&#128;")==QString::fromUtf8("€"));
    check(plain_metadata("a\u202eb")=="ab");
    const auto req=parse_search_request(R"({"mode":"pic","query":" cat\" filetype:video "})");
    check(bool(req)); check(req.value->query=="cat\" filetype:video");
    check(req.value->apiUrl.contains("filetype%3Abitmap+%22cat++filetype%3Avideo%22"));
    for (const QByteArray bytes : {QByteArray("{}"),QByteArray("[]"),QByteArray(R"({"mode":[],"query":"cat"})"),QByteArray(R"({"mode":"pic","query":"a"})"),QByteArray(R"({"mode":"pic","query":"a\u0000b"})"),QByteArray(R"({"mode":"pic","query":"\ud800x"})")}) check(!parse_search_request(bytes));
    check(bool(parse_selection_request(R"({"id":"0123456789abcdef0123456789abcdef"})")));
    check(!parse_selection_request(R"({"id":"../file"})"));
    check(!parse_selection_request(R"({"id":"0123456789abcdef0123456789abcdef","url":"x"})"));
    const QByteArray png=QByteArray::fromHex("89504e470d0a1a0a")+"synthetic";
    check(valid_content(png,"image/png")); check(!valid_content("<html>","image/png"));
    check(bool(validate_preview(png,"image/png"))); check(!validate_preview(png,"video/webm"));
    check(!validate_preview(QByteArray(MaxPreviewBytes+1,'x'),"image/png"));
    PublicMetadata meta;meta.bytes=png.size();meta.width=320;meta.height=240;meta.mime="image/png";
    check(bool(validate_download(png,"image/png",meta)));meta.bytes++;check(!validate_download(png,"image/png",meta));
    check(!meta.with_id("http://no"));
    check(bool(validate_response_headers(200,"image/png; charset=x","identity","17",100)));
    check(!validate_response_headers(200,"image/png","gzip","17",100));
    check(!validate_response_headers(200,"image/png","","101",100));
    check(bool(validate_response_headers(200,"image/png","","00000000000000000000000000000000",100)));
    check(validate_response_headers(429,"","","",100).error.code==Code::ProviderBusy);
    check(validate_response_headers(503,"","","",100).error.code==Code::ProviderBusy);
    check(validate_response_headers(404,"","","",100).error.code==Code::ProviderUnavailable);
    check(retry_delay("100000",1700000000)==100000);
    check(retry_delay("Tue, 14 Nov 2023 22:13:57 GMT",1700000000)==37);
    check(retry_delay("1",1700000000)==5);
    RetryDeadline gate; check(bool(gate.defer("30",1700000000,0)));check(bool(gate.defer("5",1700000001,1)));check(gate.deadline()==30);
    check(gate.admission(1).error.retryAfterSeconds==29);check(gate.admission(29.99).error.retryAfterSeconds==1);check(bool(gate.admission(30)));
    check(bool(gate.defer("100000",1700000000,0)));check(!gate.admission(0).error.retryAfterSeconds);check(gate.admission(86401).error.retryAfterSeconds==13599);
    check(!gate.defer("5",1700000000,std::numeric_limits<double>::infinity()));check(gate.deadline()==100000);
    std::cout<<"{\"checks\":"<<checks<<",\"passed\":true}\n"; return 0;
}
}
int main(int argc, char** argv) {
#ifdef _WIN32
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
#endif
    if (argc==1) return selftest();
    if (argc!=2 || std::string(argv[1])!="--json-lines") return 2;
    std::string line;
    while (std::getline(std::cin,line)) {
        if (line.size()>3'000'000) return 3;
        const auto doc=QJsonDocument::fromJson(QByteArray::fromStdString(line));
        if (!doc.isObject()) return 4;
        const auto data=QJsonDocument(dispatch(doc.object())).toJson(QJsonDocument::Compact);
        std::cout.write(data.constData(),data.size());std::cout<<'\n';
    }
    return 0;
}
