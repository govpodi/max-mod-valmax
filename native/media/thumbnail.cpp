#include "thumbnail.h"
#include <QBuffer>
#include <QColorSpace>
#include <QImageIOHandler>
#include <QImageReader>
#include <cstring>
#include <utility>

namespace valmax::media {
namespace {
Result<Thumbnail> fail(Code code = Code::UnsupportedMedia, int status = 422) {
    return {std::nullopt, {code, status, std::nullopt}};
}
bool bounded_source(QSize size) {
    return size.width() > 0 && size.height() > 0
        && size.width() <= MaxPreviewDimension && size.height() <= MaxPreviewDimension
        && qint64(size.width()) * size.height() <= MaxPreviewPixels;
}
QSize fitted(QSize size, QSize box) {
    auto fit = size.scaled(box, Qt::KeepAspectRatio);
    // Integer rounding must not turn a very narrow valid image into 0 pixels.
    return QSize(qMax(1, fit.width()), qMax(1, fit.height()));
}
QByteArray format_for(const QString& mime) {
    if (mime == QStringLiteral("image/jpeg")) return QByteArrayLiteral("jpeg");
    if (mime == QStringLiteral("image/png")) return QByteArrayLiteral("png");
    if (mime == QStringLiteral("image/gif")) return QByteArrayLiteral("gif");
    if (mime == QStringLiteral("image/webp")) return QByteArrayLiteral("webp");
    return {};
}
}

Result<Thumbnail> decode_thumbnail(const QByteArray& bytes, const QString& mime, QSize requested) {
    if (requested.width() < 1 || requested.height() < 1
        || requested.width() > MaxThumbnailDimension || requested.height() > MaxThumbnailDimension)
        return fail(Code::InvalidInput, 400);
    if (!validate_preview(bytes, mime)) return fail();
    const auto format = format_for(mime);
    if (format.isEmpty()) return fail();
    QBuffer source;
    source.setData(bytes);
    if (!source.open(QIODevice::ReadOnly)) return fail();
    QImageReader reader(&source, format);
    reader.setAutoDetectImageFormat(false);
    reader.setDecideFormatFromContent(false);
    reader.setAutoTransform(true);
    if (!reader.canRead()) return fail();
    const auto sourceSize = reader.size();
    if (!bounded_source(sourceSize)) return fail();

    // Scaling is applied before EXIF orientation by QImageReader. Account for
    // quarter-turns before choosing the size; the final fit below checks again.
    const bool quarterTurn = reader.transformation().testFlag(QImageIOHandler::TransformationRotate90);
    QSize unrotatedBox = quarterTurn ? requested.transposed() : requested;
    if (sourceSize.width() > unrotatedBox.width() || sourceSize.height() > unrotatedBox.height())
        reader.setScaledSize(fitted(sourceSize, unrotatedBox));
    auto decoded = reader.read();
    if (decoded.isNull() || !bounded_source(decoded.size())) return fail();
    if (decoded.width() > requested.width() || decoded.height() > requested.height())
        decoded = decoded.scaled(fitted(decoded.size(), requested), Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
    if (decoded.isNull() || decoded.width() < 1 || decoded.height() < 1
        || decoded.width() > requested.width() || decoded.height() > requested.height()) return fail();
    // Convert the small result before discarding the source profile, so wide
    // gamut thumbnails keep their appearance without retaining arbitrary ICC.
    const QColorSpace srgb(QColorSpace::SRgb);
    if (decoded.colorSpace().isValid() && decoded.colorSpace() != srgb)
        decoded = decoded.convertedToColorSpace(srgb);
    decoded = decoded.convertToFormat(QImage::Format_ARGB32_Premultiplied);
    if (decoded.isNull()) return fail();

    // Copy only pixels into a fresh image: discard input text/EXIF/profile/DPR
    // metadata and avoid retaining the larger decoder backing allocation.
    QImage output(decoded.size(), QImage::Format_ARGB32_Premultiplied);
    if (output.isNull()) return fail();
    output.setColorSpace(srgb);
    const auto rowBytes = size_t(output.width()) * 4;
    for (int row = 0; row < output.height(); ++row)
        std::memcpy(output.scanLine(row), decoded.constScanLine(row), rowBytes);
    return {Thumbnail{std::move(output), sourceSize}, {}};
}
} // namespace valmax::media
