"""Pin two native recording panels and replace their Qt Lottie indicator.

The client's recorder state, buttons, waveform, preview and media pipeline are
untouched.  The original 24px animation has a static 12px red dot and a 20px
halo that pulses over about 2.15 seconds; the 16px Qt Quick item below keeps
those proportions without importing Qt Lottie.
"""
from __future__ import annotations

import hashlib
from textwrap import indent


AUDIO_RESOURCE = ":/qt/qml/OneMeUi/InputAudioRecorderPanel.qml"
VIDEO_RESOURCE = ":/qt/qml/OneMeUi/InputVideoRecorderPanel.qml"
RESOURCE_PATHS = (AUDIO_RESOURCE, VIDEO_RESOURCE)
ORIGINAL_SHA256 = {
    AUDIO_RESOURCE: "7773cb2056a7d48a8ce60509c9ce89d0feeecc99d967faf4668ad63b62d2bf7e",
    VIDEO_RESOURCE: "a0b458ba93b360e1cacd66937ce3f27cebf4d7673ec88dad88f2d076765dc521",
}
INDENT = {AUDIO_RESOURCE: " " * 20, VIDEO_RESOURCE: " " * 12}
MARKER = "// VALMAX_QTQUICK_RECORDING_PULSE"

_OLD_BLOCK = '''LottieAnimation {
    id: progressAnimation

    width: UiKit.DesignSystem.SizeBase.px16
    height: UiKit.DesignSystem.SizeBase.px16

    loops: LottieAnimation.Infinite
    quality: LottieAnimation.HighQuality
    source: "qrc:/animations/recording.json"

    autoPlay: true
}'''

# The colour, circle proportions and cycle length match the pinned
# qrc:/animations/recording.json. Only the non-interactive indicator changes.
PULSE_QML = '''Item {
    // VALMAX_QTQUICK_RECORDING_PULSE
    id: progressAnimation
    objectName: "valmaxRecordingPulse"
    width: UiKit.DesignSystem.SizeBase.px16
    height: UiKit.DesignSystem.SizeBase.px16

    Rectangle {
        id: pulseHalo
        objectName: "valmaxRecordingPulseHalo"
        anchors.centerIn: parent
        width: progressAnimation.width * 5 / 6
        height: width
        radius: width / 2
        color: "#ff303c"
        opacity: 0.3
        antialiasing: true
        scale: 0.5
        SequentialAnimation on scale {
            running: progressAnimation.visible
            loops: Animation.Infinite
            NumberAnimation { from: 0.5; to: 1.0; duration: 1506; easing.type: Easing.InOutCubic }
            NumberAnimation { from: 1.0; to: 0.5; duration: 644; easing.type: Easing.InOutCubic }
        }
    }
    Rectangle {
        objectName: "valmaxRecordingPulseDot"
        anchors.centerIn: parent
        width: progressAnimation.width / 2
        height: width
        radius: width / 2
        color: "#ff303c"
        antialiasing: true
    }
}'''


def transform(path: str, raw: bytes) -> bytes:
    """Return a native-only resource replacement, failing on any baseline drift."""
    if path not in RESOURCE_PATHS or type(raw) is not bytes:
        raise ValueError("Unknown recording resource or invalid source")
    if hashlib.sha256(raw).hexdigest() != ORIGINAL_SHA256[path]:
        raise ValueError("Pinned recording panel changed")
    source = raw.decode("utf8").replace("\r\n", "\n")
    old_import = "import Qt.labs.lottieqt\n"
    old_block = indent(_OLD_BLOCK, INDENT[path])
    new_block = indent(PULSE_QML, INDENT[path])
    if path == VIDEO_RESOURCE:
        old_block = old_block.replace(INDENT[path] + "LottieAnimation {",
                                      INDENT[path] + "sourceComponent: LottieAnimation {", 1)
        new_block = new_block.replace(INDENT[path] + "Item {",
                                      INDENT[path] + "sourceComponent: Item {", 1)
    old_block += "\n"
    new_block += "\n"
    if (source.count(old_import) != 1 or source.count(old_block) != 1
            or source.count(MARKER) or not source.startswith("import QtQuick\n")):
        raise ValueError("Recording indicator anchors changed")
    result = source.replace(old_import, "", 1).replace(old_block, new_block, 1)
    if ("Qt.labs.lottieqt" in result or "LottieAnimation" in result
            or "qrc:/animations/recording.json" in result or result.count(MARKER) != 1):
        raise ValueError("Qt Lottie remained in the recording panel")
    return result.encode("utf8")
