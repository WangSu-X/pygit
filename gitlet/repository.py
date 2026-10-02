"""
Git 仓库协调层。

协调存储层（objects, refs, stage）和服务层（tree, merge），
提供高层的仓库操作接口，保证仓库状态的一致性。
"""

import json
import os
from pathlib import Path
import time

from .errors import GitletError
from .storage import ObjectStore, RefStore, Stage, StageStore
from .models import Blob, Commit, Tree, object_id
from .services import TreeService, MergeService
from .utils import json_bytes, validate_repo_path, name_order


UNTRACKED = ("There is an untracked file in the way; "
             "delete it, or add and commit it first.")


class Repository:
    """
    Git 仓库协调层。
    
    职责：
    1. 协调各个存储层（objects, refs, stage）
    2. 协调服务层（tree, merge）
    3. 提供高层的仓库操作接口
    4. 保证仓库状态的一致性
    """
    
    def __init__(self, cwd: Path | None = None):
        self.cwd = Path.cwd() if cwd is None else Path(cwd)
        self.git_dir = self.cwd / ".gitlet"
        
        # 存储层
        self.objects = ObjectStore(self.git_dir / "objects")
        self.refs = RefStore(self.git_dir)
        self.stage_store = StageStore(self.git_dir / "stage.json")
        
        # 服务层
        self.tree_service = TreeService(self.objects)
        self.merge_service = MergeService(self.objects)
        
        # 兼容属性（用于测试）
        self.stage = self.stage_store
        self.directory = self.git_dir

    def require_initialized(self) -> None:
        """确保仓库已初始化。"""
        if not self.git_dir.is_dir():
            raise GitletError("Not in an initialized Gitlet directory.")
        try:
            version = json.loads((self.git_dir / "format.json").read_bytes())
        except (OSError, ValueError):
            raise GitletError("Unsupported repository format; expected version 3.") from None
        if version != {"version": 3}:
            raise GitletError("Unsupported repository format; expected version 3.")

    # === 仓库状态查询 ===

    @property
    def current_branch(self) -> str:
        """当前分支名。"""
        return self.refs.current_branch()

    @property
    def head_commit_id(self) -> str:
        """当前 HEAD 指向的提交 ID。"""
        return self.refs.resolve_head()
    
    @property
    def branch_name(self) -> str:
        """当前分支名。"""
        return self.refs.current_branch()

    def snapshot(self, commit_id: str | None = None) -> dict[str, str]:
        """获取某个提交的快照（文件路径 -> blob ID）。"""
        if commit_id is None:
            commit_id = self.head_commit_id
        commit = self.objects.load_commit(commit_id)
        return self.tree_service.flatten(commit.tree)

    def resolve_commit_id(self, prefix: str) -> str:
        """根据前缀解析完整的 commit ID。"""
        return self.objects.resolve_commit_id(prefix)

    def read_stage(self) -> tuple[dict[str, str], set[str]]:
        """读取暂存区状态，返回 (additions, removals)。"""
        stage = self.stage_store.load()
        return (stage.additions, stage.removals)
    
    def write_stage(self, additions: dict[str, str], removals: set[str]) -> None:
        """写入暂存区状态。"""
        from .storage import Stage
        self.stage_store.save(Stage(additions, removals))
    
    def save_blob(self, content: bytes) -> str:
        """保存 blob 对象，返回其 ID。"""
        return self.objects.save(Blob(content))
    
    def split_point(self, first: str, second: str) -> str:
        """查找两个提交的最近公共祖先。"""
        return self.merge_service.find_split_point(first, second)

    # === 工作区操作 ===

    def working_path(self, name: str) -> Path:
        """返回工作区文件的路径（不跟踪符号链接）。"""
        name = validate_repo_path(name)
        path = self.cwd
        for part in name.split("/"):
            path = path / part
            if path.is_symlink():
                raise GitletError(UNTRACKED)
        return path

    def write_working_file(self, name: str, content: bytes) -> None:
        """写入工作区文件。"""
        path = self.working_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def delete_working_file(self, name: str) -> None:
        """删除工作区文件。"""
        path = self.working_path(name)
        if path.is_file():
            path.unlink()
            parent = path.parent
            while parent != self.cwd:
                if any(parent.iterdir()):
                    break
                parent.rmdir()
                parent = parent.parent

    def working_files(self, scope: str = ".") -> dict[str, Path]:
        """扫描工作区文件（不跟踪符号链接）。"""
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

    def blob_content(self, blob_id: str | None) -> bytes:
        """获取 blob 内容。"""
        return b"" if blob_id is None else self.objects.load_blob(blob_id).content

    def check_overwrites(self, names: set[str], tracked: dict[str, str],
                         deletes: set[str] | None = None) -> None:
        """检查是否会覆盖未跟踪的文件。"""
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
                # 只有当目录中所有文件都已跟踪且计划删除时才安全
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

    # === 简单命令 ===

    def init(self) -> None:
        """初始化仓库。"""
        if self.git_dir.exists():
            raise GitletError("A Gitlet version-control system already exists "
                              "in the current directory.")
        self.git_dir.mkdir()
        self.objects.directory.mkdir()
        self.refs.heads.mkdir(parents=True)
        (self.git_dir / "format.json").write_bytes(json_bytes({"version": 3}))
        
        # 创建初始提交
        root = self.objects.save(Tree(()))
        initial = Commit("initial commit", 0, (), root)
        self.objects.save(initial)
        self.refs.update_branch("master", initial.id)
        self.refs.set_head("master")
        self.stage_store.clear()

    def log(self) -> None:
        """显示当前分支的提交历史。"""
        commit_id = self.head_commit_id
        while commit_id:
            commit = self.objects.load_commit(commit_id)
            print(commit.log_entry(), end="")
            commit_id = commit.parents[0] if commit.parents else ""

    def global_log(self) -> None:
        """显示所有提交。"""
        for obj_id in self.objects.iter_commit_ids():
            print(self.objects.load_commit(obj_id).log_entry(), end="")

    def find(self, message: str) -> None:
        """查找具有指定消息的提交。"""
        matches = [obj_id for obj_id in self.objects.iter_commit_ids()
                   if self.objects.load_commit(obj_id).message == message]
        if not matches:
            raise GitletError("Found no commit with that message.")
        print("\n".join(matches))

    def branch(self, name: str) -> None:
        """创建新分支。"""
        if self.refs.branch_exists(name):
            raise GitletError("A branch with that name already exists.")
        self.refs.update_branch(name, self.head_commit_id)

    def rm_branch(self, name: str) -> None:
        """删除分支。"""
        if not self.refs.branch_exists(name):
            raise GitletError("A branch with that name does not exist.")
        if name == self.current_branch:
            raise GitletError("Cannot remove the current branch.")
        self.refs.delete_branch(name)

    # === 复杂命令 ===

    def add(self, name: str) -> None:
        """暂存文件、目录或整个仓库。"""
        if name != ".":
            name = validate_repo_path(name)
        path = self.cwd if name == "." else self.working_path(name)
        if path.exists() and not (path.is_file() or path.is_dir()):
            raise GitletError("Unsupported file type.")
        
        head = self.snapshot()
        stage = self.stage_store.load()
        known = head.keys() | stage.additions.keys() | stage.removals

        def in_scope(candidate):
            return name == "." or candidate == name or candidate.startswith(name + "/")

        working = self.working_files(name)
        candidates = {candidate for candidate in known if in_scope(candidate)} | working.keys()
        if not candidates and not path.is_dir():
            raise GitletError("File does not exist.")

        # 计划所有变更
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
                stage.unstage_addition(candidate)
                if candidate in head:
                    stage.stage_removal(candidate)
                else:
                    stage.removals.discard(candidate)
            else:
                raise GitletError("Unsupported file type.")
        
        # 验证
        planned = {p: obj_id for p, obj_id in head.items() if p not in stage.removals}
        planned.update(stage.additions)
        self.tree_service.validate_snapshot(planned)
        
        # 执行
        for candidate, content in contents.items():
            self.objects.save(Blob(content))
        self.stage_store.save(stage)

    def commit(self, message: str, second_parent: str | None = None) -> None:
        """创建新提交。"""
        if not message.strip():
            raise GitletError("Please enter a commit message.")
        
        stage = self.stage_store.load()
        if not stage.additions and not stage.removals:
            raise GitletError("No changes added to the commit.")
        
        parent_id = self.head_commit_id
        if second_parent is not None:
            self.objects.load_commit(second_parent)
        
        # 使用 TreeService 构建新树
        parent_commit = self.objects.load_commit(parent_id)
        root = self.tree_service.apply_changes(parent_commit.tree,
                                               stage.additions, stage.removals)
        
        parents = (parent_id,) if second_parent is None else (parent_id, second_parent)
        commit = Commit(message, time.time_ns(), parents, root)
        self.objects.save(commit)
        self.refs.update_branch(self.current_branch, commit.id)
        self.stage_store.clear()

    def status(self) -> None:
        """显示仓库状态。"""
        stage = self.stage_store.load()
        files = self.snapshot()
        working = self.working_files()
        
        modifications = {}
        for name in files.keys() | stage.additions.keys():
            if name in stage.removals:
                continue
            expected = stage.additions.get(name, files.get(name))
            if name not in working:
                modifications[name] = "deleted"
            elif object_id(Blob(working[name].read_bytes())) != expected:
                modifications[name] = "modified"
        
        untracked = {name for name in working if
                     (name not in files and name not in stage.additions) or name in stage.removals}
        
        branches = sorted(self.refs.list_branches(), key=name_order)
        sections = [
            ("Branches", [("*" if b == self.current_branch else "") + b for b in branches]),
            ("Changes Staged For Commit",
             [f"{n} ({'modified' if n in files else 'new'})"
              for n in sorted(stage.additions, key=name_order)]
             + [f"{n} (deleted)" for n in sorted(stage.removals, key=name_order)]),
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
        """检出文件到工作区。"""
        name = validate_repo_path(name)
        commit_id = self.head_commit_id if prefix is None else self.resolve_commit_id(prefix)
        blob_id = self.tree_service.lookup_blob(self.objects.load_commit(commit_id).tree, name)
        if blob_id is None:
            raise GitletError("File does not exist in that commit.")
        path = self.working_path(name)
        if path.is_dir() or any(p.is_file() for p in path.parents if p != self.cwd):
            raise GitletError(UNTRACKED)
        self.write_working_file(name, self.blob_content(blob_id))

    def restore_tree(self, target_id: str) -> None:
        """恢复整个树到工作区。"""
        current = self.snapshot()
        target = self.snapshot(target_id)
        deletes = current.keys() - target.keys()
        self.check_overwrites(set(target), current, deletes)
        for name in deletes:
            self.working_path(name)  # 验证删除操作
        contents = {name: self.blob_content(obj_id) for name, obj_id in target.items()}
        for name in sorted(deletes, key=lambda n: n.count("/"), reverse=True):
            self.delete_working_file(name)
        for name, content in contents.items():
            self.write_working_file(name, content)
        self.stage_store.clear()

    def checkout_branch(self, name: str) -> None:
        """切换分支。"""
        if not self.refs.branch_exists(name):
            raise GitletError("No such branch exists.")
        if name == self.current_branch:
            raise GitletError("No need to checkout the current branch.")
        self.restore_tree(self.refs.resolve_branch(name))
        self.refs.set_head(name)

    def reset(self, prefix: str) -> None:
        """重置到指定提交。"""
        target_id = self.resolve_commit_id(prefix)
        self.restore_tree(target_id)
        self.refs.update_branch(self.current_branch, target_id)

    def merge(self, name: str) -> None:
        """合并分支。"""
        stage = self.stage_store.load()
        if stage.additions or stage.removals:
            raise GitletError("You have uncommitted changes.")
        if not self.refs.branch_exists(name):
            raise GitletError("A branch with that name does not exist.")
        if name == self.current_branch:
            raise GitletError("Cannot merge a branch with itself.")
        
        current_id = self.head_commit_id
        given_id = self.refs.resolve_branch(name)
        
        # 使用 MergeService 查找公共祖先
        split_id = self.merge_service.find_common_ancestor(current_id, given_id)
        
        if split_id == given_id:
            print("Given branch is an ancestor of the current branch.")
            return
        if split_id == current_id:
            self.checkout_branch(name)
            print("Current branch fast-forwarded.")
            return
        
        current = self.snapshot(current_id)
        given = self.snapshot(given_id)
        base = self.snapshot(split_id)
        
        # 使用 MergeService 执行三路合并
        writes_info, deletes, conflict = self.merge_service.three_way_merge(base, current, given)
        
        # 转换合并结果
        writes = {}
        additions = {}
        for filename, info in writes_info.items():
            if isinstance(info, tuple):
                # 冲突
                c_blob, t_blob = info
                content = (b"<<<<<<< HEAD\n" + self.blob_content(c_blob)
                          + b"=======\n" + self.blob_content(t_blob) + b">>>>>>>\n")
                writes[filename] = content
            else:
                # 正常合并
                writes[filename] = self.blob_content(info)
        
        # 验证
        planned = {name: obj_id for name, obj_id in current.items() if name not in deletes}
        planned.update({name: "" for name in writes})
        self.tree_service.validate_snapshot(planned)
        self.check_overwrites(set(writes), current, deletes)
        for filename in deletes:
            self.working_path(filename)
        
        if not writes and not deletes:
            raise GitletError("No changes added to the commit.")
        
        # 执行合并
        for filename in sorted(deletes, key=lambda n: n.count("/"), reverse=True):
            self.delete_working_file(filename)
        for filename, content in writes.items():
            self.write_working_file(filename, content)
            additions[filename] = self.objects.save(Blob(content))
        
        stage.additions = additions
        stage.removals = deletes
        self.stage_store.save(stage)
        self.commit(f"Merged {name} into {self.current_branch}.", given_id)
        
        if conflict:
            print("Encountered a merge conflict.")
