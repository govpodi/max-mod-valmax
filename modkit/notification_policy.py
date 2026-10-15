"""Bounded PIN-notification policy and value-free native acknowledgements.

Only account/chat identifiers enter the native policy. Message contents,
avatars, PIN values and verifier records never cross this interface.
"""
from concurrent.futures import Future, InvalidStateError, TimeoutError as FutureTimeout
import hashlib
import itertools
import json
import re
import threading
import time
import uuid

from chat_lock_service import scope, ChatLockError, MAX_LOCKS

LEASE_MS = 5000
REQUEST = 'valmax-notification-policy'
ACK = 'valmax-notification-policy-ack'
_REVISION = re.compile(r'[0-9a-f]{64}\Z')
_GENERATIONS = itertools.count(1)
NATIVE_ERRORS = frozenset({None, 'waiting_for_modules', 'module_mismatch', 'hook_failed',
                           'invalid_policy', 'lease_expired', 'memory_guard', 'scope_guard',
                           'sanitization_failed', 'mapping_unavailable', 'module_unloaded',
                           'unsupported_platform', 'invalid_specification', 'clock_unavailable',
                           'catalog_unavailable', 'identity_unavailable', 'clone_failed',
                           'cleanup_failed', 'invalid_data', 'invalid_asset', 'bridge_unavailable'})


class NotificationPolicyError(Exception):
    """Fixed error boundary; never include input or native exception strings."""

    def __init__(self):
        super().__init__('Notification privacy is unavailable')


def from_catalog(catalog):
    """Ignore unlock grants: all PIN-protected chats keep generic previews."""
    if (type(catalog) is not dict or set(catalog) != {'schema', 'ready', 'protected'}
            or type(catalog.get('schema')) is not int or catalog.get('schema') != 1
            or catalog.get('ready') is not True or type(catalog.get('protected')) is not list
            or len(catalog['protected']) > MAX_LOCKS):
        raise NotificationPolicyError()
    rows, seen = [], set()
    for item in catalog['protected']:
        if (type(item) is not dict or set(item) != {'accountId', 'chatId', 'locked'}
                or type(item['locked']) is not bool):
            raise NotificationPolicyError()
        try:
            key = scope(item['accountId'], item['chatId'])
        except ChatLockError:
            raise NotificationPolicyError() from None
        if key in seen:
            raise NotificationPolicyError()
        seen.add(key)
        rows.append({'ownerId': item['accountId'], 'chatId': item['chatId']})
    rows.sort(key=lambda row: (row['ownerId'], row['chatId']))
    return _wire(True, rows)


def unavailable_policy():
    return _wire(False, [])


def _wire(ready, rows):
    encoded = json.dumps({'schema': 1, 'ready': ready, 'protected': rows},
                         sort_keys=True, separators=(',', ':')).encode('ascii')
    return {'schema': 1, 'revision': hashlib.sha256(encoded).hexdigest(),
            'ready': ready, 'protected': rows, 'leaseMs': LEASE_MS}


def checked_snapshot(value):
    keys = {'ready', 'applied', 'revision', 'error', 'masked', 'scopeUnknown'}
    if (type(value) is not dict or set(value) != keys
            or any(type(value[key]) is not bool for key in ('ready', 'applied'))
            or type(value['revision']) is not str or not _REVISION.fullmatch(value['revision'])
            or (value['error'] is not None and type(value['error']) is not str)
            or value['error'] not in NATIVE_ERRORS
            or any(type(value[key]) is not int or not 0 <= value[key] <= 1000000
                   for key in ('masked', 'scopeUnknown'))
            or value['applied'] and (not value['ready'] or value['error'] is not None)):
        raise NotificationPolicyError()
    return dict(value)


class PolicyChannel:
    """Dedicated ACK channel: never compete with periodic privacy snapshots."""

    def __init__(self, script):
        self.script = script
        self.lock = threading.RLock()
        self.pending = None
        self.closed = False
        self.stale = False
        self.handler = self._on_message
        try:
            script.on('message', self.handler)
        except Exception:
            raise NotificationPolicyError() from None

    def _on_message(self, message, data=None):
        if type(message) is not dict or message.get('type') != 'send' or data is not None:
            return
        payload = message.get('payload')
        if type(payload) is not dict or payload.get('kind') != ACK:
            return
        with self.lock:
            pending = self.pending
            if self.closed or pending is None or payload.get('requestId') != pending[0]:
                return
            future = pending[1]
        try:
            future.set_result(checked_snapshot(payload.get('snapshot')))
        except NotificationPolicyError:
            try:
                future.set_exception(NotificationPolicyError())
            except InvalidStateError:
                pass
        except InvalidStateError:
            pass

    def publish(self, policy, timeout=2.0):
        if type(timeout) not in (int, float) or not 0 < timeout <= 5:
            raise ValueError('Invalid policy timeout')
        identifier, future = uuid.uuid4().hex, Future()
        generation = next(_GENERATIONS)
        with self.lock:
            if self.closed or self.stale or self.pending is not None:
                raise NotificationPolicyError()
            self.pending = identifier, future
        def post():
            try:
                self.script.post({'type': REQUEST, 'requestId': identifier,
                                  'generation': generation, 'policy': policy})
            except Exception:
                try:
                    future.set_exception(NotificationPolicyError())
                except InvalidStateError:
                    pass
        try:
            threading.Thread(target=post, daemon=True, name='ValMAX notification policy').start()
            value = future.result(timeout=timeout)
            with self.lock:
                if (self.closed or self.stale or self.pending is None or self.pending[0] != identifier
                        or not value['ready'] or not value['applied'] or value['revision'] != policy['revision']):
                    raise NotificationPolicyError()
                return value
        except (FutureTimeout, NotificationPolicyError):
            with self.lock:
                self.stale = True
            raise NotificationPolicyError() from None
        finally:
            with self.lock:
                if self.pending is not None and self.pending[0] == identifier:
                    self.pending = None
            future.cancel()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            pending, self.pending = self.pending, None
        if pending is not None:
            try:
                pending[1].set_exception(NotificationPolicyError())
            except InvalidStateError:
                pass
        def remove():
            try:
                self.script.off('message', self.handler)
            except Exception:
                pass
        threading.Thread(target=remove, daemon=True, name='ValMAX notification policy close').start()


class NotificationRuntime:
    """Expose only readiness/counters; identifiers stay inside native policy."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.clients = {}

    def sync(self, pids):
        with self.lock:
            self.clients = {pid: self.clients.get(pid) for pid in pids}

    def register(self, pid):
        if type(pid) is not int or not 0 < pid <= 0xffffffff:
            raise NotificationPolicyError()
        with self.lock:
            self.clients[pid] = None

    def update(self, pid, value):
        value = checked_snapshot(value)
        with self.lock:
            if pid in self.clients:
                self.clients[pid] = self.clock(), value

    def remove(self, pid):
        with self.lock:
            self.clients.pop(pid, None)

    def invalidate(self, pid):
        with self.lock:
            self.clients[pid] = None

    def status(self, policy):
        now = self.clock()
        with self.lock:
            entries = list(self.clients.values())
        values = [entry[1] for entry in entries if entry is not None and 0 <= now - entry[0] < LEASE_MS / 1000]
        complete = bool(entries) and len(values) == len(entries)
        ready = complete and all(value['ready'] for value in values)
        applied = ready and all(value['applied'] and value['revision'] == policy['revision'] for value in values)
        return {'ready': ready, 'applied': applied,
                'masked': min(1000000, sum(value['masked'] for value in values)),
                'scopeUnknown': min(1000000, sum(value['scopeUnknown'] for value in values))}
