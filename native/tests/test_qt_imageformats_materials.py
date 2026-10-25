"""Synthetic fail-closed checks for optional QtImageFormats staging materials."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "native/tooling"))
import assemble_native_runtime as assembly  # noqa: E402


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class QtImageFormatsMaterials(unittest.TestCase):
    def setUp(self):
        parent = Path("E:/ValMAXBuild/tmp")
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="qtimageformats-materials-", dir=parent)
        self.root = Path(self.temporary.name).resolve()
        self.assertEqual(self.root.parent, parent.resolve())

    def tearDown(self):
        self.temporary.cleanup()

    def file(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def fixture(self):
        source_name = "qtimageformats-everywhere-src-6.11.2.tar.xz"
        prefix = "qtimageformats-everywhere-src-6.11.2/"
        license_rows, notice_rows, members = [], [], {}
        for name in sorted(assembly.QT_IMAGEFORMATS_LICENSES):
            data = ("original license " + name).encode()
            path = self.file("licenses/qtimageformats/" + name, data)
            member = prefix + name
            members[member] = data
            license_rows.append({"path": str(path), "archiveMember": member,
                                 "bytes": len(data), "sha256": digest(data)})
        for name in sorted(assembly.QT_IMAGEFORMATS_THIRDPARTY):
            data = ("original codec notice " + name).encode()
            part = Path(name)
            path = self.file("thirdparty/" + part.parts[-2] + "/" + part.name, data)
            member = prefix + name
            members[member] = data
            notice_rows.append({"component": part.parts[-2], "path": str(path),
                                "archiveMember": member, "bytes": len(data), "sha256": digest(data)})
        archive_path = self.root / "source-downloads" / source_name
        archive_path.parent.mkdir(parents=True)
        with tarfile.open(archive_path, "w:xz") as archive:
            for name, data in sorted(members.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        metalink = self.file("source-downloads/" + source_name + ".meta4", b"synthetic metalink")
        binary_name = "6.11.2-0-qtimageformats-Windows-MSVC2022-X86_64.7z"
        binary = self.file("binary-downloads/" + binary_name, b"synthetic official binary archive")
        index = self.file("Updates.xml", b"synthetic official index")
        images = []
        plan = assembly.Plan()
        for name in sorted(assembly.QT_IMAGEFORMATS_DLLS):
            data = ("synthetic exact DLL " + name).encode()
            selected = self.file("selected-dlls/qtimageformats/" + name, data)
            runtime_path = "mod/plugins/imageformats/" + name
            images.append({"runtimePath": runtime_path, "runtimeSha256": digest(data),
                           "officialDll": {"member": "plugins\\imageformats\\" + name,
                                           "path": str(selected), "bytes": len(data), "sha256": digest(data)}})
            plan.add(runtime_path, selected, digest(data), "synthetic existing runtime")
        audit = {
            "schema": 1, "verified": True, "qtVersion": "6.11.2", "architecture": "msvc2022_64",
            "officialIndex": str(index), "officialIndexSha256": assembly.sha(index),
            "modules": [{"module": module} for module in
                        ("qtlottie", "qtmultimedia", "qtpositioning", "qtsvg", "qtwebchannel")]
            + [{"module": "qtimageformats", "exactBinaryMatches": 5, "runtimeImages": images,
                "sourceArchive": {"module": "qtimageformats",
                                  "url": "https://download.qt.io/archive/qt/6.11/6.11.2/submodules/" + source_name,
                                  "archive": str(archive_path), "sha256": assembly.sha(archive_path),
                                  "metalink": str(metalink), "metalinkSha256": assembly.sha(metalink),
                                  "licenseTexts": license_rows},
                "binaryArchive": {"package": "qt.qt6.6112.addons.qtimageformats.win64_msvc2022_64",
                                  "url": "https://download.qt.io/online/qtsdkrepository/windows_x86/desktop/qt6_6112/" + binary_name,
                                  "archive": str(binary), "archiveBytes": binary.stat().st_size,
                                  "archiveSha256": assembly.sha(binary)}}],
        }
        audit_path = self.file("audit.json", json.dumps(audit).encode())
        notices = {"schema": 1, "verified": True, "qtVersion": "6.11.2",
                   "sourceAuditSha256": assembly.sha(audit_path),
                   "sourceArchive": str(archive_path), "sourceSha256": assembly.sha(archive_path),
                   "originalTexts": notice_rows,
                   "upstreamAttribution": [{"component": "libtiff", "licenseFile": "COPYRIGHT"},
                                           {"component": "libwebp", "licenseFile": "COPYING"}],
                   "completeForWholeClient": False}
        notices_path = self.file("notices.json", json.dumps(notices).encode())
        return plan, audit_path, notices_path

    def test_exact_runtime_bound_sources_and_notices_are_planned(self):
        plan, audit_path, notices_path = self.fixture()
        proof = assembly.add_qt_imageformats_materials(plan, audit_path, notices_path)
        material_rows = [row for row in plan.entries.values()
                         if row["path"].startswith("mod/licenses/QtImageFormats/")]
        self.assertEqual((proof["runtimeImagesMatched"], proof["copiedMaterialFiles"]), (5, 13))
        self.assertEqual(len(material_rows), 13)
        self.assertFalse(proof["codecBundlingProven"])
        self.assertFalse(proof["wholeClientMaterialsComplete"])

    def test_missing_runtime_image_fails_before_material_copy(self):
        plan, audit_path, notices_path = self.fixture()
        plan.entries.pop("mod/plugins/imageformats/qwebp.dll")
        with self.assertRaisesRegex(ValueError, "does not match planned runtime image"):
            assembly.add_qt_imageformats_materials(plan, audit_path, notices_path)
        self.assertFalse(any(row["path"].startswith("mod/licenses/QtImageFormats/")
                             for row in plan.entries.values()))

    def test_changed_audit_and_codec_notice_are_rejected(self):
        plan, audit_path, notices_path = self.fixture()
        notices = json.loads(notices_path.read_text("utf8"))
        notices["sourceAuditSha256"] = "0" * 64
        notices_path.write_text(json.dumps(notices), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "matching original QtImageFormats"):
            assembly.add_qt_imageformats_materials(plan, audit_path, notices_path)
        notices["sourceAuditSha256"] = assembly.sha(audit_path)
        notices_path.write_text(json.dumps(notices), encoding="utf8")
        Path(notices["originalTexts"][0]["path"]).write_bytes(b"X" * notices["originalTexts"][0]["bytes"])
        with self.assertRaisesRegex(ValueError, "frozen input changed"):
            assembly.add_qt_imageformats_materials(plan, audit_path, notices_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
