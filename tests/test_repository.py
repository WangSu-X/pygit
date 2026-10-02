"""Repository core functionality tests: storage, trees, objects, commits."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
import zlib

from gitlet.errors import GitletError
from gitlet.models import Blob, Tree, TreeEntry, Commit, object_id, make_tree
from gitlet.services import TreeService
from gitlet.repository import Repository


class RepositoryTests(unittest.TestCase):
    """Test repository internals: object storage, tree operations, snapshots."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.repo = Repository(self.cwd)
        self.repo.init()

    def save(self, path, content, message="save"):
        """Write, stage and commit a file."""
        target = self.cwd / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())
        self.repo.add(path)
        self.repo.commit(message)
        return self.repo.head_commit_id

    def disk_snapshot(self):
        """Capture all files in working directory."""
        return {p.relative_to(self.cwd).as_posix(): p.read_bytes()
                for p in self.cwd.rglob("*") if p.is_file() and not p.is_symlink()}

    # Object storage format and layout

    def test_repository_format_and_layout(self):
        """Verify repository structure and format version."""
        self.assertEqual(json.loads((self.repo.git_dir / "format.json").read_bytes()),
                         {"version": 3})
        self.assertEqual((self.repo.git_dir / "HEAD").read_text(),
                         "ref: refs/heads/master\n")
        self.assertEqual((self.repo.git_dir / "refs/heads/master").read_text().strip(),
                         self.repo.head_commit_id)

    def test_object_storage_with_compression_and_hash(self):
        """Verify objects are compressed, hashed correctly, and deduplicated."""
        blob = Blob(b"\0\xffhello")
        obj_id = self.repo.objects.save(blob)
        path = self.repo.objects.path_for(obj_id)
        self.assertEqual(path, self.repo.git_dir / "objects" / obj_id[:2] / obj_id[2:])
        self.assertEqual(zlib.decompress(path.read_bytes()), b"blob 7\0\0\xffhello")
        self.assertEqual(self.repo.objects.load_blob(obj_id), blob)
        # Saving same blob again doesn't overwrite
        before = path.read_bytes()
        self.repo.objects.save(blob)
        self.assertEqual(path.read_bytes(), before)

    def test_object_type_validation(self):
        """Verify loading wrong object type raises error."""
        blob_id = self.repo.objects.save(Blob(b"hello"))
        with self.assertRaises(GitletError):
            self.repo.objects.load_tree(blob_id)
        with self.assertRaises(GitletError):
            self.repo.resolve_commit_id(blob_id)

    def test_blob_and_tree_have_different_hashes(self):
        """Verify blob and tree with same bytes produce different IDs."""
        # Prevent JSON blob from being parsed as tree
        self.assertNotEqual(object_id(Blob(b'{"entries":{}}')), object_id(Tree(())))

    def test_commit_iteration(self):
        """Verify can iterate all commit objects."""
        self.assertEqual(len(list(self.repo.objects.iter_commit_ids())), 1)
        self.save("a.txt", b"a")
        self.assertEqual(len(list(self.repo.objects.iter_commit_ids())), 2)

    def test_corrupt_object_rejected(self):
        """Verify corrupted objects are detected and rejected."""
        obj_id = self.repo.objects.save(Blob(b"hello"))
        path = self.repo.objects.path_for(obj_id)
        for content in (b"not zlib", zlib.compress(b"blob 4\0hello"),
                        zlib.compress(b"blob 5\0other")):
            path.write_bytes(content)
            with self.assertRaises(GitletError):
                self.repo.objects.load(obj_id)
            with self.assertRaises(GitletError):
                self.repo.objects.save(Blob(b"hello"))

    def test_ambiguous_commit_prefix_rejected(self):
        """Verify ambiguous short IDs are rejected."""
        seen = {}
        for n in range(30):
            commit = Commit(f"candidate {n}", n, (self.repo.head_commit_id,),
                           self.repo.objects.load_commit(self.repo.head_commit_id).tree)
            self.repo.objects.save(commit)
            prefix = commit.id[0]
            if prefix in seen:
                break
            seen[prefix] = commit.id
        else:
            self.fail("Expected prefix collision")
        with self.assertRaisesRegex(GitletError, "No commit with that id exists"):
            self.repo.resolve_commit_id(prefix)

    def test_v1_format_explicitly_rejected(self):
        """Verify old format version is rejected."""
        (self.repo.git_dir / "format.json").unlink()
        with self.assertRaisesRegex(GitletError, "expected version 3"):
            self.repo.require_initialized()

    # Tree operations and subtree reuse

    def test_nested_tree_reuses_unchanged_subtrees(self):
        """Verify unchanged subdirectories share the same tree object."""
        self.save("src/main.py", b"first")
        self.save("docs/guide.txt", b"guide")
        old = self.repo.objects.load_commit(self.repo.head_commit_id)
        entries = {e.name: e for e in self.repo.objects.load_tree(old.tree).entries}
        
        self.save("src/main.py", b"second")
        new = self.repo.objects.load_commit(self.repo.head_commit_id)
        updated = {e.name: e for e in self.repo.objects.load_tree(new.tree).entries}
        
        # Root tree changed
        self.assertNotEqual(old.tree, new.tree)
        # src/ tree changed
        self.assertNotEqual(entries["src"].obj_id, updated["src"].obj_id)
        # docs/ tree unchanged (reused)
        self.assertEqual(entries["docs"].obj_id, updated["docs"].obj_id)

    def test_tree_service_lookup_and_flatten(self):
        """Verify tree service can lookup paths and flatten trees."""
        self.save("src/main.py", b"first")
        commit = self.repo.objects.load_commit(self.repo.head_commit_id)
        tree_service = TreeService(self.repo.objects)
        
        blob_id = tree_service.lookup_blob(commit.tree, "src/main.py")
        self.assertEqual(self.repo.blob_content(blob_id), b"first")
        
        flat = tree_service.flatten(commit.tree)
        self.assertEqual(flat, self.repo.snapshot())

    def test_tree_apply_changes_is_idempotent(self):
        """Verify applying no changes returns same tree."""
        self.save("src/main.py", b"code")
        commit = self.repo.objects.load_commit(self.repo.head_commit_id)
        tree_service = TreeService(self.repo.objects)
        
        before = self.disk_snapshot()
        same = tree_service.apply_changes(commit.tree, {}, set())
        self.assertEqual(same, commit.tree)
        self.assertEqual(before, self.disk_snapshot())

    def test_tree_deletion_removes_empty_parent_directories(self):
        """Verify deleting nested file cleans up empty parent trees."""
        self.save("src/nested/file", b"content")
        (self.cwd / "src/nested/file").unlink()
        self.repo.add("src/nested/file")
        self.repo.commit("remove nested file")
        
        root = self.repo.objects.load_tree(
            self.repo.objects.load_commit(self.repo.head_commit_id).tree)
        self.assertEqual([e.name for e in root.entries], [])

    def test_removing_absent_descendant_preserves_existing_files(self):
        """Verify removing non-existent nested path doesn't affect tree."""
        self.save("item", b"keep")
        root = self.repo.objects.load_commit(self.repo.head_commit_id).tree
        tree_service = TreeService(self.repo.objects)
        self.assertEqual(tree_service.apply_changes(root, {}, {"item/missing"}), root)

    # File and directory replacements

    def test_file_directory_replacement_round_trip(self):
        """Verify can replace file with directory and back."""
        file_commit = self.save("item", b"file")
        (self.cwd / "item").unlink()
        self.repo.add("item")
        directory_commit = self.save("item/sub/file", b"nested")
        
        self.repo.reset(file_commit)
        self.assertEqual((self.cwd / "item").read_bytes(), b"file")
        
        self.repo.reset(directory_commit)
        self.assertEqual((self.cwd / "item/sub/file").read_bytes(), b"nested")
        
        # Replace directory with file again
        (self.cwd / "item/sub/file").unlink()
        self.repo.add("item/sub/file")
        (self.cwd / "item/sub").rmdir()
        (self.cwd / "item").rmdir()
        new_file = self.save("item", b"replacement")
        
        self.assertEqual(set(self.repo.snapshot()), {"item"})
        self.repo.reset(directory_commit)
        self.repo.reset(new_file)
        self.assertEqual((self.cwd / "item").read_bytes(), b"replacement")

    def test_directory_replacement_preserves_untracked_files(self):
        """Verify replacing directory with file protects untracked content."""
        file_commit = self.save("item", b"file")
        (self.cwd / "item").unlink()
        self.repo.add("item")
        self.save("item/tracked", b"tracked")
        
        (self.cwd / "item/precious").write_bytes(b"keep")
        before = self.disk_snapshot()
        with self.assertRaisesRegex(GitletError, "untracked file"):
            self.repo.reset(file_commit)
        self.assertEqual(before, self.disk_snapshot())

    def test_invalid_snapshot_with_path_conflicts(self):
        """Verify commit rejects trees with file/directory conflicts."""
        self.save("item/file", b"nested")
        obj_id = self.repo.save_blob(b"file")
        self.repo.write_stage({"item": obj_id}, set())
        
        before = self.disk_snapshot()
        with self.assertRaisesRegex(GitletError, "paths conflict"):
            self.repo.commit("invalid")
        self.assertEqual(before, self.disk_snapshot())

    # Branch operations

    def test_nested_branch_checkout_and_reset(self):
        """Verify branch operations work with nested directories."""
        first = self.save("src/main.py", b"first")
        self.repo.branch("feature/login")
        second = self.save("src/main.py", b"second")
        
        self.repo.checkout_branch("feature/login")
        self.assertEqual((self.cwd / "src/main.py").read_bytes(), b"first")
        self.assertEqual(self.repo.refs.list_branches(), ["feature/login", "master"])
        
        self.repo.reset(second)
        self.assertEqual(self.repo.head_commit_id, second)
        self.assertEqual(self.repo.refs.resolve_branch("master"), second)
        
        self.repo.checkout_file("src/main.py", first[:12])
        self.assertEqual((self.cwd / "src/main.py").read_bytes(), b"first")
        
        self.repo.checkout_branch("master")
        self.repo.rm_branch("feature/login")
        self.assertFalse((self.repo.refs.heads / "feature").exists())

    def test_nested_directory_status(self):
        """Verify status works with nested directory modifications."""
        self.save("src/main.py", b"first")
        (self.cwd / "src/main.py").write_bytes(b"modified")
        (self.cwd / "src/untracked").write_bytes(b"u")
        
        output = io.StringIO()
        with redirect_stdout(output):
            self.repo.status()
        status = output.getvalue()
        self.assertIn("src/main.py (modified)", status)
        self.assertIn("src/untracked", status)

    # Commit hash and serialization

    def test_commit_hash_uses_all_metadata(self):
        """Verify commit hash depends on all fields."""
        a, b = object_id(Blob(b"a")), object_id(Blob(b"b"))
        tree = make_tree([TreeEntry("a", "blob", a), TreeEntry("b", "blob", b)])
        
        first = Commit("m", 1, (a, b), object_id(tree))
        for other in [
            Commit("n", 1, (a, b), first.tree),  # Different message
            Commit("m", 2, (a, b), first.tree),  # Different timestamp
            Commit("m", 1, (b, a), first.tree),  # Different parents
            Commit("m", 1, (a, b), object_id(Tree(())))  # Different tree
        ]:
            self.assertNotEqual(first.id, other.id)

    def test_commit_serialization_round_trip(self):
        """Verify commit can be serialized and deserialized."""
        a, b = object_id(Blob(b"a")), object_id(Blob(b"b"))
        tree = make_tree([TreeEntry("a", "blob", a), TreeEntry("b", "blob", b)])
        commit = Commit("message", 123, (a, b), object_id(tree))
        self.assertEqual(commit, Commit.from_bytes(commit.to_bytes()))

    def test_tree_entry_order_is_stable(self):
        """Verify tree entries are sorted consistently."""
        a, b = object_id(Blob(b"a")), object_id(Blob(b"b"))
        tree = make_tree([TreeEntry("a", "blob", a), TreeEntry("b", "blob", b)])
        reverse = make_tree(list(reversed(tree.entries)))
        self.assertEqual(object_id(tree), object_id(reverse))


if __name__ == "__main__":
    unittest.main()
