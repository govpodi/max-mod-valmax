#include "thumbnail.h"
#include <QBuffer>
#include <QColorSpace>
#include <QCoreApplication>
#include <QImageReader>
#include <QJsonDocument>
#include <QJsonObject>
#include <QtEndian>
#include <cstdio>
#include <cstdlib>
#include <future>

using namespace valmax::media;
namespace {
int checks = 0;
void require(bool ok, const char* name) {
    ++checks;
    if (!ok) { std::fprintf(stderr, "FAILED: %s\n", name); std::exit(1); }
}
QByteArray encode(const QImage& image, const char* format) {
    QByteArray bytes;
    QBuffer buffer(&bytes);
    require(buffer.open(QIODevice::WriteOnly) && image.save(&buffer, format), "synthetic image encode");
    return bytes;
}
quint32 crc(const QByteArray& data) {
    quint32 value = 0xffffffff;
    for (char c : data) {
        value ^= static_cast<quint8>(c);
        for (int i = 0; i < 8; ++i) value = (value >> 1) ^ (0xedb88320u & (0u - (value & 1u)));
    }
    return ~value;
}
QByteArray dimensions(QByteArray png, quint32 width, quint32 height) {
    qToBigEndian(width, png.data() + 16);
    qToBigEndian(height, png.data() + 20);
    qToBigEndian(crc(png.sliced(12, 17)), png.data() + 29);
    return png;
}
bool rejected(const Result<Thumbnail>& result, Code code = Code::UnsupportedMedia) {
    return !result && result.error.code == code;
}
}
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    QCoreApplication::setLibraryPaths({QCoreApplication::applicationDirPath() + QStringLiteral("/plugins")});
    const int allocationBefore = QImageReader::allocationLimit();
    QImage source(96, 48, QImage::Format_ARGB32);
    source.fill(QColor(60, 120, 180, 255));
    source.setText(QStringLiteral("Comment"), QStringLiteral("synthetic private metadata"));
    source.setDevicePixelRatio(2.0);
    QByteArray png = encode(source, "PNG");
    QByteArray jpeg = encode(source, "JPEG");
    QByteArray webp = encode(source, "WEBP");
    const QByteArray gif = QByteArray::fromBase64("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7");
    const QList<QPair<QByteArray, QString>> fixtures = {
        {png, QStringLiteral("image/png")}, {jpeg, QStringLiteral("image/jpeg")},
        {webp, QStringLiteral("image/webp")}, {gif, QStringLiteral("image/gif")}};
    for (const auto& [bytes, mime] : fixtures) {
        const auto result = decode_thumbnail(bytes, mime, QSize(40, 40));
        require(bool(result), "all four actual codecs decode");
        require(result.value->image.width() <= 40 && result.value->image.height() <= 40, "fit thumbnail bounds");
        require(result.value->image.format() == QImage::Format_ARGB32_Premultiplied, "single bounded output format");
        require(result.value->image.textKeys().isEmpty(), "input text discarded");
        require(result.value->image.devicePixelRatio() == 1., "input DPR discarded");
        require(result.value->image.sizeInBytes() <= 40 * 40 * 4, "bounded output bytes");
        require(rejected(decode_thumbnail(bytes.left(10), mime)), "truncated codec rejected");
    }
    const auto normal = decode_thumbnail(png, QStringLiteral("image/png"));
    require(normal && normal.value->sourceSize == QSize(96, 48), "source header size retained");
    require(normal.value->image.size() == QSize(96, 48), "small image not upscaled");
    require(normal.value->image.pixelColor(20, 20) == QColor(60, 120, 180), "pixel values retained");
    require(normal.value->image.colorSpace() == QColorSpace(QColorSpace::SRgb), "output has known sRGB profile");
    QImage wide(4, 4, QImage::Format_ARGB32);
    wide.fill(QColor(60, 120, 180));
    wide.setColorSpace(QColorSpace::DisplayP3);
    const auto wideResult = decode_thumbnail(encode(wide, "PNG"), QStringLiteral("image/png"));
    require(wideResult && wideResult.value->image.colorSpace() == QColorSpace(QColorSpace::SRgb), "wide gamut normalized");
    require(wideResult.value->image.pixelColor(0, 0) != wide.pixelColor(0, 0), "wide gamut pixels converted before profile drop");
    QImage translucent(4, 4, QImage::Format_ARGB32);
    translucent.fill(QColor(200, 100, 20, 128));
    const auto alpha = decode_thumbnail(encode(translucent, "PNG"), QStringLiteral("image/png"));
    require(alpha && alpha.value->image.pixelColor(0, 0).alpha() == 128, "alpha preserved");
    const auto half = decode_thumbnail(png, QStringLiteral("image/png"), QSize(30, 30));
    require(half && half.value->image.size() == QSize(30, 15), "aspect ratio");
    require(rejected(decode_thumbnail(png, QStringLiteral("image/jpeg"))), "signature MIME mismatch");
    require(rejected(decode_thumbnail(png, QStringLiteral("image/svg+xml"))), "unsupported decoder rejected");
    require(rejected(decode_thumbnail({}, QStringLiteral("image/png"))), "empty bytes");
    for (QSize invalid : {QSize(), QSize(0, 1), QSize(1, 0), QSize(-1, 40), QSize(513, 40), QSize(40, 513), QSize(2147483647, 2147483647)})
        require(rejected(decode_thumbnail(png, QStringLiteral("image/png"), invalid), Code::InvalidInput), "requested bounds before decode");
    require(rejected(decode_thumbnail(QByteArray(MaxPreviewBytes + 1, 'x'), QStringLiteral("image/png"))), "encoded byte bound");
    for (QSize invalid : {QSize(0, 1), QSize(1, 0), QSize(4097, 1), QSize(1, 4097), QSize(4096, 1025), QSize(2147483647, 2147483647)})
        require(rejected(decode_thumbnail(dimensions(png, quint32(invalid.width()), quint32(invalid.height())), QStringLiteral("image/png"))), "source bounds before pixel allocation");

    QImage edge(4096, 1024, QImage::Format_ARGB32);
    edge.fill(Qt::blue);
    const auto edgeBytes = encode(edge, "PNG");
    const auto edgeResult = decode_thumbnail(edgeBytes, QStringLiteral("image/png"), QSize(512, 512));
    require(edgeResult && edgeResult.value->sourceSize == edge.size(), "exact source pixel budget allowed");
    require(edgeResult.value->image.size() == QSize(512, 128), "large source final bounded");
    require(edgeResult.value->image.pixelColor(100, 30) == QColor(Qt::blue), "large source decoded pixels");
    for (QSize thinSize : {QSize(4096, 1), QSize(1, 4096)}) {
        QImage thin(thinSize, QImage::Format_ARGB32);
        thin.fill(Qt::green);
        const auto thinBytes = encode(thin, "PNG");
        for (QSize box : {QSize(240, 180), QSize(1, 1)}) {
            const auto result = decode_thumbnail(thinBytes, QStringLiteral("image/png"), box);
            require(bool(result), "extreme aspect ratio stays at least one pixel");
            require(result.value->image.width() >= 1 && result.value->image.height() >= 1
                && result.value->image.width() <= box.width() && result.value->image.height() <= box.height(), "thin result bounded");
        }
    }
    // Authored two-frame GIF: global red/blue palette, red frame then blue.
    const auto animated = QByteArray::fromHex("47494638396101000100800000ff00000000ff"
        "2c0000000001000100000202440100"
        "2c00000000010001000002024c01003b");
    QBuffer animatedBuffer;
    animatedBuffer.setData(animated);
    require(animatedBuffer.open(QIODevice::ReadOnly), "animation fixture open");
    QImageReader animationReader(&animatedBuffer, QByteArrayLiteral("gif"));
    require(animationReader.imageCount() == 2, "fixture is two frames");
    require(animationReader.read().pixelColor(0, 0) == QColor(Qt::red), "fixture first red");
    require(animationReader.read().pixelColor(0, 0) == QColor(Qt::blue), "fixture second blue");
    const auto animatedResult = decode_thumbnail(animated, QStringLiteral("image/gif"));
    require(animatedResult && animatedResult.value->image.pixelColor(0, 0) == QColor(Qt::red), "thumbnail decodes first animation frame only");

    // Orientation 6 in a minimal EXIF IFD: scaled size must account for a 90deg turn.
    const QByteArray exif = QByteArray::fromHex("ffe1002245786966000049492a0008000000010012010300010000000600000000000000");
    jpeg.insert(2, exif);
    const auto rotated = decode_thumbnail(jpeg, QStringLiteral("image/jpeg"), QSize(20, 80));
    require(rotated && rotated.value->image.size() == QSize(20, 40), "EXIF quarter-turn preserves final fit");
    require(rotated.value->image.textKeys().isEmpty(), "EXIF metadata not retained");
    // Deliberately invoke in two workers: decoder has no shared mutable policy.
    auto first = std::async(std::launch::async, [png] { return decode_thumbnail(png, QStringLiteral("image/png")); });
    auto second = std::async(std::launch::async, [webp] { return decode_thumbnail(webp, QStringLiteral("image/webp")); });
    require(bool(first.get()) && bool(second.get()), "concurrent worker decode");
    require(QImageReader::allocationLimit() == allocationBefore, "global codec allocation policy unchanged");
    const QJsonObject proof{{QStringLiteral("passed"), true}, {QStringLiteral("checks"), checks},
        {QStringLiteral("qt"), QString::fromLatin1(qVersion())}, {QStringLiteral("scope"), QStringLiteral("synthetic memory-only thumbnail decode; no network, disk staging or GUI")}};
    std::puts(QJsonDocument(proof).toJson(QJsonDocument::Compact).constData());
    return 0;
}
