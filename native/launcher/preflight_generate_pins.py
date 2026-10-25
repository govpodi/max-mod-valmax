"""Pin an owned frozen offline runtime at build time. Never used by the launcher."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
import pefile

ROOT=Path(__file__).resolve().parents[2]
ALLOWED={".dll",".exe",".json",".qm",".qmltypes",".qml",".png",".vkmlmodel",""}
def require(value,message):
    if not value:raise ValueError(message)
def sha(data):return hashlib.sha256(data).hexdigest()
def generate(runtime:Path,output:Path,proof:Path):
    runtime=runtime.absolute()
    # This build mode deliberately accepts only a documented owned test copy.
    # Final release generation must supply its own audited provenance workflow.
    require(runtime.name=="runtime" and re.fullmatch(r"probe-[0-9a-f]{32}",runtime.parent.name),"Expected owned frozen probe runtime")
    provenance_path=runtime.parent/"build-proof.json"
    provenance=json.loads(provenance_path.read_text())
    require(provenance.get("fullyReversedNativeFiles")==10 and re.fullmatch(r"N[0-9A-F]{4}",provenance.get("namespace","")),"Invalid offline provenance")
    require(Path(provenance["directory"]).resolve()==runtime.parent.resolve(),"Provenance directory mismatch")
    sys.path.insert(0,str(ROOT/"modkit"))
    import build_mod
    manifest=build_mod.verify(runtime/"mod-manifest.json")
    require(len(manifest["files"])==10 and manifest["max_version"]=="26.35.1.80662","Unexpected native source fixture")
    known={item["relative_path"]:item["sha256"] for item in build_mod.specifications(["accounts","privacy","isolation","ui_privacy"])}
    require({item["relative_path"]:item["sha256"] for item in manifest["files"]}==known,"Unsupported original native identities")
    records=[];directories=[];seen=set();imports={}
    for current,dirs,files in os.walk(runtime,followlinks=False):
        directory=Path(current);dirs.sort();files.sort()
        for name in [*dirs,*files]:
            path=directory/name;info=path.lstat()
            require(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT,"Reparse entry in build fixture")
            require(not path.is_symlink(),"Symlink in build fixture")
            if path.is_dir():directories.append(path.relative_to(runtime).as_posix());continue
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,"Nonregular/hardlinked build file")
            rel=path.relative_to(runtime).as_posix()
            require(path.suffix.lower() in ALLOWED and not any(p.casefold() in {"oneme","mxmod"} for p in path.relative_to(runtime).parts),"Unexpected profile/file in fixture")
            require(path.suffix.lower()!=".json" or rel in {"mod-manifest.json","optimization-active.json"},"Unexpected JSON in runtime")
            require(path.suffix or name=="qmldir","Unexpected extensionless file")
            require(rel.casefold() not in seen,"Case-colliding package paths");seen.add(rel.casefold())
            data=path.read_bytes();records.append({"path":rel,"size":len(data),"sha256":sha(data)})
            if path.suffix.lower() in {".dll",".exe"}:
                pe=pefile.PE(data=data,fast_load=True)
                require(pe.FILE_HEADER.Machine==0x8664 and pe.OPTIONAL_HEADER.Magic==0x20b,"Unexpected PE architecture")
                pe.parse_data_directories(directories=[1,13])
                imports[rel]={"normal":sorted(x.dll.decode("ascii") for x in getattr(pe,"DIRECTORY_ENTRY_IMPORT",[])),
                              "delay":sorted(x.dll.decode("ascii") for x in getattr(pe,"DIRECTORY_ENTRY_DELAY_IMPORT",[]))}
    records.sort(key=lambda row:row["path"])
    require(0<len(records)<=4096 and len(directories)<=4096,"Build inventory capacity")
    require(next(r["sha256"] for r in records if r["path"]=="valmax_core.dll")==provenance["rustSha256"],"Core DLL differs from fixture provenance")
    require(next(r["sha256"] for r in records if r["path"]=="qml/ValMAX/Native/valmaxnativeplugin.dll")==provenance["pluginSha256"],"Qt plugin differs from fixture provenance")
    for name,expected in provenance["qtDllHashes"].items():require(next(r["sha256"] for r in records if r["path"]==name)==expected,"Qt fixture identity mismatch")
    digest=sha(json.dumps(records,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode())
    lines=["// Generated from immutable owned offline fixture; not a release manifest.","#pragma once",'#include "preflight.h"',"namespace valmax::launcher::pins {",
           f'inline constexpr char kManifestSha256[]="{digest}";',"inline constexpr PayloadPin kFiles[]={"]
    for r in records:lines.append(f'{{L{json.dumps(r["path"],ensure_ascii=True)},{r["size"]}ull,"{r["sha256"]}"}},')
    lines.extend(["};","}"]);output.parent.mkdir(parents=True,exist_ok=True)
    text="\n".join(lines)+"\n"
    if not output.exists() or output.read_text()!=text:output.write_text(text,encoding="utf8")
    # Imported names not found among package filenames require OS/component
    # resolution later; dynamic Qt/WebView2 loading is not proven by PE imports.
    basenames={Path(r["path"]).name.lower() for r in records}
    external=sorted({name for bytype in imports.values() for names in bytype.values() for name in names if name.lower() not in basenames})
    report={"schema":1,"runtimeRoot":str(runtime),"scope":"fixed owned offline test fixture; not final release closure",
        "manifestSha256":digest,"provenanceSha256":sha(provenance_path.read_bytes()),"sourceManifestSha256":sha((runtime/"mod-manifest.json").read_bytes()),
        "generatorSha256":sha(Path(__file__).read_bytes()),"files":records,"directories":sorted(directories),"peImports":imports,"externalImportNames":external,
        "fullReleaseClosureVerified":False,"realProfilesAccessed":False,"nativeExecuted":False}
    proof.write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--runtime-root",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--proof",type=Path,required=True)
    a=p.parse_args();generate(a.runtime_root,a.output,a.proof)
