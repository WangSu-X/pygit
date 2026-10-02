"""Staging area tests: add operations, directory handling, status display."""

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


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "gitlet_cli.py"


class StagingTests(unittest.TestCase):
    """Test staging area functionality: add, status, batch operations."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.repo = Repository(self.cwd)
        self.repo.init()

    def write(self, path, content):
        """Write file to working directory."""
        target = self.cwd / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())

    def save(self, path, content):
        """Write, stage and commit a file."""
        self.write(path, content)
        self.repo.add(path)
        self.repo.commit("save")

    def disk_snapshot(self):
        """Capture all files in working directory."""
        return {p.relative_to(self.cwd).as_posix(): p.read_bytes()
                for p in self.cwd.rglob("*") if p.is_file() and not p.is_symlink()}

    def status(self):
        """Get status output as string."""
        output = io.StringIO()
        with redirect_stdout(output):
            self.repo.status()
        return output.getvalue()

    # Staging area layout

    def test_stage_file_layout(self):
        """Verify staging area uses stage.json."""
        self.assertTrue((self.repo.directory / "stage.json").is_file())
        self.assertFalse((self.repo.directory / "index.json").exists())

    def test_stage_via_cli(self):
        """Verify staging works through CLI."""
        self.write("src/new", b"new")
        result = subprocess.run([sys.executable, str(LAUNCHER), "add", "."],
                                cwd=self.cwd, capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(set(self.repo.stage.read().additions), {"src/new"})

    def test_rm_command_not_available(self):
        """Verify rm command doesn't exist (use add for deletions)."""
        self.write("file", b"content")
        result = subprocess.run([sys.executable, str(LAUNCHER), "rm", "file"],
                                cwd=self.cwd, capture_output=True, text=True)
        self.assertEqual(result.stdout, "No command with that name exists.\n")
        self.assertTrue((self.cwd / "file").exists())

    # Directory add operations

    def test_directory_add_stages_all_changes(self):
        """Verify adding directory stages new, modified, and deleted files."""
        self.save("src/changed", b"old")
        self.save("src/deleted", b"old")
        self.save("outside", b"old")
        
        # Stage change to outside
        self.write("outside", b"staged")
        self.repo.add("outside")
        self.write("outside", b"later")
        
        # Make changes in src/
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

    def test_add_removed_directory_stages_all_tracked_deletions(self):
        """Verify adding removed directory stages all tracked file deletions."""
        self.save("src/nested/tracked", b"tracked")
        self.write("src/pending", b"new")
        self.repo.add("src/pending")
        
        shutil.rmtree(self.cwd / "src")
        self.repo.add("src")
        
        self.assertEqual(self.repo.read_stage(), ({}, {"src/nested/tracked"}))
        
        # Repeated staging is idempotent
        self.repo.add("src")
        self.assertEqual(self.repo.read_stage(), ({}, {"src/nested/tracked"}))
        
        self.repo.commit("delete directory")
        self.assertEqual(self.repo.snapshot(), {})

    def test_add_dot_excludes_gitlet_directory(self):
        """Verify add . excludes .gitlet metadata."""
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

    def test_add_empty_directory_is_noop(self):
        """Verify adding empty directory does nothing."""
        (self.cwd / "empty").mkdir()
        self.repo.add("empty")
        self.repo.add(".")
        self.assertTrue(self.repo.stage.read().is_empty())

    def test_add_unknown_path_fails(self):
        """Verify adding non-existent path fails."""
        with self.assertRaisesRegex(GitletError, "File does not exist"):
            self.repo.add("unknown")

    # File lifecycle staging

    def test_stage_deletion_then_recreation(self):
        """Verify staging deletion then recreating file."""
        self.save("file", b"old")
        (self.cwd / "file").unlink()
        self.repo.add("file")
        self.write("file", b"changed")
        
        status = self.status()
        self.assertIn("file (deleted)", status)
        self.assertIn("=== Untracked Files ===\nfile", status)
        
        self.repo.add("file")
        stage = self.repo.stage.read()
        self.assertFalse(stage.removals)
        self.assertEqual(self.repo.blob_content(stage.additions["file"]), b"changed")
        
        # Restore original content
        self.write("file", b"old")
        self.repo.add("file")
        self.assertTrue(self.repo.stage.read().is_empty())

    # File/directory replacements

    def test_add_handles_file_directory_replacement(self):
        """Verify add correctly handles file replaced by directory."""
        self.save("item", b"file")
        (self.cwd / "item").unlink()
        self.write("item/sub/file", b"nested")
        
        self.repo.add("item")
        
        self.assertEqual(set(self.repo.stage.read().additions), {"item/sub/file"})
        self.assertEqual(self.repo.stage.read().removals, {"item"})
        
        self.repo.commit("directory")
        
        # Replace directory with file
        shutil.rmtree(self.cwd / "item")
        self.write("item", b"file again")
        self.repo.add("item")
        
        self.assertEqual(set(self.repo.stage.read().additions), {"item"})
        self.assertEqual(self.repo.stage.read().removals, {"item/sub/file"})
        
        self.repo.commit("file")
        self.assertEqual(set(self.repo.snapshot()), {"item"})

    # Status display

    def test_status_compares_stage_to_head_and_workspace(self):
        """Verify status shows both staged and unstaged changes."""
        self.save("file", b"A")
        self.write("file", b"B")
        self.repo.add("file")
        self.write("file", b"C")
        
        status = self.status()
        self.assertIn("=== Changes Staged For Commit ===\nfile (modified)", status)
        self.assertIn("=== Modifications Not Staged For Commit ===\nfile (modified)", status)
        
        # Delete file
        (self.cwd / "file").unlink()
        self.assertIn("file (deleted)", self.status())
        
        # Stage deletion
        self.repo.add("file")
        self.assertEqual(self.repo.read_stage(), ({}, {"file"}))
        self.assertIn("=== Modifications Not Staged For Commit ===\n\n", self.status())

    # Batch operation safety

    def test_batch_add_failure_preserves_state(self):
        """Verify failed batch add doesn't corrupt staging area."""
        self.save("src/tracked", b"tracked")
        self.write("src/new", b"new")
        (self.cwd / "src/tracked").unlink()
        
        # Create symlink to outside directory
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "precious"
            outside.write_bytes(b"keep")
            (self.cwd / "src/tracked").symlink_to(outside)
            
            before = self.disk_snapshot()
            with self.assertRaises(GitletError):
                self.repo.add("src")
            
            # State unchanged after failure
            self.assertEqual(before, self.disk_snapshot())
            self.assertEqual(outside.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
