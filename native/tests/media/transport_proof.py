"""Build/run the native QtNetwork transport against owned in-memory replies only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
import pefile

ROOT = Path(__file__).resolve().parents[3]
BUILD = Path("C:/ValMAXBuild/build/media-transport")
RUNTIME = ROOT / "release/valmax-v8.8.4-20261016/build/dist/ValMAX/runtime/mod"
SOURCES = [f"native/media/{name}{suffix}" for name in ("protocol", "scheduler", "transport") for suffix in (".h", ".cpp", ".cmake") if (ROOT / f"native/media/{name}{suffix}").exists()]
SOURCES += ["native/media/html_entities.inc", "native/tests/media/transport_test.cpp", "native/tests/media/transport_proof.py", "native/media/CMakeLists.txt"]
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    before = {name: sha(ROOT / name) for name in SOURCES}
    fixture = Path("C:/ValMAXBuild/fixtures/media-transport") / uuid.uuid4().hex
    fixture.mkdir(parents=True)
    command = "$ErrorActionPreference = 'Stop'\n. D:/ValMAX/native/tooling/native_env.ps1\ncmake --build C:/ValMAXBuild/build/media-transport --target valmax_media_transport_test\nexit $LASTEXITCODE"
    # Process-local permission to run our reviewed development environment;
    # no machine/user execution policy is changed. Terminate on script errors
    # instead of accepting a stale binary after an unavailable compiler command.
    build = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command], capture_output=True, text=True,
                           timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
    (fixture / "build.log").write_text(build.stdout + build.stderr, encoding="utf-8")
    if build.returncode:
        raise RuntimeError("synthetic_transport_build_failed")
    binary = BUILD / "valmax_media_transport_test.exe"
    if any((ROOT / name).stat().st_mtime_ns > binary.stat().st_mtime_ns for name in SOURCES if name.endswith((".cpp", ".h"))):
        raise RuntimeError("stale_native_test_binary")
    exe = fixture / "transport-test.exe"
    exe.write_bytes(binary.read_bytes())
    runtime = {}
    for name in ("Qt6Core.dll", "Qt6Network.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
                 "msvcp140_atomic_wait.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        source = RUNTIME / name
        target = fixture / name
        target.write_bytes(source.read_bytes())
        runtime[name] = sha(target)
        assert runtime[name] == sha(source)
    assert runtime["Qt6Core.dll"] == "22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa"
    pe = pefile.PE(str(exe), fast_load=True)
    pe.parse_data_directories(directories=[1, 13])
    imports = {kind: sorted(item.dll.decode("ascii") for item in getattr(pe, attr, []))
               for kind, attr in (("normal", "DIRECTORY_ENTRY_IMPORT"), ("delay", "DIRECTORY_ENTRY_DELAY_IMPORT"))}
    pe.close()
    assert "Qt6Network.dll" in imports["normal"]
    assert not any(name.lower() in ("max.exe", "core.dll", "network.dll", "qt6gui.dll") for names in imports.values() for name in names)
    env = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "COMSPEC") if name in os.environ}
    env["PATH"] = str(fixture) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32")
    run = subprocess.run([str(exe)], cwd=fixture, env=env, capture_output=True, text=True,
                         timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
    (fixture / "run.log").write_text(run.stdout + run.stderr, encoding="utf-8")
    if run.returncode:
        raise RuntimeError("synthetic_transport_regression_failed")
    result = json.loads(run.stdout)
    assert result["ok"] and result["checks"] > 1757 and not result["realHttp"]
    assert result["individualHeaders"] and result["criticalDuplicateCases"] == 5 and not result["cookiesReused"]
    assert before == {name: sha(ROOT / name) for name in SOURCES}, "source_changed_during_proof"
    proof = {
        "schema": 1, "verified": True, "checks": result["checks"],
        "scope": "Native QtNetwork transport, synthetic owned QNetworkReply streams; fake deadlines and real 20-second timer",
        "sourceSha256": before, "fixture": str(fixture),
        "binary": {"path": str(exe), "sha256": sha(exe), "imports": imports},
        "library": {"path": str(BUILD / "valmax_media_transport.lib"), "sha256": sha(BUILD / "valmax_media_transport.lib")},
        "runtimeSha256": runtime, "result": result,
        "logs": {name: {"path": str(fixture / name), "sha256": sha(fixture / name)} for name in ("build.log", "run.log")},
        "limits": {"sharedSchedulerActive": 2, "redirects": 5, "totalMillisecondsAfterAdmission": 20000,
                   "replyReadBufferBytes": 65536, "apiBytes": 1000000, "previewBytes": 2097152, "originalBytes": 26214400,
                   "parsedHeaderCount": 128, "parsedHeaderAggregateBytes": 65536},
        "covered": [
            "Controlled GET headers, identity encoding, cookies/auth reuse/cache/HTTP2/pipelining disabled",
            "Exact class body limits and limit+1 across API, preview, original; bounded 64KiB reads",
            "Content-Length exact match, MIME/signature validation and expected original metadata",
            "Manual redirect allowlist, relative/absolute raw Location validation before QUrl normalization, dot/percent/UTF8/userinfo attacks",
            "Legitimate percent-encoded space/hash/percent filenames, max5 redirects, old reply deletion does not release the next chain slot",
            "Redirect to null factory and expired budget releases slot; complete stale/cancel suppresses private bytes",
            "Shared full Retry-After cooldown; >day hint omitted, clock jump cannot turn current 429/503 into success",
            "Critical header mutation rejected, cached/decompressed responses rejected, finite auth/TLS failures via synthetic reply",
            "QHttpHeaders individual entries audited before Qt rawHeaderPairs aggregation; two legitimate Set-Cookie entries accepted",
            "All five duplicate critical headers rejected, repeated-name count128/129 and aggregate64KiB bounds enforced per entry",
            "Set-Cookie from synthetic success/redirect never copied to subsequent requests; CookieLoad/SaveManual and EmptyCookieJar remain unchanged",
            "Abort retains active slot until finished/destroyed; no early original result publication",
            "Scheduler destruction with waiting/active/completed-pending jobs delivers async unavailable once after replies stop",
            "Callback deletes transport, transport destructor suppresses callbacks and stops owned replies",
            "Nested destroyed observer pumps queued immediate-finish/poll/orphan MetaCalls safely after derived destruction",
            "Two transports share exactly2 active slots; shutdown affects only its own jobs",
            "Pure first-proxy validation strips user/password/headers, rejects unsupported route without direct fallback",
            "Wrong-thread rejection and production main-thread rejection before proxy lookup or GET",
            "Real Qt20s timer over an unfinished synthetic reply; fake clock20s budget preserved across redirects"
        ],
        "productionImplementationCompiled": True, "realHttpTested": False, "qmlIntegrated": False,
        "messageSendingImplemented": False, "filesOrCredentialsReadByNativeTests": False,
        "limitations": [
            "No live Wikimedia/TLS/proxy/PAC authentication, DNS or real network response was tested. Manager signal wiring is code-reviewed; synthetic replies test response failure handling.",
            "Production requires a dedicated service QThread; synchronous OS/app proxy lookup may block it. Deadline is checked again before GET, but lookup cannot be forcibly interrupted after20s.",
            "First approved route only; failure/authentication never falls back to direct. Multi-proxy failover and proxy credentials are intentionally unavailable.",
            "Header caps apply after Qt HTTP parsing/normalization and cannot bound all parser allocations or recover already-normalized original header octets.",
            "Trusted ReplyFactory must create a fresh same-thread reply parented to owner without reentering/deleting transport; production uses owned QNetworkAccessManager, not this injection seam.",
            "Selected-original serialization/download_busy belongs to MediaController. Disk staging, decoded thumbnails, native draft insertion and QML publication are later layers.",
            "No production bootstrap, installed-client, actual MAX UI or end-to-end @pic/@vid readiness claim. Core DLL and existing user profiles untouched."
        ],
        "primaryReferences": ["https://doc.qt.io/qt-6/qnetworkrequest.html", "https://doc.qt.io/qt-6/qnetworkreply.html",
                              "https://doc.qt.io/qt-6/qnetworkaccessmanager.html", "https://doc.qt.io/qt-6/qnetworkproxyfactory.html",
                              "https://raw.githubusercontent.com/qt/qtbase/v6.11.2/src/network/access/qnetworkrequest.cpp"]
    }
    output = ROOT / "analysis/native-migration-v1/media-transport-proof.json"
    output.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified": True, "checks": result["checks"], "proof": str(output), "sha256": sha(output)}))

if __name__ == "__main__":
    main()
