"""Behavior tests use a fresh temporary repository and real CLI processes."""

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from gitlet.models import Blob, Commit, Tree, TreeEntry, object_id
from gitlet.trees import apply_changes, make_tree
from gitlet.repository import Repository, UNTRACKED


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "gitlet_cli.py"


class GitletTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.repo = Repository(self.cwd)

    def run_cli(self, *args, expected=""):
        result = subprocess.run([sys.executable, str(LAUNCHER), *args],
                                cwd=self.cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        if expected is not None:
            self.assertEqual(result.stdout, expected)
        return result.stdout

    def write(self, name, content):
        path = self.cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())

    def snapshot(self):
        return {str(p.relative_to(self.cwd)): p.read_bytes()
                for p in self.cwd.rglob("*") if p.is_file()}

    def failure(self, args, message):
        before = self.snapshot()
        self.run_cli(*args, expected=message + "\n")
        self.assertEqual(self.snapshot(), before, args)

    def init(self):
        self.run_cli("init")

    def save(self, name="a.txt", content="base\n", message="base"):
        self.write(name, content)
        self.run_cli("add", name)
        self.run_cli("commit", message)
        return self.repo.head_id

    def test_general_errors_and_checkout_operand_validation(self):
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

    def test_initial_commit_and_identical_ids_across_repositories(self):
        self.init()
        initial = self.repo.read_commit(self.repo.head_id)
        self.assertEqual((initial.message, initial.timestamp, initial.parents, self.repo.snapshot()),
                         ("initial commit", 0, (), {}))
        self.assertEqual(self.repo.branch_name, "master")
        with tempfile.TemporaryDirectory() as other:
            repository = Repository(Path(other))
            repository.init()
            self.assertEqual(self.repo.head_id, repository.head_id)
        self.failure(("init",), "A Gitlet version-control system already exists "
                     "in the current directory.")

    def test_add_freezes_bytes_and_commit_reuses_blobs(self):
        self.init()
        self.write("a.txt", b"\x00\xffstaged\r\n")
        self.run_cli("add", "a.txt")
        self.write("a.txt", "later edits")
        self.run_cli("commit", "first snapshot")
        first = self.repo.head_id
        self.assertEqual((self.cwd / "a.txt").read_text(), "later edits")
        self.run_cli("checkout", "--", "a.txt")
        self.assertEqual((self.cwd / "a.txt").read_bytes(), b"\x00\xffstaged\r\n")
        self.save("b.txt", b"\x00\xffstaged\r\n", "same blob, two names")
        self.assertEqual(sum(isinstance(self.repo.objects.read(p.parent.name + p.name), Blob)
                             for p in self.repo.objects.directory.glob("*/*")), 1)
        self.assertEqual(self.repo.snapshot(first).keys(), {"a.txt"})
        self.assertEqual(self.repo.read_stage(), ({}, set()))

    def test_add_restages_and_unstages_reverted_content(self):
        self.init()
        self.save()
        self.write("a.txt", "changed")
        self.run_cli("add", "a.txt")
        self.write("a.txt", "changed again")
        self.run_cli("add", "a.txt")
        self.assertEqual(self.repo.read_stage()[0]["a.txt"], object_id(Blob(b"changed again")))
        self.write("a.txt", "base\n")
        self.run_cli("add", "a.txt")
        self.assertEqual(self.repo.read_stage(), ({}, set()))
        self.failure(("commit", "nothing"), "No changes added to the commit.")

    def test_add_cancels_removal(self):
        self.init()
        self.save()
        (self.cwd / "a.txt").unlink()
        self.run_cli("add", "a.txt")
        self.write("a.txt", "base\n")
        self.run_cli("add", "a.txt")
        self.assertEqual(self.repo.read_stage(), ({}, set()))

    def test_missing_file_and_blank_commit_leave_state_unchanged(self):
        self.init()
        self.failure(("add", "missing"), "File does not exist.")
        self.write("a.txt", "x")
        self.run_cli("add", "a.txt")
        for message in ("", "  \t"):
            self.failure(("commit", message), "Please enter a commit message.")

    def test_add_missing_file_cancels_new_stage_and_stages_tracked_deletion(self):
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
        self.failure(("rm", "a.txt"), "No command with that name exists.")

    def test_unstaged_deletion_does_not_change_snapshot(self):
        self.init()
        first = self.save()
        (self.cwd / "a.txt").unlink()
        self.save("b.txt", "b", "add b")
        self.assertEqual(self.repo.snapshot(first)["a.txt"],
                         self.repo.snapshot(self.repo.head_id)["a.txt"])
        self.run_cli("checkout", "--", "a.txt")
        self.assertEqual((self.cwd / "a.txt").read_text(), "base\n")

    def test_checkout_file_by_prefix_preserves_stage_and_branch(self):
        self.init()
        first = self.save()
        self.save(content="second", message="second")
        head = self.repo.head_id
        self.write("a.txt", "third")
        self.run_cli("add", "a.txt")
        stage = self.repo.read_stage()
        self.run_cli("checkout", first[:12], "--", "a.txt")
        self.assertEqual((self.cwd / "a.txt").read_text(), "base\n")
        self.assertEqual(self.repo.read_stage(), stage)
        self.assertEqual(self.repo.head_id, head)
        self.failure(("checkout", "--", "absent"), "File does not exist in that commit.")
        self.failure(("checkout", "0" * 40, "--", "a.txt"), "No commit with that id exists.")
        self.failure(("checkout", "../oops", "--", "a.txt"), "No commit with that id exists.")

    def test_ambiguous_commit_prefix_is_rejected(self):
        self.init()
        seen = {}
        for n in range(30):
            commit = Commit(f"candidate {n}", n, (self.repo.head_id,), self.repo.read_commit(self.repo.head_id).tree)
            self.repo.save_commit(commit)
            prefix = commit.id[0]
            if prefix in seen:
                break
            seen[prefix] = commit.id
        else:
            self.fail("Pigeonhole principle: expected a prefix collision")
        self.failure(("reset", prefix), "No commit with that id exists.")

    def test_branches_switch_snapshots_and_clear_stage(self):
        self.init()
        initial = self.repo.head_id
        self.run_cli("branch", "dev")
        self.assertEqual(self.repo.branch_name, "master")
        self.save()
        master = self.repo.head_id
        self.write("pending.txt", "pending")
        self.run_cli("add", "pending.txt")
        self.run_cli("checkout", "dev")
        self.assertFalse((self.cwd / "a.txt").exists())
        self.assertTrue((self.cwd / "pending.txt").exists())
        self.assertEqual(self.repo.read_stage(), ({}, set()))
        self.assertEqual(self.repo.head_id, initial)
        self.save("dev.txt", "dev", "dev work")
        self.run_cli("checkout", "master")
        self.assertFalse((self.cwd / "dev.txt").exists())
        self.assertEqual(self.repo.head_id, master)
        self.failure(("checkout", "master"), "No need to checkout the current branch.")
        self.failure(("checkout", "missing"), "No such branch exists.")
        self.failure(("branch", "dev"), "A branch with that name already exists.")

    def test_checkout_and_reset_protect_untracked_files_before_changes(self):
        self.init()
        initial = self.repo.head_id
        self.run_cli("branch", "empty")
        target = self.save("z.txt", "saved", "with z")
        self.save("a.txt", "a", "with a")
        self.run_cli("checkout", "empty")
        self.write("z.txt", "untracked")
        self.failure(("checkout", "master"), UNTRACKED)
        self.failure(("reset", target), UNTRACKED)
        # Even identical bytes are protected when the current commit doesn't track them.
        self.write("z.txt", "saved")
        self.failure(("reset", target), UNTRACKED)
        self.assertEqual(self.repo.head_id, initial)

    def test_reset_moves_current_branch_only_and_keeps_old_commits(self):
        self.init()
        first = self.save()
        second = self.save("b.txt", "b", "second")
        self.run_cli("branch", "future")
        self.write("untracked", "keep")
        self.write("pending", "pending")
        self.run_cli("add", "pending")
        self.run_cli("reset", first[:12])
        self.assertEqual(self.repo.branch_name, "master")
        self.assertEqual(self.repo.head_id, first)
        self.assertEqual(self.repo.read_branch("future"), second)
        self.assertFalse((self.cwd / "b.txt").exists())
        self.assertEqual((self.cwd / "untracked").read_text(), "keep")
        self.assertTrue(self.repo.objects.contains(second))
        self.assertEqual(self.repo.read_stage(), ({}, set()))

    def test_rm_branch_preserves_history(self):
        self.init()
        self.run_cli("branch", "feature/a")
        self.run_cli("checkout", "feature/a")
        old = self.save(message="only feature")
        self.run_cli("checkout", "master")
        self.run_cli("rm-branch", "feature/a")
        self.assertTrue(self.repo.objects.contains(old))
        self.failure(("rm-branch", "master"), "Cannot remove the current branch.")
        self.failure(("rm-branch", "missing"), "A branch with that name does not exist.")

    def test_find_global_log_and_log_use_correct_histories(self):
        self.init()
        first = self.save(message="same")
        second = self.save(content="second", message="same")
        self.run_cli("reset", first)
        log = self.run_cli("log", expected=None)
        self.assertEqual(re.findall(r"^commit ([0-9a-f]{40})$", log, re.M),
                         [first, self.repo.read_commit(first).parents[0]])
        self.assertTrue(log.endswith("initial commit\n\n"))
        global_log = self.run_cli("global-log", expected=None)
        self.assertIn(second, global_log)
        found = self.run_cli("find", "same", expected=None).splitlines()
        self.assertEqual(set(found), {first, second})
        self.failure(("find", "sam"), "Found no commit with that message.")

    def test_status_staged_types_and_no_duplicate_deletions(self):
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

    def test_merge_errors_include_removal_only_and_mixed_staging(self):
        self.init()
        self.save()
        self.run_cli("branch", "dev")
        self.failure(("merge", "master"), "Cannot merge a branch with itself.")
        self.failure(("merge", "missing"), "A branch with that name does not exist.")
        (self.cwd / "a.txt").unlink()
        self.run_cli("add", "a.txt")
        self.failure(("merge", "dev"), "You have uncommitted changes.")
        self.write("b.txt", "b")
        self.run_cli("add", "b.txt")
        self.failure(("merge", "dev"), "You have uncommitted changes.")

    def test_merge_ancestor_and_fast_forward_follow_sp21_checkout_rule(self):
        self.init()
        initial = self.repo.head_id
        self.run_cli("branch", "dev")
        self.save()
        before = self.snapshot()
        self.run_cli("merge", "dev", expected="Given branch is an ancestor of the current branch.\n")
        self.assertEqual(self.snapshot(), before)
        self.run_cli("checkout", "dev")
        self.run_cli("merge", "master", expected="Current branch fast-forwarded.\n")
        self.assertEqual(self.repo.branch_name, "master")
        self.assertEqual(self.repo.read_branch("dev"), initial)
        self.assertEqual((self.cwd / "a.txt").read_text(), "base\n")

    def diverge(self):
        self.init()
        base = self.save()
        self.run_cli("branch", "dev")
        left = self.save("left.txt", "left", "left")
        self.run_cli("checkout", "dev")
        right = self.save("right.txt", "right", "right")
        self.run_cli("checkout", "master")
        return base, left, right

    def test_merge_without_conflicts_records_both_parents_and_first_parent_log(self):
        base, left, right = self.diverge()
        self.run_cli("merge", "dev")
        merged = self.repo.read_commit(self.repo.head_id)
        self.assertEqual(merged.parents, (left, right))
        self.assertEqual(merged.message, "Merged dev into master.")
        self.assertEqual(set(self.repo.snapshot()), {"a.txt", "left.txt", "right.txt"})
        self.assertEqual(self.repo.read_branch("dev"), right)
        log = self.run_cli("log", expected=None)
        self.assertIn(f"Merge: {left[:7]} {right[:7]}\n", log)
        self.assertNotIn(f"commit {right}\n", log)
        self.assertIn(f"commit {base}\n", log)
        self.assertEqual(self.repo.read_stage(), ({}, set()))

    def test_merge_untracked_preflight_does_not_partially_restore_other_files(self):
        self.diverge()
        self.write("right.txt", "precious")
        self.failure(("merge", "dev"), UNTRACKED)

    def test_merge_conflict_report_survives_later_nonconflicting_file(self):
        self.init()
        self.save()
        self.run_cli("branch", "dev")
        left = self.save(content="left without newline", message="left")
        self.run_cli("checkout", "dev")
        self.save(content="right without newline", message="right")
        right = self.save("z.txt", "nonconflict", "z")
        self.run_cli("checkout", "master")
        self.run_cli("merge", "dev", expected="Encountered a merge conflict.\n")
        expected = b"<<<<<<< HEAD\nleft without newline=======\nright without newline>>>>>>>\n"
        self.assertEqual((self.cwd / "a.txt").read_bytes(), expected)
        merged = self.repo.read_commit(self.repo.head_id)
        self.assertEqual(merged.parents, (left, right))
        self.assertEqual(self.repo.blob_content(self.repo.snapshot()["a.txt"]), expected)
        self.assertEqual((self.cwd / "z.txt").read_text(), "nonconflict")

    def test_merge_with_no_snapshot_changes_is_an_error(self):
        self.init()
        self.save()
        self.run_cli("branch", "dev")
        self.save(content="same change", message="left")
        self.run_cli("checkout", "dev")
        self.save(content="same change", message="right")
        self.run_cli("checkout", "master")
        self.failure(("merge", "dev"), "No changes added to the commit.")

    def test_merge_file_cases(self):
        # base/current/given, resulting bytes, conflict. These cases include
        # deletion vs empty-file and additions independently on both branches.
        cases = [
            (b"base", b"base", b"given", b"given", False),
            (b"base", b"current", b"base", b"current", False),
            (b"base", b"same", b"same", b"same", False),
            (b"base", None, None, None, False),
            (None, b"current", None, b"current", False),
            (None, None, b"given", b"given", False),
            (b"base", b"base", None, None, False),
            (b"base", None, b"base", None, False),
            (b"base", b"current", b"given",
             b"<<<<<<< HEAD\ncurrent=======\ngiven>>>>>>>\n", True),
            (b"base", b"current", None,
             b"<<<<<<< HEAD\ncurrent=======\n>>>>>>>\n", True),
            (b"base", None, b"given",
             b"<<<<<<< HEAD\n=======\ngiven>>>>>>>\n", True),
            (None, b"current", b"given",
             b"<<<<<<< HEAD\ncurrent=======\ngiven>>>>>>>\n", True),
            (None, b"", b"given", b"<<<<<<< HEAD\n=======\ngiven>>>>>>>\n", True),
            (b"", None, b"", None, False),
        ]
        for base, current, given, expected, conflict in cases:
            with self.subTest(base=base, current=current, given=given):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repository(Path(directory))
                    repo.init()
                    def tree(value, extra):
                        result = {extra: repo.save_blob(extra.encode())} if extra else {}
                        if value is not None:
                            result["a.txt"] = repo.save_blob(value)
                        return apply_changes(repo.objects, repo.objects.write(Tree(())), result, set())
                    b = Commit("base", 1, (repo.head_id,), tree(base, ""))
                    c = Commit("current", 2, (b.id,), tree(current, "left"))
                    g = Commit("given", 3, (b.id,), tree(given, "right"))
                    for commit in (b, c, g):
                        repo.save_commit(commit)
                    repo.write_branch("master", c.id)
                    repo.write_branch("dev", g.id)
                    for name, blob_id in repo.snapshot(c.id).items():
                        (repo.cwd / name).write_bytes(repo.blob_content(blob_id))
                    # A file deleted on both branches may be recreated as untracked.
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
                    if base is not None and current is None and given is None:
                        self.assertEqual((repo.cwd / "a.txt").read_bytes(), b"keep untracked")

    def test_split_point_handles_merge_shortcuts_and_second_parent_ancestry(self):
        self.init()
        root = self.repo.head_id
        def node(message, *parents):
            commit = Commit(message, 1, parents, self.repo.read_commit(root).tree)
            self.repo.save_commit(commit)
            return commit.id
        a = node("a", root)
        b = node("b", a)
        c = node("c", b)
        # root is closer to left via a second-parent shortcut, but a is latest.
        left = node("left", c, root)
        right = node("right", a)
        self.assertEqual(self.repo.split_point(left, right), a)
        merged = node("merged", right, left)
        self.assertEqual(self.repo.split_point(merged, left), left)
        self.assertEqual(self.repo.split_point(left, merged), left)

    def test_split_point_handles_long_history_without_recursion(self):
        self.init()
        root = parent = self.repo.head_id
        for n in range(1100):
            commit = Commit(str(n), n, (parent,), self.repo.read_commit(root).tree)
            self.repo.save_commit(commit)
            parent = commit.id
        self.assertEqual(self.repo.split_point(parent, root), root)

    def test_commit_hash_uses_all_metadata_and_tree_order_is_stable(self):
        a, b = object_id(Blob(b"a")), object_id(Blob(b"b"))
        tree = make_tree([TreeEntry("a", "blob", a), TreeEntry("b", "blob", b)])
        reverse = make_tree(list(reversed(tree.entries)))
        self.assertEqual(object_id(tree), object_id(reverse))
        first = Commit("m", 1, (a, b), object_id(tree))
        for other in [Commit("n", 1, (a, b), first.tree),
                      Commit("m", 2, (a, b), first.tree),
                      Commit("m", 1, (b, a), first.tree),
                      Commit("m", 1, (a, b), object_id(Tree(())))]:
            self.assertNotEqual(first.id, other.id)
        self.assertEqual(first, Commit.from_bytes(first.to_bytes()))

    def test_module_entry_point_works_without_installation(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        result = subprocess.run([sys.executable, "-m", "gitlet", "init"],
                                cwd=self.cwd, env=environment, capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertTrue(self.repo.directory.is_dir())


if __name__ == "__main__":
    unittest.main()
