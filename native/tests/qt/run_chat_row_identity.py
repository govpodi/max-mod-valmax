"""Synthetic row-identity regression using the actual row QML/native admission queue.

Copies only Qt/native fixture dependencies. Never starts MAX, opens accounts,
reads credentials, connects to a service, or displays an interactive window.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[3]
OWNED = Path("C:/ValMAXBuild/build")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qml_block(source, anchor):
    begin = source.index(anchor)
    brace = source.index("{", begin)
    depth = 0
    quote = None
    escaped = False
    comment = None
    cursor = brace
    while cursor < len(source):
        char = source[cursor]
        following = source[cursor + 1] if cursor + 1 < len(source) else ""
        if comment == "line":
            if char == "\n": comment = None
        elif comment == "block":
            if char == "*" and following == "/": comment = None; cursor += 1
        elif quote:
            if escaped: escaped = False
            elif char == "\\": escaped = True
            elif char == quote: quote = None
        elif char in ("'", '"'): quote = char
        elif char == "/" and following == "/": comment = "line"; cursor += 1
        elif char == "/" and following == "*": comment = "block"; cursor += 1
        elif char == "{": depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0: return textwrap.dedent(source[begin:cursor + 1])
        cursor += 1
    raise ValueError("Unterminated synthetic source block")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--harness", type=Path, required=True)
    parser.add_argument("--row", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--case", choices=("chat_row_identity.qml", "chat_row_model_identity.qml", "chat_row_visual_owner.qml", "chat_row_generated_shell.qml"), default="chat_row_identity.qml")
    parser.add_argument("--chat-list", type=Path)
    parser.add_argument("--apply-chat-identity-fix", action="store_true")
    args = parser.parse_args()
    fixture = args.fixture.absolute()
    assert fixture != OWNED and fixture.is_relative_to(OWNED) and not fixture.exists()
    assert args.harness.name == "valmax_qml_bridge_test.exe" and args.harness.is_file()
    assert args.row.name == "ValMAXAsyncListRow.qml" and args.row.is_file()
    fixture.mkdir(parents=True)
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    for path in args.runtime.glob("*.dll"):
        if path.name.lower().startswith(("qt6", "msvcp", "vcruntime", "concrt", "api-ms-", "ucrt", "valmax_")):
            shutil.copy2(path, fixture / path.name)
    for name in ("plugins", "qml"):
        shutil.copytree(args.runtime / name, fixture / name)
    executable = fixture / args.harness.name
    shutil.copy2(args.harness, executable)
    (fixture / "qt.conf").write_text("[Paths]\nPrefix=.\nPlugins=plugins\nQmlImports=qml\n", "utf8")
    rows = fixture / "RuntimeRows"
    rows.mkdir()
    shutil.copy2(args.row, rows / args.row.name)
    appearance = fixture / "qml/ClientAppearanceModule"
    appearance.mkdir(exist_ok=True)
    (appearance / "qmldir").write_text("module ClientAppearanceModule\nsingleton AppearanceTokens 1.0 AppearanceTokens.qml\n", "utf8")
    (appearance / "AppearanceTokens.qml").write_text(
        "pragma Singleton\nimport QtQuick\nQtObject { property color colorStatesBackgroundCardSelectedPressed: '#333'; "
        "property color colorBackgroundPrimary: '#111'; property color colorIconSecondary: '#888' }\n", "utf8")
    for name in ("home", "local", "roaming", "temp"):
        (fixture / name).mkdir()
    test = fixture / args.case
    shutil.copy2(Path(__file__).with_name(test.name), test)
    chat_source_sha = None
    chat_tested_sha = None
    transform_sha = None
    sdk_stubs = {}
    if args.case in ("chat_row_visual_owner.qml", "chat_row_generated_shell.qml"):
        assert args.chat_list is not None and args.chat_list.name == "ChatListView.qml"
        chat_source = args.chat_list.read_text("utf8")
        chat_source_sha = sha(args.chat_list)
        if args.apply_chat_identity_fix:
            spec = importlib.util.spec_from_file_location("chat_row_identity_patch", ROOT / "native/tooling/chat_row_identity.py")
            patch = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(patch)
            chat_source = patch.apply(chat_source)
            transform_sha = sha(ROOT / "native/tooling/chat_row_identity.py")
        chat_tested_sha = hashlib.sha256(chat_source.encode("utf8")).hexdigest()
        (fixture / "source-ChatListView.qml").write_text(chat_source, "utf8", newline="\n")
        reuse = next(line.strip() for line in chat_source.splitlines() if line.strip().startswith("reuseItems:"))
        test_source = test.read_text("utf8")
        assert test_source.count("/* ACTUAL_REUSE_BINDING */") == 1
        if args.case == "chat_row_visual_owner.qml":
            identity_binding = qml_block(chat_source, "                identityKey: {")
            assert test_source.count("/* ACTUAL_IDENTITY_BINDING */") == 1
            test_source = test_source.replace("/* ACTUAL_IDENTITY_BINDING */", identity_binding)
        else:
            delegate = qml_block(chat_source, "            delegate: ValMAX.ValMAXAsyncListRow {")
            sdk_stubs = {"UiKit.DesignSystem.AvatarSize.px64": "64", "UiKit.DesignSystem.AvatarSize.px56": "56",
                         "UiKit.DesignSystem.SpacingCommon._S": "8", "UiKit.DesignSystem.SpacingCommon._L": "16",
                         "UiKit.DesignSystem.SpacingCommon._M": "12"}
            for name, value in sdk_stubs.items():
                delegate = delegate.replace(name, value)
            assert "UiKit." not in delegate
            assert test_source.count("/* ACTUAL_DELEGATE */") == 1
            test_source = test_source.replace("/* ACTUAL_DELEGATE */", delegate)
            shutil.copy2(Path(__file__).with_name("identity_stub_ChatDelegate.qml"), fixture / "ChatDelegate.qml")
        test.write_text(test_source.replace("/* ACTUAL_REUSE_BINDING */", reuse), "utf8")
        utilities = fixture / "qml/QmlSingletonesModule"
        utilities.mkdir(exist_ok=True)
        (utilities / "qmldir").write_text("module QmlSingletonesModule\nsingleton QmlUtils 1.0 QmlUtils.qml\n", "utf8")
        (utilities / "QmlUtils.qml").write_text("pragma Singleton\nimport QtQml\nQtObject { "
            "function contactIdString(value) { return String(value); } "
            "function chatIdString(value) { return String(value); } "
            "function clientChatIdString(value) { return String(value); } }\n", "utf8")
        optimization = fixture / "RuntimeOptimization"
        optimization.mkdir()
        (optimization / "qmldir").write_text("singleton OptimizationConfig 1.0 OptimizationConfig.qml\n", "utf8")
        (optimization / "OptimizationConfig.qml").write_text("pragma Singleton\nimport QtQml\nQtObject { "
            "function isEnabled(name) { return true; } }\n", "utf8")
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(fixture) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1",
               QML_IMPORT_PATH=str(fixture / "qml"), QML2_IMPORT_PATH=str(fixture / "qml"),
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"), APPDATA=str(fixture / "roaming"),
               TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    run = subprocess.run([str(executable), str(fixture / "qml"), str(test)], cwd=fixture, env=env,
                         timeout=15, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    (fixture / "stdout.log").write_text(run.stdout, "utf8")
    (fixture / "stderr.log").write_text(run.stderr, "utf8")
    result = json.loads(run.stdout.strip()) if run.stdout.strip().startswith("{") else None
    report = {"schema": 1, "fixture": str(fixture), "exitCode": run.returncode, "result": result,
              "rowSha256": sha(args.row), "pluginSha256": sha(fixture / "qml/ValMAX/Native/valmaxnativeplugin.dll"),
              "testCase": args.case, "chatListInputSha256": chat_source_sha,
              "chatListTestedNormalizedSha256": chat_tested_sha, "chatIdentityTransformSha256": transform_sha,
              "nativeChatIdentityTransformApplied": args.apply_chat_identity_fix,
              "stubbedVisualSdkConstants": sdk_stubs,
              "testQmlSha256": sha(test), "harnessSha256": sha(executable),
              "scope": "Synthetic visual-body ownership lifecycle; shipped SDK cache behavior and rendered user data are not reproduced",
              "realClientStarted": False, "realProfileAccessed": False, "networkUsed": False}
    assert not args.report.exists()
    args.report.write_text(json.dumps(report, indent=2) + "\n", "utf8")
    print(json.dumps(report))
    if run.returncode:
        print(run.stderr[-2000:])
        raise SystemExit(run.returncode)


if __name__ == "__main__":
    main()
