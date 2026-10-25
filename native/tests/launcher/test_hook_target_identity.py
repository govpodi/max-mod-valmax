"""Static packaging identities on owned synthetic PE bytes; no DLL execution."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "native/tooling"))
import hook_target_identity as identity


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pe_bytes(marker, image_size=4096):
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, 0x8664)
    struct.pack_into("<H", data, 0x94, 0xf0)
    struct.pack_into("<H", data, 0x98, 0x20b)
    struct.pack_into("<I", data, 0xd0, image_size)
    return bytes(data) + marker


class HookTargetIdentity(unittest.TestCase):
    def setUp(self):
        base = Path("D:/ValMAXBuild/tmp")
        base.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="hook-identity-", dir=base)).resolve()
        self.assertEqual(self.root.parent, base.resolve())
        # Small fixtures are retained; no cleanup of caches, builds or profiles.
        paths = tuple(dict.fromkeys(path for spec in identity._FAMILIES.values() for path in spec[4]))
        self.targets = {path: pe_bytes(path.encode("ascii"), 4096 + 4096 * index) for index, path in enumerate(paths)}
        self.metadata = {}
        for family, (_, _, _, _, paths) in identity._FAMILIES.items():
            rows = {path: {"sha256": digest(self.targets[path]), "size": len(self.targets[path]),
                           "imageSize": identity._pe_image_size(self.targets[path])} for path in paths}
            common = {"schema": 1, "runtimeRoot": "X:/not-read-provenance", "realProfilesAccessed": False}
            if family == "privacy":
                files = [{"relative": path, **row, "role": 2 if path.startswith("max-service/") else 1,
                          "patches": [], "reversedVendorSha256": "0" * 64} for path, row in rows.items()]
                value = {**common, "namespace": "OWNED", "files": files, "manifestSha256": "1" * 64,
                         "sourceSha256": {"own.py": "2" * 64}, "generatedHeaderSha256": "",
                         "rules": 6, "staticPatches": 0, "callbacks": 0, "networkObserved": False}
            elif family == "ghost":
                value = {**common, "isolatedNamespace": "OWNED", "images": rows, "canonicalCoreSha256": "3" * 64,
                         "sourceSha256": {"own.py": "4" * 64}, "headerSha256": "",
                         "codeGuards": [1, 1], "vtableGuards": [1, 1]}
            else:
                for row in rows.values():
                    row.pop("imageSize")  # This legacy sidecar derives SizeOfImage from its header.
                    row.update(codeGuards=1, vtableGuards=1)
                value = {**common, "images": rows, "specSha256": "5" * 64, "generatedSha256": "",
                         "asset": {"name": "own.png", "sha256": "6" * 64, "size": 1}}
            self.metadata[family] = value
        self.commit()

    def commit(self, family=None):
        for name, (dll, pins, header, header_key, paths) in identity._FAMILIES.items():
            if family is not None and family != name:
                continue
            value = self.metadata[name]
            if name == "privacy":
                rows = value["files"]
                literals = [f'{{L"{row["relative"]}","{row["sha256"]}",{row["size"]}ull,{row["imageSize"]},{row["role"]},kPatches{index}}}'
                            for index, row in enumerate(rows)]
                text = "inline constexpr FilePin kFiles[] = {" + ",".join(literals) + "};\n"
            else:
                rows = [value["images"][path] for path in paths]
                names = ("Core", "Main") if name == "ghost" else ("Main", "Backend")
                text = "".join(f'inline constexpr ImagePin k{pin} = {{"{row["sha256"]}",{row["size"]}ull,'
                               f'{row.get("imageSize", identity._pe_image_size(self.targets[path]))}u,k{pin}Guards,k{pin}Slots}};\n'
                               for pin, path, row in zip(names, paths, rows))
            header_bytes = text.encode("ascii")
            value[header_key] = digest(header_bytes)
            (self.root / header).write_bytes(header_bytes)
            (self.root / pins).write_text(json.dumps(value), encoding="utf8")
            (self.root / dll).write_bytes(pe_bytes(b"\0".join(row["sha256"].encode("ascii") for row in rows) + b"\0"))

    def verify(self, reader=None):
        return identity.verify_hook_targets(self.root, reader or self.targets.__getitem__)

    def expect_error(self, reason, reader=None):
        with self.assertRaises(identity.HookTargetIdentityError) as caught:
            self.verify(reader)
        self.assertEqual(caught.exception.reason, reason)
        return caught.exception

    def test_exact_positive_contract_and_one_read_per_unique_target(self):
        calls = []
        def reader(path):
            calls.append(path)
            return self.targets[path]
        proof = self.verify(reader)
        self.assertEqual(set(proof), {"schema", "verified", "activationVerified", "verifierSha256", "targetCount",
                                     "uniqueTargetCount", "hooks", "targets"})
        self.assertTrue(proof["verified"])
        self.assertFalse(proof["activationVerified"])
        self.assertEqual((proof["targetCount"], proof["uniqueTargetCount"]), (10, 8))
        self.assertEqual(len(calls), len(set(calls)))
        self.assertEqual(proof["verifierSha256"], digest(Path(identity.__file__).read_bytes()))
        self.assertEqual(proof, self.verify())
        for row in proof["targets"]:
            self.assertEqual(set(row), {"hook", "relative", "sha256", "size", "imageSize"})
        for row in proof["hooks"].values():
            self.assertEqual(set(row), {"dll", "dllSha256", "dllBytes", "pinsFile", "pinsSha256", "headerFile", "headerSha256"})

    def test_missing_sidecar_header_and_dll_are_rejected(self):
        for index, missing in enumerate(identity._FAMILIES["privacy"][:3]):
            with self.subTest(missing=missing):
                folder = self.root / f"missing-{index}"
                folder.mkdir()
                for source in self.root.iterdir():
                    if source.is_file() and source.name != missing:
                        (folder / source.name).write_bytes(source.read_bytes())
                with self.assertRaises(identity.HookTargetIdentityError) as caught:
                    identity.verify_hook_targets(folder, self.targets.__getitem__)
                self.assertEqual(caught.exception.reason, "hook_input_missing")

    def test_changed_header_is_rejected(self):
        path = self.root / "privacy_pins.generated.h"
        path.write_bytes(path.read_bytes() + b"// changed\n")
        self.expect_error("pin_header_sha_mismatch")

    def test_metadata_sha_change_is_not_enough_to_forge_header_identity(self):
        self.metadata["ghost"]["images"]["core.dll"]["sha256"] = "f" * 64
        (self.root / "ghost-pins.json").write_text(json.dumps(self.metadata["ghost"]), encoding="utf8")
        self.expect_error("pin_header_identity_mismatch")

    def test_compiled_sha_literal_required_even_with_matching_sidecars(self):
        (self.root / "valmax_ghost.dll").write_bytes(pe_bytes(b"NO_TARGET_SHA_LITERAL"))
        self.expect_error("compiled_pin_literal_missing")

    def test_hook_dll_must_have_pe_identity(self):
        (self.root / "valmax_ghost.dll").write_bytes(b"NOT_PE" * 20)
        self.expect_error("invalid_target_pe")

    def test_traversal_absolute_normalized_and_windows_paths_are_rejected(self):
        original = copy.deepcopy(self.metadata["privacy"])
        for path in ("../core.dll", "/core.dll", "C:/core.dll", "max-service/../core.dll",
                     "max-service//core.dll", "max-service\\core.dll", "./core.dll", ".", "core.dll:stream"):
            with self.subTest(path=path):
                self.metadata["privacy"] = copy.deepcopy(original)
                self.metadata["privacy"]["files"][0]["relative"] = path
                self.commit("privacy")
                self.expect_error("invalid_pin_header" if "\\" in path else "unsafe_target_path")

    def test_duplicate_target_records_are_rejected(self):
        self.metadata["privacy"]["files"][1] = copy.deepcopy(self.metadata["privacy"]["files"][0])
        self.commit("privacy")
        self.expect_error("unexpected_or_duplicate_targets")

    def test_conflicting_cross_family_identity_is_rejected(self):
        self.metadata["ghost"]["images"]["core.dll"]["sha256"] = "f" * 64
        self.commit("ghost")
        self.expect_error("conflicting_target_identity")

    def test_duplicate_json_keys_are_rejected(self):
        path = self.root / "ghost-pins.json"
        path.write_text(path.read_text("utf8").replace('"schema": 1', '"schema": 1, "schema": 1'), encoding="utf8")
        self.expect_error("duplicate_metadata_key")

    def test_wrong_schema_keys_types_and_nonfinite_json_are_rejected(self):
        original = (self.root / "ghost-pins.json").read_bytes()
        for replacement, expected in ((original.replace(b'"schema": 1', b'"schema": true'), "invalid_hook_metadata_schema"),
                                      (original[:-1] + b',"extra":1}', "invalid_hook_metadata_keys"),
                                      (b'{"schema":NaN}', "invalid_hook_metadata")):
            (self.root / "ghost-pins.json").write_bytes(replacement)
            self.expect_error(expected)

    def test_target_full_sha_size_and_image_size_mismatches_are_rejected(self):
        path = "config.dll"
        original = self.targets[path]
        cases = (original[:-1] + bytes([original[-1] ^ 1]), original + b"extra", pe_bytes(path.encode("ascii"), 0x90000))
        for altered in cases:
            with self.subTest(altered=digest(altered)):
                self.targets[path] = altered
                error = self.expect_error("target_identity_mismatch")
                self.assertEqual(len(error.details), 1)
                self.assertEqual(error.details[0]["relative"], path)
        self.targets[path] = original

    def test_missing_bad_type_and_invalid_pe_target_are_rejected(self):
        for value, reason in ((None, "target_missing"), (bytearray(b"x"), "target_reader_must_return_bytes"), (b"not_pe", "invalid_target_pe")):
            with self.subTest(reason=reason):
                def reader(path):
                    if path != "config.dll":
                        return self.targets[path]
                    if value is None:
                        raise FileNotFoundError(path)
                    return value
                self.expect_error(reason, reader)

    def test_sidecar_changed_during_target_reads_is_rejected(self):
        changed = False
        def reader(path):
            nonlocal changed
            if not changed:
                file = self.root / "ghost-pins.json"
                raw = file.read_bytes()
                file.write_bytes(raw.replace(b'"OWNED"', b'"OTHER"'))
                changed = True
            return self.targets[path]
        self.expect_error("hook_input_changed", reader)

    def test_relative_hook_root_is_rejected(self):
        with self.assertRaises(identity.HookTargetIdentityError) as caught:
            identity.verify_hook_targets(Path("relative"), self.targets.__getitem__)
        self.assertEqual(caught.exception.reason, "absolute_hook_root_required")

    @unittest.skipUnless(Path("C:/ValMAXBuild/build/hooks-all-features/privacy-pins.json").is_file()
                         and Path("D:/ValMAXBuild/build/native-model-cursor-staging-20261021/runtime/mod/max.exe").is_file(),
                         "immutable old hook metadata and current staging are unavailable")
    def test_read_only_old_hook_guards_reject_current_staging_seven_of_ten(self):
        old = Path("C:/ValMAXBuild/build/hooks-all-features")
        stage = Path("D:/ValMAXBuild/build/native-model-cursor-staging-20261021/runtime/mod")
        before = {path.name: digest(path.read_bytes()) for path in old.iterdir()
                  if path.name in {name for spec in identity._FAMILIES.values() for name in spec[:3]}}
        with self.assertRaises(identity.HookTargetIdentityError) as caught:
            identity.verify_hook_targets(old, lambda relative: (stage / relative).read_bytes())
        self.assertEqual(caught.exception.reason, "target_identity_mismatch")
        self.assertEqual(len(caught.exception.details), 7)
        self.assertEqual(before, {name: digest((old / name).read_bytes()) for name in before})


if __name__ == "__main__":
    unittest.main()
