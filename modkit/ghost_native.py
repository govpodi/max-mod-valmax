"""Version-pinned native ghost entry points, with no profile/credential access.

Only the privacy+MXMOD core image is accepted. The public wrappers finish
without exposing a coroutine result to their callers; blocking them at entry
prevents coroutine allocation and packet enqueue. READ_REACTION is untouched.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import struct

from build_mod import payload_file


CORE_SHA256 = "352aed752b8802646d2a927dcd9de4031046083963f6bddaf4b32b99f75622e6"
BASELINE_SHA256 = "c59e3d753820478858a3abce85a002f63d306f4212e8d8de6a7dd42f9403945a"
CORE_FILE_SIZE = 32905040
CORE_IMAGE_SIZE = 0x1F77000
WRAPPERS = (
    {"feature": "hideTyping", "counter": "typing", "rva": 0x12A8B80,
     "expected": "4883ec28458bc84c8bc2488d542448e83184defe4883c428c3",
     "arguments": ["pointer", "int64", "int"],
     "vtableRva": 0x190CA98, "slot": 8, "thunkRva": 0x264FE,
     "thunkExpected": "e97d262801"},
    {"feature": "hideRead", "counter": "read", "rva": 0x9CA190,
     "expected": "4883ec38498b40084d8b084c8bc2488d542450c7442428000000004889442420e89af870ff4883c438c3",
     "arguments": ["pointer", "int64", "pointer"],
     "vtableRva": 0x18CD300, "slot": 8, "thunkRva": 0x979B,
     "thunkExpected": "e9f0099c00"},
    {"feature": "hideRead", "counter": "read", "rva": 0xA940C0,
     "expected": "4883ec28458bc84c8bc2488d542448e8befe62ff4883c428c3",
     "arguments": ["pointer", "int64", "int"], "selectorExcept": 1,
     "vtableRva": 0x18D4478, "slot": 8, "thunkRva": 0x365C,
     "thunkExpected": "e95f0aa900"},
    {"feature": "hideStories", "counter": "stories", "rva": 0x1241F70,
     "expected": "4883ec380f10024d8bc8488d5424484c8d4424200f29442420e82a90defe4883c438c3",
     "arguments": ["pointer", "pointer", "int64"],
     "vtableRva": 0x190A4F0, "slot": 0x58, "thunkRva": 0xB5BD1,
     "thunkExpected": "e99ac31801"},
)

# Login's exact interactive bool member and Ping's exact computed bool store.
# Neither hook reads credentials, serializes a packet, or drops a keepalive.
ONLINE = {
    "login": {"rva": 0x1531AB0, "expected": "4883c120e9073aadfe",
              "vtableRva": 0x189D5C8, "slot": 8,
              "thunkRva": 0xCF54, "thunkExpected": "e9574b5201"},
    "encoder": {"rva": 0x683550, "expected": "4883ec28803900488d4220488bc8c6442430c3488d5424307505c6442430c2e851c0a1ff4883c428c3"},
    "ping": {"rva": 0x15432AA, "expected": "888424c8000000488d542450488bcbe862f0ffff",
             "serializerRva": 0x15431C0,
             "serializerExpected": "48895c2408574881ece0000000488b05ac117d004833c448898424d0000000488bda488bf9488954242833c038812401000075104889024889420848894210e9d30000000f57c0f30f7f4424304889442440488b150f0a8b00488d4c2450e82c68b4fe90488d05bd3d42004889442450488d0599a8350048898424b000000048c78424b80000000b000000488d056eaf340048898424a8000000488d44245048898424c0000000488d9424a8000000488d4c2450e8af48affe488d05183d420048898424a8000000c78424cc0000000100000080bf2401000000740c83bf20010000000f94c0eb0232c0888424c8000000488d542450488bcbe862f0ffff90488d8c2480000000e880b8b1fe488d4c2468e8820fb3fe90488bc3488b8c24d00000004833cce82061b9fe488b9c24f00000004881c4e00000005fc3",
             "vtableRva": 0x1966E58, "slot": 0x48,
             "thunkRva": 0x694AC, "thunkExpected": "e90f9d4d01"},
}


def specification(root: Path) -> dict:
    """Return a fresh guard specification for the exact verified MXMOD DLL.

    The host must also validate its full mod manifest before spawning. Full
    disk identity is pinned here; the injected monitor additionally verifies
    code, virtual slots, image size, platform and the exact loaded module path.
    """
    path = payload_file(root, "core.dll")
    data = path.read_bytes()
    import ghost_unread_native
    value = specification_from_bytes(data, module_path=path)
    value['unread'] = ghost_unread_native.specification(root)
    return value


def specification_from_bytes(data: bytes, *, module_path: Path) -> dict:
    """Pure byte validation, also useful to verify a freshly prepared build."""
    if (type(data) is not bytes or len(data) != CORE_FILE_SIZE
            or hashlib.sha256(data).hexdigest() != CORE_SHA256):
        raise ValueError("Unsupported ghost core image")
    try:
        pe_at = struct.unpack_from("<I", data, 0x3C)[0]
        optional = pe_at + 24
        if (data[:2] != b"MZ" or data[pe_at:pe_at + 4] != b"PE\0\0"
                or struct.unpack_from("<H", data, pe_at + 4)[0] != 0x8664
                or struct.unpack_from("<H", data, optional)[0] != 0x20B
                or struct.unpack_from("<I", data, optional + 56)[0] != CORE_IMAGE_SIZE):
            raise ValueError("Unsupported ghost PE layout")
    except (IndexError, struct.error) as error:
        raise ValueError("Unsupported ghost PE layout") from error
    return {"schema": 1, "modulePath": str(module_path), "moduleSha256": CORE_SHA256,
            "moduleSize": CORE_FILE_SIZE, "imageSize": CORE_IMAGE_SIZE,
            "wrappers": copy.deepcopy(list(WRAPPERS)), "online": copy.deepcopy(ONLINE)}
