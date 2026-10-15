pragma Singleton
import QtQuick

// This file contains feature booleans only. The launcher writes the active
// snapshot beside the verified executable after the previous client exits and
// before the next one starts. QML_XHR_ALLOW_FILE_READ is child-process-only.
QtObject {
    id: root
    property var captured: null
    property bool loaded: false
    property string failureCode: ""
    readonly property var keys: Object.freeze(["enabled", "smoothWheel", "listScheduling", "listPrefetch", "readyCache",
        "historyCache", "historyBookkeeping", "photoDecodeResize", "windowPosition", "hiddenPageWork", "profileRows"])
    function defaults() {
        const value = {};
        for (let i = 0; i < keys.length; ++i) value[keys[i]] = true;
        return value;
    }
    function localSnapshotUrl() {
        if (!Qt.application.arguments.length) return "";
        const executable = String(Qt.application.arguments[0]).replace(/\\/g, "/");
        if (executable.length > 4096 || /[\u0000-\u001f\u007f]/.test(executable) ||
            !/^[A-Za-z]:\/.*\/max\.exe$/i.test(executable)) return "";
        const path = executable.slice(0, executable.lastIndexOf("/") + 1) + "optimization-active.json";
        const components = path.slice(2).split("/");
        for (let i = 0; i < components.length; ++i) components[i] = encodeURIComponent(components[i]);
        return "file:///" + path.slice(0, 2) + components.join("/");
    }
    function readSnapshot() {
        const fallback = defaults();
        const url = localSnapshotUrl();
        if (!url) { failureCode = "invalid_launch_path"; return fallback; }
        try {
            const request = new XMLHttpRequest();
            request.open("GET", url, false);
            request.send();
            if (request.readyState !== XMLHttpRequest.DONE || request.status !== 200 ||
                typeof request.responseText !== "string" || request.responseText.length > 4096) {
                failureCode = "unavailable";
                return fallback;
            }
            const value = JSON.parse(request.responseText);
            if (!value || Array.isArray(value) || value.schema !== 1 ||
                Object.keys(value).length !== 2 || !value.settings || Array.isArray(value.settings) ||
                Object.keys(value.settings).length !== keys.length) {
                failureCode = "invalid_snapshot";
                return fallback;
            }
            const clean = {};
            for (let i = 0; i < keys.length; ++i) {
                const key = keys[i];
                if (typeof value.settings[key] !== "boolean") {
                    failureCode = "invalid_snapshot";
                    return fallback;
                }
                clean[key] = value.settings[key];
            }
            loaded = true;
            return clean;
        } catch (_) {
            failureCode = "unavailable";
            return fallback;
        }
    }
    function snapshot() {
        // Completion precedes the first external singleton method call. Keep
        // reads side-effect-free: assigning inside a binding creates a loop.
        return captured === null ? Object.freeze(defaults()) : captured;
    }
    function isEnabled(key) {
        const active = snapshot();
        return active.enabled === true && active[key] === true;
    }
    Component.onCompleted: captured = Object.freeze(readSnapshot())
}
