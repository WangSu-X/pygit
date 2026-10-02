"""Security and boundary tests: path validation, symlink protection, untracked files."""

from pathlib import Path
import tempfile
import unittest

from gitlet.errors import GitletError
from gitlet.repository import Repository


class SecurityTests(unittest.TestCase):
    """Test security features: path validation, symlink protection, untracked file safety."""

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

    def save(self, path, content, message="save"):
        """Write, stage and commit a file."""
        self.write(path, content)
        self.repo.add(path)
        self.repo.commit(message)
        return self.repo.head_commit_id

    def disk_snapshot(self):
        """Capture all files in working directory."""
        return {p.relative_to(self.cwd).as_posix(): p.read_bytes()
                for p in self.cwd.rglob("*") if p.is_file() and not p.is_symlink()}

    # Path validation

    def test_invalid_paths_rejected(self):
        """Verify invalid and malicious paths are rejected."""
        invalid_paths = [
            "../outside",           # Parent directory escape
            "/absolute",            # Absolute path
            "src//file",            # Double slash
            "src/./file",           # Dot component
            ".gitlet/HEAD",         # Metadata directory
            "src/../file",          # Parent reference
            "C:/outside",           # Windows absolute
            "src\\file",            # Windows separator
        ]
        
        for path in invalid_paths:
            with self.subTest(path=path):
                before = self.disk_snapshot()
                with self.assertRaises(GitletError):
                    self.repo.add(path)
                self.assertEqual(before, self.disk_snapshot())

    # Symlink traversal protection

    def test_symlink_traversal_protection_on_add(self):
        """Verify add doesn't follow symlinks outside repository."""
        self.save("src/file", b"inside")
        (self.cwd / "src/file").unlink()
        (self.cwd / "src").rmdir()
        
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "file"
            outside.write_bytes(b"outside")
            (self.cwd / "src").symlink_to(other, target_is_directory=True)
            
            with self.assertRaises(GitletError):
                self.repo.add("src/file")
            
            self.assertEqual(outside.read_bytes(), b"outside")

    def test_symlink_traversal_protection_on_checkout(self):
        """Verify checkout doesn't follow symlinks."""
        self.save("src/file", b"inside")
        (self.cwd / "src/file").unlink()
        (self.cwd / "src").rmdir()
        
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "file"
            outside.write_bytes(b"outside")
            (self.cwd / "src").symlink_to(other, target_is_directory=True)
            
            with self.assertRaises(GitletError):
                self.repo.checkout_file("src/file")
            
            self.assertEqual(outside.read_bytes(), b"outside")

    def test_symlink_traversal_protection_on_reset(self):
        """Verify reset doesn't follow symlinks."""
        commit_id = self.save("src/file", b"inside")
        (self.cwd / "src/file").unlink()
        (self.cwd / "src").rmdir()
        
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "file"
            outside.write_bytes(b"outside")
            (self.cwd / "src").symlink_to(other, target_is_directory=True)
            
            with self.assertRaises(GitletError):
                self.repo.reset(commit_id)
            
            self.assertEqual(outside.read_bytes(), b"outside")

    # Branch name validation

    def test_branch_name_validation(self):
        """Verify invalid branch names are rejected."""
        self.repo.branch("feature")
        
        invalid_names = [
            "feature/login",    # Conflicts with existing branch
            "../escape",        # Parent directory escape
            "/absolute",        # Absolute path
            "bad.lock",         # Lock file extension
            "a//b",             # Double slash
        ]
        
        for branch in invalid_names:
            with self.subTest(branch=branch):
                before = self.disk_snapshot()
                with self.assertRaises(GitletError):
                    self.repo.branch(branch)
                self.assertEqual(before, self.disk_snapshot())

    def test_branch_symlink_conflicts_rejected(self):
        """Verify branch creation rejects symlink conflicts."""
        (self.repo.refs.heads / "alias").symlink_to(self.repo.refs.heads / "master")
        with self.assertRaises(GitletError):
            self.repo.branch("alias")

    # Untracked file protection

    def test_untracked_file_protection_on_checkout(self):
        """Verify checkout protects untracked files from being overwritten."""
        initial = self.repo.head_commit_id
        self.repo.branch("empty")
        target = self.save("z.txt", b"saved", "with z")
        self.save("a.txt", b"a", "with a")
        self.repo.checkout_branch("empty")
        
        # Untracked file would be overwritten
        self.write("z.txt", b"untracked")
        with self.assertRaisesRegex(GitletError, "untracked file"):
            self.repo.checkout_branch("master")
        self.assertEqual(self.repo.head_commit_id, initial)
        
        # Even identical content is protected
        self.write("z.txt", b"saved")
        with self.assertRaisesRegex(GitletError, "untracked file"):
            self.repo.reset(target)
        self.assertEqual(self.repo.head_commit_id, initial)

    def test_untracked_file_protection_on_merge(self):
        """Verify merge protects untracked files."""
        self.save("base", b"base")
        self.repo.branch("dev")
        self.save("left", b"left")
        self.repo.checkout_branch("dev")
        self.save("right", b"right")
        self.repo.checkout_branch("master")
        
        self.write("right", b"precious")
        before = self.disk_snapshot()
        
        with self.assertRaisesRegex(GitletError, "untracked file"):
            self.repo.merge("dev")
        
        self.assertEqual(before, self.disk_snapshot())

    def test_directory_replacement_protects_untracked(self):
        """Verify replacing directory with file protects untracked content."""
        file_commit = self.save("item", b"file")
        (self.cwd / "item").unlink()
        self.repo.add("item")
        dir_commit = self.save("item/tracked", b"tracked")
        
        (self.cwd / "item/precious").write_bytes(b"keep")
        before = self.disk_snapshot()
        
        with self.assertRaisesRegex(GitletError, "untracked file"):
            self.repo.reset(file_commit)
        
        self.assertEqual(before, self.disk_snapshot())

    # Path conflict validation

    def test_commit_rejects_conflicting_paths(self):
        """Verify commit rejects trees with file/directory conflicts."""
        self.save("item/file", b"nested")
        
        # Try to stage both "item" (file) and "item/file" (in directory)
        obj_id = self.repo.save_blob(b"file")
        self.repo.write_stage({"item": obj_id}, set())
        
        before = self.disk_snapshot()
        with self.assertRaisesRegex(GitletError, "paths conflict"):
            self.repo.commit("invalid")
        
        self.assertEqual(before, self.disk_snapshot())

    def test_merge_rejects_conflicting_paths_early(self):
        """Verify merge rejects file/directory conflicts before any changes."""
        self.save("base", b"base")
        self.repo.branch("dev")
        self.save("item", b"left")
        self.repo.checkout_branch("dev")
        self.save("item/file", b"right")
        self.repo.checkout_branch("master")
        
        before = self.disk_snapshot()
        with self.assertRaisesRegex(GitletError, "paths conflict"):
            self.repo.merge("dev")
        
        self.assertEqual(before, self.disk_snapshot())


if __name__ == "__main__":
    unittest.main()
