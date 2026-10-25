"""Synthetic Python/Rust MData format and Unicode differential checks.

Build `cargo build --release --locked --offline -p valmax-core --example
mdata_interop_driver` first. Generated artifacts remain in C:/ValMAXBuild.
No profile/credential discovery, user data or network access.
"""
from __future__ import annotations
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "modkit"))
import mdata_archive as py
from session_archive import ArchivePolicy

DRIVER = Path("C:/ValMAXBuild/target/release/examples/mdata_interop_driver.exe")
ARTIFACTS = Path("C:/ValMAXBuild/fixtures/mdata-stream-v1")
PASSWORD = "Synthetic-MData-Password-123"
CREDENTIAL_POLICY = {"credential_names": ["access_token_42@MXMOD"]}
FILES = {"user42/История.db": b"SYNTHETIC-DATABASE\0" * 125000,
         "settings/привет%.ini": "искусственный профиль".encode(), "user42/пустой.txt": b""}
MANIFEST = {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
            "credentials": [{"name": "access_token_42@MXMOD", "blob": base64.b64encode(b"SYNTHETIC-NOT-REAL").decode()}],
            "files": [{"path": path, "size": len(data)} for path, data in FILES.items()]}
POLICY = py.MDataPolicy(ArchivePolicy(credential_names=tuple(CREDENTIAL_POLICY["credential_names"])),
                       lambda path: path in FILES)
CHECKS = []


def record(name, passed=True):
    CHECKS.append({"name": name, "passed": bool(passed)})
    if not passed:
        raise AssertionError(name)


def rust(action, **kwargs):
    request = {"action": action, "password": PASSWORD, "credentialPolicy": CREDENTIAL_POLICY,
               "allowedPaths": list(FILES), **kwargs}
    completed = subprocess.run([str(DRIVER)], input=json.dumps(request, ensure_ascii=False).encode(),
                               capture_output=True, check=True, timeout=45)
    return json.loads(completed.stdout)


class Staging(io.BytesIO):
    def close(self):
        pass  # test-owned memory remains inspectable after factory context exit


def python_decrypt(archive, **options):
    outputs = {}
    def sink(path, size):
        outputs[path] = Staging()
        return outputs[path]
    try:
        manifest = py.decrypt_stream(io.BytesIO(archive), PASSWORD, sink, policy=POLICY, **options)
        return {"ok": True, "manifest": manifest, "files": {p: s.getvalue() for p, s in outputs.items()}}
    except py.MDataArchiveError as error:
        return {"ok": False, "error": error.code}


def compare_error(name, archive, **kwargs):
    native = rust("decrypt", archive=base64.b64encode(archive).decode(), **kwargs)
    reference = python_decrypt(archive, **({"expected_kind": kwargs["expectedKind"]} if "expectedKind" in kwargs else {}))
    record(name, native.get("error") == reference.get("error") and not native["ok"] and not reference["ok"])


def parts(archive):
    offset = 10 + struct.unpack(">H", archive[8:10])[0]
    chunks, position = [], offset
    while position < len(archive):
        length = struct.unpack(">I", archive[position:position + 4])[0]
        end = position + length + 21
        chunks.append(archive[position:end])
        position = end
    return archive[:offset], chunks


def edit_header(archive, callback):
    header, chunks = parts(archive)
    value = json.loads(header[10:])
    callback(value)
    raw = py._canonical(value)
    return header[:8] + struct.pack(">H", len(raw)) + raw + b"".join(chunks)


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    archives = {}
    for kind in ("profile", "login"):
        destination = io.BytesIO()
        summary = py.encrypt_stream(MANIFEST, PASSWORD, lambda path: io.BytesIO(FILES[path]), destination,
                                    policy=POLICY, archive_kind=kind)
        archive = destination.getvalue()
        archives[kind] = archive
        (ARTIFACTS / f"python-{kind}.mdata").write_bytes(archive)
        response = rust("decrypt", archive=base64.b64encode(archive).decode(), expectedKind=kind)
        record(f"Python to Rust {kind} manifest", response["ok"] and response["result"]["manifest"] == MANIFEST)
        record(f"Python to Rust {kind} files", {p: base64.b64decode(b) for p,b in response["result"]["files"].items()} == FILES)
        response = rust("encrypt", manifest=MANIFEST, files={p: base64.b64encode(b).decode() for p,b in FILES.items()}, kind=kind)
        native_archive = base64.b64decode(response["result"]["archive"])
        (ARTIFACTS / f"rust-{kind}.mdata").write_bytes(native_archive)
        decoded = python_decrypt(native_archive, expected_kind=kind)
        record(f"Rust to Python {kind} manifest/files", decoded["ok"] and decoded["manifest"] == MANIFEST and decoded["files"] == FILES)
        record(f"Summary contract {kind}", response["result"]["summary"] == summary)

    archive = archives["profile"]
    header, chunks = parts(archive)
    for end in (0, 7, 9, 10, len(header)-1, len(header), len(archive)-1, len(archive)-21):
        compare_error(f"Truncated {end}", archive[:end])
    compare_error("Trailing byte", archive+b"x")
    compare_error("Kind mismatch", archive, expectedKind="login")
    compare_error("Magic v1/v2 swap", py.LOGIN_MAGIC+archive[8:])
    compare_error("Reordered data frames", header+chunks[1]+chunks[0]+b"".join(chunks[2:]))
    compare_error("Duplicated data frame", header+chunks[0]+b"".join(chunks))
    compare_error("Corrupted terminal", archive[:-1]+bytes([archive[-1]^1]))
    for length, flag in ((py.CHUNK_BYTES+1,0),(1,2),(1,1),(0,0)):
        changed = struct.pack(">IB",length,flag)+chunks[0][5:]
        compare_error(f"Frame shape {length}/{flag}", header+changed+b"".join(chunks[1:]))
    for field, value in (("n",2**40),("n",True),("r",4096),("p",2048),("name","other"),("extra",0)):
        compare_error(f"KDF {field}/{value}", edit_header(archive, lambda h,f=field,v=value:h["kdf"].__setitem__(f,v)))
    for field, value in (("schema",2),("chunkBytes",True),("plaintextBytes",py.MAX_PLAINTEXT_BYTES+1),("sourceVersion","other"),("extra",0)):
        compare_error(f"Header {field}/{value}", edit_header(archive, lambda h,f=field,v=value:h.__setitem__(f,v)))

    # Exhaustively compare every Unicode scalar, rather than a hand-picked
    # lowercase approximation; surrogates are invalid UTF-8 in both codecs.
    all_scalars = ''.join(chr(n) for n in range(0x110000) if not 0xD800 <= n <= 0xDFFF)
    response = rust("casefold", text=all_scalars)
    record("Casefold all 1,112,064 Unicode scalars", response["ok"] and response["result"]["text"] == all_scalars.casefold())
    record("Unicode version pinned", response["result"]["unicodeVersion"] == unicodedata.unidata_version == "16.0.0")
    paths = ["a", "профиль/История.db", " leading/file", "emoji/😀.png", "a/CONıN$", "com¹.ini", "LPT²", "CLOCK$",
             "a/con.txt", "a/PRN .txt", "a\u0085b", "a\x00b", "a/../b", "a//b", "%2e%2e/file", "a%2fb", "/abs", "a:", "a\\b", "a?b",
             "😀"*127, "😀"*128, "я"*255, "я"*256, "a." ,"a ", "Straße.txt", "ς.txt", "ﬃ.txt", "İ.txt"]
    response = rust("paths", paths=paths)
    record("Unicode and Windows path differential", response["result"]["valid"] == [py.valid_path(path) for path in paths])
    for first, second in (("Straße","STRASSE"),("ς","Σ"),("ﬃ","FFI"),("İ","i\u0307"),("k","K")):
        manifest = copy.deepcopy(MANIFEST)
        manifest["files"] = [{"path": first, "size": 0}, {"path": second, "size": 0}]
        response = rust("validate", manifest=manifest, allowedPaths=[first,second])
        record(f"Duplicate casefold {first}", response == {"ok":False,"error":"invalid_payload"})
    report = {"schema":1,"scope":"Pure MData v1/v2 format and policy; synthetic data only", "checks":CHECKS,
              "passed":sum(item["passed"] for item in CHECKS),"failed":sum(not item["passed"] for item in CHECKS),
              "unicodeVersion":unicodedata.unidata_version,"realProfilesAccessed":False,
              "fullMigrationOrInstallerVerified":False,
              "sources":{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in
                  ("modkit/mdata_archive.py","modkit/session_archive.py","native/core/src/mdata_archive.rs",
                   "native/core/src/session_archive.rs","native/core/src/mdata_casefold.rs",
                   "native/core/tests/mdata_interop.py","native/core/tests/mdata_archive_contract.rs")},
              "limits":["Staging commit/profile installation remain separate caller responsibilities",
                        "4 GiB limit tested through manifest metadata, not a 4 GiB allocation",
                        "Best-effort zeroization reviewed by code paths; heap remnants not probed"]}
    (ARTIFACTS/"proof.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"MData interop: {report['passed']} passed, {report['failed']} failed; proof {ARTIFACTS/'proof.json'}")


if __name__ == "__main__":
    main()
