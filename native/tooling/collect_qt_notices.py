"""Collect official corresponding-source archives and Qt notice materials.

Scope is derived from the already verified standalone helper package. No SDK,
package or installed client is changed. Source archives are kept intact; only
bounded license/attribution metadata is extracted. This is material collection,
not a legal opinion or a claim of reproducible Qt binary builds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import tarfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
QT = Path("C:/ValMAXBuild/Qt")
DEPS = Path("C:/ValMAXBuild/deps")
ANALYSIS = ROOT / "analysis/native-migration-v1"
HELPER_PROOF = ANALYSIS / "native-transfer-helper-proof.json"
VERSION = "6.11.2"
SOURCE_URL = "https://download.qt.io/archive/qt/6.11/6.11.2/submodules/"
MODULES = ("qtbase", "qtdeclarative", "qtshadertools")
MAX_ARCHIVE = 256 << 20
MAX_DOWNLOAD_TOTAL = 768 << 20
MAX_EXTRACT_FILE = 2 << 20
MAX_EXTRACT_TOTAL = 64 << 20
MAX_MEMBERS = 200000
MAX_UNCOMPRESSED = 2 << 30


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ordinary(path, directory=False):
    info = path.lstat()
    require(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, "reparse path")
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "unexpected path type")
    return info


def safe_output(path):
    require(path.is_absolute() and path.parent == DEPS and path.name.startswith("qt-redistribution-"), "invalid material output")
    for parent in path.parents:
        ordinary(parent, True)
    path.mkdir(exist_ok=True)
    ordinary(path, True)
    return path


def owned_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in path.parents:
        ordinary(parent, True)
    if path.exists():
        ordinary(path)
        require(path.read_bytes() == data, "existing material differs: " + path.name)
    else:
        with path.open("xb") as stream:
            stream.write(data)


def fetch_small(url, limit=16384):
    with urllib.request.urlopen(url, timeout=30) as response:
        require(urllib.parse.urlsplit(response.url).scheme == "https", "non-HTTPS redirect")
        data = response.read(limit + 1)
    require(len(data) <= limit, "metadata download too large")
    return data


def acquire(module, output):
    name = f"{module}-everywhere-src-{VERSION}.tar.xz"
    url = SOURCE_URL + name
    checksum_url = url + ".sha256"
    checksum = fetch_small(checksum_url)
    match = re.fullmatch(rb"([a-f0-9]{64})\s+\*?" + re.escape(name.encode()) + rb"\s*", checksum)
    require(match is not None, "invalid upstream checksum")
    expected = match[1].decode()
    path = output / "sources" / name
    owned_write(output / "sources" / (name + ".sha256"), checksum)
    final_url = url
    if path.exists():
        require(ordinary(path).st_size <= MAX_ARCHIVE and sha(path) == expected, "cached source archive mismatch")
    else:
        partial = path.with_name(path.name + ".part")
        require(not partial.exists(), "unfinished archive exists; inspect it before resuming")
        total, started = 0, time.monotonic()
        with urllib.request.urlopen(url, timeout=30) as response, partial.open("xb") as stream:
            final_url = response.url
            require(urllib.parse.urlsplit(final_url).scheme == "https", "non-HTTPS archive redirect")
            length = response.headers.get("Content-Length")
            if length:
                require(int(length) <= MAX_ARCHIVE, "archive Content-Length bound")
            while chunk := response.read(1 << 20):
                total += len(chunk)
                require(total <= MAX_ARCHIVE and time.monotonic() - started < 300, "archive download bound")
                stream.write(chunk)
        require(sha(partial) == expected, "archive upstream checksum mismatch")
        partial.rename(path)
    print(json.dumps({"downloaded": module, "bytes": path.stat().st_size, "sha256": expected}), flush=True)
    return {"module": module, "path": "sources/" + name, "bytes": path.stat().st_size,
            "sha256": expected, "upstreamSha256": expected, "officialUrl": url,
            "upstreamChecksumUrl": checksum_url, "upstreamChecksumSha256": digest(checksum), "downloadFinalUrl": final_url}


def load_inputs(helper_proof=HELPER_PROOF):
    proof_bytes = helper_proof.read_bytes()
    proof = json.loads(proof_bytes)
    package = Path(proof["package"])
    require(package.parent in (Path("C:/ValMAXBuild/packages"), Path("D:/ValMAXBuild/packages")),
            "unexpected helper package")
    ordinary(package, True)
    raw = (package / "package-manifest.json").read_bytes()
    manifest_sha = proof.get("packageManifestSha256", proof.get("manifestSha256"))
    require(digest(raw) == manifest_sha, "helper manifest changed")
    manifest = json.loads(raw)
    require(manifest["qtVersion"] == VERSION, "wrong Qt version")
    sboms = {module: json.loads((QT / "sbom" / f"{module}-{VERSION}.spdx.json").read_text("utf8")) for module in MODULES}
    lookup = {}
    for module, sbom in sboms.items():
        for item in sbom["files"]:
            relative = item["fileName"].removeprefix("./")
            lookup.setdefault(relative.lower(), []).append((module, item))
    rows, module_names, checksums = [], set(), []
    for entry in manifest["files"]:
        if entry["origin"]["component"] not in ("qt", "qt-bin"):
            continue
        relative = PurePosixPath(entry["path"])
        require(not relative.is_absolute() and ".." not in relative.parts, "unsafe package entry")
        supplied = package / relative
        ordinary(supplied)
        require(sha(supplied) == entry["sha256"], "helper Qt bytes changed")
        source = entry["origin"]["source"]
        if entry["origin"]["component"] == "qt-bin":
            source = "bin/" + source
        require(sha(QT / source) == entry["sha256"], "SDK and package Qt bytes differ")
        matches = lookup.get(source.lower(), [])
        if relative.suffix.lower() == ".dll":
            require(len(matches) == 1, "unmapped or ambiguous shipped Qt binary")
            module, sbom_file = matches[0]
            module_names.add(module)
            actual_sha1 = hashlib.sha1(supplied.read_bytes()).hexdigest()
            expected = next((item["checksumValue"] for item in sbom_file.get("checksums", []) if item["algorithm"] == "SHA1"), None)
            checksums.append({"path": entry["path"], "expectedSbomSha1": expected, "actualSha1": actual_sha1,
                              "matches": expected == actual_sha1})
            rows.append({"path": entry["path"], "source": source, "sha256": entry["sha256"], "module": module,
                         "sbomFileId": sbom_file["SPDXID"], "licenseExpression": sbom_file.get("licenseConcluded"),
                         "copyright": sbom_file.get("copyrightText")})
        elif source.startswith("qml/"):
            module_names.add("qtdeclarative")
            rows.append({"path": entry["path"], "source": source, "sha256": entry["sha256"],
                         "module": "qtdeclarative", "attribution": "QtDeclarative SDK QML resource path"})
        else:
            raise ValueError("unmapped Qt runtime resource")
    require(module_names and module_names <= set(MODULES), "unexpected module set")
    return proof, digest(proof_bytes), rows, sorted(module_names), sboms, checksums


def normalized_member(name, root):
    path = PurePosixPath(name)
    require(not path.is_absolute() and path.parts and path.parts[0] == root and ".." not in path.parts
            and "\\" not in name and "\0" not in name, "unsafe source archive member")
    return PurePosixPath(*path.parts[1:]).as_posix()


def windows_material_path(relative):
    path = PurePosixPath(relative)
    require(not path.is_absolute() and path.parts and ".." not in path.parts, "unsafe material path")
    require(all(not any(c in piece for c in '<>:"\\|?*\0') and not piece.endswith((".", " "))
                and piece.split(".", 1)[0].upper() not in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
                for piece in path.parts), "unsafe Windows material name")
    return path


def reference_path(attribution, reference):
    require(isinstance(reference, str) and len(reference) <= 4096 and "\\" not in reference and ":" not in reference,
            "invalid attribution license reference")
    require(not PurePosixPath(reference).is_absolute(), "absolute attribution reference")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(attribution), reference))
    require(resolved != ".." and not resolved.startswith("../"), "attribution reference escapes archive")
    return windows_material_path(resolved).as_posix()


def extract_materials(module, archive, output):
    # Metadata catalog is bounded; no extractall, executable source, symlink or
    # hardlink is written. Full archives remain the corresponding-source form.
    root = f"{module}-everywhere-src-{VERSION}"
    members, data, total, extracted_total = {}, {}, 0, 0
    def read(stream, item):
        nonlocal extracted_total
        require(item.isfile() and item.size <= MAX_EXTRACT_FILE, "linked or oversized notice")
        handle = stream.extractfile(item)
        require(handle is not None, "unreadable source notice")
        with handle:
            content = handle.read(MAX_EXTRACT_FILE + 1)
        require(len(content) == item.size and len(content) <= MAX_EXTRACT_FILE, "notice size mismatch")
        extracted_total += len(content)
        require(extracted_total <= MAX_EXTRACT_TOTAL, "notice aggregate size bound")
        return content
    def primary(name):
        return (name.startswith("LICENSES/") or PurePosixPath(name).name in ("qt_attribution.json", "qt_attributions.json")
                or ("/" not in name and name in (".tag", ".git_archival.txt", ".cmake.conf", "LICENSE", "COPYING")))
    # Two streaming passes avoid repeatedly seeking/decompressing the XZ archive
    # for every LicenseFile. Metadata and all extracted bytes remain bounded.
    with tarfile.open(output / archive["path"], "r|xz") as stream:
        for number, item in enumerate(stream):
            require(number < MAX_MEMBERS, "source archive entry count bound")
            relative = normalized_member(item.name, root)
            require(relative not in members, "duplicate archive member")
            members[relative] = item
            total += item.size
            require(total <= MAX_UNCOMPRESSED, "source archive expanded size bound")
            if item.isfile() and primary(relative):
                windows_material_path(relative)
                data[relative] = read(stream, item)
    wanted, records, gaps = set(data), [], []
    for name in sorted(data):
        if PurePosixPath(name).name not in ("qt_attribution.json", "qt_attributions.json"):
            continue
        # Qt's upstream attribution metadata contains literal newlines inside
        # strings (for example Unicode and forkfd copyright). Preserve them;
        # the original bytes remain supplied alongside the parsed summary.
        values = json.loads(data[name].decode("utf-8-sig"), strict=False)
        values = values if isinstance(values, list) else [values]
        require(len(values) <= 512 and all(isinstance(value, dict) for value in values), "invalid attribution structure")
        for value in values:
            references = value.get("LicenseFile", [])
            references = [references] if isinstance(references, str) else references
            require(isinstance(references, list) and len(references) <= 128, "license reference count bound")
            resolved = []
            for reference in references:
                try:
                    target = reference_path(name, reference)
                    require(target in members and members[target].isfile(), "missing or linked notice: " + target)
                    wanted.add(target)
                    resolved.append(target)
                except (ValueError, KeyError) as error:
                    gaps.append({"code": "attribution_license_reference", "module": module,
                                 "attribution": name, "reference": reference, "detail": str(error)})
            records.append({"source": name, "data": value, "resolvedLicenseFiles": resolved})
    with tarfile.open(output / archive["path"], "r|xz") as stream:
        for item in stream:
            relative = normalized_member(item.name, root)
            if relative in wanted and relative not in data:
                data[relative] = read(stream, item)
    require(set(data) == wanted, "notice extraction incomplete")
    for name, content in sorted(data.items()):
        owned_write(output / "notices" / module / windows_material_path(name), content)
    tag = data.get(".tag", b"").decode("ascii", errors="replace").strip()
    print(json.dumps({"extracted": module, "files": len(wanted), "attributions": len(records), "gaps": len(gaps)}), flush=True)
    return {"module": module, "archiveMembers": len(members), "archiveExpandedBytes": total,
            "extractedFiles": len(wanted), "extractedBytes": extracted_total, "tag": tag,
            "attributions": records, "gaps": gaps}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf8")


def cgit_identity(content):
    # Only parse the two official cgit commit-info table rows. The HTML is kept
    # as evidence, never rendered or executed by the collector.
    values = {}
    for field in ("commit", "tree"):
        matches = re.findall(rb"<tr><th>" + field.encode() + rb"</th><td[^>]*><a[^>]*>([a-f0-9]{40})</a>", content)
        require(len(matches) == 1, "ambiguous upstream commit metadata")
        values[field] = matches[0].decode("ascii")
    return values


def source_identity(module, sbom, collected, output):
    roots = [r["relatedSpdxElement"] for r in sbom["relationships"] if r["relationshipType"] == "DESCRIBES"]
    packages = [p for p in sbom["packages"] if p["SPDXID"] in roots]
    require(len(packages) == 1 and packages[0]["name"] == module, "ambiguous SBOM module root")
    location = packages[0]["downloadLocation"]
    match = re.fullmatch(r"git://code\.qt\.io/qt/" + module + r"\.git@([0-9a-f]{40})", location)
    require(match is not None, "unexpected SBOM source location")
    commit = match[1]
    url = f"https://code.qt.io/cgit/qt/{module}.git/commit/?id={commit}"
    path = output / "provenance" / (module + "-commit.html")
    if path.exists():
        ordinary(path)
        require(path.stat().st_size <= 256 << 10, "cached metadata bound")
        metadata = path.read_bytes()
    else:
        metadata = fetch_small(url, 256 << 10)
        owned_write(path, metadata)
    identity = cgit_identity(metadata)
    require(identity["commit"] == commit, "upstream commit mismatch")
    source_tag = collected["tag"]
    tag_kind = "commit" if source_tag == commit else "tree" if source_tag == identity["tree"] else "unmatched"
    return {"module": module, "sdkCommit": commit, "upstreamTree": identity["tree"],
            "archiveTag": source_tag, "archiveTagKind": tag_kind, "matches": tag_kind != "unmatched",
            "upstreamMetadataUrl": url, "metadataPath": path.relative_to(output).as_posix(),
            "metadataSha256": digest(metadata), "sdkRootPackage": packages[0]}


def runtime_packages(rows, modules, sboms):
    packages, edges, owners = {}, {}, {}
    def ref(module, identifier):
        if ":" not in identifier:
            return module, identifier
        document, identifier = identifier.split(":", 1)
        matches = [m for m in modules if document.startswith("DocumentRef-" + m + "-")]
        return (matches[0], identifier) if len(matches) == 1 else (document, identifier)
    for module in modules:
        for package in sboms[module]["packages"]:
            packages[module, package["SPDXID"]] = package
        for item in sboms[module]["relationships"]:
            source, target = ref(module, item["spdxElementId"]), ref(module, item["relatedSpdxElement"])
            if item["relationshipType"] == "DEPENDS_ON":
                edges.setdefault(source, set()).add(target)
            elif item["relationshipType"] == "CONTAINS" and source in packages:
                owners.setdefault(target, set()).add(source)
    seeds = set()
    for row in rows:
        if "sbomFileId" not in row:
            continue
        found = owners.get((row["module"], row["sbomFileId"]), set())
        require(found, "shipped Qt binary has no SBOM package owner")
        seeds.update(found)
    visited, pending, missing = set(), list(seeds), []
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        if current not in packages:
            missing.append({"module": current[0], "package": current[1]})
            continue
        pending.extend(edges.get(current, []))
    return [{"module": module, "directlyShipped": (module, identifier) in seeds,
             "package": packages[module, identifier]} for module, identifier in sorted(visited) if (module, identifier) in packages], missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof", type=Path, default=ANALYSIS / "qt-transfer-redistribution-proof.json")
    parser.add_argument("--fetch-only", action="store_true", help="Acquire verified archives; leave the collection incomplete.")
    args = parser.parse_args()
    require(args.proof.parent == ANALYSIS and not args.proof.exists(), "proof must be new in analysis directory")
    proof, proof_hash, rows, modules, sboms, checksum_rows = load_inputs()
    output = safe_output(DEPS / ("qt-redistribution-" + VERSION + "-" + proof["packageManifestSha256"][:16]))
    archives = [acquire(module, output) for module in modules]
    require(sum(item["bytes"] for item in archives) <= MAX_DOWNLOAD_TOTAL, "source archive aggregate bound")
    if args.fetch_only:
        print(json.dumps({"fetchedOnly": True, "output": str(output), "modules": modules}), flush=True)
        return
    collected = [extract_materials(module, archive, output) for module, archive in zip(modules, archives)]
    # Final receipt generation is kept explicit below; unlike acquisition,
    # it never claims a successful result before all selected materials exist.
    finish(output, args.proof, proof, proof_hash, rows, modules, sboms, checksum_rows, archives, collected)


def finish(output, proof_path, proof, proof_hash, rows, modules, sboms, checksum_rows, archives, collected):
    gaps = [gap for item in collected for gap in item["gaps"]]
    identities, sbom_files = [], []
    for module, collection in zip(modules, collected):
        identity = source_identity(module, sboms[module], collection, output)
        identities.append(identity)
        if not identity["matches"]:
            gaps.append({"code": "source_commit_tree_mismatch", "module": module,
                         "sdkCommit": identity["sdkCommit"], "archiveTag": identity["archiveTag"]})
        source = QT / "sbom" / f"{module}-{VERSION}.spdx.json"
        destination = output / "sbom" / source.name
        owned_write(destination, source.read_bytes())
        sbom_files.append({"module": module, "path": destination.relative_to(output).as_posix(), "sha256": sha(destination)})
        for license_info in sboms[module].get("hasExtractedLicensingInfos", []):
            name = windows_material_path(license_info["licenseId"] + ".txt")
            text = license_info.get("extractedText", "")
            if not isinstance(text, str) or not text.strip() or text in ("NOASSERTION", "NONE"):
                gaps.append({"code": "sbom_license_text_missing", "module": module, "licenseId": license_info["licenseId"]})
                continue
            owned_write(output / "notices" / "sdk-extracted" / module / name, text.encode("utf8"))
    packages, missing = runtime_packages(rows, modules, sboms)
    gaps.extend({"code": "runtime_sbom_dependency_unresolved", **item} for item in missing)
    package_attributions = {"schema": 1, "qtVersion": VERSION,
                            "scope": "Packages owning shipped Qt binaries and their SPDX DEPENDS_ON closure; build dependencies excluded.",
                            "packages": packages}
    owned_write(output / "runtime-attributions.json", json_bytes(package_attributions))
    source_attributions = {"schema": 1, "qtVersion": VERSION,
                           "scope": "All attribution metadata of the two shipped source modules, conservatively including examples/tests/tools; inclusion is not a claim that all components are linked into the helper.",
                           "modules": [{"module": item["module"], "attributions": item["attributions"]} for item in collected]}
    owned_write(output / "source-attributions.json", json_bytes(source_attributions))

    # Every SPDX identifier in the runtime/package expressions and source
    # attribution identifiers must have a supplied source or SDK license text.
    supplied_licenses = {path.stem for path in (output / "notices").rglob("*.txt")}
    expressions = {row["licenseExpression"] for row in rows if row.get("licenseExpression")}
    for item in packages:
        for field in ("licenseDeclared", "licenseConcluded"):
            if item["package"].get(field):
                expressions.add(item["package"][field])
    for collection in collected:
        for record in collection["attributions"]:
            if isinstance(record["data"].get("LicenseId"), str):
                expressions.add(record["data"]["LicenseId"])
    identifiers = set()
    for expression in expressions:
        identifiers.update(re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", expression))
    identifiers -= {"OR", "AND", "WITH", "NONE", "NOASSERTION"}
    for identifier in sorted(identifiers - supplied_licenses):
        # A component's LicenseFile may have a project-specific name rather
        # than SPDX-ID.txt. This is still a resolved source license text.
        explicit = any(record["data"].get("LicenseId") == identifier and record["resolvedLicenseFiles"]
                       for collection in collected for record in collection["attributions"])
        if not explicit:
            gaps.append({"code": "license_identifier_text_missing", "licenseId": identifier})

    sbom_mismatches = [row for row in checksum_rows if not row["matches"]]
    provenance = {"schema": 1, "qtVersion": VERSION, "moduleSources": identities,
                  "sdkSboms": sbom_files, "shippedQtFiles": rows, "sdkSbomFileChecksums": checksum_rows,
                  "checksumInterpretation": "The supplied Qt bytes are matched by SHA256 to the immutable helper manifest and installed official SDK. SPDX SHA1 entries are attribution metadata and do not authenticate the runtime bytes. Any observed mismatch is retained without inferring its cause.",
                  "sourceInterpretation": "Official release archive checksums and source .tag match the SDK SBOM source commit or that commit's official Git tree. This establishes release-source correspondence, not a reproducible binary rebuild."}
    owned_write(output / "provenance.json", json_bytes(provenance))
    lines = ["Qt 6.11.2 — source and notice materials for the ValMAX transfer helper", "",
             "Copyright (C) The Qt Company Ltd. and other contributors.", "",
             "The helper dynamically links to the supplied Qt shared libraries. These materials preserve the upstream license alternatives; ValMAX does not acquire or grant a Qt commercial license.",
             "Qt library sources are supplied in full official archives under sources/. Their license texts are under notices/<module>/LICENSES/. See LGPL-3.0-only.txt and GPL-3.0-only.txt for the LGPL route; each individual third-party component keeps its own terms.",
             "This collection does not modify the Qt runtime. It provides source/notice material, not legal advice or a claim that an entire distributor release satisfies every applicable obligation.", "",
             "Rebuilding: the complete QtBase and QtDeclarative source archives include their build files and third-party source. The original SDK's configure arguments and source commits are preserved in sbom/ and provenance.json. Build dependencies/toolchain are not shipped as application runtime.", "",
             "Files: runtime-attributions.json identifies the packages associated with the shipped binaries and their recorded dependencies. source-attributions.json retains all upstream attribution entries for those source modules, including some unshipped examples/tests/tools. Referenced component license files are extracted without changing their bytes.", "",
             "Source archive correspondence:"]
    for identity in identities:
        lines.append(f"- {identity['module']}: SDK commit {identity['sdkCommit']}; archive .tag {identity['archiveTag']} ({identity['archiveTagKind']}).")
    lines.extend(["", f"Observed SPDX file-checksum differences: {len(sbom_mismatches)}. Actual runtime SHA256 identities and every SPDX expected/actual SHA1 are retained in provenance.json; no reason for the differences is assumed.", "",
                  "Runtime package license and copyright statements:"])
    for item in packages:
        package = item["package"]
        lines.extend(["", f"[{item['module']}] {package['name']}",
                      "License: " + package.get("licenseConcluded", package.get("licenseDeclared", "NOASSERTION")),
                      "Copyright: " + package.get("copyrightText", "NOASSERTION")])
    lines.extend(["", "Source third-party attribution statements:"])
    for collection in collected:
        for record in collection["attributions"]:
            value = record["data"]
            copyright_text = value.get("Copyright", "NOASSERTION")
            if isinstance(copyright_text, list):
                copyright_text = "\n".join(copyright_text)
            lines.extend(["", f"[{collection['module']}] {value.get('Name', value.get('Id', 'Unnamed'))}",
                          "License: " + str(value.get("LicenseId", value.get("License", "NOASSERTION"))),
                          "Copyright: " + str(copyright_text), "Usage: " + str(value.get("QtUsage", "Not specified")),
                          "License files: " + ", ".join("notices/" + collection["module"] + "/" + name for name in record["resolvedLicenseFiles"])])
    lines.extend(["", "Collection gaps:", json.dumps(gaps, ensure_ascii=False, indent=2), ""])
    owned_write(output / "QT-NOTICES.txt", "\n".join(lines).encode("utf8"))
    materials = []
    for path in sorted(output.rglob("*")):
        ordinary(path, path.is_dir())
        if path.is_dir() or path.name == "materials-manifest.json":
            continue
        require(path.suffix != ".part", "unfinished download in materials")
        materials.append({"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)})
    manifest = {"schema": 1, "kind": "valmax-qt-source-materials", "qtVersion": VERSION,
                "sourcePackageManifestSha256": proof["packageManifestSha256"], "helperProofSha256": proof_hash,
                "sourceCollectorSha256": sha(Path(__file__)), "modules": modules,
                "archives": archives, "moduleSources": identities, "files": materials,
                "gaps": gaps, "materialsComplete": not gaps,
                "limitations": ["Source and notice material collection only; not a legal conclusion for the complete release.",
                                "No reproducible Qt binary rebuild was performed.",
                                "SPDX SHA1 mismatches are retained; payload SHA256 and SDK-origin checks are separate."]}
    manifest_path = output / "materials-manifest.json"
    owned_write(manifest_path, json_bytes(manifest))
    materials.append({"path": manifest_path.name, "bytes": manifest_path.stat().st_size, "sha256": sha(manifest_path)})
    # Recheck the immutable package and SDK after collection, not only before it.
    after = load_inputs()
    require(after[1] == proof_hash and after[2] == rows and after[3] == modules and after[5] == checksum_rows,
            "helper/SDK provenance changed during collection")
    report = {"schema": 1, "kind": "valmax-qt-source-materials", "qtVersion": VERSION,
              "output": str(output), "materialsFiles": materials,
              "materialsManifest": str(manifest_path), "materialsManifestSha256": sha(manifest_path),
              "sourcePackageManifestSha256": proof["packageManifestSha256"], "helperProof": str(HELPER_PROOF),
              "helperProofSha256": proof_hash, "sourceCollector": str(Path(__file__)), "sourceCollectorSha256": sha(Path(__file__)),
              "modules": modules, "archives": archives, "moduleSources": identities,
              "sdkSboms": sbom_files, "shippedQtFiles": rows, "gaps": gaps,
              "sdkSbomChecksumMismatches": len(sbom_mismatches), "materialsComplete": not gaps,
              "archiveBytes": sum(item["bytes"] for item in archives),
              "totalMaterialBytes": sum(item["bytes"] for item in materials),
              "checks": {"officialArchiveChecksums": True, "packageSdkByteIdentity": True,
                         "sourceCommitOrTreeIdentity": all(item["matches"] for item in identities),
                         "allSourceLicenseReferences": not any(item["code"] == "attribution_license_reference" for item in gaps),
                         "sdkAndPackageUnchanged": True},
              "limitations": manifest["limitations"]}
    owned_write(proof_path, json_bytes(report))
    print(json.dumps({"proof": str(proof_path), "sha256": sha(proof_path), "materials": len(materials),
                      "bytes": report["totalMaterialBytes"], "materialsComplete": not gaps, "gaps": gaps}), flush=True)


if __name__ == "__main__":
    main()
