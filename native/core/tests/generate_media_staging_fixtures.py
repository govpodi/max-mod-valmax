"""Extract only the pure legacy magic predicate; never import the old service."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modkit/inline_media.py"
OUTPUT = Path(__file__).with_name("media_staging_fixtures.json")

def generate() -> dict:
    source = SOURCE.read_bytes()
    tree = ast.parse(source.decode("utf-8-sig"))
    pure = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "valid_content")
    # No imports, decorators, defaults or service initialization are executed.
    assert not pure.decorator_list and not pure.args.defaults
    scope = {}
    exec(compile(ast.Module(body=[pure], type_ignores=[]), str(SOURCE), "exec"), scope)
    constants = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            name = n.targets[0].id
            if name in {"MAX_FILE_BYTES", "MAX_CACHE_BYTES", "STAGING_TTL_SECONDS"}:
                assert all(isinstance(x, (ast.Expression, ast.BinOp, ast.Mult, ast.Constant)) for x in ast.walk(ast.Expression(n.value)))
                constants[name] = eval(compile(ast.Expression(n.value), "<constant>", "eval"), {"__builtins__": {}})
    samples = [b"\xff\xd8\xffPUBLIC", b"\x89PNG\r\n\x1a\nPUBLIC", b"GIF87aPUBLIC", b"GIF89aPUBLIC", b"RIFF0000WEBPPUBLIC", b"\x1a\x45\xdf\xa3webmPUBLIC"]
    samples += [b"0000ftyp" + brand + b"PUBLIC" for brand in (b"isom",b"iso2",b"mp41",b"mp42",b"avc1",b"M4V ",b"avif",b"heic",b"junk")]
    samples += [b"\x1a\x45\xdf\xa3" + b"0" * n + b"webm" for n in (4087,4088,4089,4092)]
    mimes = ["image/jpeg","image/png","image/gif","image/webp","video/webm","video/mp4","text/html","IMAGE/PNG"]
    inputs = [(data,mime) for data in samples for mime in mimes]
    inputs += [(data[:n],mime) for data in samples[:6] for n in range(len(data)) for mime in mimes[:6]]
    rng=random.Random(0x56414c)
    inputs += [(rng.randbytes(rng.randint(1,80)), rng.choice(mimes)) for _ in range(64)]
    cases=[{"hex":data.hex(),"mime":mime,"valid":bool(scope["valid_content"](data,mime))} for data,mime in inputs]
    return {"schema":1,"sourceSha256":hashlib.sha256(source).hexdigest(),"predicate":"valid_content","constants":constants,"cases":cases}

if __name__ == "__main__":
    result = generate()
    OUTPUT.write_text(json.dumps(result,ensure_ascii=True,separators=(",",":"))+"\n",encoding="utf-8")
    print(json.dumps({"cases":len(result["cases"]),"fixtureSha256":hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}))
