"""Version-pinned MAX 26.35.1 local namespace patches.

Use MXMOD instead of ONEME for known application folders and Windows keychain
prefixes; use mxmod_* for instance/pipe identities. All replacements preserve
byte lengths and apply only to the exact baseline hashes below.

The config JSON value alone is insufficient: config.dll has a separate UTF-16
ONEME path literal, and both executables contain an ONEME prefix getter and a
credential cleanup search. The service also uses its executable literal to
construct a folder path. core.dll shares ONEME between protocol enums and its
credential helpers: only the latter references are redirected to a new MXMOD
literal in existing .rdata padding. Keep every patch in this module together.
The RpcServer taskkill /F /IM launch is disabled at its sole QProcess::execute
call site; normal QProcess handle cleanup is preserved. Updates are disabled
through the existing native capability getter: its false branch constructs the
stock DummyUpdateDispatcher and hides update actions in QML. WinSparkle's four
worker/check entry points also return before their original prologues. Neither
application versions nor messaging protocol values are changed.

These patches separate the identified local namespaces. They are not a sandbox
or proof that every registry key, notification ID, updater, IPC, or path has
been isolated. No application, file, or credential is accessed by this module.
"""


def _text_patch(identifier, offset, before, after, encoding, reason):
    expected, replacement = before.encode(encoding), after.encode(encoding)
    if len(expected) != len(replacement):
        raise ValueError("Identity patches must preserve resource/string lengths")
    return {
        "id": identifier,
        "offset": offset,
        "expected": expected.hex(),
        "replacement": replacement.hex(),
        "reason": reason,
    }


def _config_patches(suffix):
    return [
        _text_patch(
            "isolation.app_folder" + suffix, 0x8A4C6, "ONEME", "MXMOD", "ascii",
            "Change embedded app_folder_name default, including the prefix passed to PlatformKeychain::WinKeychain.",
        ),
        _text_patch(
            "isolation.hardcoded_folder" + suffix, 0x933F8, "ONEME", "MXMOD", "utf-16le",
            "Change the independent hardcoded ONEME folder used to locate local config.json.",
        ),
        _text_patch(
            "isolation.single_instance" + suffix, 0x8A642, "oneme_instance", "mxmod_instance", "ascii",
            "Separate the configured single-instance identity from the installed client.",
        ),
        _text_patch(
            "isolation.local_server_pipe" + suffix, 0x8A671, "oneme_local_pipe", "mxmod_local_pipe", "ascii",
            "Separate the configured local server pipe identity from the installed client.",
        ),
        {
            "id": "isolation.disable_auto_update" + suffix,
            "offset": 0x1E9A,
            "expected": "e9d1650200",
            "replacement": "31c0c39090",
            "reason": (
                "Return false from the version-pinned Config::getAutoUpdateEnabled bool export thunk; "
                "local config cannot re-enable automatic replacement of the modded files."
            ),
        },
        {
            "id": "isolation.disable_rooms_auto_update" + suffix,
            "offset": 0x2C28,
            "expected": "e9b3660200",
            "replacement": "31c0c39090",
            "reason": (
                "Return false from Config::getRoomsAutoUpdateEnabled bool export thunk; "
                "leave messaging, call transport, and credential cleanup code intact."
            ),
        },
    ]


PATCH_FILES = [
    {
        "relative_path": "config.dll",
        "sha256": "8e7d9d53f77d0f2599ed2bb5b117b3c8a4256ee361434ebd3d977b01d4685035",
        "patches": _config_patches(""),
    },
    {
        "relative_path": "max-service/config.dll",
        "sha256": "950531233749920b60fdcb05a0465eb84f8f2acf3fbb76fc4d1279cb67f714f3",
        "patches": _config_patches("_service"),
    },
    {
        "relative_path": "max.exe",
        "sha256": "d79837f5090c63bce820818355d9cc05b73b8c9bf5da9dddb47cdaf11aea3182",
        "patches": [
            _text_patch(
                "isolation.client_keychain_cleanup_prefix", 0x304709C,
                "ONEME", "MXMOD", "ascii",
                "Change the shared prefix getter and credential cleanup substring so cleanup does not target ONEME entries.",
            ),
            {
                "id": "isolation.disable_update_dispatcher",
                "offset": 0x800F6,
                "expected": "e9e59f4b01",
                "replacement": "31c0c39090",
                "reason": (
                    "Return false from the pinned updates-capability thunk at RVA0x80cf6. "
                    "The existing factory at RVA0x9eedf0 then constructs the stock "
                    "Ui::DummyUpdateDispatcher with normal shared_ptr cleanup, while "
                    "QmlUtils.isUpdatesEnabled is false. No version or registry value is changed."
                ),
            },
        ],
    },
    {
        "relative_path": "WinSparkle.dll",
        "sha256": "a69acfcbcb2af0eb4c3a27511401bac56a157e6663c4b48d488ef2b659fc8b37",
        "patches": [
            {
                "id": "isolation.disable_update_worker",
                "offset": 0xDD90,
                "expected": "48895c2408",
                "replacement": "c390909090",
                "reason": (
                    "Return at win_sparkle_init RVA0xe990 before its prologue; "
                    "no automatic update worker can start in this pinned updater library."
                ),
            },
            {
                "id": "isolation.disable_update_check_with_ui",
                "offset": 0xEDCC,
                "expected": "40534883ec30",
                "replacement": "c39090909090",
                "reason": (
                    "Return at void win_sparkle_check_update_with_ui RVA0xf9cc "
                    "before allocating or dispatching a check."
                ),
            },
            {
                "id": "isolation.disable_update_check_and_install",
                "offset": 0xEE24,
                "expected": "40534883ec30",
                "replacement": "c39090909090",
                "reason": (
                    "Return at void win_sparkle_check_update_with_ui_and_install "
                    "RVA0xfa24 before allocating a download/install task."
                ),
            },
            {
                "id": "isolation.disable_update_check_without_ui",
                "offset": 0xEE7C,
                "expected": "40534883ec30",
                "replacement": "c39090909090",
                "reason": (
                    "Return at void win_sparkle_check_update_without_ui RVA0xfa7c "
                    "before allocating or dispatching a silent check."
                ),
            },
        ],
    },
    {
        "relative_path": "max-service/max-service.exe",
        "sha256": "bd82b3fa0a6d24ee87bb9e0ec32ee311df7c40d8d233a71537685af00d50e0eb",
        "patches": [
            _text_patch(
                "isolation.service_folder_keychain_cleanup_prefix", 0x4E68770,
                "ONEME", "MXMOD", "ascii",
                "Change the service hardcoded folder, prefix getter, and credential cleanup substring together.",
            ),
        ],
    },
    {
        "relative_path": "calls.dll",
        "sha256": "633c3bb79e9baa5ef8883e0ecc2bf1cf1fc4ac67debe3cf0ee4514b54dc58aca",
        "patches": [
            {
                "id": "isolation.disable_rpc_name_based_taskkill",
                "offset": 0x13A058,
                "expected": "ff152a5a1400",
                "replacement": "31c090909090",
                "reason": (
                    "Skip only QProcess::execute(taskkill, /F /IM image_name) at RVA0x13ac58; "
                    "its return value is unused, QString/QList destructors remain, and normal "
                    "QProcess::kill calls on owned process handles remain unchanged. Prevent "
                    "the mod's startup cleanup from killing the installed client's service."
                ),
            },
        ],
    },
    {
        "relative_path": "core.dll",
        "sha256": "c59e3d753820478858a3abce85a002f63d306f4212e8d8de6a7dd42f9403945a",
        "patches": [
            {
                "id": "isolation.core_rdata_literal_virtual_size",
                "offset": 0x260,
                "expected": "6aee4800",
                "replacement": "70ee4800",
                "reason": (
                    "Extend .rdata VirtualSize by six bytes within its existing raw allocation; "
                    "keep section/file alignment, adjacent sections and SizeOfImage unchanged."
                ),
            },
            {
                "id": "isolation.core_namespace_literal",
                "offset": 0x1D1286A,
                "expected": "000000000000",
                "replacement": "4d584d4f4400",
                "reason": (
                    "Place MXMOD plus NUL in verified trailing .rdata padding at RVA0x1d13e6a; "
                    "preserve the existing ONEME protocol-enum literal."
                ),
            },
            {
                "id": "isolation.core_keychain_cleanup_prefix",
                "offset": 0x16673CA,
                "expected": "4c8d05df372300",
                "replacement": "4c8d0599be6a00",
                "reason": "Redirect only the cleanup search LEA at RVA0x1667fca from shared ONEME to new MXMOD.",
            },
            {
                "id": "isolation.core_keychain_prefix_getter",
                "offset": 0x16675D0,
                "expected": "488d05d9352300",
                "replacement": "488d0593bc6a00",
                "reason": "Redirect only the five-byte prefix getter LEA at RVA0x16681d0 to new MXMOD.",
            },
        ],
    },
]


EVIDENCE = {
    "namespace": "MXMOD",
    "instance_id": "mxmod_instance",
    "local_pipe_name": "mxmod_local_pipe",
    "config_writable_location_calls_rva": (0x28E4D, 0x296C7, 0x3AEB0),
    "config_get_app_folder_path_export_rva": 0x2801,
    "config_get_app_folder_path_target_rva": 0x28E20,
    "config_get_app_folder_name_export_rva": 0x2653,
    "config_get_app_folder_name_target_rva": 0x28D60,
    "config_get_local_config_file_path_export_rva": 0x2FB3,
    "config_get_local_config_file_path_target_rva": 0x3AE30,
    "config_hardcoded_folder_lea_rva": 0x3AF39,
    "config_hardcoded_folder_literal_rva": 0x94BF8,
    "storage_app_folder_import_call_rva": 0x436860,
    "storage_win_keychain_constructor_call_rva": 0x4368C4,
    "storage_win_keychain_constructor_rva": 0x441E00,
    "storage_win_keychain_rtti_vtable_rva": 0x98C260,
    "storage_win_keychain_rtti_type": ".?AVWinKeychain@PlatformKeychain@@",
    "client_prefix_getter_rva": 0x489190,
    "client_cleanup_prefix_reference_rva": 0x488F1A,
    "client_cleanup_cred_delete_call_rva": 0x488F43,
    "service_folder_prefix_reference_rva": 0x93B244,
    "service_prefix_getter_rva": 0x363CAA0,
    "service_cleanup_prefix_reference_rva": 0x363C95A,
    "service_cleanup_cred_delete_call_rva": 0x363C983,
    "core_protocol_literal_rva": 0x189B7B0,
    "core_protocol_references_preserved_rva": (0x512A64, 0x514490),
    "core_credential_references_redirected_rva": (0x1667FCA, 0x16681D0),
    "core_cleanup_cred_delete_call_rva": 0x1667FF3,
    "core_new_namespace_literal_rva": 0x1D13E6A,
    "core_rdata_virtual_size_before": 0x48EE6A,
    "core_rdata_virtual_size_after": 0x48EE70,
    "core_rdata_existing_raw_allocation": 0x48F000,
    "auto_update_bool_thunks_rva": (0x2A9A, 0x3828),
    "auto_update_bool_targets_rva": (0x29070, 0x29EE0),
    "updates_capability_thunk_rva": 0x80CF6,
    "updates_capability_original_target_rva": 0x153ACE0,
    "updates_dispatcher_factory_rva": (0x9EEDF0, 0x9EF213),
    "updates_dispatcher_capability_call_rva": 0x9EEE2A,
    "updates_dispatcher_branch_rva": 0x9EEE31,
    "updates_dummy_constructor_rva": 0x9F2BD0,
    "updates_dummy_vtable_rva": 0x3520580,
    "updates_qml_capability_getter_rva": 0xA54BF0,
    "updates_qml_capability_metaobject_rva": 0x36F16E8,
    "updates_qml_capability_property_index": 5,
    "updates_qml_property_read_jump_table_rva": 0xA480EC,
    "updates_qml_capability_read_case_rva": 0xA47D05,
    "updates_qml_capability_read_call_rva": 0xA47D08,
    "updates_original_registry_selector": "HKCU\\Software\\max: disableUpdates",
    "updates_registry_access_after_patch": False,
    "updates_protocol_version_changed": False,
    "updates_app_version_changed": False,
    "winsparkle_blocked_void_exports_rva": {
        "win_sparkle_init": 0xE990,
        "win_sparkle_check_update_with_ui": 0xF9CC,
        "win_sparkle_check_update_with_ui_and_install": 0xFA24,
        "win_sparkle_check_update_without_ui": 0xFA7C,
    },
    "updater_winsparkle_import_calls_rva": {
        "win_sparkle_init": 0x29BAF,
        "win_sparkle_check_update_with_ui": 0x2828C,
        "win_sparkle_check_update_with_ui_and_install": 0x2873C,
        "win_sparkle_check_update_without_ui": 0x28227,
    },
    "updater_original_sha256": "13629f25f25ca01f79fd82ec7384220fe1427e709154791ba0776eb6549566a8",
    "rpc_taskkill_literal_rva": 0x1E8318,
    "rpc_taskkill_image_arg_literal_rva": 0x1E8328,
    "rpc_name_based_kill_function_rva": (0x13A450, 0x13ACB3),
    "rpc_taskkill_execute_call_skipped_rva": 0x13AC58,
    "rpc_taskkill_execute_iat_rva": 0x280688,
    "unmodified_non_namespace_literals": (
        "core.dll ONEME protocol enum",
        "D3D compiler LF_ONEMETHOD debug-format names",
    ),
    "limits": (
        "No live credential enumeration/read/write performed during analysis.",
        "Existing official profiles are not migrated or imported.",
        "No claim of complete registry, notification, updater, IPC, or OS isolation.",
        "Local config overrides can replace defaults; test/use a fresh MXMOD profile.",
    ),
}
