"""Own native worker-queue fixture; no network, account data or window."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import pefile
from thumbnail_proof import ROOT, RUNTIME, sha

def main():
    build=Path('C:/ValMAXBuild/build/media-decode-queue')
    fixture=Path('C:/ValMAXBuild/build/media-decode-fixtures')/uuid.uuid4().hex
    fixture.mkdir(parents=True)
    pending=[RUNTIME/'Qt6Core.dll',RUNTIME/'Qt6Gui.dll']; hashes={}
    while pending:
        source=pending.pop()
        if source.name in hashes:continue
        target=fixture/source.name
        shutil.copy2(source,target);hashes[source.name]=sha(target)
        with pefile.PE(str(source),fast_load=True) as pe:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT']])
            for item in list(getattr(pe,'DIRECTORY_ENTRY_IMPORT',[]))+list(getattr(pe,'DIRECTORY_ENTRY_DELAY_IMPORT',[])):
                child=RUNTIME/item.dll.decode('ascii')
                if child.is_file():pending.append(child)
    assert hashes['Qt6Core.dll']=='22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa'
    assert hashes['Qt6Gui.dll']=='19265a4e76efc12ef5f5bcf59be6c6a5b9b59ec4c89660797738bd2da7f719cc'
    exe=fixture/'valmax_media_decode_queue_test.exe'
    shutil.copy2(build/exe.name,exe)
    env={k:v for k,v in os.environ.items() if not k.upper().startswith(('QT_','QML_','PYTHON'))}
    env['PATH']=str(fixture)+';C:/Windows/System32;C:/Windows'
    run=subprocess.run([str(exe)],cwd=fixture,env=env,capture_output=True,text=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    if run.returncode:raise RuntimeError(f'decode test failed {run.returncode}: {run.stdout}\n{run.stderr}')
    result=json.loads(run.stdout);assert result['passed'] and result['qt']=='6.11.2'
    sources=['native/media/decode_queue.h','native/media/decode_queue.cpp','native/media/decode_queue.cmake',
             'native/media/thumbnail.h','native/media/thumbnail.cpp','native/media/protocol.cpp','native/media/protocol.h',
             'native/media/CMakeLists.txt','native/tests/media/decode_queue_test.cpp',
             'native/tests/media/decode_queue_proof.py','native/tests/media/thumbnail_proof.py']
    proof=dict(schema=1,passed=True,native=result,fixture=str(fixture),runtimeFiles=hashes,
               executableSha256=sha(exe),librarySha256=sha(build/'valmax_media_decode_queue.lib'),
               sources={p:sha(ROOT/p) for p in sources},stderr=run.stderr,installedClientModified=False,
               limitations=['Owner-thread event loop must stay alive through asynchronous drain.',
                   'Two active decoders and eight waiting jobs; cancellation cannot interrupt a running image codec.',
                   'Destructor waits for active workers; no hard codec execution-time bound or GUI-shutdown latency claim.',
                   'Only decoded-image callbacks are proved. Controller stamps, preview provider wiring and draft attachment remain separate.'])
    output=ROOT/'analysis/native-migration-v1/media-decode-queue-proof.json'
    output.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(passed=True,checks=result['checks'],proof=str(output),fixture=str(fixture))))

if __name__=='__main__':main()
