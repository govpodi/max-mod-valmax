"""Fail-closed metadata descriptions of MAX SessionInit MessagePack packets.

This module performs no I/O, networking or logging. It never decodes scalar
values, emits arbitrary map keys, or accepts message/auth/file packet bodies.
The caller must filter the ten-byte frame header to opcode 6 before reading its
body from process memory. Frame/schema details are pinned to MAX 26.35.1.80662.
"""
from __future__ import annotations

SESSION_INIT_OPCODE = 6
MAX_PAYLOAD_BYTES = 65536
MAX_FRAME_BYTES = MAX_PAYLOAD_BYTES + 10
MAX_METADATA_BYTES = 64 * 1024 * 1024
TOP_FIELDS = frozenset(("userAgent", "deviceId", "clientSessionId"))
USER_AGENT_FIELDS = frozenset(("deviceType", "appVersion", "osVersion", "locale",
                               "deviceLocale", "deviceName", "screen", "timezone",
                               "msiProductCode", "buildNumber"))
ALLOWED_FIELDS = TOP_FIELDS | USER_AGENT_FIELDS
DIAGNOSTIC_REASONS = frozenset(("ok", "input", "bounds", "header", "opcode", "flags", "length",
                                "marker", "layout", "unknown_key", "duplicate_key", "truncated",
                                "trailing", "depth", "scalar_type", "missing_user_agent"))


class _Invalid(Exception):
    """Fixed rejection code; packet contents never enter exception arguments."""

    def __init__(self, reason: str = "layout"):
        self.reason = reason if type(reason) is str and reason in DIAGNOSTIC_REASONS and reason != "ok" else "layout"
        super().__init__(self.reason)


def validate_metadata(metadata: object) -> dict | None:
    """Return only allowlisted, bounded metadata suitable for a status display.

    Extra top-level keys are discarded without inspecting their values. Unknown
    field names reject the record rather than turning arbitrary input into text.
    No sequence number, timestamp, address, token, device ID or user-agent value
    is accepted by this interface.
    """
    if type(metadata) is not dict:
        return None
    protocol = metadata.get("protocol")
    opcode = metadata.get("opcode")
    byte_count = metadata.get("bytes")
    fields = metadata.get("fields")
    if (type(protocol) is not str or protocol != "msgpack"
            or type(opcode) is not int or not 0 <= opcode <= 65535
            or type(byte_count) is not int or not 0 <= byte_count <= MAX_METADATA_BYTES
            or type(fields) is not list or len(fields) > len(ALLOWED_FIELDS)):
        return None
    if any(type(field) is not str or field not in ALLOWED_FIELDS for field in fields):
        return None
    # Only SessionInit has a statically reviewed field schema. Other opcodes may
    # report header-only metadata but must not claim payload field inspection.
    if opcode != SESSION_INIT_OPCODE and fields:
        return None
    return {"protocol":"msgpack", "opcode":opcode,
            "fields":sorted(set(fields)), "bytes":byte_count}


class _Cursor:
    def __init__(self, payload: bytes | bytearray | memoryview):
        if type(payload) not in (bytes, bytearray, memoryview):
            raise _Invalid("input")
        self.data = memoryview(payload).cast("B")
        self.offset = 0
        self.fields: set[str] = set()
        self.structure: list[dict] = []

    def take(self, size: int) -> memoryview:
        if size < 0 or size > len(self.data)-self.offset:
            raise _Invalid("truncated")
        result = self.data[self.offset:self.offset+size]
        self.offset += size
        return result

    def number(self, size: int) -> int:
        return int.from_bytes(self.take(size), "big")

    def byte(self) -> int:
        return self.number(1)

    def map_size(self) -> int:
        marker = self.byte()
        if 0x80 <= marker <= 0x8f:
            return marker & 15
        if marker == 0xde:
            return self.number(2)
        if marker == 0xdf:
            return self.number(4)
        raise _Invalid("marker")

    def string_size(self, marker: int) -> int:
        if 0xa0 <= marker <= 0xbf:
            return marker & 31
        widths = {0xd9:1, 0xda:2, 0xdb:4}
        if marker in widths:
            return self.number(widths[marker])
        raise _Invalid("marker")

    def key(self, allowed: frozenset[str]) -> str:
        length = self.string_size(self.byte())
        if not 1 <= length <= 32:
            raise _Invalid("unknown_key")
        value = self.take(length)
        # Compare bytes to static literals. Unknown names are never decoded,
        # retained as Python strings, or included in a diagnostic.
        for field in allowed:
            if value == field.encode("ascii"):
                return field
        raise _Invalid("unknown_key")

    def scalar(self, field: str) -> None:
        marker = self.byte()
        length = None
        if marker <= 0x7f or marker >= 0xe0:
            kind = "integer"
        elif marker in (0xc0, 0xc2, 0xc3):
            kind = "nil" if marker == 0xc0 else "boolean"
        elif marker in (0xcc, 0xcd, 0xce, 0xcf, 0xd0, 0xd1, 0xd2, 0xd3):
            kind = "integer"
            self.take({0xcc:1, 0xcd:2, 0xce:4, 0xcf:8,
                       0xd0:1, 0xd1:2, 0xd2:4, 0xd3:8}[marker])
        elif marker in (0xca, 0xcb):
            kind = "float"
            self.take(4 if marker == 0xca else 8)
        elif 0xa0 <= marker <= 0xbf or marker in (0xd9, 0xda, 0xdb):
            kind = "string"
            length = self.string_size(marker)
            self.take(length)  # Scalar bytes are never decoded or retained.
        else:
            # No binary/extension/array/nested arbitrary-map interpretation.
            raise _Invalid("depth" if 0x80 <= marker <= 0x8f or marker in (0xde, 0xdf) else "scalar_type")
        self.fields.add(field)
        row = {"field":field, "type":kind}
        if length is not None:
            row["length"] = length
        self.structure.append(row)

    def object(self, allowed: frozenset[str], *, top: bool) -> None:
        count = self.map_size()
        if not 1 <= count <= len(allowed):
            raise _Invalid("layout")
        seen: set[str] = set()
        for _ in range(count):
            field = self.key(allowed)
            if field in seen:
                raise _Invalid("duplicate_key")
            seen.add(field)
            if top and field == "userAgent":
                self.fields.add(field)
                self.structure.append({"field":field, "type":"map"})
                self.object(USER_AGENT_FIELDS, top=False)
            else:
                self.scalar(field)
        if top and "userAgent" not in seen:
            raise _Invalid("missing_user_agent")


def _describe_payload(payload: bytes | bytearray | memoryview,
                      opcode: int) -> tuple[dict | None, str]:
    if type(opcode) is not int or opcode != SESSION_INIT_OPCODE:
        return None, "opcode"
    try:
        if not 0 < len(payload) <= MAX_PAYLOAD_BYTES:
            return None, "bounds"
        cursor = _Cursor(payload)
        # A typed memoryview's len counts elements, not bytes. Enforce the
        # budget again after cast before inspecting any MessagePack marker.
        if not 0 < len(cursor.data) <= MAX_PAYLOAD_BYTES:
            return None, "bounds"
        cursor.object(TOP_FIELDS, top=True)
        if cursor.offset != len(cursor.data):
            return None, "trailing"
        metadata = {"protocol":"msgpack", "opcode":opcode,
                    "fields":sorted(cursor.fields), "bytes":len(cursor.data)}
        checked = validate_metadata(metadata)
        if checked is None:
            return None, "layout"
        checked["structure"] = cursor.structure
        return checked, "ok"
    except _Invalid as error:
        return None, error.reason
    except (ValueError, TypeError):
        return None, "input"


def describe_session_init_payload(payload: bytes | bytearray | memoryview,
                                  *, opcode: int = SESSION_INIT_OPCODE) -> dict | None:
    """Describe only reviewed fields/types/lengths; never scalar values.

    Use validate_metadata before a status UI or collector record; that drops
    structural details. Unknown layouts remain rejected rather than scanned.
    """
    return _describe_payload(payload, opcode)[0]


def _describe_frame(frame: bytes | bytearray | memoryview) -> tuple[dict | None, str]:
    """Describe one complete uncompressed opcode-6 frame, with strict bounds.

    Core's header serializer writes ten bytes: opcode is big-endian at +4, and
    payload length is big-endian uint24 at +7. Byte +6 is a control/flags byte;
    only zero is accepted until compression semantics are independently proven.
    No heuristic offset scanning or decoding of other packet opcodes is allowed.
    """
    if type(frame) not in (bytes, bytearray, memoryview):
        return None, "input"
    try:
        data = memoryview(frame).cast("B")
        if len(data) < 10:
            return None, "header"
        if not 10 < len(data) <= MAX_FRAME_BYTES:
            return None, "bounds"
        opcode = int.from_bytes(data[4:6], "big")
        if opcode != SESSION_INIT_OPCODE:
            return None, "opcode"
        if data[6] != 0:
            return None, "flags"
        length = int.from_bytes(data[7:10], "big")
        if length != len(data)-10:
            return None, "length"
        result, reason = _describe_payload(data[10:], opcode)
        if result is not None:
            result["bytes"] = len(data)
        return result, reason
    except (ValueError, TypeError):
        return None, "input"


def describe_session_init_frame(frame: bytes | bytearray | memoryview) -> dict | None:
    """Describe reviewed SessionInit fields/types/lengths, or None on doubt."""
    return _describe_frame(frame)[0]


def packet_metadata_diagnostic(frame: bytes | bytearray | memoryview) -> tuple[dict | None, str]:
    """Return (sanitized metadata or None, one constant diagnostic reason).

    Successful metadata has exactly protocol/opcode/fields/bytes. A rejection
    emits no field name, marker byte, offset, prefix, value, exception text or
    partial result. Reasons belong to DIAGNOSTIC_REASONS; unknown schemas remain
    rejected. The caller may count a reason without retaining packet data.
    """
    described, reason = _describe_frame(frame)
    return validate_metadata(described), reason


def packet_metadata(frame: bytes | bytearray | memoryview) -> dict | None:
    """Collector entry point: only the four sanitized status-contract keys.

    Input exists only in caller RAM. This function performs no I/O and emits no
    raw bytes, scalar values, dynamic field names or structural detail.
    """
    return packet_metadata_diagnostic(frame)[0]


def session_init_header_metadata(frame: object) -> dict | None:
    """Count a reviewed frame header when its body schema is rejected.

    Inspect only the ten header bytes and total length. Never infer any body
    fields, and do not count arbitrary buffers or another opcode as SessionInit.
    """
    if type(frame) not in (bytes, bytearray, memoryview):
        return None
    try:
        data = memoryview(frame).cast("B")
        if (not 10 <= len(data) <= MAX_FRAME_BYTES
                or int.from_bytes(data[4:6], "big") != SESSION_INIT_OPCODE
                or data[6] != 0 or int.from_bytes(data[7:10], "big") != len(data) - 10):
            return None
        return {"protocol": "msgpack", "opcode": SESSION_INIT_OPCODE,
                "fields": [], "bytes": len(data)}
    except (ValueError, TypeError):
        return None
