"""Recover bounded compressed Qt resource candidates without loading any DLL."""
from pathlib import Path
import argparse
import hashlib
import json
import zstandard
import zlib

parser = argparse.ArgumentParser()
parser.add_argument('binary', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
data = args.binary.read_bytes()
args.output.mkdir(parents=True, exist_ok=True)
decoder = zstandard.ZstdDecompressor()
results = []
offset = 0
count = 0
while True:
    offset = data.find(b'\x28\xb5\x2f\xfd', offset)
    if offset < 0:
        break
    frame_offset = offset
    offset += 4
    try:
        plain = decoder.decompress(memoryview(data)[frame_offset:frame_offset+1024*1024], max_output_size=8*1024*1024)
    except zstandard.ZstdError:
        continue
    count += 1
    if b'import ' not in plain[:1024]:
        continue
    terms = [term for term in ('multiaccount', 'Multiaccount', 'accountModel', 'maxAccountsCount', 'newAccount') if term.encode() in plain]
    if not terms:
        continue
    digest = hashlib.sha256(plain).hexdigest()
    filename = f'{frame_offset:08x}-{digest[:12]}.qml'
    (args.output / filename).write_bytes(plain)
    results.append({'offset': frame_offset, 'offsetHex':hex(frame_offset), 'length': len(plain), 'sha256': digest, 'file': filename, 'terms': terms})
(args.output / 'index.json').write_text(json.dumps({'decodedFrames':count,'candidates':results}, indent=2), encoding='utf-8')
print(json.dumps({'decodedFrames':count,'candidates':results}))
zlib_count = 0
for magic in (b'\x78\xda', b'\x78\x9c', b'\x78\x01'):
    offset = 0
    while True:
        offset = data.find(magic, offset)
        if offset < 0:
            break
        frame_offset = offset
        offset += 2
        if frame_offset < 4:
            continue
        expected = int.from_bytes(data[frame_offset-4:frame_offset], 'big')
        if not 20 <= expected <= 8*1024*1024:
            continue
        try:
            inflater = zlib.decompressobj()
            plain = inflater.decompress(memoryview(data)[frame_offset:frame_offset+1024*1024], expected+1)
            if not inflater.eof or len(plain) != expected:
                continue
        except zlib.error:
            continue
        zlib_count += 1
        if b'import ' not in plain[:1024]:
            continue
        terms = [term for term in ('multiaccount', 'Multiaccount', 'accountModel', 'maxAccountsCount', 'newAccount') if term.encode() in plain]
        if not terms:
            continue
        digest = hashlib.sha256(plain).hexdigest()
        filename = f'{frame_offset:08x}-{digest[:12]}.qml'
        (args.output / filename).write_bytes(plain)
        results.append({'offset':frame_offset,'offsetHex':hex(frame_offset),'length':len(plain),'sha256':digest,'file':filename,'terms':terms,'compression':'qCompress/zlib'})
(args.output / 'index.json').write_text(json.dumps({'decodedZstdFrames':count,'decodedZlibFrames':zlib_count,'candidates':results}, indent=2), encoding='utf-8')
print(json.dumps({'decodedZlibFrames':zlib_count,'candidates':results}))
