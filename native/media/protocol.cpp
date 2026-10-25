#include "protocol.h"
#include <QDateTime>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QRegularExpression>
#include <QStringDecoder>
#include <QUrl>
#include <QTimeZone>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>

namespace valmax::media {
namespace {
template<class T> Result<T> ok(T value) { return {std::move(value), {}}; }
template<class T> Result<T> fail(Code code, int status) { return {{}, {code, status, {}}}; }
bool scalar_string(QStringView s) {
    for (qsizetype i = 0; i < s.size(); ++i) {
        if (s[i].isHighSurrogate()) {
            if (++i >= s.size() || !s[i].isLowSurrogate()) return false;
        } else if (s[i].isLowSurrogate()) return false;
    }
    return true;
}
QString cut_points(const QString& s, qsizetype count) {
    qsizetype i = 0;
    while (i < s.size() && count-- > 0) {
        if (s[i].isHighSurrogate()) ++i;
        ++i;
    }
    return s.first(i);
}
qsizetype points(const QString& s) { return s.size() - std::count_if(s.begin(), s.end(), [](QChar c) { return c.isLowSurrogate(); }); }
bool py_space(QChar c) {
    const auto u = c.unicode();
    return c.isSpace() || (u >= 0x1c && u <= 0x1f);
}
QString py_strip(const QString& s) {
    qsizetype a = 0, b = s.size();
    while (a < b && py_space(s[a])) ++a;
    while (b > a && py_space(s[b - 1])) --b;
    return s.sliced(a, b - a);
}
bool valid_tree(const QJsonValue& v, int depth = 0) {
    if (depth > 64) return false;
    if (v.isString()) return scalar_string(v.toString());
    if (v.isArray()) for (const auto& x : v.toArray()) { if (!valid_tree(x, depth + 1)) return false; }
    if (v.isObject()) {
        const auto o = v.toObject();
        for (auto it = o.begin(); it != o.end(); ++it)
            if (!scalar_string(it.key()) || !valid_tree(it.value(), depth + 1)) return false;
    }
    return true;
}
// QJsonValue intentionally erases the lexical distinction 1 / 1.0. Validate
// the original JSON first, then replace nonzero floating-point tokens with 0.5
// so an integral float cannot pass Python's type(value) is int policy. Zero
// remains zero (already outside every accepted integer range). Preserve JSON
// type and truthiness: an invalid numeric thumbnail must NOT become absent.
std::optional<QJsonObject> json_object(const QByteArray& bytes, qsizetype limit) {
    if (bytes.size() > limit || bytes.startsWith("\xef\xbb\xbf")) return {};
    QStringDecoder decoder(QStringDecoder::Utf8, QStringConverter::Flag::Stateless | QStringConverter::Flag::ConvertInitialBom);
    const QString decoded = decoder.decode(bytes);
    if (decoder.hasError()) return {};
    QJsonParseError error;
    auto doc = QJsonDocument::fromJson(bytes, &error);
    if (error.error != QJsonParseError::NoError || !doc.isObject() || !valid_tree(doc.object())) return {};
    QByteArray normalized;
    normalized.reserve(bytes.size());
    bool inString = false, escape = false;
    for (qsizetype i = 0; i < bytes.size();) {
        const char ch = bytes[i];
        if (inString) {
            normalized += ch; ++i;
            if (escape) escape = false;
            else if (ch == '\\') escape = true;
            else if (ch == '"') inString = false;
        } else if (ch == '"') { inString = true; normalized += ch; ++i; }
        else if (ch == '-' || (ch >= '0' && ch <= '9')) {
            const auto start = i;
            bool floating = false;
            while (i < bytes.size() && (bytes[i] == '-' || bytes[i] == '+' || bytes[i] == '.' || bytes[i] == 'e' || bytes[i] == 'E' || (bytes[i] >= '0' && bytes[i] <= '9'))) {
                floating |= bytes[i] == '.' || bytes[i] == 'e' || bytes[i] == 'E'; ++i;
            }
            const auto token = bytes.sliced(start, i - start);
            if (floating) normalized += token.toDouble() == 0 ? "0" : "0.5"; else normalized += token;
        } else { normalized += ch; ++i; }
    }
    doc = QJsonDocument::fromJson(normalized, &error);
    if (error.error != QJsonParseError::NoError) return {};
    return doc.object();
}
bool opaque_id(const QString& s) {
    if (s.size() != 32) return false;
    return std::all_of(s.begin(), s.end(), [](QChar c) { return (c >= u'0' && c <= u'9') || (c >= u'a' && c <= u'f'); });
}
QString form_encode(const QString& s) {
    QByteArray out;
    constexpr char hex[] = "0123456789ABCDEF";
    for (const unsigned char c : s.toUtf8()) {
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') out += char(c);
        else if (c == ' ') out += '+';
        else { out += '%'; out += hex[c >> 4]; out += hex[c & 15]; }
    }
    return QString::fromLatin1(out);
}
bool ascii_alpha(QChar c) { return (c >= u'a' && c <= u'z') || (c >= u'A' && c <= u'Z'); }
int hex_value(QChar c) {
    const auto u = c.toLower().unicode();
    if (u >= '0' && u <= '9') return u - '0';
    if (u >= 'a' && u <= 'f') return u - 'a' + 10;
    return -1;
}
std::optional<QString> percent_decode(const QString& s, bool plus = false) {
    QByteArray bytes;
    for (qsizetype i = 0; i < s.size(); ++i) {
        if (s[i] == u'%') {
            if (i + 2 >= s.size() || hex_value(s[i + 1]) < 0 || hex_value(s[i + 2]) < 0) return {};
            bytes += char((hex_value(s[i + 1]) << 4) | hex_value(s[i + 2])); i += 2;
        } else if (plus && s[i] == u'+') bytes += ' ';
        else {
            qsizetype n = s[i].isHighSurrogate() ? 2 : 1;
            bytes += s.sliced(i, n).toUtf8(); i += n - 1;
        }
    }
    QStringDecoder decoder(QStringDecoder::Utf8, QStringConverter::Flag::Stateless | QStringConverter::Flag::ConvertInitialBom);
    QString text = decoder.decode(bytes);
    if (decoder.hasError()) return {};
    return text;
}
struct Parts { QString path, query; qsizetype queryAt = -1; };
std::optional<Parts> raw_parts(const QString& s) {
    if (!scalar_string(s) || points(s) > 2048 || s.isEmpty()) return {};
    for (const auto c : s) if (c.unicode() <= 0x20 || c == u'\\' || c.unicode() == 0x7f) return {};
    const auto colon = s.indexOf(QStringLiteral("://"));
    if (colon < 0) return {};
    const auto slash = s.indexOf(u'/', colon + 3);
    if (slash < 0) return {};
    // Reject even empty fragment/query delimiters on media; no QUrl cleanup
    // may hide a raw path or user-info delimiter before policy is applied.
    const auto hash = s.indexOf(u'#');
    if (hash >= 0) return {};
    const auto question = s.indexOf(u'?', colon + 3);
    if (question >= 0 && question < slash) return {};
    Parts p{question < 0 ? s.sliced(slash) : s.sliced(slash, question - slash), {}, question};
    if (question >= 0) p.query = s.sliced(question + 1);
    return p;
}
bool safe_decoded_path(const QString& raw) {
    const auto decoded = percent_decode(raw);
    if (!decoded) return false;
    for (QChar c : *decoded) if (c == u'\\' || c.unicode() < 32 || c.unicode() == 127) return false;
    const auto segments = decoded->split(u'/');
    return !segments.contains(QStringLiteral(".")) && !segments.contains(QStringLiteral(".."));
}
#include "html_entities.inc"
QString entity_value(QStringView name) {
    const QByteArray key = name.toLatin1();
    const auto end = std::end(Entities);
    auto found = std::lower_bound(std::begin(Entities), end, key.constData(), [](const Entity& a, const char* b) { return std::strcmp(a.name, b) < 0; });
    return found != end && std::strcmp(found->name, key.constData()) == 0 ? QString::fromUtf8(found->utf8) : QString();
}
QString character_reference(char32_t cp) {
    // HTML numeric-reference replacement, matching html.unescape's C1 map.
    constexpr char32_t c1[] = {0x20ac,0x81,0x201a,0x192,0x201e,0x2026,0x2020,0x2021,0x2c6,0x2030,0x160,0x2039,0x152,0x8d,0x17d,0x8f,
        0x90,0x2018,0x2019,0x201c,0x201d,0x2022,0x2013,0x2014,0x2dc,0x2122,0x161,0x203a,0x153,0x9d,0x17e,0x178};
    if (cp == 0 || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) cp = 0xfffd;
    else if (cp >= 0x80 && cp <= 0x9f) cp = c1[cp - 0x80];
    else if ((cp >= 1 && cp <= 8) || cp == 0xb || (cp >= 0xe && cp <= 0x1f) || cp == 0x7f || (cp >= 0xfdd0 && cp <= 0xfdef) || (cp & 0xffff) == 0xfffe || (cp & 0xffff) == 0xffff) return {};
    return QString::fromUcs4(&cp, 1);
}
QString unescape_text(const QString& s) {
    QString out;
    for (qsizetype i = 0; i < s.size();) {
        if (s[i] != u'&') { out += s[i++]; continue; }
        if (i + 2 < s.size() && s[i + 1] == u'#') {
            qsizetype j = i + 2;
            const bool hex = s[j] == u'x' || s[j] == u'X'; if (hex) ++j;
            const auto start = j;
            quint64 value = 0;
            while (j < s.size()) {
                const int digit = hex_value(s[j]);
                if (digit < 0 || (!hex && digit >= 10)) break;
                value = std::min<quint64>(0x110000, value * (hex ? 16 : 10) + digit); ++j;
            }
            if (j > start) { out += character_reference(char32_t(value)); if (j < s.size() && s[j] == u';') ++j; i = j; continue; }
        } else {
            const qsizetype available = std::min<qsizetype>(33, s.size() - i - 1);
            bool matched = false;
            for (qsizetype n = available; n > 0; --n) {
                const auto value = entity_value(QStringView(s).sliced(i + 1, n));
                if (!value.isEmpty()) { out += value; i += n + 1; matched = true; break; }
            }
            if (matched) continue;
        }
        out += s[i++];
    }
    return out;
}
bool integer_between(const QJsonValue& v, qint64 low, qint64 high) {
    return v.isDouble() && std::floor(v.toDouble()) == v.toDouble() && v.toInteger(-1) >= low && v.toInteger(-1) <= high;
}
bool dimensions(qint64 bytes, int w, int h) {
    return bytes >= 1 && bytes <= MaxFileBytes && w >= 1 && w <= 16000 && h >= 1 && h <= 16000 && qint64(w) * h <= 80'000'000;
}
} // namespace

QString code_name(Code code) {
    switch (code) {
    case Code::None: return QStringLiteral("ok");
    case Code::InvalidInput: return QStringLiteral("invalid_input");
    case Code::InvalidResponse: return QStringLiteral("invalid_response");
    case Code::InvalidRemoteUrl: return QStringLiteral("invalid_remote_url");
    case Code::FileTooLarge: return QStringLiteral("file_too_large");
    case Code::UnsupportedMedia: return QStringLiteral("unsupported_media");
    case Code::ProviderBusy: return QStringLiteral("provider_busy");
    case Code::ProviderUnavailable: return QStringLiteral("provider_unavailable");
    }
    return QStringLiteral("invalid_response");
}
Result<SearchRequest> parse_search_request(const QByteArray& bytes) {
    const auto o = json_object(bytes, 8192);
    if (!o || o->size() != 2 || !o->value(QStringLiteral("mode")).isString() || !o->value(QStringLiteral("query")).isString()) return fail<SearchRequest>(Code::InvalidInput, 400);
    const auto modeText = o->value(QStringLiteral("mode")).toString();
    if (modeText != QStringLiteral("pic") && modeText != QStringLiteral("vid")) return fail<SearchRequest>(Code::InvalidInput, 400);
    const auto query = py_strip(o->value(QStringLiteral("query")).toString());
    if (points(query) < 2 || points(query) > 160) return fail<SearchRequest>(Code::InvalidInput, 400);
    for (QChar c : query) if (c.unicode() < 32 || c.unicode() == 127) return fail<SearchRequest>(Code::InvalidInput, 400);
    auto literal = query; literal.replace(u'\\', u' '); literal.replace(u'"', u' ');
    const Mode mode = modeText == QStringLiteral("pic") ? Mode::Picture : Mode::Video;
    const QVector<QPair<QString, QString>> fields = {
        {QStringLiteral("action"),QStringLiteral("query")},{QStringLiteral("format"),QStringLiteral("json")},{QStringLiteral("formatversion"),QStringLiteral("2")},
        {QStringLiteral("generator"),QStringLiteral("search")},{QStringLiteral("gsrnamespace"),QStringLiteral("6")},{QStringLiteral("gsrlimit"),QStringLiteral("6")},
        {QStringLiteral("gsrsearch"),QStringLiteral("filetype:") + (mode == Mode::Picture ? QStringLiteral("bitmap") : QStringLiteral("video")) + QStringLiteral(" \"") + literal + u'"'},
        {QStringLiteral("prop"),QStringLiteral("imageinfo")},{QStringLiteral("iiprop"),QStringLiteral("url|size|mime|extmetadata")},{QStringLiteral("iiurlwidth"),QStringLiteral("240")},
        {QStringLiteral("iiextmetadatafilter"),QStringLiteral("Artist|LicenseShortName|LicenseUrl|AttributionRequired")},{QStringLiteral("iiextmetadatalanguage"),QStringLiteral("ru")}};
    QString url = QString::fromLatin1(Api) + u'?';
    for (const auto& pair : fields) { if (!url.endsWith(u'?')) url += u'&'; url += form_encode(pair.first) + u'=' + form_encode(pair.second); }
    return ok(SearchRequest{mode,query,url});
}
Result<QString> parse_selection_request(const QByteArray& bytes) {
    const auto o = json_object(bytes, 1024);
    if (!o || o->size() != 1 || !o->value(QStringLiteral("id")).isString()) return fail<QString>(Code::InvalidInput,400);
    const auto id = o->value(QStringLiteral("id")).toString();
    return opaque_id(id) ? ok(id) : fail<QString>(Code::InvalidInput,400);
}
Result<QString> validate_url(const QString& s, UrlKind kind) {
    auto invalid = [] { return fail<QString>(Code::InvalidRemoteUrl,502); };
    if (kind != UrlKind::Api && kind != UrlKind::Source && kind != UrlKind::Media) return invalid();
    const auto p = raw_parts(s);
    if (!p || !safe_decoded_path(p->path)) return invalid();
    const QUrl url(s, QUrl::StrictMode);
    if (!url.isValid() || url.isRelative() || url.scheme() != QStringLiteral("https") || url.port(443) != 443 || !url.userInfo().isEmpty()) return invalid();
    const auto authority = s.sliced(s.indexOf(QStringLiteral("://")) + 3).section(u'/',0,0);
    if (authority.contains(u'@') || authority.contains(u'%')) return invalid();
    // QUrl applies IDNA/nameprep (including fullwidth ASCII mappings). Compare
    // the raw ASCII hostname as well: normalization must never widen allowlist.
    const auto rawHost = authority.section(u':',0,0).toLower();
    if (rawHost != url.host()) return invalid();
    const bool mediaHost = url.host() == QStringLiteral("upload.wikimedia.org") || url.host() == QStringLiteral("thumb.wikimedia.org");
    if ((kind == UrlKind::Media && !mediaHost) || (kind != UrlKind::Media && url.host() != QStringLiteral("commons.wikimedia.org"))) return invalid();
    if (kind == UrlKind::Api) { if (p->path != QStringLiteral("/w/api.php")) return invalid(); }
    else if (p->queryAt >= 0 || !p->path.startsWith(kind == UrlKind::Source ? QStringLiteral("/wiki/File:") : QStringLiteral("/wikipedia/commons/"))) return invalid();
    return ok(s);
}
Result<QString> canonical_media_url(const QString& s) {
    const auto p = raw_parts(s);
    if (!p) return fail<QString>(Code::InvalidRemoteUrl,502);
    if (p->queryAt < 0) return validate_url(s,UrlKind::Media);
    if (p->query.isEmpty()) return fail<QString>(Code::InvalidRemoteUrl,502);
    const auto fields = p->query.split(u'&');
    if (fields.size() > 8) return fail<QString>(Code::InvalidRemoteUrl,502);
    bool found = false;
    for (const auto& f : fields) {
        if (f.isEmpty()) continue;
        found = true;
        const auto eq = f.indexOf(u'=');
        const auto key = percent_decode(eq < 0 ? f : f.first(eq),true);
        if (!key || (*key != QStringLiteral("utm_source") && *key != QStringLiteral("utm_campaign") && *key != QStringLiteral("utm_content"))) return fail<QString>(Code::InvalidRemoteUrl,502);
        if (!percent_decode(eq < 0 ? QString() : f.sliced(eq + 1),true)) return fail<QString>(Code::InvalidRemoteUrl,502);
    }
    if (!found) return fail<QString>(Code::InvalidRemoteUrl,502);
    return validate_url(s.first(p->queryAt),UrlKind::Media);
}
Result<QString> validate_redirect(const QString& current, const QString& target, UrlKind kind) {
    if (kind == UrlKind::Source || !validate_url(current,kind)) return fail<QString>(Code::InvalidRemoteUrl,502);
    return kind == UrlKind::Api ? validate_url(target,kind) : canonical_media_url(target);
}
QString plain_metadata(const QString& html, int limit) {
    if (limit < 0 || limit > 240 || !scalar_string(html)) return {};
    const auto s = cut_points(html,8000);
    QString raw, text;
    auto flush = [&] { text += unescape_text(raw); raw.clear(); };
    for (qsizetype i = 0; i < s.size();) {
        if (s[i] != u'<') { raw += s[i++]; continue; }
        if (s.sliced(i).startsWith(QStringLiteral("<!--"))) {
            flush();
            const auto end = s.indexOf(QStringLiteral("-->"),i + 4); if (end < 0) return {}; i = end + 3; continue;
        }
        qsizetype start = i + 1;
        if (start < s.size() && s[start] == u'/') ++start;
        const bool tag = start < s.size() && ascii_alpha(s[start]);
        const bool declaration = i + 1 < s.size() && (s[i+1] == u'!' || s[i+1] == u'?');
        if (!tag && !declaration) { raw += s[i++]; continue; }
        flush();
        qsizetype nameEnd = start;
        while (nameEnd < s.size() && (ascii_alpha(s[nameEnd]) || s[nameEnd].isDigit() || s[nameEnd] == u'-' || s[nameEnd] == u':')) ++nameEnd;
        const auto name = s.sliced(start,nameEnd-start).toLower();
        // Conservative rejection avoids silently flattening executable/style
        // raw-text contexts into author/license; never creates a DOM or loads.
        if (name == QStringLiteral("script") || name == QStringLiteral("style") || (declaration && !s.sliced(i).startsWith(QStringLiteral("<!DOCTYPE"),Qt::CaseInsensitive))) return {};
        QChar quote;
        qsizetype end = nameEnd;
        for (; end < s.size(); ++end) {
            if (!quote.isNull()) { if (s[end] == quote) quote = {}; }
            else if (s[end] == u'\'' || s[end] == u'"') quote = s[end];
            else if (s[end] == u'>') break;
            else if (s[end] == u'<') return {};
        }
        if (end >= s.size()) return {};
        i = end + 1;
    }
    // HTMLParser.feed (without close) withholds its last text segment when
    // a character reference might continue in a later chunk. Keep that legacy
    // behavior, including the 34-character lookbehind and ASCII delimiters.
    const auto amp = raw.lastIndexOf(u'&');
    bool pendingReference = amp >= std::max<qsizetype>(0,raw.size()-34) && amp >= 0;
    if (pendingReference) for (qsizetype j=amp;j<raw.size();++j)
        if (raw[j]==u'\t' || raw[j]==u'\n' || raw[j]==u'\r' || raw[j]==u'\f' || raw[j]==u' ' || raw[j]==u';') { pendingReference=false;break; }
    if (!pendingReference) flush();
    QString collapsed;
    bool space = false;
    for (QChar c : text) {
        if (py_space(c)) { space = !collapsed.isEmpty(); continue; }
        if (space) { collapsed += u' '; space = false; }
        collapsed += c;
    }
    QString clean;
    for (QChar c : collapsed) {
        const auto u = c.unicode();
        if (u < 32 || u == 127 || u == 0x202a || u == 0x202b || u == 0x202d || u == 0x202e || (u >= 0x2066 && u <= 0x2069)) continue;
        clean += c;
    }
    return cut_points(clean,limit);
}
QString media_suffix(const QString& mime) {
    if (mime == QStringLiteral("image/jpeg")) return QStringLiteral(".jpg");
    if (mime == QStringLiteral("image/png")) return QStringLiteral(".png");
    if (mime == QStringLiteral("image/gif")) return QStringLiteral(".gif");
    if (mime == QStringLiteral("image/webp")) return QStringLiteral(".webp");
    if (mime == QStringLiteral("video/webm")) return QStringLiteral(".webm");
    if (mime == QStringLiteral("video/mp4")) return QStringLiteral(".mp4");
    return {};
}
Result<QVector<Candidate>> parse_search_response(const QByteArray& bytes, const QString& mime, Mode mode) {
    auto unavailable = [] { return fail<QVector<Candidate>>(Code::ProviderUnavailable,503); };
    if ((mode != Mode::Picture && mode != Mode::Video) || (mime != QStringLiteral("application/json") && mime != QStringLiteral("application/json; charset=utf-8"))) return unavailable();
    const auto o = json_object(bytes,MaxApiBytes);
    if (!o || o->contains(QStringLiteral("error"))) return unavailable();
    const auto query = o->value(QStringLiteral("query"));
    if (!query.isUndefined() && !query.isObject()) return unavailable();
    const auto pages = query.toObject().value(QStringLiteral("pages"));
    if ((!pages.isUndefined() && !pages.isArray()) || pages.toArray().size() > ResultLimit) return unavailable();
    QVector<Candidate> result;
    for (const auto pageValue : pages.toArray()) {
        if (!pageValue.isObject()) continue;
        const auto page = pageValue.toObject();
        const auto infos = page.value(QStringLiteral("imageinfo"));
        if (!infos.isArray() || infos.toArray().isEmpty() || !infos.toArray()[0].isObject()) continue;
        const auto info = infos.toArray()[0].toObject();
        const auto mediaMime = info.value(QStringLiteral("mime")).toString();
        if (media_suffix(mediaMime).isEmpty() || !mediaMime.startsWith(mode == Mode::Picture ? QStringLiteral("image/") : QStringLiteral("video/"))) continue;
        if (!integer_between(info.value(QStringLiteral("size")),1,MaxFileBytes) || !integer_between(info.value(QStringLiteral("width")),1,16000) || !integer_between(info.value(QStringLiteral("height")),1,16000)) continue;
        const auto size = info.value(QStringLiteral("size")).toInteger();
        const int width = info.value(QStringLiteral("width")).toInt(), height = info.value(QStringLiteral("height")).toInt();
        if (!dimensions(size,width,height)) continue;
        const auto original = canonical_media_url(info.value(QStringLiteral("url")).toString());
        const auto source = validate_url(info.value(QStringLiteral("descriptionurl")).toString(),UrlKind::Source);
        if (!original || !source) continue;
        QString preview;
        const auto thumb = info.value(QStringLiteral("thumburl"));
        if (!thumb.isUndefined() && !thumb.isNull() && thumb != QJsonValue(false) && thumb != QJsonValue(0) && thumb != QJsonValue(QString())) {
            const auto p = canonical_media_url(thumb.toString()); if (!p) continue; preview = *p.value;
        }
        const auto metadata = info.value(QStringLiteral("extmetadata"));
        if (!metadata.isUndefined() && !metadata.isObject()) continue;
        const auto field = [&](const QString& key) { return plain_metadata(metadata.toObject().value(key).toObject().value(QStringLiteral("value")).toString()); };
        const auto author = field(QStringLiteral("Artist")), license = field(QStringLiteral("LicenseShortName"));
        if (author.isEmpty() || license.isEmpty()) continue;
        auto title = plain_metadata(page.value(QStringLiteral("title")).toString(),160);
        if (title.startsWith(QStringLiteral("File:"))) title.remove(0,5);
        result.push_back({{title,author,license,*source.value,mediaMime,size,width,height,!preview.isEmpty()},*original.value,preview});
    }
    return ok(std::move(result));
}
Result<QJsonObject> PublicMetadata::with_id(const QString& id) const {
    if (!opaque_id(id)) return fail<QJsonObject>(Code::InvalidInput,400);
    return ok(QJsonObject{{QStringLiteral("id"),id},{QStringLiteral("title"),title},{QStringLiteral("author"),author},{QStringLiteral("license"),license},
        {QStringLiteral("source"),source},{QStringLiteral("mime"),mime},{QStringLiteral("bytes"),bytes},{QStringLiteral("width"),width},{QStringLiteral("height"),height},{QStringLiteral("preview"),preview}});
}
bool valid_content(const QByteArray& bytes, const QString& mime) {
    if (mime == QStringLiteral("image/jpeg")) return bytes.startsWith(QByteArray::fromHex("ffd8ff"));
    if (mime == QStringLiteral("image/png")) return bytes.startsWith(QByteArray::fromHex("89504e470d0a1a0a"));
    if (mime == QStringLiteral("image/gif")) return bytes.startsWith("GIF87a") || bytes.startsWith("GIF89a");
    if (mime == QStringLiteral("image/webp")) return bytes.size() >= 12 && bytes.startsWith("RIFF") && bytes.sliced(8,4) == "WEBP";
    if (mime == QStringLiteral("video/webm")) return bytes.startsWith(QByteArray::fromHex("1a45dfa3")) && bytes.first(std::min<qsizetype>(4096,bytes.size())).contains("webm");
    if (mime == QStringLiteral("video/mp4")) return bytes.size() >= 12 && bytes.sliced(4,4) == "ftyp" && (bytes.sliced(8,4) == "isom" || bytes.sliced(8,4) == "iso2" || bytes.sliced(8,4) == "mp41" || bytes.sliced(8,4) == "mp42" || bytes.sliced(8,4) == "avc1" || bytes.sliced(8,4) == "M4V ");
    return false;
}
Result<Unit> validate_preview(const QByteArray& bytes, const QString& mime) {
    return bytes.size() <= MaxPreviewBytes && mime.startsWith(QStringLiteral("image/")) && valid_content(bytes,mime) ? ok(Unit{}) : fail<Unit>(Code::UnsupportedMedia,422);
}
Result<Unit> validate_download(const QByteArray& bytes, const QString& mime, const PublicMetadata& expected) {
    return dimensions(expected.bytes,expected.width,expected.height) && bytes.size() <= MaxFileBytes && bytes.size() == expected.bytes && mime == expected.mime && valid_content(bytes,mime) ? ok(Unit{}) : fail<Unit>(Code::UnsupportedMedia,422);
}
QString attribution(const PublicMetadata& metadata) { return QStringLiteral("Источник: Wikimedia Commons\n") + metadata.author + QStringLiteral(" · ") + metadata.license + u'\n' + metadata.source; }
Result<QString> validate_response_headers(int status, const QString& type, const QString& encoding, const QString& length, qsizetype limit) {
    if (limit < 1 || limit > MaxFileBytes) return fail<QString>(Code::InvalidInput,400);
    if (status == 429 || status == 503) return fail<QString>(Code::ProviderBusy,503);
    if (status < 200 || status > 299) return fail<QString>(Code::ProviderUnavailable,503);
    if (type.size() > 256 || encoding.size() > 128 || length.size() > 32) return fail<QString>(Code::InvalidResponse,502);
    if (!length.isEmpty()) {
        quint64 n = 0;
        for (QChar c : length) {
            if (c < u'0' || c > u'9') return fail<QString>(Code::FileTooLarge,413);
            n = n * 10 + c.unicode() - '0'; if (n > quint64(limit)) return fail<QString>(Code::FileTooLarge,413);
        }
    }
    if (!encoding.isEmpty() && encoding.toLower() != QStringLiteral("identity")) return fail<QString>(Code::UnsupportedMedia,422);
    const auto mime = type.section(u';',0,0).trimmed().toLower();
    static const QRegularExpression token(QStringLiteral("^[a-z0-9!#$&^_.+\\-]+/[a-z0-9!#$&^_.+\\-]+$"));
    if (!token.match(mime).hasMatch() || type.contains(u'\r') || type.contains(u'\n')) return fail<QString>(Code::InvalidResponse,502);
    return ok(mime);
}
double retry_delay(const QString& input, double now) {
    if (!std::isfinite(now) || points(input) > 128 || !scalar_string(input)) return 5;
    const auto header = py_strip(input);
    bool digits = !header.isEmpty() && header.size() <= 12;
    for (QChar c : header) digits &= c >= u'0' && c <= u'9';
    if (digits) return std::max(5.0,header.toDouble());
    // All three HTTP-date formats, parsed without locale or local timezone.
    // Also preserve Python's common RFC2822 numeric/missing timezone forms.
    static const QRegularExpression imf(QStringLiteral("^(?:[A-Za-z]+,\\s*)?([0-9]{1,2})[- ]([A-Za-z]{3})[- ]([0-9]{2,4})\\s+([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?(?:\\s+(GMT|UTC|UT|[+-][0-9]{4}|EST|EDT|CST|CDT|MST|MDT|PST|PDT))?$"),QRegularExpression::CaseInsensitiveOption);
    static const QRegularExpression asc(QStringLiteral("^[A-Za-z]{3}\\s+([A-Za-z]{3})\\s+([0-9]{1,2})\\s+([0-9]{2}):([0-9]{2}):([0-9]{2})\\s+([0-9]{4})$"),QRegularExpression::CaseInsensitiveOption);
    auto m = imf.match(header);
    int year=0,month=0,day=0,hour=0,minute=0,second=0,offset=0;
    QString monthName,zone;
    if (m.hasMatch()) {
        day=m.captured(1).toInt(); monthName=m.captured(2); year=m.captured(3).toInt();
        hour=m.captured(4).toInt();minute=m.captured(5).toInt();second=m.captured(6).toInt();zone=m.captured(7).toUpper();
        if (year < 100) year += year < 69 ? 2000 : 1900;
    } else {
        m=asc.match(header); if (!m.hasMatch()) return 5;
        monthName=m.captured(1);day=m.captured(2).toInt();hour=m.captured(3).toInt();minute=m.captured(4).toInt();second=m.captured(5).toInt();year=m.captured(6).toInt();
    }
    const auto months=QStringLiteral("jan feb mar apr may jun jul aug sep oct nov dec").split(u' ');
    month=int(months.indexOf(monthName.toLower()))+1;
    if (zone.startsWith(u'+') || zone.startsWith(u'-')) {
        const int h=zone.sliced(1,2).toInt(),mi=zone.sliced(3,2).toInt(); if (h>23 || mi>59) return 5;
        offset=(h*60+mi)*60*(zone[0]==u'-' ? -1 : 1);
    } else if (zone==QStringLiteral("EST") || zone==QStringLiteral("CDT")) offset=-5*3600;
    else if (zone==QStringLiteral("EDT")) offset=-4*3600;
    else if (zone==QStringLiteral("CST") || zone==QStringLiteral("MDT")) offset=-6*3600;
    else if (zone==QStringLiteral("MST") || zone==QStringLiteral("PDT")) offset=-7*3600;
    else if (zone==QStringLiteral("PST")) offset=-8*3600;
    const QDateTime date(QDate(year,month,day),QTime(hour,minute,second),QTimeZone::fromSecondsAheadOfUtc(offset));
    if (!date.isValid()) return 5;
    const double delay = double(date.toSecsSinceEpoch()) - now;
    return std::isfinite(delay) ? std::max(5.0,delay) : 5;
}
Result<Unit> RetryDeadline::defer(const QString& header, double wall, double mono) {
    if (!std::isfinite(wall) || !std::isfinite(mono) || mono < 0) return fail<Unit>(Code::InvalidInput,400);
    const double deadline = mono + retry_delay(header,wall);
    if (!std::isfinite(deadline)) return fail<Unit>(Code::InvalidInput,400);
    until_ = std::max(until_,deadline);
    return ok(Unit{});
}
Result<Unit> RetryDeadline::admission(double now) const {
    if (!std::isfinite(now) || now < 0) return fail<Unit>(Code::InvalidInput,400);
    if (now >= until_) return ok(Unit{});
    auto result = fail<Unit>(Code::ProviderBusy,503);
    const double remaining = std::ceil(until_ - now);
    if (remaining <= MaxRetrySeconds) result.error.retryAfterSeconds = int(std::max(1.0,remaining));
    return result;
}
} // namespace valmax::media
