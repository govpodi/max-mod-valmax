"""Generate bounded synthetic full-profile policy expectations; no adapter I/O.

Only named pure functions/constants are compiled from the Python oracle AST.
No module-level profile discovery, native APIs, filesystem adapter or service
constructor is executed. The output contains names and empty/nonempty booleans,
not credential blobs. Run before the centralized Rust test command.
"""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import re
import types
import unicodedata

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path("C:/ValMAXBuild/fixtures/mdata-profile/contract.json")
SOURCES = {}


class CodeError(Exception):
    def __init__(self, code):
        self.code = code


def load(name, functions, assignments, namespace):
    path = ROOT / "modkit" / name
    raw = path.read_bytes()
    SOURCES[name] = hashlib.sha256(raw).hexdigest()
    tree = ast.parse(raw, filename=str(path))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in assignments for target in node.targets):
            selected.append(node)
    assert {node.name for node in selected if isinstance(node, ast.FunctionDef)} == set(functions)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def outcome(call, field):
    try:
        value = call()
        return {field: sorted(value) if isinstance(value,set) else value}
    except CodeError as error:
        return {"error": error.code}


def main():
    accounts = load("session_credentials.py", {"account_id"}, set(), {"re":re})
    archive = load("mdata_archive.py", {"valid_path"}, {"_DEVICES"}, {"unicodedata":unicodedata})
    files = load("mdata_files.py", {"included", "profile_ids"}, {"_HELPER_FILES", "_HELPER_PREFIXES"},
                 {"re":re,"account_id":accounts["account_id"],"MDataFileError":CodeError})
    credentials = load("mdata_credentials.py", {"allowed_name","owner_id","targets"},
                       {"ACCOUNT_KEYS","COMMON_KEYS","COMMON_NAMES","_NAME","_WEB"},
                       {"re":re,"account_id":accounts["account_id"],"CredentialError":CodeError})
    service = load("mdata_service.py", {"_validate_storage"}, set(),
                   {"re":re,"files":types.SimpleNamespace(profile_ids=files["profile_ids"]),
                    "owner_id":credentials["owner_id"],"MDataError":CodeError})
    names = set(credentials["COMMON_NAMES"])
    ids = ["0","1","2","42","01","-1","9223372036854775807","9223372036854775808","9999999999999999999","10000000000000000000","١"]
    for key in credentials["ACCOUNT_KEYS"]:
        for account in ids:
            for suffix in ("@MXMOD","@ONEME","@mxmod"):
                names.add(f"{key}_{account}{suffix}")
    for prefix in ("x","a-b_@site", "space name", "x"*192,"x"*193,"é","x\n"):
        for left in ("1","42","01","9223372036854775807","9223372036854775808"):
            for right in ("0","1","42","01","9223372036854775807","9223372036854775808","9999999999999999999"):
                names.add(f"{prefix}-{left}_{right}@MXMOD")
    names.update(("", "Access_token_42@MXMOD","multi_state_enabled_common@MXMOD","-42_0@MXMOD","a--42_0@MXMOD"))
    paths=["", ".", "../a", "a//b", "a\\b", "a:ads", "CON.txt", "a.", "a ", "a%2fb", "user42/history/История.db",
           "logs/a", "LOGS/a", "user42/logſ/a", "user42/CrashDumps/x", "mdata-result.json", "MDATA-result.json",
           "session-transfer-result.json", ".mxsession-old/a", ".MXSESSION-old/a", ".mdata-x/a", ".session-result-x",
           "x.MDATA/a", "x.MaxSession", "user42/x.mdata", "user42/mdata-result.json", "α"*255, "α"*256,
           "a/"+"😀"*127, "a/"+"😀"*128, "A/x", "a/y", "a", "a/b", "NUL.txt", "COM¹/a", "a\0b"]
    id_cases=[[],["AUTH_2","User10/x","user2/y","auth_01","user9223372036854775808/x"],
              [f"user{i}/settings" for i in range(1,64)], [f"auth_{i}" for i in range(1,65)],
              ["user42x/a","auth_42/more","USER42/history/x.db"]]
    target_cases=[[],["2","10","2"],["9223372036854775807"],["0"],["01"],["1"]*63,["1"]*64]
    storage=[]
    for paths_group in ([],["user42/history/x.db"],["USER42/HISTORY/sub/X.DB"],["user42/history/.db"],
                        ["app/x.db"],["APP/sub/x.DB"],["user9223372036854775808/history/x.db"],["user43/settings"]):
        for state in ({},{"history_storage_key_42@MXMOD":False},{"history_storage_key_42@MXMOD":True},
                      {"app_data_storage_key_Common@MXMOD":False},{"app_data_storage_key_Common@MXMOD":True},
                      {"web-42_1@MXMOD":True},{"web-42_1@MXMOD":True,"calls_token_43@MXMOD":False},
                      {"web-42_1@MXMOD":True,"access_token_42@MXMOD":True},{"bad_Common@MXMOD":True}):
            manifest={"files":[{"path":path,"size":0} for path in paths_group]}
            blobs={name: b"S" if present else b"" for name,present in state.items()}
            result=outcome(lambda:service["_validate_storage"](manifest,blobs),"unused")
            if "unused" in result:result={"ok":True}
            storage.append({"manifest":manifest,"credentialStates":state,"result":result})
    result={"schema":1,"syntheticOnly":True,"oracleSources":SOURCES,
            "names":[{"name":name,"allowed":credentials["allowed_name"](name),"result":outcome(lambda name=name:credentials["owner_id"](name),"owner")} for name in sorted(names)],
            "paths":[{"path":path,"included":files["included"](path),"safe":archive["valid_path"](path) and files["included"](path)} for path in paths],
            "profileIds":[{"files":[{"path":path,"size":0} for path in group],"result":outcome(lambda group=group:files["profile_ids"]([{"path":path,"size":0} for path in group]),"ids")} for group in id_cases],
            "targets":[{"ids":group,"result":outcome(lambda group=group:credentials["targets"](group),"names")} for group in target_cases],
            "storage":storage}
    encoded=(json.dumps(result,ensure_ascii=True,sort_keys=True,indent=2)+"\n").encode("ascii")
    assert len(encoded)<2*1024*1024
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    if OUTPUT.exists(): assert OUTPUT.read_bytes()==encoded,"fixture differs; use a new reviewed revision"
    else: OUTPUT.write_bytes(encoded)
    print(json.dumps({"path":str(OUTPUT),"sha256":hashlib.sha256(encoded).hexdigest(),"cases":sum(len(result[key]) for key in ("names","paths","profileIds","targets","storage")),"oracleSources":SOURCES}))


if __name__=="__main__":
    main()
