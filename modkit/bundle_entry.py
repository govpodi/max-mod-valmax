"""Fixed-role frozen entry point; import performs no native/profile operation."""
import importlib
import os
import sys

from helper_command import ROLES, default_manifest
import startup_status


_STARTUP_ERROR = startup_status.message_for("startup_failed")


def _ensure_standard_streams():
    """Windowed bootloaders supply None; use the null device, never file logs."""
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, mode, encoding="utf-8"))


def _dispatch(argv):
    if type(argv) not in (list, tuple) or any(type(arg) is not str or "\0" in arg for arg in argv):
        raise ValueError("Invalid MAX Mod helper command")
    args = list(argv)
    if not args or args[0].startswith("-"):
        role = "launcher"
    else:
        role = args.pop(0)
    if role not in ROLES:
        raise ValueError("Invalid MAX Mod helper command")
    if role == "recover" and args not in ([], ["--recover-mdata"]):
        raise ValueError("Invalid MAX Mod helper command")
    if role == "mdata" and args not in ([], ["--import-mdata"]):
        raise ValueError("Invalid MAX Mod helper command")
    if any(arg == "--password" or arg.startswith("--password=") for arg in args):
        raise ValueError("Invalid MAX Mod helper command")
    return role, args


def _show_startup_error():
    """One fixed local message; never show the exception or an arbitrary path."""
    if os.name != "nt":
        return
    try:
        import ctypes
        api = ctypes.WinDLL("user32", use_last_error=True)
        api.MessageBoxW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p,
                                   ctypes.c_wchar_p, ctypes.c_uint)
        api.MessageBoxW.restype = ctypes.c_int
        message = startup_status.message_for(startup_status.failure_code())
        api.MessageBoxW(None, message, "ValMAX", 0x10)
    except Exception:
        pass


def main(argv=None):
    # One-shot proof of an outer launcher which will present this child's
    # failure. Long-lived observers and MData restarts cannot inherit it.
    parent_waits = os.environ.pop("MAX_MOD_STARTUP_PARENT_WAITS", None) == "1"
    _ensure_standard_streams()
    try:
        role, args = _dispatch(sys.argv[1:] if argv is None else argv)
    except ValueError:
        return 2
    startup_status.reset()
    startup_status.set_role(role)
    previous = sys.argv
    status = 1
    imported = False
    try:
        # Set the default before importing the observer or recovery modules.
        launcher = importlib.import_module("launch_mod")
        if bool(getattr(sys, "frozen", False)):
            launcher.DEFAULT_MANIFEST = default_manifest()
        # Recovery must pass the launcher's desktop bootstrap and binary guard.
        target = importlib.import_module("privacy_server") if role == "observe" else launcher
        imported = True
        if role == "recover":
            args = ["--recover-mdata"]
        if role == "mdata":
            args = ["--import-mdata"]
        sys.argv = [sys.executable, *args]
        result = target.main()
        status = result if type(result) is int else 0
    except SystemExit as error:
        # argparse help/error must not expose an arbitrary exit object.
        status = 0 if error.code is None else error.code if type(error.code) is int else 1
    except Exception as error:
        # No exception text, paths, messages, passwords or traceback in GUI/logs.
        startup_status.fail(error.code if isinstance(error, startup_status.StartupError)
                            else "startup_failed" if imported else "import_failed")
        status = 1
    finally:
        sys.argv = previous
    if (status != 0 and role != "observe" and "--check-only" not in args
            and bool(getattr(sys, "frozen", False))):
        # The ordinary root waits for the desktop child and presents its
        # receipt. A child MessageBox would keep child.wait() blocked and
        # eventually cause a second, misleading timeout popup in the parent.
        try:
            inherited = startup_status.operation_from_environment() is not None
        except ValueError:
            inherited = False
        desktop_child = os.environ.get("MAX_MOD_DESKTOP_CHILD") == "1"
        dedicated_gui = (role in ("recover", "mdata")
                         or "--recover-mdata" in args or "--import-mdata" in args)
        if not (parent_waits and desktop_child and inherited) or dedicated_gui:
            _show_startup_error()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
