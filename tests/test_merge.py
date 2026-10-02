"""Merge algorithm tests: three-way merge, conflicts, fast-forward."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gitlet.models import Blob, Tree, Commit
from gitlet.services import TreeService
from gitlet.repository import Repository, UNTRACKED


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "gitlet_cli.py"


class MergeTests(unittest.TestCase):
    """Test merge algorithm: conflict detection, fast-forward, ancestor finding."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.repo = Repository(self.cwd)
        self.repo.init()

    def run_cli(self, *args, expected=""):
        """Run gitlet CLI command."""
        result = subprocess.run([sys.executable, str(LAUNCHER), *args],
                                cwd=self.cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        if expected is not None:
            self.assertEqual(result.stdout, expected)
        return result.stdout

    def write(self, name, content):
        """Write file to working directory."""
        path = self.cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())

    def save(self, name="a.txt", content="base\n", message="base"):
        """Write, stage and commit a file."""
        self.write(name, content)
        self.run_cli("add", name)
        self.run_cli("commit", message)
        return self.repo.head_commit_id

    def snapshot(self):
        """Capture current working directory state."""
        return {str(p.relative_to(self.cwd)): p.read_bytes()
                for p in self.cwd.rglob("*") if p.is_file()}

    def failure(self, args, message):
        """Verify command fails with expected message."""
        before = self.snapshot()
        self.run_cli(*args, expected=message + "\n")
        self.assertEqual(self.snapshot(), before, args)

    def diverge(self):
        """Create divergent branches for merge testing."""
        base = self.save()
        self.run_cli("branch", "dev")
        left = self.save("left.txt", "left", "left")
        self.run_cli("checkout", "dev")
        right = self.save("right.txt", "right", "right")
        self.run_cli("checkout", "master")
        return base, left, right

    # Merge error handling

    def test_merge_error_self_branch(self):
        """Verify cannot merge branch with itself."""
        self.save()
        self.failure(("merge", "master"), "Cannot merge a branch with itself.")

    def test_merge_error_missing_branch(self):
        """Verify merge fails on non-existent branch."""
        self.save()
        self.failure(("merge", "missing"), "A branch with that name does not exist.")

    def test_merge_error_uncommitted_changes(self):
        """Verify merge rejects uncommitted changes in staging area."""
        self.save()
        self.run_cli("branch", "dev")
        
        # Deletion only
        (self.cwd / "a.txt").unlink()
        self.run_cli("add", "a.txt")
        self.failure(("merge", "dev"), "You have uncommitted changes.")
        
        # Mixed staging (addition + deletion)
        self.write("b.txt", "b")
        self.run_cli("add", "b.txt")
        self.failure(("merge", "dev"), "You have uncommitted changes.")

    # Fast-forward and ancestor detection

    def test_merge_ancestor_is_noop(self):
        """Verify merging ancestor branch does nothing."""
        initial = self.repo.head_commit_id
        self.run_cli("branch", "dev")
        self.save()
        before = self.snapshot()
        self.run_cli("merge", "dev", expected="Given branch is an ancestor of the current branch.\n")
        self.assertEqual(self.snapshot(), before)

    def test_merge_fast_forward_updates_branch(self):
        """Verify fast-forward updates current branch pointer."""
        initial = self.repo.head_commit_id
        self.run_cli("branch", "dev")
        ahead = self.save()
        self.run_cli("checkout", "dev")
        
        self.run_cli("merge", "master", expected="Current branch fast-forwarded.\n")
        self.assertEqual(self.repo.branch_name, "master")
        self.assertEqual(self.repo.refs.resolve_branch("dev"), initial)
        self.assertEqual((self.cwd / "a.txt").read_text(), "base\n")

    # Successful merge without conflicts

    def test_merge_without_conflicts_creates_merge_commit(self):
        """Verify successful merge creates commit with two parents."""
        base, left, right = self.diverge()
        self.run_cli("merge", "dev")
        
        merged = self.repo.objects.load_commit(self.repo.head_commit_id)
        self.assertEqual(merged.parents, (left, right))
        self.assertEqual(merged.message, "Merged dev into master.")
        self.assertEqual(set(self.repo.snapshot()), {"a.txt", "left.txt", "right.txt"})
        self.assertEqual(self.repo.refs.resolve_branch("dev"), right)
        self.assertEqual(self.repo.read_stage(), ({}, set()))

    def test_merge_commit_log_shows_both_parents(self):
        """Verify merge commit displays both parent hashes in log."""
        base, left, right = self.diverge()
        self.run_cli("merge", "dev")
        
        log = self.run_cli("log", expected=None)
        self.assertIn(f"Merge: {left[:7]} {right[:7]}\n", log)
        # Log follows first parent, so right commit not in history
        self.assertNotIn(f"commit {right}\n", log)
        self.assertIn(f"commit {base}\n", log)

    def test_nested_merge_without_conflicts(self):
        """Verify merge works correctly with nested directories."""
        self.save("src/base", b"base")
        self.run_cli("branch", "dev")
        left = self.save("src/left", b"left")
        self.run_cli("checkout", "dev")
        right = self.save("docs/right", b"right")
        self.run_cli("checkout", "master")
        
        self.run_cli("merge", "dev")
        commit = self.repo.objects.load_commit(self.repo.head_commit_id)
        self.assertEqual(commit.parents, (left, right))
        self.assertEqual(set(self.repo.snapshot()), {"src/base", "src/left", "docs/right"})
        self.assertEqual((self.cwd / "docs/right").read_bytes(), b"right")

    # Untracked file protection

    def test_merge_protects_untracked_files(self):
        """Verify merge aborts if it would overwrite untracked files."""
        self.diverge()
        self.write("right.txt", "precious")
        self.failure(("merge", "dev"), UNTRACKED)

    def test_checkout_and_reset_protect_untracked_files(self):
        """Verify checkout and reset protect untracked files."""
        initial = self.repo.head_commit_id
        self.run_cli("branch", "empty")
        target = self.save("z.txt", "saved", "with z")
        self.save("a.txt", "a", "with a")
        self.run_cli("checkout", "empty")
        
        self.write("z.txt", "untracked")
        self.failure(("checkout", "master"), UNTRACKED)
        self.failure(("reset", target), UNTRACKED)
        
        # Even identical bytes are protected when untracked
        self.write("z.txt", "saved")
        self.failure(("reset", target), UNTRACKED)
        self.assertEqual(self.repo.head_commit_id, initial)

    # Merge conflicts

    def test_merge_conflict_leaves_conflict_markers(self):
        """Verify merge conflict creates conflict markers in file."""
        base, left, right = self.diverge()
        left_head = self.save(content="left without newline", message="left")
        self.run_cli("checkout", "dev")
        self.save(content="right without newline", message="right")
        right_head = self.save("z.txt", "nonconflict", "z")
        self.run_cli("checkout", "master")
        
        self.run_cli("merge", "dev", expected="Encountered a merge conflict.\n")
        
        expected = b"<<<<<<< HEAD\nleft without newline=======\nright without newline>>>>>>>\n"
        self.assertEqual((self.cwd / "a.txt").read_bytes(), expected)
        
        merged = self.repo.objects.load_commit(self.repo.head_commit_id)
        self.assertEqual(merged.parents, (left_head, right_head))
        self.assertEqual(self.repo.blob_content(self.repo.snapshot()["a.txt"]), expected)
        # Non-conflicting file still merged
        self.assertEqual((self.cwd / "z.txt").read_text(), "nonconflict")

    def test_merge_identical_changes_is_error(self):
        """Verify merge with no snapshot changes fails."""
        self.save()
        self.run_cli("branch", "dev")
        self.save(content="same change", message="left")
        self.run_cli("checkout", "dev")
        self.save(content="same change", message="right")
        self.run_cli("checkout", "master")
        
        self.failure(("merge", "dev"), "No changes added to the commit.")

    def test_merge_directory_conflict_rejected(self):
        """Verify merge with file/directory conflict is rejected early."""
        self.save("base", b"base")
        self.run_cli("branch", "dev")
        self.save("item", b"left")
        self.run_cli("checkout", "dev")
        self.save("item/file", b"right")
        self.run_cli("checkout", "master")
        
        before = self.snapshot()
        with self.assertRaisesRegex(Exception, "paths conflict"):
            self.repo.merge("dev")
        self.assertEqual(before, self.snapshot())

    # Three-way merge file cases

    def test_merge_file_scenarios(self):
        """Test all three-way merge scenarios systematically."""
        # (base, current, given) -> (expected_result, is_conflict)
        cases = [
            # No conflict cases
            (b"base", b"base", b"given", b"given", False),      # Other changed
            (b"base", b"current", b"base", b"current", False),  # Current changed
            (b"base", b"same", b"same", b"same", False),        # Both same change
            (b"base", None, None, None, False),                  # Both deleted
            (None, b"current", None, b"current", False),        # Current added
            (None, None, b"given", b"given", False),            # Given added
            (b"base", b"base", None, None, False),              # Other deleted
            (b"base", None, b"base", None, False),              # Current deleted
            (b"", None, b"", None, False),                       # Both delete empty
            
            # Conflict cases
            (b"base", b"current", b"given",
             b"<<<<<<< HEAD\ncurrent=======\ngiven>>>>>>>\n", True),
            (b"base", b"current", None,
             b"<<<<<<< HEAD\ncurrent=======\n>>>>>>>\n", True),
            (b"base", None, b"given",
             b"<<<<<<< HEAD\n=======\ngiven>>>>>>>\n", True),
            (None, b"current", b"given",
             b"<<<<<<< HEAD\ncurrent=======\ngiven>>>>>>>\n", True),
            (None, b"", b"given",
             b"<<<<<<< HEAD\n=======\ngiven>>>>>>>\n", True),
        ]
        
        for base, current, given, expected, conflict in cases:
            with self.subTest(base=base, current=current, given=given):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repository(Path(directory))
                    repo.init()
                    tree_service = TreeService(repo.objects)
                    
                    def tree(value, extra):
                        """Create tree with optional file."""
                        result = {extra: repo.objects.save(Blob(extra.encode()))} if extra else {}
                        if value is not None:
                            result["a.txt"] = repo.objects.save(Blob(value))
                        return tree_service.apply_changes(repo.objects.save(Tree(())), result, set())
                    
                    b = Commit("base", 1, (repo.head_commit_id,), tree(base, ""))
                    c = Commit("current", 2, (b.id,), tree(current, "left"))
                    g = Commit("given", 3, (b.id,), tree(given, "right"))
                    
                    for commit in (b, c, g):
                        repo.objects.save(commit)
                    
                    repo.refs.update_branch("master", c.id)
                    repo.refs.update_branch("dev", g.id)
                    
                    # Restore current branch files
                    for name, blob_id in repo.snapshot(c.id).items():
                        (repo.cwd / name).write_bytes(repo.blob_content(blob_id))
                    
                    # File deleted on both may be recreated as untracked
                    if base is not None and current is None and given is None:
                        (repo.cwd / "a.txt").write_bytes(b"keep untracked")
                    
                    output = io.StringIO()
                    with redirect_stdout(output):
                        repo.merge("dev")
                    
                    self.assertEqual(output.getvalue(),
                                   "Encountered a merge conflict.\n" if conflict else "")
                    
                    files = repo.snapshot()
                    if expected is None:
                        self.assertNotIn("a.txt", files)
                    else:
                        self.assertEqual(repo.blob_content(files["a.txt"]), expected)
                        self.assertEqual((repo.cwd / "a.txt").read_bytes(), expected)
                    
                    # Verify untracked file preserved
                    if base is not None and current is None and given is None:
                        self.assertEqual((repo.cwd / "a.txt").read_bytes(), b"keep untracked")

    # Split point (LCA) algorithm

    def test_split_point_with_second_parent_shortcuts(self):
        """Verify split point follows merge shortcuts correctly."""
        root = self.repo.head_commit_id
        
        def node(message, *parents):
            """Create commit node in test graph."""
            commit = Commit(message, 1, parents,
                          self.repo.objects.load_commit(root).tree)
            self.repo.objects.save(commit)
            return commit.id
        
        a = node("a", root)
        b = node("b", a)
        c = node("c", b)
        # root is closer to left via second-parent, but a is latest common
        left = node("left", c, root)
        right = node("right", a)
        
        self.assertEqual(self.repo.split_point(left, right), a)
        
        merged = node("merged", right, left)
        self.assertEqual(self.repo.split_point(merged, left), left)
        self.assertEqual(self.repo.split_point(left, merged), left)

    def test_split_point_handles_long_history(self):
        """Verify split point algorithm doesn't use recursion."""
        root = parent = self.repo.head_commit_id
        
        # Create 1100 commits (would overflow stack with recursion)
        for n in range(1100):
            commit = Commit(str(n), n, (parent,),
                          self.repo.objects.load_commit(root).tree)
            self.repo.objects.save(commit)
            parent = commit.id
        
        self.assertEqual(self.repo.split_point(parent, root), root)


if __name__ == "__main__":
    unittest.main()
