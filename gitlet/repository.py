"""Gitlet's local commands, with all persistent state under cwd/.gitlet."""

from collections import deque
import json
from pathlib import Path
import re
import time
from urllib.parse import quote, unquote

from .models import Commit, json_bytes, sha1


class GitletError(Exception):
    """An expected command failure, printed without a traceback by the CLI."""


UNTRACKED = ("There is an untracked file in the way; "
             "delete it, or add and commit it first.")


def file_name(name: str) -> str:
    """Only flat working-directory files belong to the exercise."""
    if (not name or name in {".", "..", ".gitlet"}
            or "/" in name or "\\" in name or "\0" in name):
        raise GitletError("Incorrect operands.")
    return name


def name_order(name: str) -> bytes:
    # Java compares UTF-16 code units; this also handles non-BMP filenames.
    return name.encode("utf-16-be", errors="surrogatepass")


class Repository:
    def __init__(self, cwd: Path | None = None):
        self.cwd = Path.cwd() if cwd is None else Path(cwd)
        self.directory = self.cwd / ".gitlet"
        self.commits = self.directory / "commits"
        self.blobs = self.directory / "blobs"
        self.branches = self.directory / "branches"
        self.head_file = self.directory / "HEAD"
        self.index_file = self.directory / "index.json"

    def require_initialized(self) -> None:
        if not self.directory.is_dir():
            raise GitletError("Not in an initialized Gitlet directory.")

    def init(self) -> None:
        if self.directory.exists():
            raise GitletError("A Gitlet version-control system already exists "
                              "in the current directory.")
        self.directory.mkdir()
        for directory in (self.commits, self.blobs, self.branches):
            directory.mkdir()
        initial = Commit("initial commit", 0, (), {})
        self.save_commit(initial)
        self.write_branch("master", initial.id)
        self.head_file.write_text("master", encoding="utf-8")
        self.write_index({}, set())

    @property
    def branch_name(self) -> str:
        return self.head_file.read_text(encoding="utf-8")

    @property
    def head_id(self) -> str:
        return self.read_branch(self.branch_name)

    def branch_path(self, name: str) -> Path:
        if not name or "\0" in name:
            raise GitletError("Incorrect operands.")
        # Prefix avoids '.'/'..'; encoding lets names like 'feature/a' stay flat.
        return self.branches / ("ref-" + quote(name, safe=""))

    def read_branch(self, name: str) -> str:
        return self.branch_path(name).read_text(encoding="utf-8")

    def write_branch(self, name: str, commit_id: str) -> None:
        self.branch_path(name).write_text(commit_id, encoding="utf-8")

    def read_commit(self, commit_id: str) -> Commit:
        return Commit.from_bytes((self.commits / commit_id).read_bytes())

    def save_commit(self, commit: Commit) -> None:
        path = self.commits / commit.id
        if not path.exists():
            path.write_bytes(commit.to_bytes())

    def resolve_id(self, prefix: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{1,40}", prefix):
            raise GitletError("No commit with that id exists.")
        if len(prefix) == 40:
            if (self.commits / prefix).is_file():
                return prefix
        else:
            matches = [p.name for p in self.commits.iterdir()
                       if p.name.startswith(prefix)]
            if len(matches) == 1:
                return matches[0]
        # An ambiguous prefix is not a unique ID. Never pick a random commit.
        raise GitletError("No commit with that id exists.")

    def read_index(self) -> tuple[dict[str, str], set[str]]:
        index = json.loads(self.index_file.read_bytes())
        return index["add"], set(index["remove"])

    def write_index(self, additions: dict[str, str], removals: set[str]) -> None:
        self.index_file.write_bytes(json_bytes({"add": additions,
                                                "remove": sorted(removals)}))

    def save_blob(self, content: bytes) -> str:
        blob_id = sha1(content)
        path = self.blobs / blob_id
        if not path.exists():
            path.write_bytes(content)
        return blob_id

    def blob_content(self, blob_id: str | None) -> bytes:
        return b"" if blob_id is None else (self.blobs / blob_id).read_bytes()

    def add(self, name: str) -> None:
        name = file_name(name)
        path = self.cwd / name
        if not path.is_file() or path.is_symlink():
            raise GitletError("File does not exist.")
        content = path.read_bytes()
        blob_id = sha1(content)
        additions, removals = self.read_index()
        removals.discard(name)
        if self.read_commit(self.head_id).files.get(name) == blob_id:
            additions.pop(name, None)
        else:
            additions[name] = self.save_blob(content)
        self.write_index(additions, removals)

    def commit(self, message: str, second_parent: str | None = None) -> None:
        if not message.strip():
            raise GitletError("Please enter a commit message.")
        additions, removals = self.read_index()
        if not additions and not removals:
            raise GitletError("No changes added to the commit.")
        parent_id = self.head_id
        files = self.read_commit(parent_id).files.copy()
        for name in removals:
            files.pop(name, None)
        files.update(additions)
        parents = (parent_id,) if second_parent is None else (parent_id, second_parent)
        commit = Commit(message, time.time_ns(), parents, files)
        self.save_commit(commit)
        self.write_branch(self.branch_name, commit.id)
        self.write_index({}, set())

    def rm(self, name: str) -> None:
        name = file_name(name)
        additions, removals = self.read_index()
        tracked = name in self.read_commit(self.head_id).files
        if name not in additions and not tracked:
            raise GitletError("No reason to remove the file.")
        additions.pop(name, None)
        if tracked:
            removals.add(name)
            self.delete_working_file(name)
        self.write_index(additions, removals)

    def delete_working_file(self, name: str) -> None:
        path = self.cwd / name
        if path.is_file() or path.is_symlink():
            path.unlink()

    def log(self) -> None:
        commit_id = self.head_id
        while commit_id:
            commit = self.read_commit(commit_id)
            print(commit.log_entry(), end="")
            commit_id = commit.parents[0] if commit.parents else ""

    def global_log(self) -> None:
        for path in self.commits.iterdir():
            print(self.read_commit(path.name).log_entry(), end="")

    def find(self, message: str) -> None:
        matches = [path.name for path in self.commits.iterdir()
                   if self.read_commit(path.name).message == message]
        if not matches:
            raise GitletError("Found no commit with that message.")
        print("\n".join(matches))

    def status(self) -> None:
        additions, removals = self.read_index()
        files = self.read_commit(self.head_id).files
        working = {p.name: p for p in self.cwd.iterdir()
                   if p.is_file() and not p.is_symlink()}
        modifications = {}
        for name in files.keys() | additions.keys():
            if name in removals:
                continue
            expected = additions.get(name, files.get(name))
            if name not in working:
                modifications[name] = "deleted"
            elif sha1(working[name].read_bytes()) != expected:
                modifications[name] = "modified"
        untracked = {name for name in working if
                     (name not in files and name not in additions) or name in removals}
        branches = sorted((unquote(p.name[4:]) for p in self.branches.iterdir()),
                          key=name_order)
        sections = [
            ("Branches", [("*" if b == self.branch_name else "") + b for b in branches]),
            ("Staged Files", sorted(additions, key=name_order)),
            ("Removed Files", sorted(removals, key=name_order)),
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
        commit = self.read_commit(commit_id)
        if name not in commit.files:
            raise GitletError("File does not exist in that commit.")
        path = self.cwd / name
        if path.is_symlink() or path.is_dir():
            raise GitletError(UNTRACKED)
        path.write_bytes(self.blob_content(commit.files[name]))

    def check_overwrites(self, names: set[str], tracked: dict[str, str]) -> None:
        for name in names:
            path = self.cwd / name
            if (path.is_symlink() or path.is_dir()
                    or (path.exists() and name not in tracked)):
                raise GitletError(UNTRACKED)

    def restore_tree(self, target_id: str) -> None:
        current = self.read_commit(self.head_id).files
        target = self.read_commit(target_id).files
        self.check_overwrites(set(target), current)
        for name in current.keys() - target.keys():
            self.delete_working_file(name)
        for name, blob_id in target.items():
            (self.cwd / name).write_bytes(self.blob_content(blob_id))
        self.write_index({}, set())

    def checkout_branch(self, name: str) -> None:
        if not self.branch_path(name).is_file():
            raise GitletError("No such branch exists.")
        if name == self.branch_name:
            raise GitletError("No need to checkout the current branch.")
        self.restore_tree(self.read_branch(name))
        self.head_file.write_text(name, encoding="utf-8")

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
        path.unlink()

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
        additions, removals = self.read_index()
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
        current = self.read_commit(current_id).files
        given = self.read_commit(given_id).files
        base = self.read_commit(split_id).files
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
        self.check_overwrites(set(writes) | deletes, current)
        if not writes and not deletes:
            raise GitletError("No changes added to the commit.")
        for filename, content in writes.items():
            (self.cwd / filename).write_bytes(content)
            additions[filename] = self.save_blob(content)
        for filename in deletes:
            self.delete_working_file(filename)
        self.write_index(additions, deletes)
        self.commit(f"Merged {name} into {self.branch_name}.", given_id)
        if conflict:
            print("Encountered a merge conflict.")
