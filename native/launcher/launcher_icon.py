"""Build-only extraction/verification of the pinned stock launcher icon.

No image decoder or resizer is used. ICON frames and the GROUP_ICON directory
remain exact PE resource bytes; the generated ICO is only a container for them.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import stat
import struct

import pefile


def require(value, reason):
    if not value:
        raise ValueError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ordinary(path):
    for parent in (path, *path.parents):
        if parent.exists():
            require(not parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                    "reparse icon path")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "ordinary icon input required")
    return info


def icon_resources(data):
    pe = pefile.PE(data=data, fast_load=True)
    try:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
        types = {entry.id: entry for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries}
        require(14 in types and 3 in types, "stock icon resources missing")
        # PyInstaller --icon stock.exe also chooses the first GROUP_ICON name.
        group = types[14].directory.entries[0]
        require(group.name is None and group.id == 1, "unexpected stock icon group")
        languages = group.directory.entries
        require(len(languages) == 1, "ambiguous stock icon language")
        language = languages[0]
        group_data = pe.get_data(language.data.struct.OffsetToData, language.data.struct.Size)
        require(len(group_data) >= 6, "truncated icon directory")
        reserved, kind, count = struct.unpack_from('<HHH', group_data)
        require(reserved == 0 and kind == 1 and 1 <= count <= 128 and len(group_data) == 6 + count * 14,
                "invalid stock icon directory")
        icons = {entry.id: entry for entry in types[3].directory.entries if entry.name is None}
        frames = []
        seen = set()
        for index in range(count):
            width, height, colors, zero, planes, bits, size, resource_id = struct.unpack_from('<BBBBHHIH', group_data, 6 + index * 14)
            require(zero == 0 and resource_id not in seen and resource_id in icons and 0 < size <= 16 << 20,
                    "invalid stock icon frame")
            seen.add(resource_id)
            choices = [entry for entry in icons[resource_id].directory.entries if entry.id == language.id]
            require(len(choices) == 1, "icon frame language mismatch")
            entry = choices[0]
            frame = pe.get_data(entry.data.struct.OffsetToData, entry.data.struct.Size)
            require(len(frame) == size == entry.data.struct.Size, "icon frame size mismatch")
            frames.append({'id': resource_id, 'width': width, 'height': height, 'colors': colors,
                           'planes': planes, 'bits': bits, 'bytes': frame})
        return {'groupId': group.id, 'language': language.id, 'group': group_data, 'frames': frames}
    finally:
        pe.close()


def write_asset(path, data):
    if path.exists():
        ordinary(path)
    with path.open('wb') as stream:
        stream.write(data)


def extract(runtime, pins_path, output):
    require(runtime.is_absolute() and runtime.name == 'runtime', "explicit runtime required")
    source = runtime / 'mod/max.exe'
    ordinary(source)
    ordinary(pins_path)
    pins = json.loads(pins_path.read_text(encoding='utf8'))
    matches = [row for row in pins['files'] if row['path'] == 'mod/max.exe']
    require(len(matches) == 1, "pinned stock executable missing")
    data = source.read_bytes()
    require(len(data) == matches[0]['size'] and digest(data) == matches[0]['sha256'], "stock executable changed")
    stock = icon_resources(data)
    require(output.is_absolute() and any(output.is_relative_to(Path(root)) for root in
            ('C:/ValMAXBuild/build', 'D:/ValMAXBuild/build', 'E:/ValMAXBuild/build')), "owned build icon directory required")
    require(all(part not in ('.', '..') for part in output.parts), "ambiguous icon output")
    for parent in (output, *output.parents):
        if parent.exists():
            require(not parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                    "reparse icon output")
    output.mkdir(parents=True, exist_ok=True)
    # Preserve the original frames in a standard ICO container. RC's resulting
    # GROUP_ICON and ICON bytes/IDs/language are verified after actual linking.
    group_name = 'launcher-stock-group.bin'
    write_asset(output / group_name, stock['group'])
    rc = ['// Exact stock PE icon resources. Generated; do not edit.',
          f"LANGUAGE {stock['language'] & 0x3ff}, {stock['language'] >> 10}",
          f"{stock['groupId']} ICON \"{(output / 'launcher-stock.ico').as_posix()}\""]
    offset = 6 + 16 * len(stock['frames'])
    ico = bytearray(struct.pack('<HHH', 0, 1, len(stock['frames'])))
    records = []
    for frame in stock['frames']:
        raw = frame['bytes']
        file = output / f"launcher-stock-frame-{frame['id']}.bin"
        write_asset(file, raw)
        ico.extend(struct.pack('<BBBBHHII', frame['width'], frame['height'], frame['colors'], 0,
                               frame['planes'], frame['bits'], len(raw), offset))
        offset += len(raw)
        records.append({key: value for key, value in frame.items() if key != 'bytes'} |
                       {'sha256': digest(raw), 'asset': file.name})
    for frame in stock['frames']:
        ico.extend(frame['bytes'])
    write_asset(output / 'launcher-stock.ico', ico)
    rc_bytes = ('\n'.join(rc) + '\n').encode('utf8')
    write_asset(output / 'launcher-stock.rc', rc_bytes)
    proof = {'schema': 1, 'source': str(source), 'sourceSha256': digest(data),
             'groupId': stock['groupId'], 'language': stock['language'],
             'groupSha256': digest(stock['group']), 'frames': records,
             'assets': {name: digest((output / name).read_bytes()) for name in
                        [group_name, 'launcher-stock.ico', 'launcher-stock.rc'] +
                        [record['asset'] for record in records]},
             'imagePixelsDecodedOrModified': False, 'runtimeExecuted': False}
    write_asset(output / 'launcher-icon.json', (json.dumps(proof, indent=2) + '\n').encode('utf8'))
    return proof


def verify(source, executable, proof_path):
    ordinary(source)
    ordinary(executable)
    ordinary(proof_path)
    proof = json.loads(proof_path.read_text(encoding='utf8'))
    original = source.read_bytes()
    require(digest(original) == proof['sourceSha256'], "stock icon source changed")
    for name, expected in proof['assets'].items():
        asset = proof_path.parent / name
        require(asset.name == name, "invalid icon asset name")
        ordinary(asset)
        require(digest(asset.read_bytes()) == expected, "generated icon asset changed")
    stock = icon_resources(original)
    native = icon_resources(executable.read_bytes())
    require(stock['groupId'] == native['groupId'] and stock['language'] == native['language']
            and stock['group'] == native['group'], "native icon group differs from stock")
    require(stock['frames'] == native['frames'], "native icon frames differ from stock")
    return {'verified': True, 'groupResourceByteIdentical': True, 'frameResourcesByteIdentical': True,
            'groupId': stock['groupId'], 'language': stock['language'], 'frames': len(stock['frames']),
            'stockExecutableSha256': digest(original), 'launcherSha256': digest(executable.read_bytes()),
            'runtimeExecuted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', required=True, type=Path)
    parser.add_argument('--pins', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    proof = extract(args.runtime_root, args.pins, args.output)
    print(json.dumps({'iconExtracted': True, 'frames': len(proof['frames']), 'pixelsModified': False}))


if __name__ == '__main__':
    main()
