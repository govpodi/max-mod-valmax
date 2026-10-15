"""Bounded asynchronous snapshots for the local privacy observer.

Frida 17.23.3 on this host has only exports_sync. Its _rpc_request waits on a
Condition without consulting Cancellable, so a Cancellable deadline cannot
bound that RPC. This module uses public script.on/off/post instead, with a
single outstanding request and a host-side Future deadline. It never attaches
to a process, reads memory, or sends values from MAX.

The trusted JS observer must re-arm this receiver after every response:

    recv('privacy-snapshot', function (message) {
        send({kind: 'privacy-snapshot', requestId: message.requestId,
              snapshot: takeSnapshot()});
        waitForSnapshot();
    });

On timeout/error the channel becomes stale and must be closed and replaced
with a fresh observer session; do not retry it. A script.post/off native hang
cannot block the caller because those operations use daemon threads. This is
not a hard isolation guarantee against native code holding Python's GIL; an
observer subprocess remains the strongest shutdown boundary.
"""
from __future__ import annotations

from concurrent.futures import CancelledError, Future, InvalidStateError, TimeoutError as FutureTimeout
import math
import threading
from typing import Any
import uuid


PROTOCOL = "privacy-snapshot"


class SnapshotError(Exception):
    """Generic, value-free snapshot error safe for an error-code boundary."""


class SnapshotTimeout(SnapshotError):
    """The host deadline expired; this channel is now stale."""


class SnapshotUnavailable(SnapshotError):
    """The channel is closed, stale, busy, malformed, or unable to post."""


class SnapshotChannel:
    """One public-message snapshot channel per verified Frida Script.

    Returned dictionaries still require the collector's normal bounded schema
    validation. Late, unrelated, and binary messages are ignored. Never reuse
    a stale channel; its unreturned native sender may still be finishing.
    """

    def __init__(self, script: Any):
        self._script = script
        self._lock = threading.RLock()
        self._pending: tuple[str, Future] | None = None
        self._stale = False
        self._closed = False
        self._cleanup_finished = threading.Event()
        self._cleanup_success = False
        self._handler = self._on_message
        try:
            script.on("message", self._handler)
        except Exception:
            self._stale = True
            raise SnapshotUnavailable("Cannot register snapshot channel") from None

    @property
    def stale(self) -> bool:
        with self._lock:
            return self._stale or self._closed

    @property
    def pending_count(self) -> int:
        with self._lock:
            return int(self._pending is not None)

    def _on_message(self, message: Any, data: Any = None) -> None:
        if (type(message) is not dict or message.get("type") != "send"
                or data is not None):
            return
        payload = message.get("payload")
        if (type(payload) is not dict or payload.get("kind") != PROTOCOL
                or type(payload.get("requestId")) is not str):
            return
        with self._lock:
            pending = self._pending
            if self._closed or pending is None or pending[0] != payload["requestId"]:
                return
            future = pending[1]
        try:
            if type(payload.get("snapshot")) is dict:
                future.set_result(payload["snapshot"])
            else:
                future.set_exception(SnapshotUnavailable("Malformed snapshot response"))
        except InvalidStateError:
            pass  # Deadline/close won the race; a late reply has no effect.

    def _post(self, identifier: str) -> None:
        try:
            self._script.post({"type": PROTOCOL, "requestId": identifier})
        except Exception:
            with self._lock:
                pending = self._pending
                future = pending[1] if pending is not None and pending[0] == identifier else None
            if future is not None:
                try:
                    future.set_exception(SnapshotUnavailable("Cannot post snapshot request"))
                except InvalidStateError:
                    pass

    def snapshot(self, timeout: float = 5.0) -> dict:
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= 60):
            raise ValueError("Snapshot timeout must be a finite number in (0, 60]")
        identifier = uuid.uuid4().hex
        future = Future()
        with self._lock:
            if self._closed or self._stale:
                raise SnapshotUnavailable("Snapshot channel is stale or closed")
            if self._pending is not None:
                raise SnapshotUnavailable("A snapshot request is already pending")
            self._pending = identifier, future
        sender = threading.Thread(target=self._post, args=(identifier,),
                                  name="MAX privacy snapshot post", daemon=True)
        try:
            sender.start()
            try:
                return future.result(timeout=timeout)
            except FutureTimeout:
                with self._lock:
                    self._stale = True
                raise SnapshotTimeout("Privacy snapshot deadline expired") from None
            except (CancelledError, SnapshotUnavailable):
                with self._lock:
                    self._stale = True
                raise SnapshotUnavailable("Privacy snapshot unavailable") from None
        except BaseException:
            with self._lock:
                self._stale = True
            raise
        finally:
            with self._lock:
                if self._pending is not None and self._pending[0] == identifier:
                    self._pending = None
            future.cancel()

    def close(self, timeout: float = 0.25) -> bool:
        """Cancel waiters and attempt listener removal for at most one second.

        Return True if removal completed. A False result needs observer-session
        cleanup; it must not cause the server shutdown thread to wait forever.
        """
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 <= timeout <= 1):
            raise ValueError("Close timeout must be a finite number in [0, 1]")
        with self._lock:
            if self._closed:
                finished = self._cleanup_finished
                pending = None
                already_closed = True
            else:
                already_closed = False
                self._closed = True
                self._stale = True
                pending = self._pending
                self._pending = None
                finished = self._cleanup_finished
        if already_closed:
            return finished.wait(timeout) and self._cleanup_success
        if pending is not None:
            pending[1].cancel()
        def unregister():
            try:
                self._script.off("message", self._handler)
                self._cleanup_success = True
            except Exception:
                pass
            finally:
                finished.set()
        remover = threading.Thread(target=unregister, name="MAX privacy snapshot close", daemon=True)
        remover.start()
        finished.wait(timeout)
        return finished.is_set() and self._cleanup_success

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
