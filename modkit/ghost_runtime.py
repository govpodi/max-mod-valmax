"""Value-free confirmation of native ghost filters.

Requested preferences and observed application are separate. No packet,
account ID, message content or native argument crosses this boundary.
"""
import threading
import time

BASE_FEATURES = ("hideTyping", "hideRead")
FEATURES = (*BASE_FEATURES, "hideOnline", "hideStories")
COUNTERS = ("typing", "read", "presence", "stories")
ERRORS = {None, "unsupported_platform", "module_mismatch", "hook_failed", "invalid_settings",
          "invalid_specification", "waiting_for_core", "module_unloaded"}


def checked_snapshot(value):
    legacy_keys = {"ready", "features", "revision", "enabled", *BASE_FEATURES, "blocked", "error"}
    if type(value) is not dict or set(value) not in (legacy_keys, legacy_keys | set(FEATURES)):
        raise ValueError("Invalid ghost runtime state")
    if (any(type(value[key]) is not bool for key in ("ready", "enabled", *FEATURES) if key in value)
            or type(value["features"]) is not dict or set(value["features"]) not in (set(BASE_FEATURES), set(FEATURES))
            or any(type(flag) is not bool for flag in value["features"].values())
            or type(value["revision"]) is not str or len(value["revision"]) != 64
            or any(char not in "0123456789abcdef" for char in value["revision"])
            or type(value["blocked"]) is not dict or set(value["blocked"]) not in ({"typing", "read"}, set(COUNTERS))
            or any(type(count) is not int or not 0 <= count <= 1000000 for count in value["blocked"].values())
            or value["error"] is not None and (type(value["error"]) is not str or value["error"] not in ERRORS)):
        raise ValueError("Invalid ghost runtime state")
    if value["ready"] and (not all(value["features"][name] for name in BASE_FEATURES) or value["error"] is not None):
        raise ValueError("Invalid ghost runtime confirmation")
    return {**value, "hideOnline": value.get("hideOnline", False), "hideStories": value.get("hideStories", False),
            "features": {name: value["features"].get(name, False) for name in FEATURES},
            "blocked": {name: value["blocked"].get(name, 0) for name in COUNTERS}}


class GhostRuntime:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._clients = {}

    def update(self, pid, snapshot):
        if type(pid) is not int or not 0 < pid <= 4294967295:
            raise ValueError("Invalid ghost runtime identity")
        value = checked_snapshot(snapshot)
        with self._lock:
            self._clients[pid] = self._clock(), value

    def remove(self, pid):
        with self._lock:
            self._clients.pop(pid, None)

    def invalidate(self, pid):
        """A live client without an ACK must prevent a global active claim."""
        with self._lock:
            self._clients[pid] = self._clock(), None

    def sync(self, live):
        with self._lock:
            self._clients = {pid: self._clients.get(pid, (self._clock(), None)) for pid in live}

    def status(self, desired):
        now = self._clock()
        with self._lock:
            observed = list(self._clients.values())
        clients = [value for seen, value in observed if value is not None and 0 <= now - seen <= 10]
        complete = bool(observed) and len(clients) == len(observed)
        features = {name: complete and all(value["features"][name] for value in clients) for name in FEATURES}
        ready = complete and all(value["ready"] for value in clients)
        # A legacy client can acknowledge legacy controls, but can never confirm
        # a newly requested filter that it does not actually install.
        applied = (ready and all(value["revision"] == desired["revision"] and
                   all(value[name] == desired["settings"].get(name, False) for name in ("enabled", *FEATURES)) for value in clients)
                   and all(not desired["settings"].get(name, False) or features[name] for name in FEATURES))
        active = applied and desired["settings"]["enabled"] and any(desired["settings"].get(name, False) for name in FEATURES)
        return {"scope": "all_accounts", "state": "active" if active else "off" if applied else "pending" if ready else "unavailable",
                "ready": ready, "features": features, "applied": applied,
                "blocked": {name: min(1000000, sum(value["blocked"][name] for value in clients)) for name in COUNTERS}}
