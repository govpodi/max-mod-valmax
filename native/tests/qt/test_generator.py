"""Exact legacy equivalence plus opt-in native resource assertions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(PROJECT / "modkit"), str(PROJECT / "native/tooling")]
import build_native_qml
import ui_prepare_features
import ui_privacy
from list_work_queue_ui import QUEUE_RESOURCE
from chat_lock_ui import CONTROLLER_RESOURCE as LOCK_RESOURCE
from message_retention_ui import CONTROLLER_RESOURCE as RETENTION_RESOURCE
from optimization_gates import transform_extra, flag
from list_work_queue_ui import queue_source
from recording_indicator_ui import RESOURCE_PATHS as RECORDING_RESOURCES, MARKER as RECORDING_MARKER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=PROJECT / "analysis/native-migration-v1/qt-generator-proof.json")
    parser.add_argument("--build-root", type=Path, default=Path(os.environ.get("VALMAX_TEST_TEMP_ROOT") or "E:/ValMAXBuild/tmp"))
    args = parser.parse_args()
    baseline = Path("D:/CodexLabs/max-mod-26.35.1/runtime/baseline/max.exe").read_bytes()
    args.build_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="qt-generator-", dir=args.build_root))
    legacy_sources, legacy_spec, bundle, legacy = build_native_qml.prepare(
        baseline, scratch / "legacy", native_runtime=False, rcc=Path("C:/ValMAXBuild/Qt/bin/rcc.exe"))
    stock_sources, stock = ui_prepare_features.prepare(baseline, icon_bundle=bundle)
    assert legacy_sources == stock_sources and legacy_spec == stock["spec"]
    assert not legacy["nativeRuntime"] and legacy["requiredNativeFiles"] == []
    assert "import ValMAX.Native" not in legacy_sources[":/qt/qml/OneMeUi/MeWindow.qml"].decode()
    assert legacy["extraResourceHashes"][QUEUE_RESOURCE] == hashlib.sha256(transform_extra(queue_source(), QUEUE_RESOURCE).encode()).hexdigest()
    native_sources, _, _, native = build_native_qml.prepare(
        baseline, scratch / "native", native_runtime=True, rcc=Path("C:/ValMAXBuild/Qt/bin/rcc.exe"))
    stable_sources, _, _, stable = build_native_qml.prepare(
        baseline, scratch / "stable-candidate", native_runtime=True,
        rcc=Path("C:/ValMAXBuild/Qt/bin/rcc.exe"), ui_release_channel="stable-candidate")
    settings_key = ":/qt/qml/OneMeUi/LeadingSettingsBar.qml"
    menu_key = ":/qt/qml/OneMeUi/ContactMoreMenu.qml"
    export_panel_key = ":/valmax/ChatExportPanel.qml"
    assert native["uiReleaseChannel"] == native["resourceEvidence"]["uiReleaseChannel"] == "beta"
    assert stable["uiReleaseChannel"] == stable["resourceEvidence"]["uiReleaseChannel"] == "stable-candidate"
    assert {key for key in native_sources if native_sources[key] != stable_sources[key]} == {settings_key, menu_key}
    assert "ValMAXBetaSettingsCellImpl" in native_sources[settings_key].decode("utf8")
    assert "ValMAXBetaSettingsCellImpl" not in stable_sources[settings_key].decode("utf8")
    from optimization_ui import RETENTION_BETA_NOTICE, RETENTION_STABLE_NOTICE
    assert RETENTION_BETA_NOTICE in native_sources[settings_key].decode("utf8")
    assert RETENTION_BETA_NOTICE not in stable_sources[settings_key].decode("utf8")
    assert RETENTION_STABLE_NOTICE in stable_sources[settings_key].decode("utf8")
    assert 'objectName: "optimizationBeta"' not in stable_sources[settings_key].decode("utf8")
    assert 'objectName: "valmaxChatExportBeta"' in native_sources[menu_key].decode("utf8")
    assert 'objectName: "valmaxChatExportBeta"' not in stable_sources[menu_key].decode("utf8")
    assert {key for key in native["extraResourceHashes"]
            if native["extraResourceHashes"][key] != stable["extraResourceHashes"][key]} == {export_panel_key}
    assert native["sourceHashes"][settings_key] != stable["sourceHashes"][settings_key]
    assert native["sourceHashes"][menu_key] != stable["sourceHashes"][menu_key]
    assert set(native_sources) - set(legacy_sources) == set(RECORDING_RESOURCES)
    for key in RECORDING_RESOURCES:
        recorder = native_sources[key].decode("utf8")
        assert recorder.count(RECORDING_MARKER) == 1
        assert "Qt.labs.lottieqt" not in recorder and "LottieAnimation" not in recorder
        assert 'objectName: "valmaxRecordingPulseDot"' in recorder
        assert 'objectName: "valmaxRecordingPulseHalo"' in recorder
    changed = [key for key in legacy_sources if legacy_sources[key] != native_sources[key]]
    account_readers = [":/qt/qml/OneMeUi/" + name for name in build_native_qml.ACCOUNT_READERS
                       if ":/qt/qml/OneMeUi/" + name in native_sources]
    assert sorted(changed) == sorted([":/qt/qml/OneMeUi/MeWindow.qml", ":/qt/qml/OneMeUi/LeadingSettingsBar.qml", ":/qt/qml/OneMeUi/BaseSendPanel.qml", ":/qt/qml/OneMeUi/ChatListView.qml", ":/qt/qml/OneMeUi/HistoryListView.qml",
                                      ":/qt/qml/OneMeAuthorization/PhoneNumberPage.qml", ":/qt/qml/OneMeAuthorization/QrCodePage.qml"] + account_readers)
    from chat_row_identity import RESOURCE as CHAT_LIST_RESOURCE, reverse as reverse_chat_identity
    chat_list = native_sources[CHAT_LIST_RESOURCE].decode()
    assert reverse_chat_identity(chat_list) == legacy_sources[CHAT_LIST_RESOURCE].decode()
    assert 'reuseItems: false' in chat_list and 'required property int index' in chat_list
    for name in ("PhoneNumberPage.qml", "QrCodePage.qml"):
        login = native_sources[":/qt/qml/OneMeAuthorization/" + name].decode()
        assert "ValMAXNative.MDataTransferEntry" in login and "valmaxMDataEntry.open()" in login
        assert "127.0.0.1:18973" not in login and "settingsModMData" not in login
    for name in account_readers:
        reader = native_sources[name].decode()
        assert 'SettingsTransport.createRequest("accounts")' in reader
        assert "modValidateAccountPrefs" in reader and "modAccountPrefFor" in reader
        assert "XMLHttpRequest" not in reader
    assert not any(b"http://127.0.0.1:18973/account-settings" in source for source in native_sources.values())
    settings = native_sources[":/qt/qml/OneMeUi/LeadingSettingsBar.qml"].decode()
    for family, endpoint in build_native_qml.SETTINGS_PAGES.values():
        assert 'SettingsTransport.createRequest("' + family + '")' in settings
        assert "http://127.0.0.1:18973/" + endpoint not in settings
    # Configuration/status no longer needs the local helper; capture
    # readiness is a separate, deliberately unresolved product contract.
    assert settings.count("new XMLHttpRequest()") == legacy_sources[":/qt/qml/OneMeUi/LeadingSettingsBar.qml"].decode().count("new XMLHttpRequest()") - 8
    assert "XMLHttpRequest" not in settings and "127.0.0.1:18973" not in settings
    assert 'ValMAXNative.RetentionTransport.createRequest("settings")' in settings
    assert 'qml/ValMAX/Native/RetentionTransport.qml' in native["requiredNativeFiles"]
    assert "ValMAXNative.MDataTransport.createRequest()" in settings
    assert 'xhr.open(method, route || "/mdata");' in settings
    assert 'qml/ValMAX/Native/MDataTransport.qml' in native["requiredNativeFiles"]
    assert 'qml/ValMAX/Native/MDataTransferEntry.qml' in native["requiredNativeFiles"]
    assert 'property bool exportAvailable: false' in settings
    assert 'outcome_unknown: "Результат не подтверждён.' in settings
    assert 'text: "Создать MData"; enabled: mdataPage.exportAvailable && !mdataPage.busy;' in settings
    assert 'text: "Войти с MData"; enabled: mdataPage.available && !mdataPage.busy;' in settings
    assert 'if (busy || !(action === "export" ? exportAvailable : available)' in settings
    assert 'if (busy || !exportAvailable || dialog.transferAction !== "export")' in settings
    assert "mdataHandoff.open()" in settings and "ValMAXNative.MDataTransferEntry" in settings
    assert "loadFileDialog" not in settings and 'request("POST", body, "import"' not in settings
    assert "ValMAXNative.SupportTransport.createRequest()" in settings
    assert "http://127.0.0.1:18973/support-diagnostics" not in settings
    assert "if (!value.report || value.report.schema !== 2)" in settings
    assert 'onAccepted: supportPage.request("POST", selectedFile.toString())' in settings
    assert 'qml/ValMAX/Native/SupportTransport.qml' in native["requiredNativeFiles"]
    assert "ValMAXNative.Bridge.privacyReport" in settings
    assert "http://127.0.0.1:18973/status" not in settings
    assert "tracerCardValue" not in settings and "privacyUnknownScope" in settings
    assert "function releaseBusinessView()" in settings
    assert "if (businessInitializationFailed) releaseBusinessView();" in settings
    assert "Проверь установку Microsoft Edge WebView2" not in settings
    assert "request.errorString" in settings
    assert "Проверь установку Microsoft Edge WebView2" in legacy_sources[":/qt/qml/OneMeUi/LeadingSettingsBar.qml"].decode()
    composer = native_sources[":/qt/qml/OneMeUi/BaseSendPanel.qml"].decode()
    assert 'SettingsTransport.createRequest("quick_replies")' in composer
    assert "http://127.0.0.1:18973/quick-replies" not in composer
    assert "modQuickReplyAttachmentsRequested(files, media);" in composer
    assert "modQuickReplyNativeCleared" in composer
    changed_extra = [key for key in legacy["extraResourceHashes"] if legacy["extraResourceHashes"][key] != native["extraResourceHashes"][key]]
    assert set(changed_extra) == {QUEUE_RESOURCE, LOCK_RESOURCE, RETENTION_RESOURCE, ":/valmax/ChatExportPanel.qml"}
    archive_resource = ":/valmax/MessageRetentionArchive.qml"
    assert archive_resource not in legacy["extraResourceHashes"]
    assert archive_resource not in native["extraResourceHashes"]
    inline_resource = build_native_qml.RETENTION_INLINE_RESOURCE
    assert inline_resource not in legacy["extraResourceHashes"]
    assert inline_resource in native["extraResourceHashes"]
    inline = (scratch / "native/icons/inputs/valmax/MessageRetentionInline.qml").read_text(encoding="utf8")
    assert 'RetentionTransport.createRequest("inline")' in inline
    assert 'delegate: ValMAX.MessageRetentionBubble {' in inline
    assert 'approximatePlacement: true' in inline
    assert 'valmaxRetentionInlineCaption' not in inline
    for resource in (build_native_qml.RETENTION_RANGE_RESOURCE,
                     build_native_qml.RETENTION_BUBBLE_RESOURCE):
        assert resource not in legacy["extraResourceHashes"]
        assert resource in native["extraResourceHashes"]
    range_qml = (scratch / "native/icons/inputs/valmax/MessageRetentionRange.qml").read_text(encoding="utf8")
    bubble_qml = (scratch / "native/icons/inputs/valmax/MessageRetentionBubble.qml").read_text(encoding="utf8")
    assert 'RetentionTransport.createRequest("range")' in range_qml
    assert 'view.model.data(modelIndex, 0x100)' in range_qml
    assert 'view.model.data(modelIndex, 0x101)' not in range_qml
    assert 'day: calendarDay(key)' in range_qml
    assert 'property var windowCursors: [null]' in range_qml
    assert 'limit: limit' in range_qml
    assert 'function nextWindow()' in range_qml and 'function previousWindow()' in range_qml
    assert native_sources[":/qt/qml/OneMeUi/HistoryListView.qml"].decode().count(
        'messageTimeMs: modelData.messageTimeMs') == 2
    assert 'textFormat: Text.PlainText' in bubble_qml
    assert 'source: "qrc:/icon_pack/delete_fill.svg"' in bubble_qml
    assert 'property bool approximatePlacement: false' in bubble_qml
    history = native_sources[":/qt/qml/OneMeUi/HistoryListView.qml"].decode()
    assert 'objectName: "valmaxRetentionInlineFooter"' not in history
    assert 'objectName: "valmaxRetentionApproximateBubbles"' in history
    assert 'objectName: "valmaxRetentionEmptyChatBubbles"' in history
    assert history.count('source: "qrc:/valmax/MessageRetentionInline.qml"') == 2
    assert 'active: valmaxHistoryRow.index === listView.count - 1 &&' in history
    assert 'active: listView.count === 0 &&' in history
    assert "return valmaxRetentionRange.fallbackToFooter;" in history
    assert "return valmaxRetentionRange.cachedRows;" in history
    assert 'objectName: "valmaxRetentionHistoryRow"' in history
    assert history.count('HistoryMessageDelegate {') == 1
    assert 'delegate: HistoryMessageDelegate {' not in history
    assert 'messageContext: valmaxHistoryRow.messageContext' in history
    assert 'property alias isHighlighted: messageDelegate.isHighlighted' in history
    assert 'ValMAX.MessageRetentionBubble {' in history
    assert 'valmaxRetentionRange.loadMore()' in history
    assert history.count('valmaxRetentionRange.nextWindow()') == 2
    assert history.count('valmaxRetentionRange.previousWindow()') == 2
    for name in ('NextBefore', 'NextAfter', 'PreviousBefore', 'PreviousAfter'):
        assert 'objectName: "valmaxRetentionRange' + name + '"' in history
    assert 'property real valmaxLargestDelegate: 500' in history
    assert 'DelegateModelGroup' not in history
    assert 'height: valmaxRetentionEmptyLoader.height +' in history
    assert '(bottomLoader.visible ? bottomLoader.height : root.chatBottomPadding)' in history
    assert "valmaxRetentionArchive" not in history
    assert "Открыть архив сохранённых сообщений" not in history
    export = (scratch / "native/icons/inputs/valmax/ChatExportPanel.qml").read_text(encoding="utf8")
    assert "ValMAXNative.ExportTransport.createRequest()" in export
    assert "http://127.0.0.1:18973/chat-export" not in export
    # The native variant streams through the C++ downloader and Rust sink;
    # retaining the buffered legacy audio XHR would reintroduce GUI memory work.
    assert "XMLHttpRequest" not in export and "function downloadAudio(" not in export
    assert "ValMAXNative.ExportMediaOwner.start(" in export
    assert "ValMAXNative.ExportMediaOwner.cancelForJob(jobId)" in export
    assert 'url: String(attachment.url)' in export
    assert 'qml/ValMAX/Native/ExportTransport.qml' in native["requiredNativeFiles"]
    assert 'qml/ValMAX/Native/ExportMediaOwner.qml' in native["requiredNativeFiles"]
    pin = (scratch / "native/icons/inputs/valmax/ChatLockController.qml").read_text(encoding="utf8")
    assert "ValMAXNative.ChatLockTransport.createRequest()" in pin
    assert "http://127.0.0.1:18973" not in pin and "XMLHttpRequest" not in pin
    assert 'notification_unavailable:' in pin and 'function clearSecrets()' in pin
    queue = (scratch / "native/icons/inputs/valmax/ValMAXListWorkQueue.qml").read_text()
    assert flag("listScheduling") in queue and "Native.ListWorkQueue" in queue
    retention = (scratch / "native/icons/inputs/valmax/MessageRetentionController.qml").read_text(encoding="utf8")
    assert "XMLHttpRequest" not in retention and "127.0.0.1:18973" not in retention
    assert not any(b"127.0.0.1:18973" in source or b"localhost:18973" in source for source in native_sources.values())
    for resource in native["extraResourceHashes"]:
        extra_path = scratch / "native/icons/inputs" / resource.removeprefix(":/")
        assert extra_path.is_file(), resource
        content = extra_path.read_bytes()
        assert b"127.0.0.1:18973" not in content and b"localhost:18973" not in content
    assert native["compiledPreserved"] == legacy["compiledPreserved"]
    assert native["compiledPreserved"] == [":/qt/qml/OneMeUi/HistoryMessageDelegate.qml", ":/qt/qml/OneMeUi/VideoMessageBubbleContent.qml", ":/qt/qml/OneMeUi/MessageTimeCapsule.qml"]
    # Selected-chat retained bubbles are present, while capture readiness still
    # depends on a verified native producer and durable worker status.
    import package_native_client
    eligibility = package_native_client.release_preflight()
    assert not eligibility["releaseEligible"]
    assert "message_retention" in {item["id"] for item in eligibility["blockers"]}
    proof = {"schema": 1, "verified": True, "legacySourcesAndSpecIdentical": True,
             "changedNativeSources": changed, "changedNativeExtraResources": changed_extra,
             "nativeOnlyRecordingResources": list(RECORDING_RESOURCES),
             "nativeAccountReaders": account_readers, "accountReadersUseHttp": False,
             "optimizationGatingPreserved": True, "compiledResourcesPreserved": native["compiledPreserved"],
             "nativeLocalHelperReferences": 0, "retentionArchiveResourceExcluded": True,
             "retentionInlineIntegrated": True,
             "retentionRangeIntegrated": True,
             "retentionReleaseGatePreserved": True, "releaseEligible": False,
             "productionGeneratedFileModified": False, "fixture": str(scratch)}
    args.report.write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof))


if __name__ == "__main__":
    main()
