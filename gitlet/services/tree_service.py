"""树操作服务：处理目录树的展开、查找和变更应用等复杂逻辑。"""

from typing import TYPE_CHECKING

from ..errors import GitletError
from ..models.objects import Tree, TreeEntry
from ..utils import validate_repo_path

if TYPE_CHECKING:
    from ..storage.object_store import ObjectStore


class TreeService:
    """
    树操作服务。
    
    处理目录树的展开、查找和变更应用等复杂逻辑。
    """
    
    def __init__(self, object_store: "ObjectStore"):
        self.objects = object_store

    def lookup_blob(self, root: str, path: str) -> str | None:
        """在树中查找文件的 blob ID。"""
        parts = validate_repo_path(path).split("/")
        for i, part in enumerate(parts):
            entries = {e.name: e for e in self.objects.load_tree(root).entries}
            entry = entries.get(part)
            if entry is None:
                return None
            if i == len(parts) - 1:
                return entry.obj_id if entry.type == "blob" else None
            if entry.type != "tree":
                return None
            root = entry.obj_id
        return None

    def flatten(self, root: str) -> dict[str, str]:
        """将树展开成文件路径 -> blob ID 的映射。"""
        files = {}
        stack = [("", root)]
        while stack:
            prefix, obj_id = stack.pop()
            for entry in self.objects.load_tree(obj_id).entries:
                path = prefix + entry.name
                validate_repo_path(path)
                if entry.type == "tree":
                    stack.append((path + "/", entry.obj_id))
                else:
                    files[path] = entry.obj_id
        return files

    def validate_snapshot(self, files: dict[str, str]) -> None:
        """验证快照中没有文件和目录路径冲突。"""
        for path in files:
            parts = validate_repo_path(path).split("/")
            for i in range(1, len(parts)):
                if "/".join(parts[:i]) in files:
                    raise GitletError("File and directory paths conflict.")

    def apply_changes(self, root: str, additions: dict[str, str],
                      removals: set[str]) -> str:
        """
        对树应用增删改，返回新树的 ID。
        
        支持文件和目录之间的相互转换。
        """
        if additions.keys() & removals:
            raise GitletError("Invalid stage.")
        
        # 验证并构建最终快照
        files = self.flatten(root)
        existing = set(files)
        for path in removals:
            validate_repo_path(path)
            files.pop(path, None)
        for path, obj_id in additions.items():
            validate_repo_path(path)
            self.objects.load_blob(obj_id)
            files[path] = obj_id
        self.validate_snapshot(files)

        def update(old_id, edits):
            """递归更新树结构。"""
            old = self.objects.load_tree(old_id) if old_id is not None else Tree(())
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
            tree = Tree(tuple(entries.values()))
            if tree == old and old_id is not None:
                return old_id, not tree.entries
            return self.objects.save(tree), not tree.entries

        # 构建变更字典树
        changes = {}
        for path in sorted((removals & existing) | additions.keys(), key=lambda p: (p.count("/"), p)):
            parts = path.split("/")
            # 用文件替换目录时，忽略该目录的子项删除
            if any("/".join(parts[:i]) in additions for i in range(1, len(parts))):
                continue
            node = changes
            for part in parts[:-1]:
                if not isinstance(node.get(part), dict):
                    node[part] = {}
                node = node[part]
            node[parts[-1]] = additions.get(path)
        
        return update(root, changes)[0]
