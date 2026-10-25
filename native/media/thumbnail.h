#pragma once

#include "protocol.h"
#include <QImage>
#include <QSize>

namespace valmax::media {
inline constexpr int MaxPreviewDimension = 4096;
inline constexpr qint64 MaxPreviewPixels = 4 * 1024 * 1024;
inline constexpr int MaxThumbnailDimension = 512;
struct Thumbnail {
    QImage image;
    QSize sourceSize;
};

// Memory-only, synchronous worker primitive. Caller limits concurrent decodes
// and checks its generation again before publication. Never run on the GUI
// thread. Reads only the first frame, using the explicitly selected bundled Qt
// JPEG/PNG/GIF/WebP decoder. No URL, path, network or QPixmap is involved.
// Header/pixel and output bounds reduce ordinary allocation work; this is not
// an image-codec sandbox or a hard bound on every codec's internal allocation.
// Does not change process-wide QImageReader allocation settings.
Result<Thumbnail> decode_thumbnail(const QByteArray& bytes, const QString& mime,
                                   QSize requestedSize = QSize(240, 180));
} // namespace valmax::media
