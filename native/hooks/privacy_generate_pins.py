"""Exact build-time pins for existing static privacy patches; no runtime Python."""
from __future__ import annotations
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import pefile
from ghost_generate_pins import constants,require

def rule(relative,identifier):
    if identifier.startswith('no_tracer_'):return 2 if relative.startswith('max-service/') else 1
    if identifier.startswith('no_analytics_'):return 4
    if identifier=='no_mytracker_stats_init':return 8
    if identifier.startswith('mask_logs'):return 16
    if identifier.startswith('no_error_logs'):return 32
    raise ValueError('Unknown static privacy rule')

def generate(runtime:Path,output:Path,proof:Path):
    source=Path(__file__).resolve().parents[2]/'modkit'
    specifications=constants(source/'privacy.py')['PATCH_FILES']
    sys.path.insert(0,str(source));import build_mod
    merged={s['relative_path']:s for s in deepcopy(build_mod.specifications(['accounts','privacy','isolation']))}
    # Only the known offline namespace transform is allowed in QA fixtures.
    # Reversing the full reviewed patch set must recover every vendor SHA.
    namespace='MXMOD'
    fixture=runtime.parent/'build-proof.json'
    if fixture.exists():
        namespace=json.loads(fixture.read_text())['namespace']
        require(re.fullmatch(r'N[0-9A-F]{4}',namespace) is not None,'Invalid QA namespace')
    for item in merged.values():
        for patch in item['patches']:
            if patch['id'].startswith('isolation.'):
                raw=bytes.fromhex(patch['replacement'])
                patch['replacement']=raw.replace(b'MXMOD',namespace.encode()).replace(b'mxmod',namespace.lower().encode()).replace('MXMOD'.encode('utf-16le'),namespace.encode('utf-16le')).hex()
    lines=['// Generated from reviewed privacy patches; do not edit.','#pragma once','#include "privacy_internal.h"','namespace valmax::privacy::pins {']
    files=[]
    manifest=[]
    for index,spec in enumerate(specifications):
        relative=spec['relative_path'];data=(runtime/relative).read_bytes();sha=hashlib.sha256(data).hexdigest()
        original=build_mod.patch_bytes(data,merged[relative]['patches'],reverse=True)
        require(hashlib.sha256(original).hexdigest()==spec['sha256'],'Cannot reverse complete image '+relative)
        pe=pefile.PE(data=data,fast_load=True)
        require(pe.FILE_HEADER.Machine==0x8664 and pe.OPTIONAL_HEADER.Magic==0x20b,'Unsupported PE architecture')
        patches=[];details=[]
        for j,patch in enumerate(spec['patches']):
            raw=bytes.fromhex(patch['replacement']);offset=patch['offset'];rva=pe.get_rva_from_offset(offset)
            section=pe.get_section_by_rva(rva)
            require(section is not None and section.Characteristics&0x20000000 and data[offset:offset+len(raw)]==raw,'Patch bytes mismatch '+patch['id'])
            category=rule(relative,patch['id'])
            lines.append(f'inline constexpr uint8_t kBytes{index}_{j}[] = {{{",".join(map(str,raw))}}};')
            patches.append(f'{{{rva},{offset},{category},kBytes{index}_{j}}}')
            details.append({'id':patch['id'],'rva':rva,'offset':offset,'rule':category,'patchedBytes':raw.hex()})
        lines.append(f'inline constexpr PatchPin kPatches{index}[] = {{{",".join(patches)}}};')
        role=2 if relative.startswith('max-service/') else 1
        files.append(f'{{L"{relative}","{sha}",{len(data)}ull,{pe.OPTIONAL_HEADER.SizeOfImage},{role},kPatches{index}}}')
        manifest.append({'relative':relative,'sha256':sha,'size':len(data),'imageSize':pe.OPTIONAL_HEADER.SizeOfImage,'role':role,'reversedVendorSha256':spec['sha256'],'patches':details})
    manifest_sha=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    lines += [f'inline constexpr FilePin kFiles[] = {{{",".join(files)}}};',f'inline constexpr char kManifestSha256[] = "{manifest_sha}";','}']
    output.parent.mkdir(parents=True,exist_ok=True)
    text='\n'.join(lines)+'\n'
    if not output.exists() or output.read_text()!=text:output.write_text(text,encoding='utf8')
    proof.write_text(json.dumps({'schema':1,'runtimeRoot':str(runtime.resolve()),'namespace':namespace,'files':manifest,
        'manifestSha256':manifest_sha,'sourceSha256':{p:hashlib.sha256((source/p).read_bytes()).hexdigest() for p in ['privacy.py','accounts.py','isolation.py','build_mod.py']},
        'generatedHeaderSha256':hashlib.sha256(output.read_bytes()).hexdigest(),'rules':6,'staticPatches':sum(len(i['patches']) for i in manifest),
        'callbacks':0,'networkObserved':False,'realProfilesAccessed':False},indent=2)+'\n',encoding='utf8')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--proof',type=Path,required=True)
    args=parser.parse_args();generate(args.runtime_root,args.output,args.proof)
