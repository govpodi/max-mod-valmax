"""Offline, exact-image proof for the disabled native retention row codec.

Reads only PE binaries supplied as --runtime-root. It does not load MAX,
profiles, credentials, network data or message records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pefile

EXPECTED = {
    'core.dll': ('352aed752b8802646d2a927dcd9de4031046083963f6bddaf4b32b99f75622e6', 32993280),
    'storage.dll': ('9e6c7e50912b5b9bae66fc5d0388b9ab917dba4bb1243786bf6dab758d01fcc7', 13066240),
}
CODE = {
    'core.dll': {
        'owner_constructor': (0xbf36b0, '40555356574154415541564157488d6c24f94881ecf8000000488b05b00c1201'),
        'owner_self_id': (0xbf3820, '488b457f488983d800000044888be000'),
        'owner_destructor': (0xbf4a30, '48895c24104889742418574881ec90000000488b0537f911014833c448898424'),
    },
    'storage.dll': {
        'upsert': (0x3d0b90, '40555356574154415541564157488dac2498f9ffff4881ec68070000488b054d'),
        'history_chat_id': (0x3d0bf9, '4d8b8dd8000000'),
        'message_id': (0x3e3953, '498b18'),
        'sender': (0x3e39d0, '488b9e80000000'),
        'text': (0x3e3a32, '4c8d4630'),
        'status': (0x3e3a85, '8b9e28010000'),
    },
}
LITERALS = {'id': 0x983014, 'sender': 0x989a54, 'text': 0x987030, 'status': 0x986e68}


def verify(runtime: Path) -> dict:
    result = {'schema': 1, 'wholeImagePinned': True, 'codeAnchors': {}, 'sqlColumns': {},
              'runtimeTest': False, 'retentionReady': False}
    for name, (digest, image_size) in EXPECTED.items():
        data = (runtime / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Unsupported native image: ' + name)
        pe = pefile.PE(data=data, fast_load=True)
        try:
            if pe.FILE_HEADER.Machine != 0x8664 or pe.OPTIONAL_HEADER.Magic != 0x20b \
                    or pe.OPTIONAL_HEADER.SizeOfImage != image_size:
                raise ValueError('Unsupported PE layout: ' + name)
            for label, (rva, expected) in CODE[name].items():
                raw = bytes.fromhex(expected)
                section = pe.get_section_by_rva(rva)
                if section is None or not section.Characteristics & 0x20000000 \
                        or pe.get_data(rva, len(raw)) != raw:
                    raise ValueError('Native code anchor mismatch: ' + label)
                result['codeAnchors'][label] = True
            if name == 'storage.dll':
                for label, rva in LITERALS.items():
                    if pe.get_data(rva, len(label)+1) != label.encode('ascii') + b'\0':
                        raise ValueError('SQL column mismatch: ' + label)
                    result['sqlColumns'][label] = True
        finally:
            pe.close()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    proof = verify(args.runtime_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('RETENTION_ROW_STATIC_PIN_OK')
