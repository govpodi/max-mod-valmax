"""Prepare native privacy, Dev entry and slash-reply resources from baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import pprint

from ui_multi_resource_patch import RESOURCES, read_source, make_resource_spec, PRESERVED_COMPILED_RESOURCES
from ui_prepare_privacy import prepare_source


def prepare(binary: bytes, *, icon_bundle: bytes) -> tuple[dict[str, bytes], dict]:
    from quick_replies_ui import transform_sources
    if len(RESOURCES) != 32 or set(RESOURCES) & set(PRESERVED_COMPILED_RESOURCES):
        raise ValueError("Expected 32 modified resources; compiled bubbles stay native")
    originals = {path.rsplit("/", 1)[1]: read_source(binary, path).decode("utf8").replace("\r\n", "\n")
                 for path in RESOURCES}
    privacy, _ = prepare_source(binary, Path(__file__).with_name("ui_privacy_panel.qml").read_bytes())
    originals["LeadingSettingsBar.qml"] = privacy.decode("utf8")
    transformed = transform_sources(originals)
    from inline_media_ui import transform_sources as inline_media_transform
    transformed = inline_media_transform(transformed)
    from mdata_ui import transform_leading
    transformed["LeadingSettingsBar.qml"] = transform_leading(transformed["LeadingSettingsBar.qml"])
    from account_preferences_ui import transform_sources as account_transform
    transformed = account_transform(transformed)
    from profile_details_ui import transform as profile_transform
    transformed["SidebarContactBrief.qml"] = profile_transform(transformed["SidebarContactBrief.qml"])
    from ghost_ui import transform_leading as ghost_transform
    transformed["LeadingSettingsBar.qml"] = ghost_transform(transformed["LeadingSettingsBar.qml"])
    from business_ui import transform_sources as business_transform
    transformed = business_transform(transformed)
    from mdata_login_ui import transform_sources as login_transform
    transformed = login_transform(transformed)
    from chat_export_ui import transform_sources as export_transform
    transformed = export_transform(transformed)
    from performance_ui import transform_sources as performance_transform
    transformed = performance_transform(transformed)
    from media_resize_ui import transform_sources as media_resize_transform
    transformed = media_resize_transform(transformed)
    from history_cache_ui import transform_sources as history_cache_transform
    transformed = history_cache_transform(transformed)
    from scroll_performance_ui import transform_sources as scroll_performance_transform
    transformed = scroll_performance_transform(transformed)
    from message_retention_ui import transform_sources as retention_transform, transform_leading as retention_leading
    transformed = retention_transform(transformed)
    transformed["LeadingSettingsBar.qml"] = retention_leading(transformed["LeadingSettingsBar.qml"])
    from chat_lock_ui import transform_sources as chat_lock_transform
    transformed = chat_lock_transform(transformed)
    from chat_pool_ui import transform_sources as chat_pool_transform
    transformed = chat_pool_transform(transformed)
    from chat_async_ui import transform_sources as chat_async_transform
    transformed = chat_async_transform(transformed)
    from async_list_rows_ui import transform_sources as async_lists_transform
    transformed = async_lists_transform(transformed)
    from hidden_pages_ui import transform_sources as hidden_pages_transform
    transformed = hidden_pages_transform(transformed)
    from list_work_queue_ui import transform_sources as list_queue_transform
    transformed = list_queue_transform(transformed)
    from popup_lifetime_ui import transform_sources as popup_lifetime_transform
    transformed = popup_lifetime_transform(transformed)
    from viewport_activity_ui import transform_sources as viewport_activity_transform
    transformed = viewport_activity_transform(transformed)
    from list_prefetch_ui import transform_sources as list_prefetch_transform
    transformed = list_prefetch_transform(transformed)
    from history_wheel_ui import transform_sources as history_wheel_transform
    transformed = history_wheel_transform(transformed)
    from profile_parent_wheel_ui import transform_sources as profile_parent_wheel_transform
    transformed = profile_parent_wheel_transform(transformed)
    from menu_icons_ui import transform_window as menu_icons_transform
    transformed["MeWindow.qml"] = menu_icons_transform(transformed["MeWindow.qml"])
    from quick_replies_ui import once
    transformed["MeWindow.qml"] = once(transformed["MeWindow.qml"],
        'title: QmlUtils.productFriendlyName +', 'title: "ValMAX" +')
    transformed["LeadingSettingsBar.qml"] = transformed["LeadingSettingsBar.qml"].replace('title: "Приватность мода"', 'title: "Приватность ValMAX"')
    transformed["QrCodePage.qml"] = transformed["QrCodePage.qml"].replace('.arg(QmlUtils.productFriendlyName)', '.arg("ValMAX")')
    from optimization_gates import transform_sources as optimization_gates_transform
    transformed = optimization_gates_transform(transformed)
    from optimization_ui import transform_sources as optimization_settings_transform
    transformed = optimization_settings_transform(transformed)
    if set(transformed) != set(originals) or any(type(value) is not str for value in transformed.values()):
        raise ValueError("Unexpected source transform contract")
    sources = {path: transformed[path.rsplit("/", 1)[1]].replace("\r\n", "\n").encode("utf8")
               for path in RESOURCES}
    if set(sources) != set(RESOURCES) or set(sources) & set(PRESERVED_COMPILED_RESOURCES):
        raise ValueError("Compiled preservation resource selection mismatch")
    spec, evidence = make_resource_spec(binary, sources, icon_bundle=icon_bundle)
    evidence["preservedCompiledResources"] = list(PRESERVED_COMPILED_RESOURCES)
    for patch in spec["patches"]:
        if patch["id"].startswith("ui_privacy.leading"):
            patch["id"] = patch["id"].replace("ui_privacy.leading", "ui_privacy.mdata_ghost_leading", 1)
    return sources, {"spec": spec, "evidence": evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--emit-spec", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    from icon_resources import prepare as prepare_icons
    binary = args.baseline.read_bytes()
    from message_retention_ui import CONTROLLER_RESOURCE, controller_source
    from chat_lock_ui import (CONTROLLER_RESOURCE as LOCK_RESOURCE,
                              controller_source as lock_source, transform_export_panel)
    from async_list_rows_ui import ROW_RESOURCE, row_source
    from list_work_queue_ui import QUEUE_RESOURCE, queue_source
    from history_wheel_ui import WHEEL_RESOURCE, wheel_source
    from optimization_gates import transform_extra_resources
    from optimization_ui import transform_export_panel as beta_export_panel
    icon_bundle, icon_evidence = prepare_icons(binary, args.output / "icons",
        extra_resources=transform_extra_resources({":/valmax/ChatExportPanel.qml": beta_export_panel(transform_export_panel(Path(__file__).with_name("chat_export_panel.qml").read_text(encoding="utf8"))).encode("utf8"),
                         CONTROLLER_RESOURCE: controller_source().encode("utf8"),
                         LOCK_RESOURCE: lock_source().encode("utf8"),
                         ROW_RESOURCE: row_source().encode("utf8"),
                         QUEUE_RESOURCE: queue_source().encode("utf8"),
                         WHEEL_RESOURCE: wheel_source().encode("utf8")}))
    sources, prepared = prepare(binary, icon_bundle=icon_bundle)
    for path, source in sources.items():
        (args.output / path.rsplit("/", 1)[1]).write_bytes(source)
    args.emit_spec.write_text(
        '"""Generated version-pinned MAX Mod native UI; do not hand-edit patch bytes."""\n\nPATCH_FILES = '
        + pprint.pformat([prepared["spec"]], width=110, sort_dicts=False) + "\n", encoding="utf8")
    evidence = prepared["evidence"]
    evidence["icons"]["verification"] = icon_evidence
    evidence["sourceFiles"] = {path: str((args.output / path.rsplit("/", 1)[1]).resolve())
                               for path in sources}
    evidence["features"] = ["privacy_categories", "dev_panel_entry", "scoped_multipart_quick_replies", "automatic_quick_reply_attachments", "native_send_hint_cleanup", "mdata_login_preview_selection", "account_labels_colors_work_hours", "internal_business_tab", "available_profile_details", "own_profile_invite_link", "profile_link_source_and_refresh", "global_ghost_typing_read_online_stories", "explicit_commons_media_search", "local_chat_opening_pin", "offline_chat_export", "native_theme_svg_icons", "server_deleted_message_retention", "debounced_window_position", "synchronous_window_size", "coalesced_photo_decode", "bounded_history_cache", "valmax_brand"]
    evidence["features"].extend(["pooled_chat_rows", "deduplicated_retention_markers"])
    evidence["features"].extend(["independent_async_chat_contact_call_rows", "static_scroll_skeletons", "hidden_page_work_suspension"])
    evidence["features"].extend(["frame_bounded_row_admission", "retained_ready_viewport_cache", "visual_viewport_observer_gating", "identity_scoped_pending_input", "owned_popup_lifetime"])
    evidence["features"].extend(["bounded_history_and_profile_wheel_admission", "stable_profile_detail_rows"])
    evidence["features"].extend(["restart_scoped_optimization_switches", "native_beta_badges"])
    evidence["templateEndpoints"] = {"GET": "/quick-replies", "POST": "/quick-replies"}
    evidence["sendSemantics"] = "Selection inserts a draft and stages attachments immediately; native Send remains manual; native cleared closes the final-part hint."
    evidence["panelSha256"] = {
        name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ("ui_privacy_panel.qml", "quick_replies_panel.qml", "quick_replies_composer.qml", "inline_media_composer.qml", "chat_lock_controller.qml", "mdata_panel.qml", "account_preferences_panel.qml", "business_panel.qml", "profile_details_panel.qml", "ghost_panel.qml", "chat_export_panel.qml", "message_retention_controller.qml", "message_retention_settings_panel.qml", "ValMAXAsyncListRow.qml", "ValMAXListWorkQueue.qml", "ValMAXWheelAdmission.qml", "optimization_panel.qml", "OptimizationConfig.qml")}
    (args.output / "feature-resources.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
