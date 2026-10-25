"""Read-only source/table review evidence; does not execute the native ABI."""
import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path("C:/ValMAXBuild/fixtures/startup-ffi-review")
FILES = ["native/core/src/startup_ffi.rs", "native/core/src/startup_status.rs", "native/core/valmax_core.h",
         "native/core/src/support_diagnostics.rs", "native/core/src/settings_io.rs",
         "native/launcher/startup_client.cpp", "native/launcher/startup_client.h",
         "native/tests/launcher/startup_client_test.cpp", "native/tests/launcher/startup_integration_test.cpp",
         "native/tests/launcher/startup_client_proof.py", "modkit/startup_status.py"]
sources = {name: (ROOT / name).read_bytes() for name in FILES}
legacy = {}
for node in ast.parse(sources["modkit/startup_status.py"]).body:
    if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        continue
    name = node.targets[0].id
    if name in ("ROLES", "STAGES", "STATES"):
        legacy[name] = list(ast.literal_eval(node.value.args[0]))
    elif name == "_MESSAGES":
        legacy["STARTUP_CODES"] = list(ast.literal_eval(node.value))
rust = sources["native/core/src/support_diagnostics.rs"].decode("utf-8")
tables = {}
for name in ("ROLES", "STAGES", "STATES", "STARTUP_CODES"):
    content = re.search(r"const " + name + r":.*?=\s*&\[(.*?)\];", rust, re.S).group(1)
    values = re.findall(r'"([a-z_]+)"', content)
    assert values == legacy[name], name
    tables[name] = {"count": len(values), "orderedValues": values, "legacyAstEqualsRust": True}
proof_path = ROOT / "analysis/native-migration-v1/startup-client-proof.json"
proof_raw = proof_path.read_bytes()
proof = json.loads(proof_raw)
regression_path = ROOT / "analysis/native-migration-v1/core-startup-regression-proof.json"
regression_raw = regression_path.read_bytes()
regression = json.loads(regression_raw)
assert regression["ok"] and regression["passed"] == 142 and regression["failed"] == 0 and regression["ignored"] == 0
assert regression["dll"]["sha256"] == proof["corePair"]["valmax_core.dll"]
hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
for name, digest in hashes.items():
    assert proof["sourceSha256"][name] == digest, name
    if name in regression["sourceSnapshotAtReportTime"]:
        assert regression["sourceSnapshotAtReportTime"][name] == digest, name
OUT.mkdir(parents=True, exist_ok=True)
table_path = OUT / "finite-table-comparison.json"
table_path.write_text(json.dumps(tables, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
report = {"schema": 1, "reviewKind": "independent read-only source review plus finite-table comparison",
          "reviewerExecutedNativeTests": False, "blockers": [], "sourceSha256": hashes,
          "proof": {"path": str(proof_path), "sha256": hashlib.sha256(proof_raw).hexdigest(),
                    "syntheticAdapterChecks": proof["syntheticAdapter"]["checks"],
                    "nativeFirstChecks": proof["nativeFirst"]["checks"], "pythonFirstChecks": proof["pythonFirst"]["checks"],
                    "coreSha256": proof["corePair"]["valmax_core.dll"]},
          "parentFullRegression": {"path": str(regression_path), "sha256": hashlib.sha256(regression_raw).hexdigest(),
                                   "passed": 142, "failed": 0, "ignored": 0, "sameExactCore": True},
          "tableComparison": {"path": str(table_path), "sha256": hashlib.sha256(table_path.read_bytes()).hexdigest()},
          "conclusions": [
              "Both ABI structs are repr(C), 64 bytes; eight u32 plus a 32-byte lower-hex operation ID. C++ and Rust pin sizes and offsets.",
              "Pointers are trusted aligned disjoint host buffers valid for the full input/struct lifetime. Null/length bounds are checked before input slicing; non-null output is initialized. Arbitrary dangling pointers are outside the ABI contract.",
              "Rust validates exact v1 size/action/role/stage/state/code/claim and UTF-8/path shape before invoking the IO closure. StartupStatus::new uses lexical SafeStorePath and the supplied operation ID, so this precheck performs no filesystem or entropy IO.",
              "Read requires zero stage/state/code/claim. Claim is restricted to non-observe roles in starting state. Error state iff code is nonzero. Numeric legacy tables match Python AST order and Rust arrays exactly.",
              "Storage reads filter exact operation ID, stale children cannot overwrite a different operation, first error is sticky, and malformed receipts are not replaced. Existing per-file mutex and atomic revalidation remain underneath this new ABI.",
              "No allocation crosses the receipt ABI. Result errors are finite 0..6; receipt fields are zero except outcome2. Owned JSON is limited to small non-secret startup receipts, not profiles/PIN/tokens.",
              "C++ validates bounded Unicode conversion and request enums before calling the trusted function. It validates every result field, state/code consistency and exact requested operation ID before accepting a receipt.",
              "No QML or Runtime dispatch route registers this host-only receipt API. No process, UI, credential or startup-permission action is implemented by this adapter.",
              "Receipt persistence errors cannot establish readiness or override primary launch failure; this remains an explicit caller integration contract."
          ],
          "actualTestHoles": [
              "No new independent native execution in this review; 142 full Rust tests, 10 targeted Rust tests and the supplied C++/interop proof are reported as parent evidence only.",
              "Cross-process simultaneous legacy/native mutex contention, disk-full/fsync fault injection and partial publication failures were not added by this ABI test suite.",
              "Production launcher bootstrap/UI delivery and protection-readiness ordering remain outside this receipt layer.",
              "C++ tests exercise malformed responses extensively but do not prove OS SEH recovery from an invalid function pointer; only a verified loaded core function is permitted.",
              "31,000-byte bound acceptance at its exact limit and a caught Rust panic output are not directly exercised; source checks cover their branches."
          ]}
destination = ROOT / "analysis/native-migration-v1/startup-ffi-review.json"
destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"reviewed": True, "blockers": 0, "tables": {k: v["count"] for k, v in tables.items()},
                  "report": str(destination)}))
