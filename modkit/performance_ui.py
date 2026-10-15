"""Debounce position writes without delaying native resize geometry."""
MARKER = "// VALMAX_WINDOW_POSITION_DEBOUNCE"
PERSISTENCE = '''    // VALMAX_WINDOW_POSITION_DEBOUNCE
    function commitValMAXWindowPosition() {
        valmaxWindowPositionTimer.stop();
        if (windowState.x !== root.x) windowState.x = root.x;
        if (windowState.y !== root.y) windowState.y = root.y;
    }
    Timer {
        id: valmaxWindowPositionTimer
        objectName: "valmaxWindowPositionTimer"
        interval: 120
        repeat: false
        onTriggered: root.commitValMAXWindowPosition()
    }
    onVisibleChanged: if (!visible) {
        if (valmaxWindowPositionTimer) commitValMAXWindowPosition();
    }
'''


def _once(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError("Unexpected native window-position anchor")
    return source.replace(anchor, replacement, 1)


def transform_window(source):
    if type(source) is not str or MARKER in source:
        raise ValueError("Window-position transform requires unmodified handler source")
    source = source.replace("\r\n", "\n")
    # WindowState updates QProperty bindings, frame count and native consumers
    # synchronously. Its save callback also emits windowResized. Deferring the
    # setters therefore defers layout, not just persistence, and splits media
    # geometry from the actual surface size. Keep both native handlers exact.
    size_handlers = "    onWidthChanged: windowState.width = width\n    onHeightChanged: windowState.height = height\n"
    if source.count(size_handlers) != 1:
        raise ValueError("Unexpected native window-size handlers")
    source = _once(source, "    onXChanged: windowState.x = x\n    onYChanged: windowState.y = y\n",
                   "    onXChanged: if (valmaxWindowPositionTimer) valmaxWindowPositionTimer.restart()\n"
                   "    onYChanged: if (valmaxWindowPositionTimer) valmaxWindowPositionTimer.restart()\n\n" + PERSISTENCE)
    source = _once(source, "    onClosing: closeEvent => {\n",
                   "    onClosing: closeEvent => {\n        root.commitValMAXWindowPosition();\n")
    return source


def transform_sources(sources):
    if "MeWindow.qml" not in sources:
        raise ValueError("Native window resource is required")
    result = dict(sources)
    result["MeWindow.qml"] = transform_window(result["MeWindow.qml"])
    return result
