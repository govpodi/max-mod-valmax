"""Actual quick-reply controller statements -> Qt bridge -> Rust synthetic store.

Only SDK visuals/current-account inputs/file picker and native draft targets are
stubbed. This is not a live MAX send or visual integration test. No attachment
target is opened, no HTTP transport or user profile is used.
"""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "native/tooling"))
import build_native_qml


def settings_controller(source):
    body = source.split("    SettingsBaseItem {\n", 1)[1].split("        component ", 1)[0]
    start = body.index("        QuickReplyDialogs.FileDialog {")
    body = body[:start] + '        QtObject { id: replyFiles; function open() { ++quickReplySettings.pickerCalls; } }\n'
    lines = []
    for line in body.splitlines():
        if "property var selfComponent:" in line or line.strip().startswith(("title:", "onBackClicked:")):
            continue
        if any("readonly property real " + key + ":" in line for key in ("gap", "sectionGap", "radius")):
            line = line.split(":", 1)[0] + ": 8"
        if "readonly property string currentAccountId:" in line:
            line = '        property string currentAccountId: "101"'
        lines.append(line)
    return ('import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\nItem {\nproperty int pickerCalls: 0\n'
            + "\n".join(lines) + "\n}\n")


def composer_controller(source):
    body = source.split("// BEGIN MENU LOADER", 1)[0]
    body = build_native_qml.native_quick_replies_declarations(body)
    original_account = 'readonly property string modQuickReplyAccountId: commonDependency && commonDependency.selfInfo ? QmlUtils.contactIdString(commonDependency.selfInfo.id) : ""'
    assert body.count(original_account) == 1
    body = body.replace(original_account, 'property string modQuickReplyAccountId: "101"')
    return '''import QtQuick
import ValMAX.Native 1.0 as ValMAXNative
Item {
    id: root
    property string currentOpenedChatId: "501"
    property bool textInputVisible: true
    property var botCommandListContext: null
    property int stageCalls: 0
    property int manualSendCalls: 0
    property var stagedFiles: []
    property var stagedMedia: []
    property bool autoObserve: true
    property string draftText: chatInput.text
    property int draftFiles: attachmentUploadModel.attachmentsCount
    function setText(text, formats) { chatInput.text = text; }
    function query(text) { chatInput.text = text; chatInput.activeFocus = true; }
    function emptyDraft() { chatInput.text = ""; attachmentUploadModel.attachmentsCount = 0; }
    function observeFiles() { attachmentUploadModel.attachmentsCount = stagedFiles.length + stagedMedia.length; }
    function nativeClear() { emptyDraft(); modQuickReplyNativeCleared(); }
    QtObject { id: chatInput; property string text: ""; property bool activeFocus: false; property bool mentionStarted: false; property bool botCommandListStarted: false; function moveCursorToEnd() {} function forceActiveFocus() { activeFocus = true; } }
    QtObject { id: attachmentUploadModel; property int attachmentsCount: 0 }
    onModQuickReplyAttachmentsRequested: (files, media) => {
        ++stageCalls; stagedFiles = files; stagedMedia = media;
        if (autoObserve) observeFiles();
    }
''' + body + "\n}\n"


def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--rust-dll", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=PROJECT / "analysis/native-migration-v1/quick-reply-qt-proof.json")
    args = parser.parse_args()
    test_root = Path(os.environ.get("VALMAX_TEST_TEMP_ROOT", tempfile.gettempdir()))
    test_root.mkdir(parents=True, exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix="qt-quick-replies-", dir=test_root))
    shutil.copytree(args.fixture, fixture, dirs_exist_ok=True)
    module = fixture / "qml/ValMAX/Native"
    shutil.copytree(args.build / "qml/ValMAX/Native", module, dirs_exist_ok=True)
    shutil.copy2(args.rust_dll, fixture / "valmax_core.dll")
    settings = fixture / "quick-reply-test-profile"
    runtime = fixture / "quick-reply-test-runtime"
    settings.mkdir(); runtime.mkdir()
    hashes = {}
    for source_name, output_name, transform in (
        ("quick_replies_panel.qml", "QuickReplySettings.qml", lambda s: settings_controller(build_native_qml.native_settings_panel(s, "quick_replies", "quick-replies"))),
        ("quick_replies_composer.qml", "QuickReplyComposer.qml", composer_controller),
    ):
        raw = (PROJECT / "modkit" / source_name).read_bytes()
        transformed = transform(raw.decode("utf8").replace("\r\n", "\n"))
        assert "XMLHttpRequest" not in transformed and "http://" not in transformed
        (fixture / output_name).write_text(transformed, encoding="utf8")
        hashes[source_name] = hashlib.sha256(raw).hexdigest()
    env = {key: value for key, value in os.environ.items() if not key.startswith(("QT_", "QML", "PYTHON", "MAX_MOD", "MXMOD", "VALMAX_NATIVE"))}
    env.update({"PATH": str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32"),
                "QT_QPA_PLATFORM": "windows", "QT_QUICK_BACKEND": "software", "QML_DISABLE_DISK_CACHE": "1",
                "QML_IMPORT_PATH": str(fixture / "qml"), "QML2_IMPORT_PATH": str(fixture / "qml"),
                "TEMP": str(fixture), "TMP": str(fixture),
                "VALMAX_NATIVE_RUNTIME_CONFIG": json.dumps({"profileDirectory": str(settings), "runtimeDirectory": str(runtime), "existingClient": False})})
    cases = []
    for phase in ("write", "restart"):
        qml = fixture / "quick_reply_pages.qml"
        qml.write_text(Path(__file__).with_name("quick_reply_pages.qml").read_text(encoding="utf8").replace("@PHASE@", phase), encoding="utf8")
        run = subprocess.run([str(fixture / "valmax_qml_bridge_test.exe"), str(fixture / "qml"), str(qml)], cwd=fixture, env=env,
                             capture_output=True, text=True, timeout=20)
        (fixture / (phase + ".stderr.log")).write_text(run.stderr, encoding="utf8")
        if run.returncode:
            print(run.stdout); print(run.stderr[-12000:])
        assert run.returncode == 0, (phase, run.returncode)
        result = json.loads(run.stdout); assert result["ok"]
        result["phase"] = phase; cases.append(result)
        print(json.dumps({"phase": phase, "checks": result["checks"], "ok": True}))
    store = json.loads((settings / "quick-replies.json").read_text(encoding="utf8"))
    assert len(store["replies"]) == 4 and store["schema"] == 1
    proof = {"schema": 1, "verified": True, "fixture": str(fixture), "tests": cases,
             "actualControllerTemplateHashes": hashes, "syntheticOnly": True, "noHttpTransport": True,
             "attachmentTargetsOpened": False, "messagesSent": False,
             "fullVisualSdkTest": False, "liveMaxUiIntegrationVerified": False,
             "legacyTemplatesModified": False,
             "sources": {name: hashlib.sha256((PROJECT / name).read_bytes()).hexdigest() for name in (
                 "native/qt/SettingsTransport.qml", "native/tooling/build_native_qml.py", "native/tests/qt/quick_reply_controllers.py", "native/tests/qt/quick_reply_pages.qml")},
             "pluginSha256": hashlib.sha256((module / "valmaxnativeplugin.dll").read_bytes()).hexdigest(),
             "rustSha256": hashlib.sha256((fixture / "valmax_core.dll").read_bytes()).hexdigest()}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    main()
