"""Pure argv/path adapter for the source scripts and the installed helper.

No command is executed here. Only four fixed application roles are available;
the target parser validates each role's options. Passwords belong to the GUI
or the existing local HTTP request, never to a helper command line.
"""
from pathlib import Path
import sys


ROLES = frozenset(("launcher", "observe", "recover", "mdata"))
SOURCE_DEFAULT_MANIFEST = Path(
    r"D:\CodexLabs\max-mod-26.35.1\runtime\valmax-live-v8\mod-manifest.json")
_SCRIPTS = {
    "launcher": "launch_mod.py",
    "observe": "privacy_server.py",
    "recover": "mdata_recovery.py",
    "mdata": "mdata_import.py",
}
_ERROR = "Invalid MAX Mod helper command"


def _runtime(executable, frozen):
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if type(frozen) is not bool:
        raise ValueError(_ERROR)
    executable = sys.executable if executable is None else executable
    if isinstance(executable, Path):
        executable = str(executable)
    if type(executable) is not str or not executable or "\0" in executable:
        raise ValueError(_ERROR)
    if not Path(executable).is_absolute():
        raise ValueError(_ERROR)
    return executable, frozen


def default_manifest(*, executable=None, frozen=None):
    """Installed runtime is relative to the EXE; source default stays pinned."""
    executable, frozen = _runtime(executable, frozen)
    if frozen:
        return Path(executable).parent / "runtime" / "mod" / "mod-manifest.json"
    return SOURCE_DEFAULT_MANIFEST


def command(role="launcher", args=(), *, executable=None, frozen=None,
            module_directory=None):
    """Return argv for Popen(..., shell=False); never quote or join tokens."""
    if type(role) is not str or role not in ROLES:
        raise ValueError(_ERROR)
    if type(args) not in (tuple, list) or any(type(arg) is not str or "\0" in arg for arg in args):
        raise ValueError(_ERROR)
    if role == "recover" and list(args) not in ([], ["--recover-mdata"]):
        raise ValueError(_ERROR)
    if role == "mdata" and list(args) not in ([], ["--import-mdata"]):
        raise ValueError(_ERROR)
    # The existing GUI/HTTP APIs are the only password input channels.
    if any(arg == "--password" or arg.startswith("--password=") for arg in args):
        raise ValueError(_ERROR)
    executable, frozen = _runtime(executable, frozen)
    tail = list(args)
    if frozen:
        return [executable, role, *tail]
    directory = Path(__file__).parent if module_directory is None else module_directory
    if not isinstance(directory, Path):
        if type(directory) is not str or "\0" in directory:
            raise ValueError(_ERROR)
        directory = Path(directory)
    if not directory.is_absolute():
        raise ValueError(_ERROR)
    if role == "recover" and "--recover-mdata" not in tail:
        tail.insert(0, "--recover-mdata")
    if role == "mdata" and "--import-mdata" not in tail:
        tail.insert(0, "--import-mdata")
    return [executable, "-B", str(directory / _SCRIPTS[role]), *tail]
