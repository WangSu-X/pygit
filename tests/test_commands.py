"""CLI command integration tests using real processes."""

from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from gitlet.repository import Repository


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "gitlet_cli.py"


class CommandTests(unittest.TestCase):
    """Test all CLI commands through the command line interface."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.repo = Repository(self.cwd)

    def run_cli(self, *args, expected=""):
        """Run gitlet CLI and verify output."""
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

    def snapshot(self):
        """Capture current working directory state."""
        return {str(p.relative_to(self.cwd)): p.read_bytes()
                for p in self.cwd.rglob("*") if p.is_file()}

    def failure(self, args, message):
        """Verify command fails with expected message and no side effects."""
        before = self.snapshot()
        self.run_cli(*args, expected=message + "\n")
        self.assertEqual(self.snapshot(), before, args)

    def init(self):
        """Initialize repository."""
        self.run_cli("init")

    def save(self, name="a.txt", content="base\n", message="base"):
        """Write, stage, and commit a file."""
        self.write(name, content)
        self.run_cli("add", name)
        self.run_cli("commit", message)
        return self.repo.head_commit_id

    # Command validation and error handling

    def test_command_validation_and_error_messages(self):
        """Verify proper error messages for invalid commands and arguments."""
        for args, message in [
            ((), "Please enter a command."),
            (("wat",), "No command with that name exists."),
            (("init", "extra"), "Incorrect operands."),
            (("status",), "Not in an initialized Gitlet directory."),
            (("add",), "Incorrect operands."),
            (("checkout",), "Incorrect operands."),
            (("checkout", "id", "wrong", "a"), "Incorrect operands."),
            (("checkout", "wrong", "a"), "Incorrect operands."),
            (("checkout", "a", "b", "c", "d"), "Incorrect operands."),
        ]:
            self.failure(args, message)

    # Init command

    def test_init_creates_repository_with_initial_commit(self):
        """Verify init creates .gitlet with deterministic initial commit."""
        self.init()
        initial = self.repo.objects.load_commit(self.repo.head_commit_id)
        self.assertEqual((initial.message, initial.timestamp, initial.parents, self.repo.snapshot()),
                         ("initial commit", 0, (), {}))
        self.assertEqual(self.repo.branch_name, "master")

    def test_init_creates_identical_commits_across_repositories(self):
        """Verify initial commit ID is reproducible across repositories."""
        self.init()
        first_id = self.repo.head_commit_id
        with tempfile.TemporaryDirectory() as other:
            repository = Repository(Path(other))
            repository.init()
            self.assertEqual(first_id, repository.head_commit_id)

    def test_init_rejects_reinitialize_existing_repository(self):
        """Verify init fails when repository already exists."""
        self.init()
        self.failure(("init",), "A Gitlet version-control system already exists "
                     "in the current directory.")

    # Add and commit commands

    def test_add_freezes_file_content_at_staging_time(self):
        """Verify add captures file content, not affected by later edits."""
        self.init()
        self.write("a.txt", b"\x00\xffstaged\r\n")
        self.run_cli("add", "a.txt")
        self.write("a.txt", "later edits")
        self.run_cli("commit", "first snapshot")
        self.assertEqual((self.cwd / "a.txt").read_text(), "later edits")
        self.run_cli("checkout", "--", "a.txt")
        self.assertEqual((self.cwd / "a.txt").read_bytes(), b"\x00\xffstaged\r\n")

    def test_add_restages_and_unstages_on_revert(self):
        """Verify add removes from stage when content reverts to HEAD."""
        self.init()
        self.save()
        self.write("a.txt", "changed")
        self.run_cli("add", "a.txt")
        self.write("a.txt", "changed again")
        self.run_cli("add", "a.txt")
        self.assertIn("a.txt", self.repo.read_stage()[0])
        self.write("a.txt", "base\n")
        self.run_cli("add", "a.txt")
        self.assertEqual(self.repo.read_stage(), ({}, set()))
        self.failure(("commit", "nothing"), "No changes added to the commit.")

    def test_add_cancels_removal_when_file_recreated(self):
        """Verify adding recreated file cancels staged deletion."""
        self.init()
        self.save()
        (self.cwd / "a.txt").unlink()
        self.run_cli("add", "a.txt")
        self.write("a.txt", "base\n")
        self.run_cli("add", "a.txt")
        self.assertEqual(self.repo.read_stage(), ({}, set()))

    def test_add_missing_file_error_and_blank_commit_rejected(self):
        """Verify add fails on missing file and commit rejects blank message."""
        self.init()
        self.failure(("add", "missing"), "File does not exist.")
        self.write("a.txt", "x")
        self.run_cli("add", "a.txt")
        for message in ("", "  \t"):
            self.failure(("commit", message), "Please enter a commit message.")

    def test_add_missing_new_file_cancels_stage_and_tracked_deletion(self):
        """Verify add handles missing files for new and tracked scenarios."""
        self.init()
        self.write("new.txt", "new")
        self.run_cli("add", "new.txt")
        (self.cwd / "new.txt").unlink()
        self.run_cli("add", "new.txt")
        self.assertEqual(self.repo.read_stage(), ({}, set()))
        self.save()
        (self.cwd / "a.txt").unlink()
        self.run_cli("add", "a.txt")
        self.assertEqual(self.repo.read_stage(), ({}, {"a.txt"}))
        self.run_cli("commit", "remove a")
        self.assertNotIn("a.txt", self.repo.snapshot())
        self.failure(("add", "a.txt"), "File does not exist.")

    def test_rm_command_not_supported(self):
        """Verify rm command is not implemented (use add for deletions)."""
        self.init()
        self.save()
        self.failure(("rm", "a.txt"), "No command with that name exists.")

    def test_commit_reuses_identical_blobs(self):
        """Verify commits with identical content share the same blob."""
        self.init()
        self.save("a.txt", b"\x00\xffcontent\r\n", "first")
        self.save("b.txt", b"\x00\xffcontent\r\n", "same blob, different name")
        from gitlet.models import Blob
        blob_count = sum(isinstance(self.repo.objects.read(p.parent.name + p.name), Blob)
                        for p in self.repo.objects.directory.glob("*/*"))
        self.assertEqual(blob_count, 1)

    def test_unstaged_deletion_preserved_in_snapshot(self):
        """Verify unstaged deletions don't affect committed snapshots."""
        self.init()
        first = self.save()
        (self.cwd / "a.txt").unlink()
        self.save("b.txt", "b", "add b")
        self.assertEqual(self.repo.snapshot(first)["a.txt"],
                         self.repo.snapshot(self.repo.head_commit_id)["a.txt"])
        self.run_cli("checkout", "--", "a.txt")
        self.assertEqual((self.cwd / "a.txt").read_text(), "base\n")

    # Checkout command

    def test_checkout_file_by_commit_prefix(self):
        """Verify checkout restores file from commit by prefix."""
        self.init()
        first = self.save()
        self.save(content="second", message="second")
        head = self.repo.head_commit_id
        self.write("a.txt", "third")
        self.run_cli("add", "a.txt")
        stage = self.repo.read_stage()
        self.run_cli("checkout", first[:12], "--", "a.txt")
        self.assertEqual((self.cwd / "a.txt").read_text(), "base\n")
        self.assertEqual(self.repo.read_stage(), stage)
        self.assertEqual(self.repo.head_commit_id, head)

    def test_checkout_file_error_cases(self):
        """Verify checkout file error messages."""
        self.init()
        self.save()
        self.failure(("checkout", "--", "absent"), "File does not exist in that commit.")
        self.failure(("checkout", "0" * 40, "--", "a.txt"), "No commit with that id exists.")
        self.failure(("checkout", "../oops", "--", "a.txt"), "No commit with that id exists.")

    def test_checkout_branch_switches_snapshot_and_clears_stage(self):
        """Verify checkout switches branches and restores working directory."""
        self.init()
        initial = self.repo.head_commit_id
        self.run_cli("branch", "dev")
        self.assertEqual(self.repo.branch_name, "master")
        self.save()
        master = self.repo.head_commit_id
        self.write("pending.txt", "pending")
        self.run_cli("add", "pending.txt")
        self.run_cli("checkout", "dev")
        self.assertFalse((self.cwd / "a.txt").exists())
        self.assertTrue((self.cwd / "pending.txt").exists())
        self.assertEqual(self.repo.read_stage(), ({}, set()))
        self.assertEqual(self.repo.head_commit_id, initial)
        self.save("dev.txt", "dev", "dev work")
        self.run_cli("checkout", "master")
        self.assertFalse((self.cwd / "dev.txt").exists())
        self.assertEqual(self.repo.head_commit_id, master)

    def test_checkout_branch_error_cases(self):
        """Verify checkout branch error messages."""
        self.init()
        self.run_cli("branch", "dev")
        self.failure(("checkout", "master"), "No need to checkout the current branch.")
        self.failure(("checkout", "missing"), "No such branch exists.")

    # Branch commands

    def test_branch_create_and_remove(self):
        """Verify branch creation and removal."""
        self.init()
        self.run_cli("branch", "feature/a")
        self.run_cli("checkout", "feature/a")
        old = self.save(message="only feature")
        self.run_cli("checkout", "master")
        self.run_cli("rm-branch", "feature/a")
        self.assertTrue(self.repo.objects.contains(old))

    def test_branch_error_cases(self):
        """Verify branch command error messages."""
        self.init()
        self.run_cli("branch", "dev")
        self.failure(("branch", "dev"), "A branch with that name already exists.")
        self.failure(("rm-branch", "master"), "Cannot remove the current branch.")
        self.failure(("rm-branch", "missing"), "A branch with that name does not exist.")

    # Reset command

    def test_reset_moves_branch_pointer_and_restores_snapshot(self):
        """Verify reset moves current branch and updates working directory."""
        self.init()
        first = self.save()
        second = self.save("b.txt", "b", "second")
        self.run_cli("branch", "future")
        self.write("untracked", "keep")
        self.write("pending", "pending")
        self.run_cli("add", "pending")
        self.run_cli("reset", first[:12])
        self.assertEqual(self.repo.branch_name, "master")
        self.assertEqual(self.repo.head_commit_id, first)
        self.assertEqual(self.repo.refs.resolve_branch("future"), second)
        self.assertFalse((self.cwd / "b.txt").exists())
        self.assertEqual((self.cwd / "untracked").read_text(), "keep")
        self.assertTrue(self.repo.objects.contains(second))
        self.assertEqual(self.repo.read_stage(), ({}, set()))

    # Log and history commands

    def test_log_follows_first_parent_chain(self):
        """Verify log shows only first-parent history."""
        self.init()
        first = self.save(message="same")
        second = self.save(content="second", message="same")
        self.run_cli("reset", first)
        log = self.run_cli("log", expected=None)
        commits = re.findall(r"^commit ([0-9a-f]{40})$", log, re.M)
        self.assertEqual(commits, [first, self.repo.objects.load_commit(first).parents[0]])
        self.assertTrue(log.endswith("initial commit\n\n"))

    def test_global_log_shows_all_commits(self):
        """Verify global-log shows all commits regardless of reachability."""
        self.init()
        first = self.save(message="first")
        second = self.save(content="second", message="second")
        self.run_cli("reset", first)
        global_log = self.run_cli("global-log", expected=None)
        self.assertIn(second, global_log)

    def test_find_searches_by_exact_message(self):
        """Verify find returns all commits with exact message match."""
        self.init()
        first = self.save(message="same")
        second = self.save(content="second", message="same")
        found = self.run_cli("find", "same", expected=None).splitlines()
        self.assertEqual(set(found), {first, second})
        self.failure(("find", "sam"), "Found no commit with that message.")

    # Status command

    def test_status_shows_branches_staging_modifications_untracked(self):
        """Verify status displays all workspace states correctly."""
        self.init()
        self.save("deleted.txt", "d", "d")
        self.save("changed.txt", "c", "c")
        self.save("removed.txt", "r", "r")
        self.run_cli("branch", "aaa")
        self.write("changed.txt", "modified")
        self.write("deleted.txt", "staged")
        self.run_cli("add", "deleted.txt")
        (self.cwd / "deleted.txt").unlink()
        self.write("new.txt", "staged")
        self.run_cli("add", "new.txt")
        self.write("new.txt", "edited after staging")
        (self.cwd / "removed.txt").unlink()
        self.run_cli("add", "removed.txt")
        self.write("removed.txt", "recreated")
        self.write("unknown.txt", "u")
        (self.cwd / "ignored-directory").mkdir()
        self.run_cli("status", expected=(
            "=== Branches ===\naaa\n*master\n\n"
            "=== Changes Staged For Commit ===\ndeleted.txt (modified)\nnew.txt (new)\nremoved.txt (deleted)\n\n"
            "=== Modifications Not Staged For Commit ===\n"
            "changed.txt (modified)\ndeleted.txt (deleted)\nnew.txt (modified)\n\n"
            "=== Untracked Files ===\nremoved.txt\nunknown.txt\n\n"))

    # Module entry point

    def test_module_entry_point_works_without_installation(self):
        """Verify python -m gitlet works without pip install."""
        import os
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        result = subprocess.run([sys.executable, "-m", "gitlet", "init"],
                                cwd=self.cwd, env=environment, capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertTrue(self.repo.directory.is_dir())


if __name__ == "__main__":
    unittest.main()
