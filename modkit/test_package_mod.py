from pathlib import Path
import tempfile
import unittest

import hashlib
import os

from package_mod import validate_output, verify_program_tree


class PackageOutputTests(unittest.TestCase):
    def test_rejects_nested_copy_and_ancestors_before_creating_any_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            runtime, source = base / "runtime", base / "source"
            runtime.mkdir()
            source.mkdir()
            for output in (runtime, runtime / "nested", source / "build", base, runtime / "deep" / "output"):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    validate_output(output, runtime, source)
            self.assertFalse((runtime / "nested").exists())
            validate_output(base / "independent-build", runtime, source)

    def test_rejects_extra_auth_archive_missing_files_and_tampered_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            program = root / "synthetic.dll"
            program.write_bytes(b"synthetic-program-only")
            expected = {program.name: hashlib.sha256(program.read_bytes()).hexdigest()}
            verify_program_tree(root, expected)
            extra = root / "session.mdata"
            extra.write_bytes(b"synthetic-extra-only")
            with self.assertRaises(ValueError):
                verify_program_tree(root, expected)
            extra.unlink()
            program.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                verify_program_tree(root, expected)
            program.unlink()
            with self.assertRaises(ValueError):
                verify_program_tree(root, expected)

    def test_rejects_hardlinked_program_even_when_content_hash_is_correct(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root = base / "payload"
            root.mkdir()
            original = base / "original"
            original.write_bytes(b"synthetic-program-only")
            os.link(original, root / "synthetic.dll")
            with self.assertRaises(ValueError):
                verify_program_tree(root, {"synthetic.dll": hashlib.sha256(original.read_bytes()).hexdigest()})

    def test_verified_source_shared_dll_is_copied_to_independent_payload(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source, payload = base / "source", base / "payload"
            source.mkdir()
            payload.mkdir()
            original = base / "original"
            original.write_bytes(b"synthetic-program-only")
            os.link(original, source / "synthetic.dll")
            expected = {"synthetic.dll": hashlib.sha256(original.read_bytes()).hexdigest()}
            verify_program_tree(source, expected, allow_shared_dlls=True)
            with self.assertRaises(ValueError):
                verify_program_tree(source, expected)
            shutil.copyfile(source / "synthetic.dll", payload / "synthetic.dll")
            verify_program_tree(payload, expected)
            self.assertEqual((source / "synthetic.dll").stat().st_nlink, 2)
            self.assertEqual((payload / "synthetic.dll").stat().st_nlink, 1)
            with self.assertRaises(ValueError):
                verify_program_tree(source, {"synthetic.dll": "0" * 64}, allow_shared_dlls=True)
            third = base / "third"
            os.link(original, third)
            with self.assertRaises(ValueError):
                verify_program_tree(source, expected, allow_shared_dlls=True)

    def test_shared_executable_and_nonboolean_policy_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source = base / "source"
            source.mkdir()
            original = base / "original"
            original.write_bytes(b"synthetic-program-only")
            os.link(original, source / "synthetic.exe")
            expected = {"synthetic.exe": hashlib.sha256(original.read_bytes()).hexdigest()}
            with self.assertRaises(ValueError):
                verify_program_tree(source, expected, allow_shared_dlls=True)
            with self.assertRaises(ValueError):
                verify_program_tree(source, expected, allow_shared_dlls="true")


if __name__ == "__main__":
    unittest.main()
