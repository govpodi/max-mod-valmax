"""Differential checks for pure launcher config; never import/launch the launcher.

Only seven selected data functions and three constants are compiled from its AST.
The only subprocess is the synthetic Rust JSON-lines example. Fixtures and proof
are written under C:/ValMAXBuild; installed configuration is never inspected.
"""
from __future__ import annotations

import argparse
import ast
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path("C:/ValMAXBuild")
FUNCTIONS = {"_unique_object", "_invalid_json_constant", "_parse_config",
             "_config_template", "make_config", "validate_config", "_local_root"}
CONSTANTS = {"CONFIG_TEMPLATE_SHA256", "CONFIG_DEFAULTS", "MAX_CONFIG_BYTES"}


def oracle():
    source = ROOT / "modkit/launch_mod.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    selected = [node for node in tree.body if
                isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS or
                isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id in CONSTANTS
                    for target in node.targets)]
    namespace = {"json": json, "hashlib": hashlib, "Path": Path, "os": os,
                 "CONFIG_TEMPLATE_PATH": ROOT / "modkit/config.defaults.json"}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    assert FUNCTIONS <= namespace.keys() and CONSTANTS <= namespace.keys()
    return namespace


def encode(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def run(executable: Path, output: Path, native_test_log: Path | None):
    output = output.resolve()
    output.relative_to(BUILD.resolve())
    output.mkdir(parents=True, exist_ok=True)
    py = oracle()
    baseline = py["make_config"]()
    cases = []

    def add(name, data, difference=None):
        try:
            expected = {"ok": True, "value": py["validate_config"](data)}
        except (ValueError, RecursionError):
            expected = {"ok": False}
        cases.append((name, {"op": "validate", "data": base64.b64encode(data).decode()}, expected, difference))

    cases.append(("make_full_74", {"op": "make"}, {"ok": True, "value": baseline}, None))
    candidates = [None, True, False, 0, -1, 1.0, "", "synthetic", [], ["a", "\u044f", "\U0001f980"], [0], {}, {"synthetic": 1}]
    for key in baseline:
        value = copy.deepcopy(baseline)
        del value[key]
        add(f"missing:{key}", encode(value))
        for index, candidate in enumerate(candidates):
            value = copy.deepcopy(baseline)
            value[key] = candidate
            add(f"type_value:{key}:{index}", encode(value))

    valid = encode(baseline)
    prefix = valid[:-1]
    add("valid", valid)
    add("valid_utf8_unicode", encode({**baseline, "synthetic": "\u041f\u0440\u0438\u0432\u0435\u0442 \U0001f980"}).decode().encode())
    add("valid_raw_utf8", json.dumps({**baseline, "synthetic": "\u041f\u0440\u0438\u0432\u0435\u0442 \U0001f980"}, ensure_ascii=False).encode())
    add("bom", b"\xef\xbb\xbf" + valid)
    add("bom_boundary", (b"\xef\xbb\xbf" + valid).ljust(65536, b" "))
    add("boundary", valid.ljust(65536, b" "))
    add("over_bound", valid.ljust(65537, b" "))
    add("double_bom", b"\xef\xbb\xbf\xef\xbb\xbf" + valid)
    for name, data in [
        ("empty", b""), ("invalid_utf8", b"\xff"), ("utf16", "{}".encode("utf-16")),
        ("array", b"[]"), ("null", b"null"), ("bool", b"true"), ("string", b'"x"'),
        ("trailing", valid + b"false"), ("sparse", b'{"app_folder_name":"MXMOD"}'),
        ("duplicate", prefix + b',"ssl_port":443}'),
        ("escaped_duplicate", prefix + b',"ssl_\\u0070ort":443}'),
        ("nested_duplicate", prefix + b',"extra":{"a":1,"a":2}}'),
        ("escaped_nested_duplicate", prefix + b',"extra":{"a":1,"\\u0061":2}}'),
        ("nul_raw", prefix + b',"extra":"\x00"}'),
        ("bad_escape", prefix + b',"extra":"\\q"}'),
        ("comment", prefix + b',"extra":1/* comment */}'),
    ]:
        add(name, data)
    for token in ["NaN", "Infinity", "-Infinity", "01", "+1", "1.", "1e", "--1", ".1"]:
        add(f"invalid_number:{token}", prefix + f',"extra":{token}}}'.encode())
    for token in ["0", "-0", "9223372036854775807", "-9223372036854775808", "18446744073709551615"]:
        value = copy.deepcopy(baseline)
        value.pop("ssl_port")
        add(f"integer:{token}", encode(value)[:-1] + f',"ssl_port":{token}}}'.encode())
    for token in ["0.0", "-0.0", "1e0", "1E+0"]:
        value = copy.deepcopy(baseline)
        value.pop("ssl_port")
        add(f"float_not_int:{token}", encode(value)[:-1] + f',"ssl_port":{token}}}'.encode())
    for token in ["18446744073709551616", "-9223372036854775809", "999999999999999999999999"]:
        add(f"native_integer_bound:{token}", prefix + f',"extra":{token}}}'.encode(), "bounded_i64_u64")
    for token in ["1e9999", "-1e9999"]:
        add(f"native_finite:{token}", prefix + f',"extra":{token}}}'.encode(), "finite_numbers_only")
    for token in ['"\\ud800"', '"\\udfff"']:
        add(f"native_unicode:{token}", prefix + f',"extra":{token}}}'.encode(), "unicode_scalars_only")
    add("numeric_strings", prefix + b',"extra":"18446744073709551616 \\\" -0"}')
    for depth in [1, 8, 32, 63]:
        add(f"depth:{depth}", prefix + b',"extra":' + b'[' * depth + b'0' + b']' * depth + b'}')
    add("depth:65", prefix + b',"extra":' + b'[' * 65 + b'0' + b']' * 65 + b'}', "depth_64")

    paths = ["C:/", "C:/synthetic", "d:/synthetic/Local/", "C:\\Synthetic\\Local\\", "C:/\u0422\u0435\u0441\u0442 \U0001f980/Local",
             "C:/synthetic-ONEME-label", "", "relative", "C:relative", "/root", "//server/share", "\\\\?\\C:\\fixture",
             "C:/ONEME", "C:/fixture/oneme/child", "D:/oNeMe", "C:/fixture/..", "C:/fixture/trailing.",
             "C:/fixture/trailing ", "C:/fixture/name:stream"]
    stricter_paths = ["C:/fixture/./child", "C:/fixture//child", "C://", "C:/fixture/NUL", "C:/fixture/COM1.txt",
                      "C:/fixture/\x7f", "C:/fixture/\x00", "C:/fixture/ab*cd", "C:/fixture/a?b"]
    for path in paths + stricter_paths:
        try:
            expected = {"ok": True, "value": str(py["_local_root"](Path(path)) / "MXMOD" / "config.json")}
        except ValueError:
            expected = {"ok": False}
        cases.append((f"path:{path!r}", {"op": "path", "root": path}, expected,
                      "strict_lexical_path" if path in stricter_paths else None))

    requests = "".join(json.dumps(request, ensure_ascii=True) + "\n" for _, request, _, _ in cases)
    (output / "requests.jsonl").write_text(requests, encoding="utf-8")
    execution = subprocess.run([str(executable)], input=requests.encode(), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=90, check=True)
    lines = execution.stdout.splitlines()
    if len(lines) != len(cases):
        raise AssertionError(f"response_count:{len(lines)} expected:{len(cases)}")
    records = []
    for (name, request, expected, difference), line in zip(cases, lines):
        actual = json.loads(line)
        if difference:
            matched = expected["ok"] and not actual["ok"] and actual["error"] in {"invalid_config", "unsafe_path"}
        else:
            matched = actual["ok"] == expected["ok"] and (not actual["ok"] or actual["value"] == expected["value"])
        records.append({"name": name, "pass": bool(matched), "pythonAccepted": expected["ok"],
                        "nativeAccepted": actual["ok"], "intentionalDifference": difference})
    failures = [item for item in records if not item["pass"]]
    files = ["modkit/launch_mod.py", "modkit/config.defaults.json", "native/core/src/launcher_config.rs",
             "native/core/src/session_archive.rs", "native/core/tests/launcher_config_contract.rs",
             "native/core/examples/launcher_config_interop.rs", "native/tooling/test_launcher_config_interop.py"]
    proof = {"schema": 1, "scope": "pure launcher configuration; synthetic inputs only",
             "utc": datetime.now(timezone.utc).isoformat(), "status": "pass" if not failures else "fail",
             "python": sys.version, "platform": platform.platform(), "cases": len(records),
             "failures": failures, "intentionalDifferences": sum(bool(item["intentionalDifference"]) for item in records),
             "hashes": {file: hashlib.sha256((ROOT / file).read_bytes()).hexdigest() for file in files},
             "example": {"path": str(executable), "sha256": hashlib.sha256(executable.read_bytes()).hexdigest()},
             "fixtureDirectory": str(output), "records": records,
             "api": {"make_config": "Result<serde_json::Value, ConfigError>",
                     "validate_config": "bytes -> Result<Value, ConfigError>; preserves accepted extra fields",
                     "config_path": "explicit local root -> lexical PathBuf ending MXMOD/config.json; no filesystem checks"},
             "contract": {"embeddedTemplateFieldCount": 74, "criticalOverrides": py["CONFIG_DEFAULTS"],
                          "maxInputBytesIncludingBom": 65536, "duplicateKeys": "reject at every level",
                          "pathRestrictions": "explicit drive root, no UNC/device namespace/ADS/dot segments/ONEME component/control chars/reserved DOS base names",
                          "nativeJsonBounds": "depth <=64, valid Unicode scalars, finite f64, integer i64/u64; no silent bigint rounding",
                          "negativeZero": "integer -0 becomes int0; -0.0/-0e0 remain float and fail required integer types"},
             "integrationRemaining": ["caller-side verified config read and safe atomic writer", "launcher C ABI or native caller wiring", "installed launcher tests"],
             "notTested": ["launcher process start", "installed config", "real profiles", "filesystem path safety", "full migration"]}
    if native_test_log is not None:
        native_test_log = native_test_log.resolve()
        native_test_log.relative_to(BUILD.resolve())
        log_bytes = native_test_log.read_bytes()
        log = log_bytes.decode("utf-8-sig")
        if not re.search(r"test result: ok\. 10 passed; 0 failed; 0 ignored;", log):
            raise AssertionError("native test log must confirm ten successful contract tests")
        proof["nativeTests"] = {"passed": 10, "failed": 0, "log": str(native_test_log),
                                "logSha256": hashlib.sha256(log_bytes).hexdigest()}
    (output / "proof.json").write_text(json.dumps(proof, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": proof["status"], "cases": len(records), "intentionalDifferences": proof["intentionalDifferences"],
                      "failures": failures, "proof": str(output / "proof.json")}, ensure_ascii=True))
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, default=BUILD / "target/debug/examples/launcher_config_interop.exe")
    parser.add_argument("--output", type=Path, default=BUILD / "fixtures/launcher-config-v1")
    parser.add_argument("--native-test-log", type=Path)
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.executable, arguments.output, arguments.native_test_log))
