"""Pinned, reversible RCC extension for MAX UI resources across pinned RCC tables.

Reads only the immutable MAX 26.35.1 executable. Each original URL retains
its locale/compression flags, gets its own timestamp and misses its own AOT
cache key. All records share one read-only, non-executable PE section.
"""
from __future__ import annotations

import hashlib
import struct
import zlib

from ui_resource_patch import (
    BASELINE_SHA256, BASELINE_LENGTH, RESOURCE_DATA_RVA,
    SECTION_RVA, SECTION_RAW, SECTION_HEADER_RAW,
)

# Pinned RCC nodes, AOT registrations and per-table data bases.
# node raw, AOT key raw, tag, data raw, data RVA, original flags, key count.
RESOURCES = {
    ":/qt/qml/OneMeUi/MessengerView.qml": (0x300ac4e, 0x2675920, "business_messenger", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/SideMenu.qml": (0x300a3a0, 0x267f488, "business_menu", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/LeadingPanel.qml": (0x300a14e, 0x267b4e0, "business_panel", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/LeadingListBar.qml": (0x300adae, 0x267c9a0, "business_list", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/LeadingSettingsBar.qml": (0x300a542, 0x267ca00, "leading", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/BaseSendPanel.qml": (0x300ac22, 0x2676ff0, "quick_replies_base", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/InputAudioRecorderPanel.qml": (0x300b814, 0x2675ba0, "recording_audio_indicator", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/InputVideoRecorderPanel.qml": (0x300b856, 0x2675c10, "recording_video_indicator", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/MessageTextInput.qml": (0x3009ba2, 0x2675370, "quick_replies_input", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/BaseMessageSendPanel.qml": (0x3009caa, 0x2676f80, "quick_replies_attachments", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/AccountButton.qml": (0x300a298, 0x267f230, "account_button", 0x2f323a0, 0x2f333a0, 0, 1),
    ":/qt/qml/OneMeUi/AccountsStackView.qml": (0x300a306, 0x2675860, "account_stack", 0x2f323a0, 0x2f333a0, 0, 1),
    ":/qt/qml/OneMeUi/SettingsAccountSelectorDelegate.qml": (0x300b6b4, 0x267d830, "account_selector", 0x2f323a0, 0x2f333a0, 0, 1),
    ":/qt/qml/OneMeUi/ProfileInfoCell.qml": (0x300b70c, 0x267f170, "account_profile", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/SidebarContactBrief.qml": (0x300bbdc, 0x2680290, "profile_details", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/ContactMoreMenu.qml": (0x300b2ec, 0x2681df0, "chat_export_menu", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/ChatDelegate.qml": (0x300b6f6, 0x267ba40, "chat_lock_delegate", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/ChatListView.qml": (0x300c2e8, 0x267bb00, "scroll_chat_list", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/ContactListView.qml": (0x300a6b8, 0x267c750, "skeleton_contacts_list", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/CallsListView.qml": (0x300c4cc, 0x267b6f0, "skeleton_calls_list", 0x2f323a0, 0x2f333a0, 0, 1),
    ":/qt/qml/OneMeUi/BaseLeadingListHeader.qml": (0x3009d02, 0x267ca60, "visible_stories_header", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/LeadingChatsBar.qml": (0x300aa28, 0x267c8e0, "visible_chats_bar", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/SearchDelegate.qml": (0x300b11e, 0x267f850, "chat_lock_search", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/MeWindow.qml": (0x3009bfa, 0x2674bf8, "valmax_window", 0x2f323a0, 0x2f333a0, 1, 2),
    ":/qt/qml/OneMeUi/NotificationsSettings.qml": (0x300b3c8, 0x267dbf0, "performance_notifications", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/PreviewGridModule/HistoryPreviewCell.qml": (0x3312c28, 0x3d13ad0, "performance_preview_cell", 0x330f6a0, 0x33106a0, 1, 1),
    ":/qt/qml/OneMeUi/HistoryListView.qml": (0x300b0b0, 0x26775b0, "retention_history", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeAuthorization/PhoneNumberPage.qml": (0x32aba2e, 0x3ac05e0, "mdata_phone", 0x3295f10, 0x3296f10, 1, 1),
    ":/qt/qml/OneMeAuthorization/QrCodePage.qml": (0x32abc12, 0x3ac0740, "mdata_qr", 0x3295f10, 0x3296f10, 1, 1),
    ':/qt/qml/OneMeUi/ContactListDelegate.qml': (0x300a844, 0x267c6f0, 'queue_contact_delegate', 0x2f323a0, 0x2f333a0, 1, 1),
    ':/qt/qml/OneMeUi/PresenceTextStatusNew.qml': (0x300baea, 0x2675560, 'viewport_presence', 0x2f323a0, 0x2f333a0, 1, 1),
    ':/qt/qml/OneMeUi/CallsHistoryDelegate.qml': (0x300bb2c, 0x267b530, 'popup_calls_delegate', 0x2f323a0, 0x2f333a0, 1, 1),
    ':/qt/qml/OneMeUi/ChatListMenu.qml': (0x300b764, 0x267bf50, 'popup_chat_menu', 0x2f323a0, 0x2f333a0, 1, 1),
    ':/qt/qml/OneMeUi/SidebarPanel.qml': (50373800, 40370000, 'profile_parent_wheel', 49488800, 49492896, 1, 1),
}
# Kept at immutable RCC nodes/timestamps and original AOT registration keys.
# These types are intentionally outside RESOURCES and cannot be selected.
PRESERVED_COMPILED_RESOURCES = {
    ":/qt/qml/OneMeUi/HistoryMessageDelegate.qml": (0x3009dde, 0x2677610, "retention_delegate", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/VideoMessageBubbleContent.qml": (0x300c852, 0x2677d10, "retention_video", 0x2f323a0, 0x2f333a0, 1, 1),
    ":/qt/qml/OneMeUi/MessageTimeCapsule.qml": (0x300b932, 0x2678780, "retention_capsule", 0x2f323a0, 0x2f333a0, 1, 1),
}


def verify_compiled_preservation(binary, spec):
    _baseline(binary)
    protected = []
    for path, record in PRESERVED_COMPILED_RESOURCES.items():
        node, key_raw, _, _, _, flags, count = record
        key = path[1:].encode("utf-16-le")
        if binary.count(key) != count or binary[key_raw:key_raw + len(key)] != key:
            raise ValueError("Preserved compiled AOT registration drift")
        if int.from_bytes(binary[node + 4:node + 6], "big") != flags:
            raise ValueError("Preserved compiled RCC flags drift")
        protected.extend(((node, node + 22), (key_raw, key_raw + len(key))))
    for patch in spec["patches"]:
        if patch.get("kind") == "append": continue
        start = patch["offset"]
        stop = start + len(bytes.fromhex(patch["replacement"]))
        if any(start < end and begin < stop for begin, end in protected):
            raise ValueError("Patch touches preserved compiled RCC/AOT bytes")

TREE_RAW = 0x30098A0
DATA_RAW = 0x2F323A0


def _baseline(binary: bytes) -> None:
    if len(binary) != BASELINE_LENGTH or hashlib.sha256(binary).hexdigest() != BASELINE_SHA256:
        raise ValueError("Exact immutable MAX 26.35.1 baseline required")


def _node(path: str) -> tuple[int, int, str]:
    if path not in RESOURCES:
        raise ValueError("Unsupported resource URL")
    node, cache_raw, tag, *_ = RESOURCES[path]
    return node, cache_raw, tag


def read_source(binary: bytes, path: str) -> bytes:
    """Decode one bounded, pinned original source; never an AOT compiled unit."""
    _baseline(binary)
    node, _, _ = _node(path)
    if int.from_bytes(binary[node + 4:node + 6], "big") != RESOURCES[path][5]:
        raise ValueError("Unexpected original RCC flags")
    offset = int.from_bytes(binary[node + 10:node + 14], "big")
    record = RESOURCES[path][3] + offset
    length = int.from_bytes(binary[record:record + 4], "big")
    if not 0 < length <= 1024 * 1024 or record + 4 + length > len(binary):
        raise ValueError("Invalid original resource size")
    packed = binary[record + 4:record + 4 + length]
    if RESOURCES[path][5] == 0:
        packed.decode("utf8")
        return packed
    declared = int.from_bytes(packed[:4], "big")
    if not 1 <= declared <= 1024 * 1024:
        raise ValueError("Invalid source size")
    decoder = zlib.decompressobj()
    source = decoder.decompress(packed[4:], declared + 1)
    if len(source) != declared or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("Invalid compressed source")
    source.decode("utf8")
    return source


def make_resource_spec(binary: bytes, sources: dict[str, bytes], *, icon_bundle: bytes | None = None) -> tuple[dict, dict]:
    _baseline(binary)
    if set(sources) & set(PRESERVED_COMPILED_RESOURCES):
        raise ValueError("Compiled native types must remain unpatched")
    if not sources or len(sources) > len(RESOURCES):
        raise ValueError("Expected a bounded set of pinned resources")
    records = bytearray()
    entries = []
    for path, source in sorted(sources.items()):
        node, cache_raw, tag = _node(path)
        read_source(binary, path)
        if type(source) is not bytes or not 1 <= len(source) <= 1024 * 1024:
            raise ValueError("Invalid replacement source")
        source.decode("utf8")
        key = path[1:].encode("utf-16-le")
        if binary.count(key) != RESOURCES[path][6] or binary[cache_raw:cache_raw + len(key)] != key:
            raise ValueError("AOT registration key is not uniquely pinned")
        offset = len(records)
        packed = struct.pack(">I", len(source)) + zlib.compress(source, 9) if RESOURCES[path][5] == 1 else source
        records.extend(struct.pack(">I", len(packed)) + packed)
        digest = hashlib.sha256(source).digest()
        timestamp = 1767225600000 + int.from_bytes(digest[:8], "big") % 31536000000
        if timestamp == int.from_bytes(binary[node + 14:node + 22], "big"):
            timestamp += 1
        entries.append((path, node, cache_raw, tag, offset, timestamp, digest.hex(), len(source)))
    icon_offsets = icon_anchors = None
    icon_start = None
    if icon_bundle is not None:
        from icon_resources import registration_anchors, split_binary_resource
        if type(icon_bundle) is not bytes or not 24 <= len(icon_bundle) <= 4 * 1024 * 1024:
            raise ValueError("Invalid bounded native icon bundle")
        icon_offsets = split_binary_resource(icon_bundle)
        icon_anchors = registration_anchors(binary)
        records.extend(b"\0" * (-len(records) % 4))
        icon_start = len(records)
        records.extend(icon_bundle)
    raw_size = (len(records) + 0x1FF) & ~0x1FF
    image_size = (SECTION_RVA + len(records) + 0xFFF) & ~0xFFF
    header = struct.pack("<8sIIIIIIHHI", b".mxmui\0\0", len(records), SECTION_RVA,
                         raw_size, SECTION_RAW, 0, 0, 0, 0, 0x40000040)
    patches = []

    def add(tag, offset, replacement, reason, *, expected=None, append=False):
        before = b"" if append else binary[offset:offset + len(replacement)]
        if expected is not None and before != expected:
            raise ValueError("Original PE/RCC byte guard failed")
        item = {"id": "ui_privacy." + tag, "offset": offset, "expected": before.hex(),
                "replacement": replacement.hex(), "reason": reason}
        if append:
            item["kind"] = "append"
        patches.append(item)

    add("section_count", 0x146, struct.pack("<H", 11), "One additional UI data section.",
        expected=struct.pack("<H", 10))
    add("initialized_data_size", 0x160, struct.pack("<I", 0x281D800 + raw_size),
        "Account for the new initialized-data section.", expected=struct.pack("<I", 0x281D800))
    add("image_size", 0x190, struct.pack("<I", image_size), "Map the UI section with original alignment.",
        expected=struct.pack("<I", SECTION_RVA))
    add("clear_obsolete_checksum", 0x198, b"\0" * 4, "Clear the original checksum.",
        expected=bytes.fromhex("a21dbd04"))
    add("section_header", SECTION_HEADER_RAW, header,
        "Read-only UI data; no writable or executable permission.", expected=b"\0" * 40)
    evidence = []
    for path, node, cache_raw, tag, offset, timestamp, digest, size in entries:
        add(tag + "_data_offset", node + 10,
            struct.pack(">I", SECTION_RVA + offset - RESOURCES[path][4]),
            "Redirect only this existing RCC node to its own replacement record.")
        add(tag + "_timestamp", node + 14, struct.pack(">Q", timestamp),
            "Invalidate only this resource's old disk cache.")
        key = path[1:].encode("utf-16-le")
        add(tag + "_aot_key", cache_raw, (path[1:-1] + "x").encode("utf-16-le"),
            "Rename only this resource's AOT registration key; load modified source.",
            expected=key)
        evidence.append({"path": path, "nodeRaw": hex(node), "cacheKeyRaw": hex(cache_raw),
                         "recordOffset": offset, "sourceSha256": digest, "sourceBytes": size,
                         "dataRva": hex(RESOURCES[path][4]), "flags": RESOURCES[path][5]})
    if icon_bundle is not None:
        for anchor in icon_anchors:
            target = SECTION_RVA + icon_start + icon_offsets[anchor["field"]]
            original = bytes.fromhex(anchor["expected"])
            replacement = original[:3] + struct.pack("<i", target - anchor["rva"] - 7)
            add("icons_" + anchor["lifetime"] + "_" + anchor["field"], anchor["raw"], replacement,
                "Retain native icon resource registration and cleanup; redirect its read-only tables.", expected=original)
    tail = b"\0" * (SECTION_RAW - BASELINE_LENGTH) + bytes(records) + b"\0" * (raw_size - len(records))
    add("append_resource_section", BASELINE_LENGTH, tail,
        "Append all UI records after the untouched original file; exact reverse removes this tail.",
        append=True)
    spec = {"relative_path": "max.exe", "sha256": BASELINE_SHA256, "patches": patches}
    verify_compiled_preservation(binary, spec)
    result = {"baselineSha256": BASELINE_SHA256, "resources": evidence,
                  "sectionRva": hex(SECTION_RVA), "sectionRaw": hex(SECTION_RAW),
                  "sectionVirtualBytes": len(records), "sectionRawBytes": raw_size,
                  "patchCount": len(patches)}
    if icon_bundle is not None:
        result["icons"] = {"bundleSha256": hashlib.sha256(icon_bundle).hexdigest(),
                           "bundleBytes": len(icon_bundle), "recordOffset": icon_start,
                           "sectionOffsets": icon_offsets, "pointerPatches": 6}
    return spec, result
