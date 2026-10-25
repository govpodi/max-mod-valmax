"""Build-only immutable export EOF pins; reads program files, never profiles."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pefile

ROOT = Path(__file__).resolve().parents[2]
def sha(data):
    return hashlib.sha256(data).hexdigest()

def generate(runtime, output, proof):
    # Reuse the reviewed read-only contract, not its JavaScript observer.
    path = ROOT / "modkit/chat_export_end_specs.py"
    spec = importlib.util.spec_from_file_location("reviewed_export_end", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.specification(runtime)  # all original native/Qt guards and exports
    lines = ['// Build-generated export-only metadata guards.', '#pragma once',
             '#include "ghost_internal.h"', 'namespace valmax::export_history_pins {',
             'using valmax::ghost::CodePin; using valmax::ghost::SlotPin; using valmax::ghost::ImagePin;']
    images = {}
    for label, name, guards, slots in (("Main", "max.exe", module.MAX_ANCHORS, module.MAX_VTABLES),
                                     ("Qt", "Qt6Core.dll", module.QT_ANCHORS, ())):
        data = (runtime / name).read_bytes()
        image = pefile.PE(data=data, fast_load=True)
        try:
            for i, (_, expected) in enumerate(guards):
                lines.append(f'inline constexpr uint8_t k{label}Bytes{i}[]={{'+','.join('0x'+expected[n:n+2] for n in range(0,len(expected),2))+'};')
            lines.append(f'inline constexpr CodePin k{label}Guards[]={{'+','.join(f'{{{rva}u,k{label}Bytes{i}}}' for i,(rva,_) in enumerate(guards))+'};')
            if slots:
                lines.append(f'inline constexpr SlotPin k{label}Slots[]={{'+','.join(f'{{{rva}u,{target}u}}' for rva,target in slots)+'};')
                slot_text = f'k{label}Slots'
            else:
                slot_text = '{}'
            lines.append(f'inline constexpr ImagePin k{label}={{"{sha(data)}",{len(data)}ull,{image.OPTIONAL_HEADER.SizeOfImage}u,k{label}Guards,{slot_text}}};')
            images[name] = {'sha256':sha(data),'size':len(data),'imageSize':image.OPTIONAL_HEADER.SizeOfImage}
        finally:
            image.close()
    lines.append('}')
    text = '\n'.join(lines)+'\n'
    output.parent.mkdir(parents=True,exist_ok=True)
    if not output.exists() or output.read_text('utf8') != text:
        output.write_text(text,encoding='utf8')
    proof.write_text(json.dumps({'schema':1,'contract':'valmax-export-history-native-v1','images':images,
        'headerSha256':sha(output.read_bytes()),'sources':{str(path.relative_to(ROOT)):sha(path.read_bytes()),
            str(Path(__file__).relative_to(ROOT)):sha(Path(__file__).read_bytes())},
        'runtimeExecuted':False,'realProfilesTouched':False},indent=2)+'\n',encoding='utf8')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--proof',type=Path,required=True)
    a=p.parse_args();generate(a.runtime_root,a.output,a.proof)
