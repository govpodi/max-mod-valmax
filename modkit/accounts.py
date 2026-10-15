"""MAX 26.35.1 local multiaccount latch patch.

The on-disk patch changes the boolean return value of the platform safe-storage
getter for ``multi_state_enabled``. It preserves credential loading, cleanup,
the security-cookie check and stack unwind metadata. A separate UI property
patch displays a local minimum of three for unsigned UI values zero, one and
two. Values of three or higher and negative/sentinel bit patterns are preserved.
The server setting storage, network replies, authentication and actual server
enforcement are not changed; a third login can still be refused by the server.

Windows x64 returns a C++ bool in AL. The verified AccountStorage::loadAccounts
consumer at RVA 0x1C15E3 explicitly uses ``movzx r15d, al``. The replacement
therefore sets AL, leaving the rest of the existing epilogue intact.

This module describes a version-pinned patch only. It performs no I/O and does
not start MAX, edit a credential, or apply a patch to an installed application.
"""

PATCH_FILES = [
    {
        "relative_path": "max.exe",
        "sha256": "d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182",
        "patches": [
            {
                "id": "accounts.ui_zero_limit_leaf",
                "offset": 0x23D2A20,
                "expected": "cccccccccccccccccccccccccccccccccccccc",
                "replacement": "488b83480400004883f8037305b803000000c3",
                "reason": (
                    "Leaf helper: read the existing maxAccountsCount UI value, "
                    "return three for unsigned values below three, preserve "
                    "larger values and negative/sentinel bit patterns."
                ),
            },
            {
                "id": "accounts.ui_zero_limit_call",
                "offset": 0xA93D66,
                "expected": "488b8348040000",
                "replacement": "e8b5ec93019090",
                "reason": (
                    "Call the leaf helper from maxAccountsCount ReadProperty; "
                    "retain Qt binding registration and the original return."
                ),
            },
        ],
    },
    {
        "relative_path": "storage.dll",
        "sha256": "3d5fcc4906b9f8de7e1ff11c81de807cbd2ba24589ac786db3198920df4c9289",
        "patches": [
            {
                "id": "accounts.local_multi_state_enabled",
                "offset": 0x43CDDB,
                "expected": "0fb6c3",
                "replacement": "b00190",
                "reason": (
                    "Return true from the local multi_state_enabled credential "
                    "getter; preserve server setting storage and normal login."
                ),
            }
        ],
    }
]

# Supporting metadata is intentionally separate from the common patch schema.
EVIDENCE = {
    "source_filename": "CM_FP_Unspecified.storage.dll",
    "image_base": 0x180000000,
    "getter_pdata": (0x43D900, 0x43D9F8),
    "getter_rva": 0x43D9DB,
    "getter_before": "movzx eax, bl",
    "getter_after": ("mov al, 1", "nop"),
    "safe_storage_vtable_rva": 0x98BCA8,
    "vtable_slot": 0x88,
    "getter_thunk_rva": 0x15933,
    "verified_caller_pdata": (0x1C1570, 0x1C18C0),
    "verified_boolean_consumer_rva": 0x1C15E3,
    "verified_boolean_consumer_bytes": "440fb6f8",
    "verified_boolean_consumer_asm": "movzx r15d, al",
    "server_setting_id_unchanged": 0xDF,
    "ui_source_filename": "CM_FP_Messenger.max.exe",
    "ui_metaobject_rva": 57693368,
    "ui_metadata_rva": 57758976,
    "ui_property_index": 64,
    "ui_qt_static_metacall_pdata": (0xA93E70, 0xA9550C),
    "ui_property_read_case_rva": 0xA94959,
    "ui_property_read_rva": 0xA94966,
    "ui_property_member_offset": 0x448,
    "ui_property_binding_call_rva": 0xA94960,
    "ui_property_read_jump_table_rva": 0xA9522C,
    "ui_leaf_rva": 0x23D3620,
    "ui_leaf_length": 19,
    "ui_minimum_accounts": 3,
    "ui_comparison": "unsigned",
    "ui_tail_padding_span": (0x23D3618, 0x23D4AA2),
    "ui_leaf_has_calls_or_stack_adjustments": False,
}
