"""Own C++/MASM/Gum metadata ABI fixture, never runs MAX or opens profiles."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[3]
def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    a=p.parse_args()
    if not a.build.is_absolute() or not a.build.resolve().is_relative_to(Path('D:/ValMAXBuild/build')) or a.report.exists():
        raise ValueError('new proof and explicit owned build required')
    sources=['native/qt/export_history_host.cpp','native/qt/export_history_host.h',
        'native/qt/export_history.cmake','native/qt/export_history_generate_pins.py',
        'native/tests/qt/export_history_host_test.cpp','native/tests/qt/export_history_owned_call.asm',
        'native/tests/qt/run_export_history_host.py','native/core/valmax_core.h',
        'native/hooks/ghost_module.cpp','native/hooks/ghost_internal.h','native/hooks/hook_set.cpp','native/hooks/hook_set.h']
    hashes={name:sha(ROOT/name) for name in sources}
    result=subprocess.run(['cmake','--build',str(a.build),'--target','valmax_export_history_host_test','--parallel','1'],
        cwd=ROOT,capture_output=True,text=True,timeout=180,creationflags=subprocess.CREATE_NO_WINDOW)
    (a.build/'export-history-build.log').write_text(result.stdout+result.stderr,encoding='utf8')
    if result.returncode:
        raise RuntimeError('owned export history compilation failed')
    env=dict(os.environ)
    env['PATH']='C:/ValMAXBuild/Qt/bin'+os.pathsep+env.get('PATH','')
    exe=a.build/'valmax_export_history_host_test.exe'
    result=subprocess.run([str(exe)],cwd=a.build,env=env,capture_output=True,text=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    (a.build/'export-history-test.log').write_text(result.stdout+result.stderr,encoding='utf8')
    if result.returncode:
        raise RuntimeError('owned export history ABI fixture failed')
    value=json.loads(result.stdout)
    assert value['ok'] and value['ownGumCallbackOnly'] and not value['realProfilesTouched'] and not value['realMaxStarted']
    assert hashes=={name:sha(ROOT/name) for name in sources},'source drift'
    proof={'schema':1,'verified':True,'result':value,'sourceHashes':hashes,
        'executable':{'path':str(exe),'sha256':sha(exe)},'build':str(a.build),
        'productionHistoryBound':False,'rustCallbacksStubbed':True,
        'scope':'C++ Gum listener plus real Win64 RDI/return-address ABI on own fake native callback; Rust ABI and MAX binding are separate checks'}
    a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('x',encoding='utf8') as f:json.dump(proof,f,indent=2);f.write('\n')
    print(json.dumps({'verified':True,'checks':value['checks'],'productionHistoryBound':False}))
if __name__=='__main__':main()
