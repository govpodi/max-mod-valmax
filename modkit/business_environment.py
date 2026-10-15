"""Give native ValMAX WebView2 descendants their own checked data directory.

Returns a child-only environment copy. It changes no global environment,
registry value, browser setting or existing cookie/profile file. The caller
must pass its already prepared MXMOD root; a missing root is never created.

Microsoft's CreateCoreWebView2EnvironmentWithOptions honours
WEBVIEW2_USER_DATA_FOLDER before its explicit userDataFolder/registry inputs.
This also isolates Qt WebView's application-local default from official MAX.
"""
from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path

from launch_mod import _check_path
from quick_reply_store import QuickReplyStoreError, _parents, _directory, _lexical_path

VARIABLE = "WEBVIEW2_USER_DATA_FOLDER"
DIRECTORY = "business-webview2"


def _data_directory(profile_root):
    try:
        root = _lexical_path(Path(profile_root) / "config.json").parent
        if root.name != "MXMOD":
            raise ValueError("Expected prepared MXMOD directory")
        _check_path(root, directory=True)
        with _parents(root / "config.json", create=False) as actual_root:
            if actual_root is None or actual_root.name != "MXMOD":
                raise ValueError("Expected prepared MXMOD directory")
            target = actual_root / DIRECTORY
            try:
                target.mkdir(mode=0o700)
            except FileExistsError:
                pass
            _check_path(target, directory=True)
            with _directory(target) as actual_target:
                if (actual_target.name != DIRECTORY
                        or os.path.normcase(str(actual_target.parent)) != os.path.normcase(str(actual_root))):
                    raise ValueError("Invalid native WebView2 directory")
                return str(actual_target)
    except QuickReplyStoreError:
        raise ValueError("Unsafe native WebView2 directory") from None


def child_environment(environment, profile_root: Path):
    """Return native child variables with one fixed MXMOD WebView2 directory.

    Ancestors and the target are pinned while validating/creating the target.
    Handles are released on return; callers should launch the child promptly.
    Native per-user ACLs, not this helper, govern later browser writes.
    """
    if not isinstance(environment, Mapping):
        raise ValueError("Invalid native environment")
    native_env = {}
    for key, value in environment.items():
        if (type(key) is not str or not key or "\x00" in key
                or type(value) is not str or "\x00" in value):
            raise ValueError("Invalid native environment")
        if key.casefold() != VARIABLE.casefold():
            native_env[key] = value
    native_env[VARIABLE] = _data_directory(profile_root)
    return native_env
