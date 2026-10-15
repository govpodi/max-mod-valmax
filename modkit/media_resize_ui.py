"""Keep photo layout live while coalescing only expensive image decode sizes."""
from __future__ import annotations

import hashlib

RESOURCE = ":/qt/qml/PreviewGridModule/HistoryPreviewCell.qml"
SOURCE_SHA256 = "d9fbf9337ca699dce0937cbd0453278f4e14b303c5939b4c6bca0e4de17a6df6"
NORMALIZED_SOURCE_SHA256 = "c5c00d1bd3537fe01525e070a5f539b8f51f66a6d3d81e4a66fbadeed740877d"
MARKER = "// VALMAX_PHOTO_DECODE_RESIZE"
DECODE_INTERVAL_MS = 120

PHOTO_DECODE_PROPERTIES = '''            // VALMAX_PHOTO_DECODE_RESIZE
            id: valmaxPhotoImage

            // Pixel geometry stays bound to previewRect. Only a replacement
            // decoded texture waits until resizing has settled.
            readonly property size valmaxRequestedDecodeSize: root.sourceSize
            property size valmaxDecodeSize: root.sourceSize
            property bool valmaxDecodeInitialized: false
            property url valmaxDecodedSource: ""

            function valmaxCommitDecodeSize() {
                if (valmaxPhotoDecodeTimer) valmaxPhotoDecodeTimer.stop();
                valmaxDecodeSize = valmaxRequestedDecodeSize;
            }
            onValmaxRequestedDecodeSizeChanged: {
                if (valmaxDecodeInitialized && valmaxPhotoDecodeTimer)
                    valmaxPhotoDecodeTimer.restart();
            }
            // URL refresh, reconnect and a new photo must never use a queued
            // decode size belonging to the previous source.
            onSourceChanged: if (valmaxDecodeInitialized) valmaxCommitDecodeSize()
            onStatusChanged: if (status === Image.Ready) valmaxDecodedSource = source
            Component.onCompleted: {
                valmaxCommitDecodeSize();
                valmaxDecodeInitialized = true;
            }
            Timer {
                id: valmaxPhotoDecodeTimer
                interval: 120
                repeat: false
                onTriggered: valmaxPhotoImage.valmaxCommitDecodeSize()
            }
            asynchronous: true
            retainWhileLoading: source.toString() === valmaxDecodedSource.toString()
            // Also hide the old pixels on a changed source. Qt can begin its
            // native load before the sourceChanged QML binding is evaluated.
            visible: status === Image.Ready || (status === Image.Loading && source.toString() === valmaxDecodedSource.toString())

'''


def transform(source: str) -> str:
    """Transform the exact pinned resource; no URL, media or model changes."""
    if type(source) is not str or MARKER in source:
        raise ValueError("Photo resize requires untouched pinned native QML")
    source = source.replace("\r\n", "\n")
    if hashlib.sha256(source.encode("utf8")).hexdigest() != NORMALIZED_SOURCE_SHA256:
        raise ValueError("Unexpected native photo-preview source")
    replacements = (
        ("        source: root.previewSource\n",
         "        source: root.previewSource\n        fillMode: Image.PreserveAspectCrop\n        clip: true\n"),
        ("        sourceComponent: Image {\n\n",
         "        sourceComponent: Image {\n\n" + PHOTO_DECODE_PROPERTIES),
        ("            sourceSize: root.sourceSize\n",
         "            sourceSize: valmaxDecodeSize\n"),
    )
    for anchor, replacement in replacements:
        if source.count(anchor) != 1:
            raise ValueError("Unexpected native photo-preview anchor")
        source = source.replace(anchor, replacement, 1)
    return source


def transform_sources(sources: dict[str, str]) -> dict[str, str]:
    """Follow the shared resource-generator basename contract."""
    name = RESOURCE.rsplit("/", 1)[1]
    if type(sources) is not dict or name not in sources:
        raise ValueError("Pinned history-photo resource is required")
    result = dict(sources)
    result[name] = transform(result[name])
    return result
