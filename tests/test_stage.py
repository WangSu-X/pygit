"""Staging workspace state for files, directories and the whole repository."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from gitlet.errors import GitletError
from gitlet.models import Blob, object_id
from gitlet.repository import Repository


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.repo = Repository(self.cwd)
        self.repo.init()

    def write(self, path, content):
        target = self.cwd / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def save(self, path, content):
        self.write(path, content)
        self.repo.add(path)
        self.repo.commit("save")

    def disk_snapshot(self):
        return {p.relative_to(self.cwd).as_posix(): p.read_bytes()
                for p in self.cwd.rglob("*") if p.is_file() and not p.is_symlink()}

    def status(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.repo.status()
        return output.getvalue()

    def test_stage_layout_and_cli_commands(self):
        self.assertTrue((self.repo.directory / "stage.json").is_file())
        self.assertFalse((self.repo.directory / "index.json").exists())
        launcher = Path(__file__).resolve().parents[1] / "gitlet_cli.py"
        self.write("src/new", b"new")
        result = subprocess.run([sys.executable, str(launcher), "add", "."],
                                cwd=self.cwd, capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(set(self.repo.stage.read().additions), {"src/new"})
        result = subprocess.run([sys.executable, str(launcher), "rm", "src/new"],
                                cwd=self.cwd, capture_output=True, text=True)
        self.assertEqual(result.stdout, "No command with that name exists.\n")
        self.assertTrue((self.cwd / "src/new").exists())

    def test_directory_add_stages_new_modified_deleted_and_preserves_other_stage(self):
        self.save("src/changed", b"old")
        self.save("src/deleted", b"old")
        self.save("outside", b"old")
        self.write("outside", b"staged")
        self.repo.add("outside")
        self.write("outside", b"later")
        self.write("src/changed", b"new")
        self.write("src/nested/new", b"new")
        (self.cwd / "src/deleted").unlink()
        self.repo.add("src")
        stage = self.repo.stage.read()
        self.assertEqual(set(stage.additions), {"src/changed", "src/nested/new", "outside"})
        self.assertEqual(stage.removals, {"src/deleted"})
        self.assertEqual(self.repo.blob_content(stage.additions["outside"]), b"staged")
        self.repo.commit("batch")
        self.assertEqual(set(self.repo.snapshot()), {"src/changed", "src/nested/new", "outside"})
        self.assertTrue(self.repo.stage.read().is_empty())

    def test_add_removed_directory_uses_head_and_stage_paths(self):
        self.save("src/nested/tracked", b"tracked")
        self.write("src/pending", b"new")
        self.repo.add("src/pending")
        shutil.rmtree(self.cwd / "src")
        self.repo.add("src")
        self.assertEqual(self.repo.read_stage(), ({}, {"src/nested/tracked"}))
        self.repo.add("src")  # Repeated staging is idempotent.
        self.assertEqual(self.repo.read_stage(), ({}, {"src/nested/tracked"}))
        self.repo.commit("delete directory")
        self.assertEqual(self.repo.snapshot(), {})

    def test_add_dot_excludes_metadata_and_cancels_missing_new_files(self):
        self.save("old", b"old")
        self.write("pending", b"new")
        self.repo.add("pending")
        (self.cwd / "pending").unlink()
        (self.cwd / "old").unlink()
        self.write("new", b"new")
        self.repo.add(".")
        self.assertEqual(self.repo.read_stage(), ({"new": object_id(Blob(b"new"))}, {"old"}))
        self.repo.commit("all")
        self.assertEqual(set(self.repo.snapshot()), {"new"})

    def test_stage_deletion_then_recreation_and_restoration(self):
        self.save("file", b"old")
        (self.cwd / "file").unlink()
        self.repo.add("file")
        self.write("file", b"changed")
        self.assertIn("file (deleted)", self.status())
        self.assertIn("=== Untracked Files ===\nfile", self.status())
        self.repo.add("file")
        stage = self.repo.stage.read()
        self.assertFalse(stage.removals)
        self.assertEqual(self.repo.blob_content(stage.additions["file"]), b"changed")
        self.write("file", b"old")
        self.repo.add("file")
        self.assertTrue(self.repo.stage.read().is_empty())

    def test_status_compares_stage_to_head_and_workspace_to_stage(self):
        self.save("file", b"A")
        self.write("file", b"B")
        self.repo.add("file")
        self.write("file", b"C")
        status = self.status()
        self.assertIn("=== Changes Staged For Commit ===\nfile (modified)", status)
        self.assertIn("=== Modifications Not Staged For Commit ===\nfile (modified)", status)
        (self.cwd / "file").unlink()
        self.assertIn("file (deleted)", self.status())
        self.repo.add("file")
        self.assertEqual(self.repo.read_stage(), ({}, {"file"}))
        self.assertIn("=== Modifications Not Staged For Commit ===\n\n", self.status())

    def test_add_scope_handles_file_directory_replacements(self):
        self.save("item", b"file")
        (self.cwd / "item").unlink()
        self.write("item/sub/file", b"nested")
        self.repo.add("item")
        self.assertEqual(set(self.repo.stage.read().additions), {"item/sub/file"})
        self.assertEqual(self.repo.stage.read().removals, {"item"})
        self.repo.commit("directory")
        shutil.rmtree(self.cwd / "item")
        self.write("item", b"file again")
        self.repo.add("item")
        self.assertEqual(set(self.repo.stage.read().additions), {"item"})
        self.assertEqual(self.repo.stage.read().removals, {"item/sub/file"})
        self.repo.commit("file")
        self.assertEqual(set(self.repo.snapshot()), {"item"})

    def test_batch_add_failure_preserves_stage_and_objects(self):
        self.save("src/tracked", b"tracked")
        self.write("src/new", b"new")
        (self.cwd / "src/tracked").unlink()
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "precious"
            outside.write_bytes(b"keep")
            (self.cwd / "src/tracked").symlink_to(outside)
            before = self.disk_snapshot()
            with self.assertRaises(GitletError):
                self.repo.add("src")
            self.assertEqual(before, self.disk_snapshot())
            self.assertEqual(outside.read_bytes(), b"keep")

    def test_empty_directory_and_unknown_path(self):
        (self.cwd / "empty").mkdir()
        self.repo.add("empty")
        self.repo.add(".")
        self.assertTrue(self.repo.stage.read().is_empty())
        with self.assertRaisesRegex(GitletError, "File does not exist"):
            self.repo.add("unknown")


if __name__ == "__main__":
    unittest.main()
