"""V2 format, directory reuse and worktree protection regression tests."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
import zlib

from gitlet.errors import GitletError
from gitlet.models import Blob, Tree, object_id
from gitlet.repository import Repository
from gitlet.trees import apply_changes, flatten_tree, lookup_blob


class V2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.repo = Repository(self.cwd)
        self.repo.init()

    def save(self, path, content, message="save"):
        target = self.cwd / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        self.repo.add(path)
        self.repo.commit(message)
        return self.repo.head_id

    def snapshot_disk(self):
        return {p.relative_to(self.cwd).as_posix(): p.read_bytes()
                for p in self.cwd.rglob("*") if p.is_file() and not p.is_symlink()}

    def test_object_layout_header_hash_and_type_validation(self):
        self.assertEqual(json.loads((self.repo.directory / "format.json").read_bytes()),
                         {"version": 2})
        self.assertEqual((self.repo.directory / "HEAD").read_text(),
                         "ref: refs/heads/master\n")
        self.assertEqual((self.repo.directory / "refs/heads/master").read_text().strip(),
                         self.repo.head_id)
        blob = Blob(b"\0\xffhello")
        obj_id = self.repo.objects.write(blob)
        path = self.repo.objects.path_for(obj_id)
        self.assertEqual(path, self.repo.directory / "objects" / obj_id[:2] / obj_id[2:])
        self.assertEqual(zlib.decompress(path.read_bytes()), b"blob 7\0\0\xffhello")
        self.assertEqual(self.repo.objects.read_blob(obj_id), blob)
        with self.assertRaises(GitletError):
            self.repo.objects.read_tree(obj_id)
        with self.assertRaises(GitletError):
            self.repo.resolve_id(obj_id)
        self.assertNotEqual(object_id(Blob(b'{"entries":{}}')), object_id(Tree(())))
        self.assertEqual(len(list(self.repo.objects.iter_commit_ids())), 1)
        before = path.read_bytes()
        self.repo.objects.write(blob)
        self.assertEqual(path.read_bytes(), before)

    def test_corrupt_object_is_rejected(self):
        obj_id = self.repo.save_blob(b"hello")
        path = self.repo.objects.path_for(obj_id)
        for content in (b"not zlib", zlib.compress(b"blob 4\0hello"),
                        zlib.compress(b"blob 5\0other")):
            path.write_bytes(content)
            with self.assertRaises(GitletError):
                self.repo.objects.read(obj_id)
            with self.assertRaises(GitletError):
                self.repo.save_blob(b"hello")

    def test_nested_tree_reuses_unchanged_subtrees(self):
        self.save("src/main.py", b"first")
        self.save("docs/guide.txt", b"guide")
        old = self.repo.read_commit(self.repo.head_id)
        entries = {e.name: e for e in self.repo.objects.read_tree(old.tree).entries}
        self.save("src/main.py", b"second")
        new = self.repo.read_commit(self.repo.head_id)
        updated = {e.name: e for e in self.repo.objects.read_tree(new.tree).entries}
        self.assertNotEqual(old.tree, new.tree)
        self.assertNotEqual(entries["src"].obj_id, updated["src"].obj_id)
        self.assertEqual(entries["docs"].obj_id, updated["docs"].obj_id)
        self.assertEqual(self.repo.blob_content(lookup_blob(self.repo.objects, old.tree,
                                                          "src/main.py")), b"first")
        self.assertEqual(flatten_tree(self.repo.objects, new.tree), self.repo.snapshot())
        before = self.snapshot_disk()
        self.assertEqual(apply_changes(self.repo.objects, new.tree, {}, set()), new.tree)
        self.assertEqual(before, self.snapshot_disk())
        self.repo.rm("src/main.py")
        self.repo.commit("remove source")
        root = self.repo.objects.read_tree(self.repo.read_commit(self.repo.head_id).tree)
        self.assertEqual([e.name for e in root.entries], ["docs"])

    def test_nested_branch_checkout_reset_and_status(self):
        first = self.save("src/main.py", b"first")
        self.repo.branch("feature/login")
        second = self.save("src/main.py", b"second")
        self.repo.checkout_branch("feature/login")
        self.assertEqual((self.cwd / "src/main.py").read_bytes(), b"first")
        self.assertEqual(self.repo.refs.list_branches(), ["feature/login", "master"])
        (self.cwd / "src/main.py").write_bytes(b"modified")
        (self.cwd / "src/untracked").write_bytes(b"u")
        output = io.StringIO()
        with redirect_stdout(output):
            self.repo.status()
        self.assertIn("src/main.py (modified)", output.getvalue())
        self.assertIn("src/untracked", output.getvalue())
        self.repo.reset(second)
        self.assertEqual(self.repo.head_id, second)
        self.assertEqual(self.repo.read_branch("master"), second)
        self.repo.checkout_file("src/main.py", first[:12])
        self.assertEqual((self.cwd / "src/main.py").read_bytes(), b"first")
        self.repo.checkout_branch("master")
        self.repo.rm_branch("feature/login")
        self.assertFalse((self.repo.refs.heads / "feature").exists())

    def test_file_directory_replacements_round_trip(self):
        file_commit = self.save("item", b"file")
        self.repo.rm("item")
        directory_commit = self.save("item/sub/file", b"nested")
        self.repo.reset(file_commit)
        self.assertEqual((self.cwd / "item").read_bytes(), b"file")
        self.repo.reset(directory_commit)
        self.assertEqual((self.cwd / "item/sub/file").read_bytes(), b"nested")
        self.repo.rm("item/sub/file")
        new_file = self.save("item", b"replacement")
        self.assertEqual(set(self.repo.snapshot()), {"item"})
        self.repo.reset(directory_commit)
        self.repo.reset(new_file)
        self.assertEqual((self.cwd / "item").read_bytes(), b"replacement")

    def test_directory_replacement_preserves_untracked_files(self):
        file_commit = self.save("item", b"file")
        self.repo.rm("item")
        self.save("item/tracked", b"tracked")
        (self.cwd / "item/precious").write_bytes(b"keep")
        before = self.snapshot_disk()
        with self.assertRaisesRegex(GitletError, "untracked file"):
            self.repo.reset(file_commit)
        self.assertEqual(before, self.snapshot_disk())

    def test_invalid_final_snapshot_leaves_commit_state_unchanged(self):
        self.save("item/file", b"nested")
        obj_id = self.repo.save_blob(b"file")
        self.repo.write_index({"item": obj_id}, set())
        before = self.snapshot_disk()
        with self.assertRaisesRegex(GitletError, "paths conflict"):
            self.repo.commit("invalid")
        self.assertEqual(before, self.snapshot_disk())

    def test_invalid_paths_and_symlink_traversal(self):
        for path in ("../outside", "/absolute", "src//file", "src/./file",
                     ".gitlet/HEAD", "src/../file", "C:/outside", "src\\file"):
            with self.subTest(path=path):
                before = self.snapshot_disk()
                with self.assertRaises(GitletError):
                    self.repo.add(path)
                self.assertEqual(before, self.snapshot_disk())
        self.save("src/file", b"inside")
        (self.cwd / "src/file").unlink()
        (self.cwd / "src").rmdir()
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "file"
            outside.write_bytes(b"outside")
            (self.cwd / "src").symlink_to(other, target_is_directory=True)
            for operation in (lambda: self.repo.add("src/file"),
                              lambda: self.repo.rm("src/file"),
                              lambda: self.repo.checkout_file("src/file"),
                              lambda: self.repo.reset(self.repo.head_id)):
                with self.assertRaises(GitletError):
                    operation()
                self.assertEqual(outside.read_bytes(), b"outside")

    def test_branch_name_conflicts_and_symlinks(self):
        self.repo.branch("feature")
        for branch in ("feature/login", "../escape", "/absolute", "bad.lock", "a//b"):
            before = self.snapshot_disk()
            with self.assertRaises(GitletError):
                self.repo.branch(branch)
            self.assertEqual(before, self.snapshot_disk())
        (self.repo.refs.heads / "alias").symlink_to(self.repo.refs.heads / "master")
        with self.assertRaises(GitletError):
            self.repo.branch("alias")

    def test_nested_merge_records_tree_and_two_parents(self):
        self.save("src/base", b"base")
        self.repo.branch("dev")
        left = self.save("src/left", b"left")
        self.repo.checkout_branch("dev")
        right = self.save("docs/right", b"right")
        self.repo.checkout_branch("master")
        self.repo.merge("dev")
        commit = self.repo.read_commit(self.repo.head_id)
        self.assertEqual(commit.parents, (left, right))
        self.assertEqual(set(self.repo.snapshot()), {"src/base", "src/left", "docs/right"})
        self.assertEqual((self.cwd / "docs/right").read_bytes(), b"right")

    def test_removing_absent_descendant_does_not_remove_existing_file(self):
        self.save("item", b"keep")
        root = self.repo.read_commit(self.repo.head_id).tree
        self.assertEqual(apply_changes(self.repo.objects, root, {}, {"item/missing"}),
                         root)

    def test_merge_directory_conflict_is_rejected_before_any_changes(self):
        self.save("base", b"base")
        self.repo.branch("dev")
        self.save("item", b"left")
        self.repo.checkout_branch("dev")
        self.save("item/file", b"right")
        self.repo.checkout_branch("master")
        before = self.snapshot_disk()
        with self.assertRaisesRegex(GitletError, "paths conflict"):
            self.repo.merge("dev")
        self.assertEqual(before, self.snapshot_disk())

    def test_v1_format_is_explicitly_rejected(self):
        (self.repo.directory / "format.json").unlink()
        with self.assertRaisesRegex(GitletError, "expected version 2"):
            self.repo.require_initialized()


if __name__ == "__main__":
    unittest.main()
