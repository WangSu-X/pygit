"""引用存储仓储：管理分支和 HEAD。"""

from pathlib import Path
from ..errors import GitletError
from ..utils import validate_branch_name, validate_obj_id


class RefStore:
    """
    引用存储仓储。
    
    管理 .gitlet/refs/heads/ 目录下的分支引用和 HEAD 符号引用。
    """
    
    def __init__(self, git_dir: Path):
        self.git_dir = git_dir
        self.heads = git_dir / "refs" / "heads"

    def branch_path(self, branch: str) -> Path:
        """返回分支的文件系统路径。"""
        validate_branch_name(branch)
        path = self.heads / branch
        current = self.git_dir
        for part in ("refs", "heads", *branch.split("/")):
            current = current / part
            if current.is_symlink():
                raise GitletError("Invalid branch reference.")
        return path

    def current_branch(self) -> str:
        """返回当前分支名。"""
        try:
            text = (self.git_dir / "HEAD").read_text(encoding="utf-8").strip()
            prefix = "ref: refs/heads/"
            if not text.startswith(prefix):
                raise ValueError("Invalid HEAD")
            return validate_branch_name(text[len(prefix):])
        except (OSError, ValueError) as error:
            raise GitletError("Invalid HEAD reference.") from error

    def set_head(self, branch: str) -> None:
        """设置 HEAD 指向指定分支。"""
        self.branch_path(branch)
        (self.git_dir / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")

    def resolve_branch(self, branch: str) -> str:
        """解析分支指向的 commit ID。"""
        try:
            obj_id = self.branch_path(branch).read_text(encoding="ascii").strip()
            return validate_obj_id(obj_id)
        except (OSError, ValueError) as error:
            raise GitletError("Invalid branch reference.") from error

    def resolve_head(self) -> str:
        """解析 HEAD 指向的 commit ID。"""
        return self.resolve_branch(self.current_branch())

    def update_branch(self, branch: str, obj_id: str) -> None:
        """更新分支指针。"""
        validate_obj_id(obj_id)
        path = self.branch_path(branch)
        if path.is_dir() or any(p.exists() and not p.is_dir()
                                for p in path.parents if p != self.git_dir.parent):
            raise GitletError("Branch name conflicts with an existing reference.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(obj_id + "\n", encoding="ascii")

    def delete_branch(self, branch: str) -> None:
        """删除分支。"""
        path = self.branch_path(branch)
        path.unlink()
        parent = path.parent
        while parent != self.heads:
            if any(parent.iterdir()):
                break
            parent.rmdir()
            parent = parent.parent

    def branch_exists(self, branch: str) -> bool:
        """检查分支是否存在。"""
        return self.branch_path(branch).is_file()

    def list_branches(self) -> list[str]:
        """列出所有分支。"""
        return sorted(p.relative_to(self.heads).as_posix()
                      for p in self.heads.rglob("*") if p.is_file() and not p.is_symlink())
