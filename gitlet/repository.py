"""Gitlet's local commands, with all persistent state under cwd/.gitlet."""

from collections import deque
import json
import os
from pathlib import Path
import time

from .errors import GitletError
from .stage import Stage, StageStore
from .models import Blob, Commit, Tree, json_bytes, object_id
from .objects import ObjectStore
from .refs import RefStore
from .trees import apply_changes, flatten_tree, lookup_blob, validate_snapshot
from .validation import validate_repo_path


UNTRACKED = ("There is an untracked file in the way; "
             "delete it, or add and commit it first.")


file_name = validate_repo_path


def name_order(name: str) -> bytes:
    # Java compares UTF-16 code units; this also handles non-BMP filenames.
    return name.encode("utf-16-be", errors="surrogatepass")


class Repository:
    def __init__(self, cwd: Path | None = None):
        self.cwd = Path.cwd() if cwd is None else Path(cwd)
        self.directory = self.cwd / ".gitlet"
        self.objects = ObjectStore(self.directory / "objects")
        self.refs = RefStore(self.directory)
        self.stage = StageStore(self.directory / "stage.json")

    def require_initialized(self) -> None:
        if not self.directory.is_dir():
            raise GitletError("Not in an initialized Gitlet directory.")
        try:
            version = json.loads((self.directory / "format.json").read_bytes())
        except (OSError, ValueError):
            raise GitletError("Unsupported repository format; expected version 3.") from None
        if version != {"version": 3}:
            raise GitletError("Unsupported repository format; expected version 3.")

    def init(self) -> None:
        if self.directory.exists():
            raise GitletError("A Gitlet version-control system already exists "
                              "in the current directory.")
        self.directory.mkdir()
        self.objects.directory.mkdir()
        self.refs.heads.mkdir(parents=True)
        (self.directory / "format.json").write_bytes(json_bytes({"version": 3}))
        root = self.objects.write(Tree(()))
        initial = Commit("initial commit", 0, (), root)
        self.save_commit(initial)
        self.write_branch("master", initial.id)
        self.refs.set_head("master")
        self.stage.clear()

    @property
    def branch_name(self) -> str:
        return self.refs.current_branch()

    @property
    def head_id(self) -> str:
        return self.refs.head_id()

    def branch_path(self, name: str) -> Path:
        return self.refs.branch_path(name)

    def read_branch(self, name: str) -> str:
        return self.refs.read_branch(name)

    def write_branch(self, name: str, commit_id: str) -> None:
        self.objects.read_commit(commit_id)
        self.refs.write_branch(name, commit_id)

    def read_commit(self, commit_id: str) -> Commit:
        return self.objects.read_commit(commit_id)

    def save_commit(self, commit: Commit) -> None:
        self.objects.read_tree(commit.tree)
        self.objects.write(commit)

    def snapshot(self, commit_id: str | None = None) -> dict[str, str]:
        commit = self.read_commit(self.head_id if commit_id is None else commit_id)
        return flatten_tree(self.objects, commit.tree)

    def resolve_id(self, prefix: str) -> str:
        return self.objects.resolve_commit_id(prefix)

    def read_stage(self) -> tuple[dict[str, str], set[str]]:
        stage = self.stage.read()
        return stage.additions, stage.removals

    def write_stage(self, additions: dict[str, str], removals: set[str]) -> None:
        self.stage.write(Stage(additions, removals))

    def save_blob(self, content: bytes) -> str:
        return self.objects.write(Blob(content))

    def blob_content(self, blob_id: str | None) -> bytes:
        return b"" if blob_id is None else self.objects.read_blob(blob_id).content

    def working_path(self, name: str) -> Path:
        """Never traverse a symlink when accessing working files."""
        name = validate_repo_path(name)
        path = self.cwd
        for part in name.split("/"):
            path = path / part
            if path.is_symlink():
                raise GitletError(UNTRACKED)
        return path

    def write_working_file(self, name: str, content: bytes) -> None:
        path = self.working_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def working_files(self, scope: str = ".") -> dict[str, Path]:
        """Scan regular files without following symlinks or repository metadata."""
        root = self.cwd if scope == "." else self.working_path(scope)
        if root.is_file():
            return {scope: root}
        working = {}
        if not root.is_dir():
            return working
        for directory, dirs, names in os.walk(root, followlinks=False):
            base = Path(directory)
            dirs[:] = [d for d in dirs if not (base / d).is_symlink()
                       and not (base == self.cwd and d == ".gitlet")]
            for name in names:
                path = base / name
                if path.is_file() and not path.is_symlink():
                    working[path.relative_to(self.cwd).as_posix()] = path
        return working

    def add(self, name: str) -> None:
        """Stage workspace state for a file, directory, or the entire repository."""
        if name != ".":
            name = validate_repo_path(name)
        path = self.cwd if name == "." else self.working_path(name)
        if path.exists() and not (path.is_file() or path.is_dir()):
            raise GitletError("Unsupported file type.")
        head = self.snapshot()
        stage = self.stage.read()
        known = head.keys() | stage.additions.keys() | stage.removals

        def in_scope(candidate):
            return name == "." or candidate == name or candidate.startswith(name + "/")

        working = self.working_files(name)
        candidates = {candidate for candidate in known if in_scope(candidate)} | working.keys()
        if not candidates and not path.is_dir():
            raise GitletError("File does not exist.")

        # Plan and validate all changes before writing blobs or Stage.
        contents = {}
        for candidate in sorted(candidates):
            target = self.working_path(candidate)
            if target.is_file():
                content = target.read_bytes()
                obj_id = object_id(Blob(content))
                stage.removals.discard(candidate)
                if obj_id == head.get(candidate):
                    stage.unstage_addition(candidate)
                else:
                    stage.stage_blob(candidate, obj_id)
                    contents[candidate] = content
            elif not target.exists() or target.is_dir():
                # A former file may now be a directory; its file entry is gone.
                stage.unstage_addition(candidate)
                if candidate in head:
                    stage.stage_removal(candidate)
                else:
                    stage.removals.discard(candidate)
            else:
                raise GitletError("Unsupported file type.")
        planned = {p: obj_id for p, obj_id in head.items() if p not in stage.removals}
        planned.update(stage.additions)
        validate_snapshot(planned)
        for candidate, content in contents.items():
            self.save_blob(content)
        self.stage.write(stage)

    def commit(self, message: str, second_parent: str | None = None) -> None:
        if not message.strip():
            raise GitletError("Please enter a commit message.")
        additions, removals = self.read_stage()
        if not additions and not removals:
            raise GitletError("No changes added to the commit.")
        parent_id = self.head_id
        if second_parent is not None:
            self.objects.read_commit(second_parent)
        root = apply_changes(self.objects, self.read_commit(parent_id).tree,
                             additions, removals)
        parents = (parent_id,) if second_parent is None else (parent_id, second_parent)
        commit = Commit(message, time.time_ns(), parents, root)
        self.save_commit(commit)
        self.write_branch(self.branch_name, commit.id)
        self.write_stage({}, set())

    def delete_working_file(self, name: str) -> None:
        path = self.working_path(name)
        if path.is_file():
            path.unlink()
            parent = path.parent
            while parent != self.cwd:
                if any(parent.iterdir()):
                    break
                parent.rmdir()
                parent = parent.parent

    def log(self) -> None:
        commit_id = self.head_id
        while commit_id:
            commit = self.read_commit(commit_id)
            print(commit.log_entry(), end="")
            commit_id = commit.parents[0] if commit.parents else ""

    def global_log(self) -> None:
        for obj_id in self.objects.iter_commit_ids():
            print(self.read_commit(obj_id).log_entry(), end="")

    def find(self, message: str) -> None:
        matches = [obj_id for obj_id in self.objects.iter_commit_ids()
                   if self.read_commit(obj_id).message == message]
        if not matches:
            raise GitletError("Found no commit with that message.")
        print("\n".join(matches))

    def status(self) -> None:
        additions, removals = self.read_stage()
        files = self.snapshot()
        working = self.working_files()
        modifications = {}
        for name in files.keys() | additions.keys():
            if name in removals:
                continue
            expected = additions.get(name, files.get(name))
            if name not in working:
                modifications[name] = "deleted"
            elif object_id(Blob(working[name].read_bytes())) != expected:
                modifications[name] = "modified"
        untracked = {name for name in working if
                     (name not in files and name not in additions) or name in removals}
        branches = sorted(self.refs.list_branches(), key=name_order)
        sections = [
            ("Branches", [("*" if b == self.branch_name else "") + b for b in branches]),
            ("Changes Staged For Commit",
             [f"{n} ({'modified' if n in files else 'new'})"
              for n in sorted(additions, key=name_order)]
             + [f"{n} (deleted)" for n in sorted(removals, key=name_order)]),
            ("Modifications Not Staged For Commit",
             [f"{n} ({modifications[n]})" for n in sorted(modifications, key=name_order)]),
            ("Untracked Files", sorted(untracked, key=name_order)),
        ]
        for title, entries in sections:
            print(f"=== {title} ===")
            for entry in entries:
                print(entry)
            print()

    def checkout_file(self, name: str, prefix: str | None = None) -> None:
        name = file_name(name)
        commit_id = self.head_id if prefix is None else self.resolve_id(prefix)
        blob_id = lookup_blob(self.objects, self.read_commit(commit_id).tree, name)
        if blob_id is None:
            raise GitletError("File does not exist in that commit.")
        path = self.working_path(name)
        if path.is_dir() or any(p.is_file() for p in path.parents if p != self.cwd):
            raise GitletError(UNTRACKED)
        self.write_working_file(name, self.blob_content(blob_id))

    def check_overwrites(self, names: set[str], tracked: dict[str, str],
                         deletes: set[str] | None = None) -> None:
        deletes = set() if deletes is None else deletes
        for name in names:
            path = self.working_path(name)
            for parent in path.parents:
                if parent == self.cwd:
                    break
                relative = parent.relative_to(self.cwd).as_posix()
                if parent.is_file() and relative not in deletes:
                    raise GitletError(UNTRACKED)
            if path.is_dir():
                # Replacing a directory is safe only when every leaf is tracked
                # and planned for deletion; preserve empty/untracked directories.
                for base, dirs, files in os.walk(path, followlinks=False):
                    base = Path(base)
                    if not dirs and not files:
                        raise GitletError(UNTRACKED)
                    for entry in dirs + files:
                        child = base / entry
                        rel = child.relative_to(self.cwd).as_posix()
                        if child.is_symlink() or (child.is_file() and rel not in deletes):
                            raise GitletError(UNTRACKED)
            elif path.exists() and name not in tracked:
                raise GitletError(UNTRACKED)

    def restore_tree(self, target_id: str) -> None:
        current = self.snapshot()
        target = self.snapshot(target_id)
        deletes = current.keys() - target.keys()
        self.check_overwrites(set(target), current, deletes)
        for name in deletes:
            self.working_path(name)  # Validate deletions before changing any file.
        contents = {name: self.blob_content(obj_id) for name, obj_id in target.items()}
        for name in sorted(deletes, key=lambda n: n.count("/"), reverse=True):
            self.delete_working_file(name)
        for name, content in contents.items():
            self.write_working_file(name, content)
        self.stage.clear()

    def checkout_branch(self, name: str) -> None:
        if not self.branch_path(name).is_file():
            raise GitletError("No such branch exists.")
        if name == self.branch_name:
            raise GitletError("No need to checkout the current branch.")
        self.restore_tree(self.read_branch(name))
        self.refs.set_head(name)

    def branch(self, name: str) -> None:
        if self.branch_path(name).exists():
            raise GitletError("A branch with that name already exists.")
        self.write_branch(name, self.head_id)

    def rm_branch(self, name: str) -> None:
        path = self.branch_path(name)
        if not path.is_file():
            raise GitletError("A branch with that name does not exist.")
        if name == self.branch_name:
            raise GitletError("Cannot remove the current branch.")
        self.refs.delete_branch(name)

    def reset(self, prefix: str) -> None:
        target_id = self.resolve_id(prefix)
        self.restore_tree(target_id)
        self.write_branch(self.branch_name, target_id)

    def split_point(self, first: str, second: str) -> str:
        """Find a latest common ancestor, including both parents of merges.

        A shortest path alone can select an older common ancestor through a
        merge shortcut. Eliminate all ancestors of other common ancestors.
        Each node/edge is visited a constant number of times (no recursion).
        """
        parents = {}

        def ancestors(start: str) -> dict[str, int]:
            distances = {start: 0}
            queue = deque([start])
            while queue:
                commit_id = queue.popleft()
                if commit_id not in parents:
                    parents[commit_id] = self.read_commit(commit_id).parents
                for parent in parents[commit_id]:
                    if parent not in distances:
                        distances[parent] = distances[commit_id] + 1
                        queue.append(parent)
            return distances

        first_ancestors = ancestors(first)
        second_ancestors = ancestors(second)
        common = first_ancestors.keys() & second_ancestors.keys()
        older = set()
        queue = deque(p for node in common for p in parents[node])
        while queue:
            node = queue.popleft()
            if node in older:
                continue
            older.add(node)
            queue.extend(parents[node])
        latest = common - older
        return min(latest, key=lambda c: (first_ancestors[c], second_ancestors[c], c))

    def merge(self, name: str) -> None:
        additions, removals = self.read_stage()
        if additions or removals:
            raise GitletError("You have uncommitted changes.")
        if not self.branch_path(name).is_file():
            raise GitletError("A branch with that name does not exist.")
        if name == self.branch_name:
            raise GitletError("Cannot merge a branch with itself.")
        current_id, given_id = self.head_id, self.read_branch(name)
        split_id = self.split_point(current_id, given_id)
        if split_id == given_id:
            print("Given branch is an ancestor of the current branch.")
            return
        if split_id == current_id:
            # The SP21 spec explicitly defines this case as checkout(given).
            self.checkout_branch(name)
            print("Current branch fast-forwarded.")
            return
        current = self.snapshot(current_id)
        given = self.snapshot(given_id)
        base = self.snapshot(split_id)
        writes, deletes = {}, set()
        conflict = False
        for filename in sorted(base.keys() | current.keys() | given.keys(), key=name_order):
            b, c, g = base.get(filename), current.get(filename), given.get(filename)
            if c == g or g == b:
                continue
            if c == b:
                if g is None:
                    deletes.add(filename)
                else:
                    writes[filename] = self.blob_content(g)
            else:
                writes[filename] = (b"<<<<<<< HEAD\n" + self.blob_content(c)
                                    + b"=======\n" + self.blob_content(g) + b">>>>>>>\n")
                conflict = True
        # Validate every affected file before writing any file or metadata.
        planned = {name: obj_id for name, obj_id in current.items() if name not in deletes}
        planned.update({name: "" for name in writes})
        validate_snapshot(planned)
        self.check_overwrites(set(writes), current, deletes)
        for filename in deletes:
            self.working_path(filename)
        if not writes and not deletes:
            raise GitletError("No changes added to the commit.")
        for filename in sorted(deletes, key=lambda n: n.count("/"), reverse=True):
            self.delete_working_file(filename)
        for filename, content in writes.items():
            self.write_working_file(filename, content)
            additions[filename] = self.save_blob(content)
        self.write_stage(additions, deletes)
        self.commit(f"Merged {name} into {self.branch_name}.", given_id)
        if conflict:
            print("Encountered a merge conflict.")
