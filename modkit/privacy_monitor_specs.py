"""Find bounded, instruction-aligned calls to disabled Tracer SDK imports.

The observer hooks callers instead of the SDK's short return stubs. Files must
come from the verified mod directory. Only code addresses and public symbol
names are produced; there is no profile or credential access.
"""
from __future__ import annotations

from bisect import bisect_right
from pathlib import Path
import hashlib
import json
import re
import struct

import capstone
import pefile

CACHE_SHA256 = "53d5d5d79c5f66d2ddc745bcc3211f460edaa7d1d4439c40bbf5d71a374f0552"


def callsites(path: Path) -> list[dict]:
    data = path.read_bytes()
    pe = pefile.PE(data=data, fast_load=False)
    iats = {}
    for library in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        if library.dll.lower() == b"tracernative.dll":
            for entry in library.imports:
                if entry.name and entry.name.startswith(b"tracer_"):
                    iats[entry.address - pe.OPTIONAL_HEADER.ImageBase] = entry.name.decode("ascii")
    if not iats:
        return []
    ranges = sorted((item.struct.BeginAddress, item.struct.EndAddress)
                    for item in getattr(pe, "DIRECTORY_ENTRY_EXCEPTION", []))
    starts = [item[0] for item in ranges]
    # Small exported adapter wrappers are leaf functions and legitimately have
    # no .pdata record. Their export starts provide an independent code anchor.
    exports = sorted(set(item.address for item in getattr(pe, "DIRECTORY_ENTRY_EXPORT", ()).symbols)) if hasattr(pe, "DIRECTORY_ENTRY_EXPORT") else []
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    records = []
    for section in pe.sections:
        if not section.Characteristics & 0x20000000:
            continue
        raw = data[section.PointerToRawData:section.PointerToRawData + section.SizeOfRawData]
        candidates = []
        thunks = {}
        for match in re.finditer(b"\xff[\x15\x25]", raw):
            index = match.start()
            if index + 6 > len(raw):
                continue
            rva = section.VirtualAddress + index
            target = rva + 6 + struct.unpack_from("<i", raw, index + 2)[0]
            if target not in iats:
                continue
            if raw[index + 1] == 0x25:
                thunk = rva - 1 if index and raw[index - 1] == 0x48 else rva
                thunks[thunk] = iats[target]
                # Hook actual CALL instructions, not a leaf JMP thunk which
                # Frida might resolve onward to the intentionally short stub.
                continue
            candidates.append((index, rva, iats[target], 6))
        for match in re.finditer(b"\xe8", raw):
            index = match.start()
            if index + 5 > len(raw):
                continue
            rva = section.VirtualAddress + index
            target = rva + 5 + struct.unpack_from("<i", raw, index + 1)[0]
            if target in thunks:
                candidates.append((index, rva, thunks[target], 5))
        for index, rva, symbol, instruction_size in candidates:
            position = bisect_right(starts, rva) - 1
            anchors = []
            if position >= 0 and ranges[position][0] <= rva < ranges[position][1]:
                anchors.append(ranges[position][0])
            export_index = bisect_right(exports, rva) - 1
            if export_index >= 0 and rva - exports[export_index] <= 4096:
                anchors.append(exports[export_index])
            aligned = False
            for begin in anchors:
                if rva - begin > 1024 * 1024:
                    continue
                for instruction in decoder.disasm(pe.get_data(begin, rva - begin + 16), begin):
                    if instruction.address == rva:
                        # MSVC emits tail jumps in the SDK adapter wrappers.
                        aligned = instruction.mnemonic == "call" and instruction.size == instruction_size
                        break
                    if instruction.address > rva:
                        break
                if aligned:
                    break
            if aligned:
                records.append({"rva": rva, "symbol": symbol,
                                "expected": raw[index:index + instruction_size].hex()})
    pe.close()
    return records


def specifications(root: Path, *, service: bool) -> dict:
    relative = "max-service/max-service.exe" if service else "tracer_crash_reporter.dll"
    path = root / relative
    cache = Path(__file__).with_name("privacy_callsites.json").read_bytes()
    if hashlib.sha256(cache).hexdigest() != CACHE_SHA256:
        raise ValueError("Unsupported observer callsite cache")
    entries = json.loads(cache)["entries"]
    entry = next(item for item in entries if item["relative_path"] == relative)
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise ValueError("Unsupported observer caller binary")
    if not service:
        network = root / "network.dll"
        if hashlib.sha256(network.read_bytes()).hexdigest() != "8bd5a114ab7a43bc4ff4ea9eaa124928fcfaa16d8fea02a100d40a18309adce7":
            raise ValueError("Unsupported protocol observer binary")
    return {"root": str(root), "service": service,
            "tracerModule": path.name, "tracerPath": str(path),
            "tracerCallsites": entry["callsites"],
            "networkPath": str(root / "network.dll") if not service else None,
            "packetHook": {"rva": 0x6E3FD, "expected": "ff150d920b00"} if not service else None}
