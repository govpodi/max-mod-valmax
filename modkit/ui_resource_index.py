"""Read-only static Qt RCC table decoder for MAX 26.35.1 x64."""
from __future__ import annotations
import argparse
import json
import re
import struct
from pathlib import Path
import pefile


def index_resources(binary: Path) -> dict:
    blob = binary.read_bytes()
    pe = pefile.PE(data=blob, fast_load=True)
    text = pe.sections[0]
    code = blob[text.PointerToRawData:text.PointerToRawData + text.SizeOfRawData]
    def raw(rva):
        return pe.get_offset_from_rva(rva)
    def be32(off):
        return struct.unpack_from('>I', blob, off)[0]
    def be16(off):
        return struct.unpack_from('>H', blob, off)[0]
    tables = []
    files = []
    seen = set()
    for match in re.finditer(b'\xe8', code):
        i = match.start()
        rva = text.VirtualAddress + i
        if i + 5 > len(code) or rva + 5 + struct.unpack_from('<i', code, i + 1)[0] != 0x12910cb:
            continue
        # Compiler-generated qInitResources routine, version 3.
        if code[i-26:i-23] != b'\x4c\x8d\x0d' or code[i-19:i-14] != b'\xb9\x03\x00\x00\x00' or code[i-14:i-11] != b'\x4c\x8d\x05' or code[i-7:i-4] != b'\x48\x8d\x15':
            continue
        data_rva = rva - 19 + struct.unpack_from('<i', code, i - 23)[0]
        name_rva = rva - 7 + struct.unpack_from('<i', code, i - 11)[0]
        tree_rva = rva + struct.unpack_from('<i', code, i - 4)[0]
        if tree_rva in seen:
            continue
        seen.add(tree_rva)
        tree, names, data = raw(tree_rva), raw(name_rva), raw(data_rva)
        table = dict(call_rva=rva, tree_rva=tree_rva, tree_raw=tree, names_rva=name_rva, names_raw=names, data_rva=data_rva, data_raw=data, version=3, file_count=0, node_count=0)
        table_id = len(tables)
        visited = set()
        def walk(idx, prefix):
            if idx in visited or idx > 100000:
                raise ValueError('invalid cyclic RCC tree')
            visited.add(idx)
            off = tree + idx * 22
            name = names + be32(off)
            nlen = be16(name)
            if nlen > 4096:
                raise ValueError('invalid RCC name length')
            label = blob[name+6:name+6+nlen*2].decode('utf-16-be')
            path = prefix + '/' + label if idx else ''
            flags = be16(off + 4)
            if flags & 2:
                count, first = be32(off+6), be32(off+10)
                if count > 50000:
                    raise ValueError('invalid RCC child count')
                for child in range(first, first+count):
                    walk(child, path)
            else:
                offset = be32(off+10)
                payload = data + offset
                length = be32(payload)
                files.append(dict(table=table_id,node=idx,path=':'+path,flags=flags,length=length,payload_raw=payload+4,payload_rva=data_rva+offset+4,length_raw=payload,data_offset=offset,tree_entry_raw=off,tree_entry_rva=tree_rva+idx*22))
                table['file_count'] += 1
        walk(0, '')
        table['node_count'] = len(visited)
        tables.append(table)
    return dict(binary=str(binary),tables=tables,files=files)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('binary', type=Path)
    parser.add_argument('output', type=Path)
    args=parser.parse_args()
    result=index_resources(args.binary)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"{len(result['tables'])} resource tables, {len(result['files'])} resource files")
    for item in result['files']:
        if any(s in item['path'].lower() for s in ['settings','account','privacy']):
            print(item['path'],hex(item['payload_raw']),item['length'],item['flags'])


if __name__ == '__main__':
    main()
