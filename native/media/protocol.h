#pragma once

#include <QByteArray>
#include <QJsonObject>
#include <QString>
#include <QVector>
#include <optional>

// Pure Wikimedia policy. No I/O, clocks, global mutable state or resource loads.
// Transport must enforce these limits while receiving, validate every redirect
// BEFORE following it, and validate the final URL again. This is not a transport.
namespace valmax::media {
inline constexpr qsizetype MaxApiBytes = 1'000'000;
inline constexpr qsizetype MaxFileBytes = 25 * 1024 * 1024;
inline constexpr qsizetype MaxPreviewBytes = 2 * 1024 * 1024;
inline constexpr int ResultLimit = 6;
inline constexpr int MaxRetrySeconds = 86400;
inline constexpr char Api[] = "https://commons.wikimedia.org/w/api.php";
inline constexpr char Provider[] = "Wikimedia Commons";
inline constexpr char UserAgent[] = "ValMAX-MediaSearch/1.1 (https://github.com/govpodi/max-mod-valmax)";

enum class Code { None, InvalidInput, InvalidResponse, InvalidRemoteUrl,
    FileTooLarge, UnsupportedMedia, ProviderBusy, ProviderUnavailable };
struct Error {
    Code code = Code::None;
    int status = 0;
    std::optional<int> retryAfterSeconds;
};
QString code_name(Code code);
template<class T> struct Result {
    std::optional<T> value;
    Error error;
    explicit operator bool() const noexcept { return value.has_value(); }
};
struct Unit {};
enum class Mode { Picture, Video };
enum class UrlKind { Api, Source, Media };
struct SearchRequest { Mode mode; QString query; QString apiUrl; };
Result<SearchRequest> parse_search_request(const QByteArray& utf8Json);
Result<QString> parse_selection_request(const QByteArray& utf8Json);
Result<QString> validate_url(const QString& url, UrlKind kind);
Result<QString> canonical_media_url(const QString& url);
// Absolute redirect targets only. Transport resolves a relative Location against
// its current validated URL first. API cannot redirect to media or vice versa.
Result<QString> validate_redirect(const QString& current, const QString& target, UrlKind kind);

// No resource-capable HTML engine is involved. Unsupported/malformed markup
// produces empty text; a row with empty author/license is deliberately omitted.
QString plain_metadata(const QString& html, int limit = 240);
struct PublicMetadata {
    QString title, author, license, source, mime;
    qint64 bytes = 0;
    int width = 0, height = 0;
    bool preview = false;
    // No original/thumbnail URL or search text. Caller supplies an opaque ID
    // only after adding the candidate to its bounded expiring private cache.
    Result<QJsonObject> with_id(const QString& opaqueId) const;
};
struct Candidate {
    PublicMetadata metadata;
    QString originalUrl, previewUrl; // PRIVATE service data, never publish to QML.
};
Result<QVector<Candidate>> parse_search_response(const QByteArray& utf8Json,
                                               const QString& mime, Mode mode);
bool valid_content(const QByteArray& bytes, const QString& mime);
QString media_suffix(const QString& mime);
Result<Unit> validate_preview(const QByteArray& bytes, const QString& mime);
Result<Unit> validate_download(const QByteArray& bytes, const QString& mime,
                               const PublicMetadata& expected);
QString attribution(const PublicMetadata& metadata);

// Raw header values, empty = absent. No decoding/compression is accepted.
// Does not consume Retry-After or schedule retry; caller owns one shared gate.
Result<QString> validate_response_headers(int httpStatus, const QString& contentType,
    const QString& contentEncoding, const QString& contentLength, qsizetype byteLimit);

double retry_delay(const QString& header, double wallSeconds);
// Caller serializes access, supplies finite monotonic clocks, and uses ONE
// instance for API, thumbnails and originals. Pure state, no timers or retries.
class RetryDeadline {
public:
    Result<Unit> defer(const QString& header, double wallSeconds, double monotonicSeconds);
    Result<Unit> admission(double monotonicSeconds) const;
    double deadline() const noexcept { return until_; }
private:
    double until_ = 0;
};
} // namespace valmax::media
