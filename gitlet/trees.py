"""Directory snapshots and persistent tree updates."""

from .errors import GitletError
from .models import Tree, TreeEntry
from .objects import ObjectStore
from .validation import validate_repo_path


def make_tree(entries: list[TreeEntry]) -> Tree:
    return Tree(tuple(entries))


def lookup_blob(store: ObjectStore, root: str, path: str) -> str | None:
    parts = validate_repo_path(path).split("/")
    for i, part in enumerate(parts):
        entries = {e.name: e for e in store.read_tree(root).entries}
        entry = entries.get(part)
        if entry is None:
            return None
        if i == len(parts) - 1:
            return entry.obj_id if entry.type == "blob" else None
        if entry.type != "tree":
            return None
        root = entry.obj_id
    return None


def flatten_tree(store: ObjectStore, root: str) -> dict[str, str]:
    files = {}
    stack = [("", root)]
    while stack:
        prefix, obj_id = stack.pop()
        for entry in store.read_tree(obj_id).entries:
            path = prefix + entry.name
            validate_repo_path(path)
            if entry.type == "tree":
                stack.append((path + "/", entry.obj_id))
            else:
                files[path] = entry.obj_id
    return files


def validate_snapshot(files: dict[str, str]) -> None:
    for path in files:
        parts = validate_repo_path(path).split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in files:
                raise GitletError("File and directory paths conflict.")


def apply_changes(store: ObjectStore, root: str, additions: dict[str, str],
                  removals: set[str]) -> str:
    if additions.keys() & removals:
        raise GitletError("Invalid staging index.")
    # Validate before creating any objects; support file <-> directory changes.
    files = flatten_tree(store, root)
    existing = set(files)
    for path in removals:
        validate_repo_path(path)
        files.pop(path, None)
    for path, obj_id in additions.items():
        validate_repo_path(path)
        store.read_blob(obj_id)
        files[path] = obj_id
    validate_snapshot(files)

    def update(old_id, edits):
        old = store.read_tree(old_id) if old_id is not None else Tree(())
        entries = {e.name: e for e in old.entries}
        for name, edit in edits.items():
            previous = entries.get(name)
            if isinstance(edit, dict):
                subtree = previous.obj_id if previous and previous.type == "tree" else None
                new_id, empty = update(subtree, edit)
                if empty:
                    entries.pop(name, None)
                else:
                    entries[name] = TreeEntry(name, "tree", new_id)
            elif edit is None:
                entries.pop(name, None)
            else:
                entries[name] = TreeEntry(name, "blob", edit)
        tree = make_tree(list(entries.values()))
        if tree == old and old_id is not None:
            return old_id, not tree.entries
        return store.write(tree), not tree.entries

    # A change trie cannot encode both a file removal and nested additions.
    # Build it in depth order so the nested edits replace removed file leaves.
    changes = {}
    for path in sorted((removals & existing) | additions.keys(), key=lambda p: (p.count("/"), p)):
        parts = path.split("/")
        # Replacing a directory by a file supersedes descendant removals.
        if any("/".join(parts[:i]) in additions for i in range(1, len(parts))):
            continue
        node = changes
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = additions.get(path)
    return update(root, changes)[0]
