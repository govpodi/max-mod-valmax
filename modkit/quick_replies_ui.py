"""Source-only native quick replies transforms, MAX 26.35.1.

No binary, profile, cache or server is touched. Root's multi-RCC generator must
guard the exact baseline binary and disable AOT lookup for all three URLs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

QML_PREFIX = ":/qt/qml/OneMeUi/"
RESOURCE_NAMES = ("LeadingSettingsBar.qml", "BaseSendPanel.qml", "MessageTextInput.qml")
OPTIONAL_RESOURCE_NAMES = ("BaseMessageSendPanel.qml",)
BEGIN_MENU = "// BEGIN MENU LOADER — inserted before mentionsListLoader in native ColumnLayout."
END_MENU = "// END MENU LOADER"


def once(source: str, anchor: str, replacement: str) -> str:
    if source.count(anchor) != 1:
        raise ValueError("Expected exactly one native QML anchor: " + anchor[:90])
    return source.replace(anchor, replacement, 1)


def snippets(directory: Path | None = None) -> tuple[str, str, str]:
    directory = directory or Path(__file__).parent
    settings = (directory / "quick_replies_panel.qml").read_text(encoding="utf-8")
    composer = (directory / "quick_replies_composer.qml").read_text(encoding="utf-8")
    declarations, rest = composer.split(BEGIN_MENU, 1)
    menu, after = rest.split(END_MENU, 1)
    if after.strip():
        raise ValueError("Unexpected composer source after menu marker")
    return settings, declarations, menu.strip() + "\n"


def transform_sources(sources: dict[str, str], directory: Path | None = None) -> dict[str, str]:
    settings, declarations, menu = snippets(directory)
    result = dict(sources)
    leading, base, editor = [sources[name].replace("\r\n", "\n") for name in RESOURCE_NAMES]
    if any(marker in source for marker, source in (("settingsModQuickReplies", leading), ("modQuickReplyRows", base), ("quickRepliesStarted", editor))):
        raise ValueError("Quick replies transform was already applied")
    dev_gate = "activeAndVisible: QmlUtils.isDevBuild || QmlUtils.canOpenDevSettings"
    leading = once(leading, "import QtQuick\n", "import QtQuick\nimport QtQuick.Dialogs as QuickReplyDialogs\n")
    leading = once(leading, dev_gate, "activeAndVisible: true // MAX Mod: explicit native developer entry")
    leading = once(leading, 'title: "Dev settings"', 'title: "Настройки разработчика"')
    dev_component = "    Component {\n        id: settingsDev\n"
    leading = once(leading, dev_component, settings + "\n" + dev_component)
    dev_loader = "                UiKit.MeLoader {\n                    Layout.fillWidth: true\n\n                    activeAndVisible: true // MAX Mod: explicit native developer entry"
    entry = '''                OpenPageSettingsCellImpl {
                    title: "Быстрые ответы"
                    iconSource: "qrc:/valmax/icons/quick-replies.svg"
                    settingsPage: settingsModQuickReplies
                }

'''
    leading = once(leading, dev_loader, entry + dev_loader)
    if "import TypographyModule as Typography" not in base:
        base = once(base, "import QtQuick\n", "import QtQuick\nimport TypographyModule as Typography\n")
    base = once(base, "    // formats\n", declarations + "\n    // formats\n")
    base = once(base, "    readonly property var activeCompletionList: {\n", "    readonly property var activeCompletionList: {\n        if (root.modQuickRepliesVisible) return modQuickRepliesLoader.item || null;\n")
    menu_anchor = "        UiKit.MeLoader {\n            id: mentionsListLoader"
    base = once(base, menu_anchor, menu + "\n" + menu_anchor)
    base = once(base, "activeAndVisible: !!root.botCommandListContext && chatInput.botCommandListStarted", "activeAndVisible: !!root.botCommandListContext && chatInput.botCommandListStarted && !root.modQuickRepliesVisible")
    base = once(base, "                    completionListIsEmpty: !root.activeCompletionList || root.activeCompletionList.count === 0", "                    quickRepliesStarted: root.modQuickRepliesVisible\n                    onQuickRepliesCancelPressed: root.modQuickRepliesDismissedQuery = chatInput.text\n                    completionListIsEmpty: !root.activeCompletionList || root.activeCompletionList.count === 0")
    editor = once(editor, "    property bool completionListIsEmpty: true", "    property bool quickRepliesStarted: false\n    signal quickRepliesCancelPressed\n    property bool completionListIsEmpty: true")
    editor = once(editor, "readonly property bool completionListStarted: mentionStarted || botCommandListStarted", "readonly property bool completionListStarted: mentionStarted || botCommandListStarted || quickRepliesStarted")
    editor = once(editor, "    function onCloseKeysPressed() {\n", "    function onCloseKeysPressed() {\n        if (root.quickRepliesStarted) { root.quickRepliesCancelPressed(); return; }\n")
    key_anchor = "        onKeysPressedHandler: event => {\n"
    key_guard = '''        onKeysPressedHandler: event => {
            if (root.quickRepliesStarted && event.key === Qt.Key_Escape) {
                root.quickRepliesCancelPressed();
                event.accepted = true;
                return;
            }
            // Local completion only changes the editor. Consume Enter/Tab even
            // when the list is empty; never fall through to message sending.
            if (root.quickRepliesStarted && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Tab)) {
                root.completionEnterPressed();
                event.accepted = true;
                return;
            }
'''
    editor = once(editor, key_anchor, key_guard)
    result.update(zip(RESOURCE_NAMES, (leading, base, editor)))
    if "BaseMessageSendPanel.qml" in sources:
        message = sources["BaseMessageSendPanel.qml"].replace("\r\n", "\n")
        # These exact handlers establish both existing argument contracts.
        for anchor in ("onPhotoOrVideoUploadSelected: fileList => viewContext.uploadView.uploadPhotoOrVideo(fileList)",
                       "onFileUploadSelected: fileList => viewContext.uploadView.uploadFiles(fileList)"):
            if message.count(anchor) != 1:
                raise ValueError("Unverified native quick-reply attachment handler")
        message = once(message, "        onCleared: root.clearText()", """        onCleared: {
            root.clearText();
            root.modQuickReplyNativeCleared();
        }""")
        message = once(message, "    // Components\n    ClipboardContext {", """    // ValMAX: selecting a reply stages its files; the stock manual Send path is unchanged.
    modQuickReplyAttachmentIntegration: true
    onModQuickReplyAttachmentsRequested: (files, media) => {
        if (files.length) viewContext.uploadView.uploadFiles(files);
        if (media.length) viewContext.uploadView.uploadPhotoOrVideo(media);
    }

    // Components
    ClipboardContext {""")
        result["BaseMessageSendPanel.qml"] = message
    return result


def write_plan(source_directory: Path, output: Path) -> dict:
    sources = {name: (source_directory / name).read_text(encoding="utf-8") for name in RESOURCE_NAMES}
    for name in OPTIONAL_RESOURCE_NAMES:
        if (source_directory / name).is_file():
            sources[name] = (source_directory / name).read_text(encoding="utf-8")
    result = transform_sources(sources)
    output.mkdir(parents=True, exist_ok=True)
    backup = output / "source-backup"
    backup.mkdir(exist_ok=True)
    records = []
    for name in sources:
        original_bytes = (source_directory / name).read_bytes()
        backup_path = backup / name
        if backup_path.exists() and backup_path.read_bytes() != original_bytes:
            raise ValueError("Refusing to replace a different source backup: " + str(backup_path))
        backup_path.write_bytes(original_bytes)
        generated = result[name].encode("utf-8")
        (output / name).write_bytes(generated)
        records.append({"resource_path": QML_PREFIX + name, "source_sha256": hashlib.sha256(original_bytes).hexdigest(), "replacement_sha256": hashlib.sha256(generated).hexdigest(), "replacement_file": str((output / name).resolve()), "aot_cache_key_must_disable": QML_PREFIX[1:] + name})
    plan = {"schema": 1, "max_version": "26.35.1.80662", "qt_version": "6.11.2", "resources": records, "modified_sources": {name: str((output / name).resolve()) for name in sources}, "binary_changed": False, "notes": ["LeadingSettingsBar source may already contain privacy v3; retain it before transforming.", "Selection sets the native draft text and immediately stages saved local file references through the verified native uploadFiles/uploadPhotoOrVideo handlers; it never calls sendPressed or botCommandSendRequested.", "Every message part needs manual Send. Native MessageSenderViewContext.cleared retires the last preview and preserves remaining parts for explicit selection.", "Template HTTP contains saved templates only, never composer drafts, chat identifiers or auth data."]}
    (output / "quick-replies-source-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(write_plan(args.source_directory, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
