"""Build-time only: exact notification module pins and fixed transparent asset."""
from __future__ import annotations
import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path
import pefile
from ghost_generate_pins import constants, require


def generate(runtime: Path, output: Path, proof: Path):
    source = Path(__file__).resolve().parents[2] / 'modkit/notification_native.py'
    specification = constants(source)['FIXED']
    lines = ['// Generated; do not edit.', '#pragma once', '#include "notification_internal.h"',
             'namespace valmax::notifications::pins {']
    report = {}
    for name, spec in zip(('Main', 'Backend'), specification['modules']):
        data = (runtime / spec['name']).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if name == 'Backend':
            require(sha == spec['baselineSha256'] and len(data) == spec['baselineSize'], 'Unsupported notification backend')
        pe = pefile.PE(data=data, fast_load=True)
        require(pe.FILE_HEADER.Machine == 0x8664 and pe.OPTIONAL_HEADER.Magic == 0x20b, 'Unsupported PE architecture')
        for i, pin in enumerate(spec['pins']):
            raw = bytes.fromhex(pin['expected'])
            section = pe.get_section_by_rva(pin['rva'])
            require(section is not None and section.Characteristics & 0x20000000
                    and pe.get_data(pin['rva'], len(raw)) == raw, 'Instruction mismatch: ' + pin['role'])
            lines.append(f'inline constexpr uint8_t k{name}Bytes{i}[] = {{{",".join(str(x) for x in raw)}}};')
            lines.append(f'inline constexpr uint32_t k{pin["role"][0].upper()+pin["role"][1:]}Rva = {pin["rva"]};')
        lines.append(f'inline constexpr CodePin k{name}Guards[] = {{' + ','.join(f'{{{p["rva"]},k{name}Bytes{i}}}' for i,p in enumerate(spec['pins'])) + '};')
        slots = []
        for slot in spec['slots']:
            rva, target = slot['vtableRva'] + slot['slot'], slot['thunkRva']
            require(pe.get_data(rva, 8) == (pe.OPTIONAL_HEADER.ImageBase + target).to_bytes(8,'little'), 'Vtable mismatch')
            slots.append(f'{{{rva},{target}}}')
        lines.append(f'inline constexpr SlotPin k{name}Slots[] = {{{",".join(slots)}}};')
        lines.append(f'inline constexpr ImagePin k{name} = {{"{sha}",{len(data)}ull,{pe.OPTIONAL_HEADER.SizeOfImage},k{name}Guards,k{name}Slots}};')
        report[spec['name']] = {'sha256':sha,'size':len(data),'codeGuards':len(spec['pins']),'vtableGuards':len(slots)}
    for key,value in specification['layout'].items():
        if isinstance(value,int):
            lines.append(f'inline constexpr uint32_t k{key[0].upper()+key[1:]} = {value};')
        else:
            lines.append(f'inline constexpr uint32_t k{key[0].upper()+key[1:]}[] = {{{",".join(map(str,value))}}};')
    def chunk(name,data):
        return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data))
    png = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'*5,9))+chunk(b'IEND',b'')
    sha = hashlib.sha256(png).hexdigest()
    filename = 'valmax-private-notification-'+sha[:16]+'.png'
    lines += [f'inline constexpr uint8_t kAsset[] = {{{",".join(map(str,png))}}};',
              f'inline constexpr wchar_t kAssetName[] = L"{filename}";', '}']
    output.parent.mkdir(parents=True,exist_ok=True)
    text = '\n'.join(lines)+'\n'
    if not output.exists() or output.read_text() != text:
        output.write_text(text,encoding='utf8')
    (output.parent / filename).write_bytes(png)
    proof.write_text(json.dumps({'schema':1,'runtimeRoot':str(runtime.resolve()),'images':report,
        'specSha256':hashlib.sha256(source.read_bytes()).hexdigest(),'asset':{'name':filename,'sha256':sha,'size':len(png)},
        'generatedSha256':hashlib.sha256(output.read_bytes()).hexdigest(),'realProfilesAccessed':False},indent=2)+'\n',encoding='utf8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--proof',type=Path,required=True)
    args=parser.parse_args()
    generate(args.runtime_root,args.output,args.proof)
