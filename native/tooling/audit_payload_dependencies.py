"""Read-only PE/QML dependency audit. Never load a target DLL or launch MAX.

Writes metadata only to C:/ValMAXBuild and the repository analysis directory.
Module resolution is an explicit packaging model, not a Windows loader proof.
"""
from __future__ import annotations
import collections
import datetime
import hashlib
import json
from pathlib import Path
import re
import stat
import struct
import sys
import zlib
import pefile

# Qt6Gui has more than 8,192 named exports. pefile's conservative default
# silently replaces names beyond that threshold with ordinals. Bound the
# offline parser above the observed tables, then verify table completeness.
MAX_EXPORTS = 65536

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "release/valmax-v8.8.4-20261016/build/dist/ValMAX/runtime/mod"
ANALYSIS = ROOT / "analysis/native-migration-v1"
OUT = Path("C:/ValMAXBuild/fixtures/payload-dependencies-v1")
SYSTEM = Path("C:/Windows/System32")
PROOFS = ["core-launcher-regression-proof.json", "startup-client-proof.json", "privacy-native-proof.json", "ghost-native-proof.json",
          "notification-native-proof.json", "retention-queue-proof.json", "qt-privacy-max-proof.json",
          "qt-webview2-bundle-proof.json", "qt-webview2-guard-good-proof.json", "qt-business-controller-proof.json",
          "qt-all-features-build.json", "qt-all-features-lifecycle-proof.json"]
PROOF_BYTES = {name: (ANALYSIS / name).read_bytes() for name in PROOFS}
PROOF_DATA = {name: json.loads(data.decode("utf-8-sig")) for name, data in PROOF_BYTES.items()}
BROWSER = Path(PROOF_DATA["qt-webview2-bundle-proof.json"]["runtime"])
PLUGINS = [Path(f"C:/ValMAXBuild/build/{name}/qml/ValMAX/Native/valmaxnativeplugin.dll") for name in
           ("qt-bridge", "qt-ghost-bridge", "qt-notification-bridge", "qt-privacy-bridge", "qt-support-bridge")]
HOOKS = [Path(f"C:/ValMAXBuild/build/hooks/{name}.dll") for name in
         ("valmax_privacy", "valmax_ghost", "valmax_notifications", "valmax_retention_queue")]
GUARDED_WEBVIEW = Path("C:/ValMAXBuild/build/qt-webview-guard/qtwebview_webview2.dll")


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(name, data):
    path = OUT / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size}


def files(root):
    for path in sorted(root.rglob("*")):
        if path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("reparse source entry")
        if path.is_file():
            yield path


def api_schema():
    path = SYSTEM / "apisetschema.dll"
    pe = pefile.PE(str(path), fast_load=True)
    data = next(section.get_data() for section in pe.sections if section.Name.rstrip(b"\0") == b".apiset")
    pe.close()
    version, size, _, count, entries, _, _ = struct.unpack_from("<7I", data)
    if version != 6 or size > len(data) or count > 8192:
        raise ValueError("unsupported static ApiSet schema")
    def text(offset, length):
        if length % 2 or offset + length > size:
            raise ValueError("invalid ApiSet string")
        return data[offset:offset + length].decode("utf-16-le")
    result = {}
    for i in range(count):
        _, offset, length, hashed_length, values, n = struct.unpack_from("<6I", data, entries + i * 24)
        contract = text(offset, length).lower() + ".dll"
        if n > 128:
            raise ValueError("invalid ApiSet value count")
        result[contract] = []
        for j in range(n):
            _, name, name_size, target, target_size = struct.unpack_from("<5I", data, values + j * 20)
            result[contract].append({"alias": text(name, name_size).lower(), "host": text(target, target_size).lower(),
                                     "schemaHashPrefix": text(offset, hashed_length).lower()})
    return result, {"path": str(path), "sha256": sha(path), "version": version, "contracts": count}


class Audit:
    def __init__(self):
        self.nodes = {}
        self.by_source = {}
        self.exports = {}
        self.symbols = {}
        self.api, self.api_evidence = api_schema()
        self.planned = {}
        self.context_plans = {}
        self.edges = []
        self.errors = []
        self.forwarders = []

    def add(self, path, group, relative=None, expected=None):
        path = path.absolute()
        key = str(path).lower()
        if key in self.by_source:
            return self.by_source[key]
        if path.suffix.lower() not in (".dll", ".exe", ".drv", ".cpl") or not path.is_file():
            raise ValueError("expected PE source")
        if path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("reparse PE source")
        digest = sha(path)
        ident = f"{group}:{relative or path.name}"
        if ident in self.nodes:
            raise ValueError("duplicate audit ID")
        pe = pefile.PE(str(path), fast_load=True, max_symbol_exports=MAX_EXPORTS)
        pe.parse_data_directories(directories=[0, 1, 2, 13])
        imports, symbols = {}, {}
        for kind, attr in (("normal", "DIRECTORY_ENTRY_IMPORT"), ("delay", "DIRECTORY_ENTRY_DELAY_IMPORT")):
            imports[kind] = []
            for descriptor in getattr(pe, attr, []):
                name = descriptor.dll.decode("ascii").lower()
                if not re.fullmatch(r"[a-z0-9_.-]+\.(?:dll|drv|cpl|exe)", name):
                    raise ValueError("unexpected imported module spelling")
                requested = [item.name.decode("ascii") if item.name else int(item.ordinal) for item in descriptor.imports]
                symbols[(kind, name)] = requested
                imports[kind].append({"name": name, "symbols": len(requested)})
        exports = {}
        export_dir = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        if export_dir is not None:
            if export_dir.struct.NumberOfNames > 65536 or export_dir.struct.NumberOfFunctions > 65536:
                raise ValueError("export table bound exceeded")
            if sum(bool(x.name) for x in export_dir.symbols) != export_dir.struct.NumberOfNames:
                raise ValueError(f"export name table incomplete: {path}")
        for symbol in getattr(getattr(pe, "DIRECTORY_ENTRY_EXPORT", None), "symbols", []):
            target = symbol.forwarder.decode("ascii") if symbol.forwarder else None
            exports[int(symbol.ordinal)] = target
            if symbol.name:
                exports[symbol.name.decode("ascii", errors="replace")] = target
        version = None
        if getattr(pe, "VS_FIXEDFILEINFO", None):
            info = pe.VS_FIXEDFILEINFO[0]
            version = ".".join(str(x) for x in (info.FileVersionMS >> 16, info.FileVersionMS & 65535,
                                                info.FileVersionLS >> 16, info.FileVersionLS & 65535))
        node = {"source": str(path), "group": group, "relative": relative or path.name,
                "bytes": path.stat().st_size, "sha256": digest, "machine": hex(pe.FILE_HEADER.Machine),
                "fileVersion": version, "imports": imports, "exports": len(exports),
                "proofExpectedSha256": expected, "matchesProof": None if expected is None else expected == digest}
        pe.close()
        self.nodes[ident] = node
        self.by_source[key] = ident
        self.exports[ident] = exports
        self.symbols[ident] = symbols
        return ident

    def api_host(self, name, importer):
        entries = self.api.get(name, [])
        match = "exact_schema_name"
        if not entries:
            # This is an explicit static candidate, not a claim that the OS
            # loader accepts another contract version. Record the distinction
            # and check its requested symbols, retaining an unresolved gate.
            prefix = name.removesuffix(".dll").rsplit("-", 1)[0]
            matches = [v for v in self.api.values() if v and v[0]["schemaHashPrefix"] == prefix]
            if len(matches) == 1:
                entries, match = matches[0], "schema_hash_prefix_candidate_not_loader_verified"
        choices = [x for x in entries if x["alias"] in (importer.lower(), importer.lower().removesuffix(".dll"))]
        if not choices:
            choices = [x for x in entries if not x["alias"]]
        hosts = sorted({x["host"] for x in choices if x["host"]})
        return (hosts[0], match) if len(hosts) == 1 else (None, "unknown_api_contract")

    def resolve(self, name, importer_id, context):
        node = self.nodes[importer_id]
        machine = node["machine"]
        system = SYSTEM if machine == "0x8664" else Path("C:/Windows/SysWOW64")
        if name.startswith(("api-ms-", "ext-ms-")):
            host, match = self.api_host(name, Path(node["source"]).name)
            if not host:
                return None, "unknown_api_contract", []
            path = system / host
            if not path.is_file():
                return None, "missing_api_host", [host]
            target = self.add(path, "system64" if machine == "0x8664" else "system32")
            return target, "api_set_host_on_audit_machine" if match == "exact_schema_name" else match, [host]
        candidates = []
        if context in ("main", "service", "all-features"):
            planned = self.context_plans.get(context, self.planned)
            relative = Path(node["relative"])
            # This model assumes application-local payload and Qt's altered
            # DLL search for plugin dependencies. Does not consult PATH/registry.
            for key in ((relative.parent / name).as_posix().lower(),
                        ("max-service/" if context == "service" else "") + name):
                candidate = planned.get(key)
                if candidate and self.nodes[candidate]["machine"] == machine and candidate not in candidates:
                    candidates.append(candidate)
        elif context == "browser":
            for path in (Path(node["source"]).parent / name, BROWSER / name):
                if path.is_file():
                    candidate = self.add(path, "webview-vendor", path.relative_to(BROWSER).as_posix())
                    if self.nodes[candidate]["machine"] == machine and candidate not in candidates:
                        candidates.append(candidate)
        if candidates:
            return candidates[0], "package_candidate", candidates
        path = system / name
        if path.is_file():
            candidate = self.add(path, "system64" if machine == "0x8664" else "system32")
            return candidate, "system_file_on_audit_machine", []
        other = sorted(key for key in self.planned if key.endswith("/" + name) or key == name)
        return None, "present_elsewhere_not_in_modeled_search" if other else "missing", other

    def symbol(self, target, name, context, depth=0):
        if depth > 16:
            return "forwarder_depth"
        exports = self.exports[target]
        if name not in exports:
            return "missing_export"
        forward = exports[name]
        if forward is None:
            return None
        module, symbol = forward.rsplit(".", 1)
        module = module.lower()
        if not module.endswith(".dll"):
            module += ".dll"
        next_id, resolution, _ = self.resolve(module, target, "system" if self.nodes[target]["group"].startswith("system") else context)
        if next_id is None:
            return "forwarder_" + resolution
        self.forwarders.append((target, module, next_id))
        return self.symbol(next_id, int(symbol[1:]) if symbol.startswith("#") else symbol, context, depth + 1)

    def scan(self, ident, context):
        for kind, imports in self.nodes[ident]["imports"].items():
            for item in imports:
                name = item["name"]
                target, resolution, alternatives = self.resolve(name, ident, context)
                edge = {"from": ident, "context": context, "kind": kind, "import": name,
                        "to": target, "resolution": resolution}
                if alternatives:
                    edge["candidates"] = alternatives
                if target:
                    missing = []
                    for symbol in self.symbols[ident][kind, name]:
                        issue = self.symbol(target, symbol, context)
                        if issue:
                            missing.append({"symbol": symbol, "issue": issue})
                    edge["symbolCheck"] = "passed" if not missing else "unresolved"
                    if missing:
                        edge["symbolIssues"] = missing
                self.edges.append(edge)


def qml_evidence():
    sys.path.insert(0, str(ROOT / "modkit"))
    from ui_resource_index import index_resources
    binary = OLD / "max.exe"
    index = index_resources(binary)
    blob = binary.read_bytes()
    pe = pefile.PE(data=blob, fast_load=True)
    records, errors, modules, special = [], [], collections.defaultdict(list), []
    def consume(text, where, digest, category):
        imports = re.findall(r"^\s*import\s+([A-Za-z][A-Za-z0-9_.]*)", text, re.M)
        for imported in sorted(set(imports)):
            modules[imported].append(where)
        terms = [term for term in ("QtWebEngine", "QtWebView", "QtPositioning", "PositionSource", "nmea.source") if term in text]
        if terms:
            special.append({"source": where, "terms": terms})
        records.append({"source": where, "sha256": digest, "category": category, "imports": sorted(set(imports))})
    for item in index["files"]:
        if not item["path"].endswith((".qml", ".js", "/qmldir")):
            continue
        try:
            # A patched RCC payload can be in an appended PE section. Convert
            # its RVA through PE; original-table raw-base arithmetic is wrong.
            offset = pe.get_offset_from_rva(item["payload_rva"] - 4)
            size = int.from_bytes(blob[offset:offset + 4], "big")
            if not 0 < size <= 8 * 1024 * 1024:
                raise ValueError("resource_size")
            payload = blob[offset + 4:offset + 4 + size]
            if item["flags"] & 1:
                expected = int.from_bytes(payload[:4], "big")
                decoder = zlib.decompressobj()
                payload = decoder.decompress(payload[4:], 8 * 1024 * 1024)
                if len(payload) != expected or not decoder.eof or decoder.unused_data:
                    raise ValueError("resource_decompression")
            elif item["flags"] & 4:
                import zstandard
                payload = zstandard.ZstdDecompressor().decompress(payload, max_output_size=8 * 1024 * 1024)
            consume(payload.decode("utf-8"), item["path"], hashlib.sha256(payload).hexdigest(), "old-max-rcc")
        except Exception as error:
            errors.append({"source": item["path"], "error": type(error).__name__})
    pe.close()
    for folder in (ROOT / "modkit", ROOT / "native/qt"):
        for path in sorted(folder.glob("*.qml")):
            consume(path.read_text(encoding="utf-8-sig"), str(path), sha(path), "current-source")
    dirs = []
    for path in files(OLD):
        if path.name != "qmldir":
            continue
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        directives = []
        for n, line in enumerate(lines, 1):
            if re.match(r"(?:module|depends|import|optional\s+import|plugin|optional\s+plugin)\s", line):
                directives.append({"line": n, "text": line})
        dirs.append({"path": path.relative_to(OLD).as_posix(), "sha256": sha(path), "directives": directives})
    return {"sourceMaxSha256": sha(binary), "tables": len(index["tables"]), "resources": len(index["files"]),
            "records": records, "decodeErrors": errors, "moduleUses": dict(sorted(modules.items())),
            "specialTerms": special, "qmldirs": dirs,
            "limitations": ["107 statically recognizable RCC tables, not arbitrary runtime strings or dynamic C++ imports",
                            "qmldir depends is deployment metadata, not identical to an import directive",
                            "Current QML sources are not proof that one final native max.exe has been built"]}


def package_requirements(audit, qml):
    normal = collections.defaultdict(list)
    for edge in audit.edges:
        if edge["kind"] == "normal" and edge["to"] and audit.nodes[edge["to"]]["group"] in ("old-payload", "native", "all-feature"):
            normal[edge["from"]].append(edge["to"])
    roots = ["old-payload:max.exe", "old-payload:max-service/max-service.exe"]
    roots += [k for k, v in audit.nodes.items() if v["group"] == "bridge-variant"]
    roots += ["native:plugins/webview/qtwebview_webview2.dll"]
    roots += [k for k, v in audit.nodes.items() if v["group"] == "all-feature" and v["relative"].endswith("valmaxnativeplugin.dll")]
    closures = {}
    for root in roots:
        seen, todo = set(), [root]
        while todo:
            node = todo.pop()
            if node not in seen:
                seen.add(node)
                todo.extend(normal[node])
        closures[root] = [{"id": n, "path": audit.nodes[n]["relative"], "sha256": audit.nodes[n]["sha256"],
                           "fileVersion": audit.nodes[n]["fileVersion"]} for n in sorted(seen)]
    qml_modules = []
    for item in qml["qmldirs"]:
        module = next((d["text"].split()[1] for d in item["directives"] if d["text"].startswith("module ")), None)
        if module in qml["moduleUses"]:
            qml_modules.append({"module": module, "qmldir": item["path"], "sha256": item["sha256"],
                                "useCount": len(qml["moduleUses"][module]), "directives": item["directives"]})
    return {"schema": 1, "isFinalShippingAllowlist": False,
            "normalAppLocalClosures": closures, "observedQmlModuleMetadata": qml_modules,
            "qtPluginCandidates": [{"path": v["relative"], "sha256": v["sha256"], "fileVersion": v["fileVersion"]}
                for v in audit.nodes.values() if v["group"] == "old-payload" and v["relative"].startswith("plugins/")],
            "decisions": [
                {"paths": ["plugins/position/qtposition_nmea.dll"], "decision": "conditional_prune_candidate",
                 "reason": "Only importer of absent Qt6SerialPort.dll; no observed QtPositioning/NMEA QML usage.",
                 "requiredBeforePruning": "Confirm no native GPS/NMEA feature, then test geolocation/calls and plugin enumeration in the owned final package."},
                {"paths": ["qml/QtWebEngine/"], "decision": "conditional_prune_candidate",
                 "reason": "Only importers of missing WebEngineQuick/Delegates libraries; observed business/FAQ/web media QML imports QtWebView instead.",
                 "requiredBeforePruning": "Account for QtWebView qmldir depends QtWebEngine deployment hint; prove runtime business/FAQ/web media paths with explicit WebView2 backend after staging exclusion."},
                {"paths": ["Qt6Positioning.dll", "plugins/position/qtposition_winrt.dll", "plugins/position/qtposition_positionpoll.dll"],
                 "decision": "retain_pending_dynamic_usage_evidence", "reason": "No QML references found, but C++ dynamic feature usage is not disproved."},
                {"paths": ["Qt6WebView.dll", "Qt6WebViewQuick.dll", "qml/QtWebView/", "WebView2Loader.dll"],
                 "decision": "retain", "reason": "Normal MAX dependency and observed business/FAQ/web media QtWebView imports."},
                {"paths": ["plugins/webview/qtwebview_webview2.dll"], "decision": "replace_with_pinned_native_guard",
                 "reason": "Guarded backend uses stock Qt6WebView private ABI 6.11.2; no alternative Qt6WebView core DLL required."},
                {"paths": ["Microsoft.WebView2.FixedVersionRuntime.155.0.4283.60.x64/"], "decision": "retain_complete_vendor_distribution",
                 "reason": "Delay imports and dynamic resources/processes make static graph an invalid browser pruning allowlist."},
                {"paths": ["valmax_retention_queue.dll"], "decision": "exclude_from_proposed_shipping_map",
                 "reason": "Synthetic queue evidence only; service/storage integration was stopped and no retention completion is claimed."},
                {"paths": ["tracer_crash_reporter.dll", "tracernative.dll", "updater_oneme.dll", "WinSparkle.dll"],
                 "decision": "retain_with_current_import_tables", "reason": "Normal loader dependencies survive behavioral privacy patches."},
                {"paths": ["plugins/imageformats/", "plugins/multimedia/", "plugins/tls/", "plugins/styles/", "max-service/"],
                 "decision": "retain_pending_feature_matrix", "reason": "Optional selection is runtime/content dependent; services have a separate process root and duplicate dependency closure."}
            ]}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    audit = Audit()
    base = []
    print("Reading packaged PE imports and versions", flush=True)
    for path in files(OLD):
        if path.suffix.lower() in (".dll", ".exe"):
            rel = path.relative_to(OLD).as_posix()
            ident = audit.add(path, "old-payload", rel)
            audit.planned[rel.lower()] = ident
            base.append(ident)
    selected = []
    core = {"path": "C:/ValMAXBuild/target/release/deps/valmax_core.dll",
            "sha256": PROOF_DATA["startup-client-proof.json"]["corePair"]["valmax_core.dll"]}
    ident = audit.add(Path(core["path"]), "native", "valmax_core.dll", core["sha256"])
    audit.planned["valmax_core.dll"] = ident
    selected.append(ident)
    for path, proof in zip(HOOKS, ("privacy-native-proof.json", "ghost-native-proof.json", "notification-native-proof.json", "retention-queue-proof.json")):
        ident = audit.add(path, "native", path.name, PROOF_DATA[proof]["dllSha256"])
        selected.append(ident)
        if "retention_queue" not in path.name:
            audit.planned[path.name] = ident
    variants = []
    for path in PLUGINS:
        build = path.parents[3].name
        cache = path.parents[3] / "CMakeCache.txt"
        flags = dict(re.findall(r"VALMAX_(CORE|GHOST|NOTIFICATION|PRIVACY)_LIBRARY:FILEPATH=(.*)", cache.read_text(encoding="utf-8")))
        ident = audit.add(path, "bridge-variant", build + "/valmaxnativeplugin.dll")
        selected.append(ident)
        variants.append({"id": ident, "build": build, "features": {k: bool(v.strip()) for k, v in flags.items()}, "cacheSha256": sha(cache)})
    guard = audit.add(GUARDED_WEBVIEW, "native", "plugins/webview/qtwebview_webview2.dll",
                      PROOF_DATA["qt-webview2-guard-good-proof.json"]["qtHashes"]["plugins/webview/qtwebview_webview2.dll"])
    selected.append(guard)
    audit.planned["plugins/webview/qtwebview_webview2.dll"] = guard
    combined = []
    audit.context_plans["all-features"] = dict(audit.planned)
    for source, expected in PROOF_DATA["qt-all-features-build.json"]["artifacts"].items():
        path = Path(source)
        if path.suffix.lower() != ".dll":
            continue
        rel = "qml/ValMAX/Native/valmaxnativeplugin.dll" if path.name == "valmaxnativeplugin.dll" else path.name
        ident = audit.add(path, "all-feature", rel, expected)
        combined.append(ident)
        audit.context_plans["all-features"][rel.lower()] = ident
    vendor = []
    browser_expected = {v["path"].lower(): v["sha256"] for v in PROOF_DATA["qt-webview2-bundle-proof.json"]["files"]}
    for path in files(BROWSER):
        if path.suffix.lower() in (".dll", ".exe"):
            rel = path.relative_to(BROWSER).as_posix()
            vendor.append(audit.add(path, "webview-vendor", rel, browser_expected.get(rel.lower())))
    print(f"PE inventory: {len(base)} old, {len(selected)} native variants, {len(combined)} combined, {len(vendor)} vendor", flush=True)
    for ident in base:
        audit.scan(ident, "service" if audit.nodes[ident]["relative"].startswith("max-service/") else "main")
    for ident in selected:
        audit.scan(ident, "main")
    for ident in vendor:
        audit.scan(ident, "browser")
    for ident in combined:
        audit.scan(ident, "all-features")
    # Recursively classify system imports. Never consult DLL search PATH or
    # load a DLL; only read fixed System32/SysWOW64 PE files and static API map.
    done = set()
    while True:
        todo = [k for k, v in audit.nodes.items() if v["group"].startswith("system") and k not in done]
        if not todo:
            break
        if len(done) + len(todo) > 1024:
            raise ValueError("system closure capacity")
        for ident in todo:
            done.add(ident)
            audit.scan(ident, "system")
    print(f"Resolved model edges: {len(audit.edges)}, system nodes: {len(done)}", flush=True)
    qml = qml_evidence()
    evidence = write("pe-closure.json", {"schema": 1, "nodes": audit.nodes, "edges": audit.edges,
                     "apiSetSchema": audit.api_evidence, "forwarderEdges": sorted(set(audit.forwarders)),
                     "bridgeVariants": variants, "nativePlan": audit.planned, "contextPlans": audit.context_plans,
                     "resolutionModel": "API-set mapping first, candidate importer dir then process root, fixed System32/SysWOW64 fallback; no PATH/registry/KnownDLL cache simulation"})
    qml_file = write("qml-dependencies.json", qml)
    requirements = package_requirements(audit, qml)
    requirements_file = write("package-requirements.json", requirements)
    unresolved = [e for e in audit.edges if e["to"] is None]
    module_issues = collections.defaultdict(list)
    for edge in unresolved:
        if not audit.nodes[edge["from"]]["group"].startswith("system"):
            module_issues[edge["import"]].append(edge)
    symbol_issues = [e for e in audit.edges if e.get("symbolCheck") == "unresolved"]
    special_importers = {}
    for name in ("icuuc.dll", "qt6serialport.dll", "qt6webenginequick.dll", "qt6webenginequickdelegatesqml.dll", "qt6positioning.dll"):
        special_importers[name] = [e for e in audit.edges if e["import"] == name and not audit.nodes[e["from"]]["group"].startswith("system")]
    groups = collections.Counter(v["group"] for v in audit.nodes.values())
    report = {"schema": 1, "checkedAtUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "auditExecuted": True, "targetCodeExecuted": False, "filesRemovedOrModified": False,
        "downloaded": False, "userInstallationTouched": False, "fullReleaseClosureVerified": False,
        "baseRuntime": str(OLD), "browserRuntime": str(BROWSER), "scriptSha256": sha(Path(__file__)),
        "corePinEvidence": "startup-client-proof.json: targeted C++/Rust startup ABI and Python receipt compatibility; not a new full-core regression",
        "evidence": {"pe": evidence, "qml": qml_file, "requirements": requirements_file}, "nodeCounts": dict(groups), "edges": len(audit.edges),
        "proofSources": {name: hashlib.sha256(data).hexdigest() for name, data in PROOF_BYTES.items()},
        "proofsChangedDuringAudit": [name for name, data in PROOF_BYTES.items() if sha(ANALYSIS / name) != hashlib.sha256(data).hexdigest()],
        "nativeProofMismatches": [{"id": k, "actual": v["sha256"], "expected": v["proofExpectedSha256"]} for k, v in audit.nodes.items() if v["matchesProof"] is False],
        "nonSystemMissingImports": {k: v for k, v in module_issues.items() if not k.startswith(("api-", "ext-"))},
        "nonSystemUnresolvedApiContracts": {k: v for k, v in module_issues.items() if k.startswith(("api-", "ext-"))},
        "apiSchemaPrefixCandidateCounts": dict(collections.Counter(
            audit.nodes[e["from"]]["group"] for e in audit.edges
            if e["resolution"] == "schema_hash_prefix_candidate_not_loader_verified")),
        "specialImporters": special_importers,
        "symbolIssues": symbol_issues, "nonSystemSymbolIssueEdges": sum(not audit.nodes[e["from"]]["group"].startswith("system") for e in symbol_issues),
        "systemUnresolvedImports": [e for e in unresolved if audit.nodes[e["from"]]["group"].startswith("system")],
        "normalAppLocalClosureCounts": {k: len(v) for k, v in requirements["normalAppLocalClosures"].items()},
        "packagingDecisions": requirements["decisions"],
        "bridgeVariants": variants, "qmlDecoded": sum(r["category"] == "old-max-rcc" for r in qml["records"]), "qmlDecodeErrors": qml["decodeErrors"],
        "limitations": ["Static PE/qmldir/RCC audit, not a loader execution proof or guaranteed minimal package",
            "Current audit machine system DLL presence does not establish supported Windows minimum versions",
            "API schema hash-prefix candidates verify exports only; alternate contract-version acceptance is not a loader proof",
            "All-feature candidate has separate frozen inputs and standalone lifecycle proof; it is not a positive MAX/early-bootstrap or final installer proof",
            "Retention queue remains synthetic and excluded from proposed shipping map",
            "Dynamic C++ LoadLibrary/Qt plugins, codecs/styles and WebView requests are not fully enumerable from PE imports",
            "Whole Microsoft fixed runtime is an independently verified distribution; do not prune it from these PE results",
            "New native runtime max.exe/gates and final installer are not assembled or certified here"]}
    (ANALYSIS / "payload-dependency-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"nodes": dict(groups), "missing": sorted(module_issues), "symbolIssueEdges": len(symbol_issues),
                      "qmlDecoded": report["qmlDecoded"], "qmlErrors": len(qml["decodeErrors"]),
                      "proofMismatches": report["nativeProofMismatches"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
