"""暂存区存储仓储：管理待提交的变更。"""

from dataclasses import dataclass, field
from pathlib import Path

from ..errors import GitletError
from ..utils import json_bytes, validate_obj_id, validate_repo_path


@dataclass
class Stage:
    """暂存区状态值对象。"""
    additions: dict[str, str] = field(default_factory=dict)
    removals: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        """检查暂存区是否为空。"""
        return not self.additions and not self.removals

    def stage_blob(self, path: str, obj_id: str) -> None:
        """暂存文件添加。"""
        validate_repo_path(path)
        validate_obj_id(obj_id)
        self.additions[path] = obj_id
        self.removals.discard(path)

    def stage_removal(self, path: str) -> None:
        """暂存文件删除。"""
        validate_repo_path(path)
        self.additions.pop(path, None)
        self.removals.add(path)

    def unstage_addition(self, path: str) -> None:
        """取消暂存的添加。"""
        self.additions.pop(path, None)


class StageStore:
    """
    暂存区存储仓储。
    
    管理 .gitlet/stage.json 文件，记录待提交的变更。
    """
    
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Stage:
        """加载暂存区状态。"""
        try:
            import json
            value = json.loads(self.path.read_bytes())
            if (set(value) != {"add", "remove"} or not isinstance(value["add"], dict)
                    or not isinstance(value["remove"], list)):
                raise ValueError("Invalid stage structure")
            stage = Stage(value["add"], set(value["remove"]))
            self._validate(stage)
            return stage
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise GitletError("Invalid stage.") from error
    
    def read(self) -> Stage:
        """加载暂存区状态（load 的别名，用于兼容性）。"""
        return self.load()

    @staticmethod
    def _validate(stage: Stage) -> None:
        """验证暂存区状态。"""
        if stage.additions.keys() & stage.removals:
            raise GitletError("Invalid stage.")
        for path in stage.additions.keys() | stage.removals:
            validate_repo_path(path)
        for obj_id in stage.additions.values():
            validate_obj_id(obj_id)

    def save(self, stage: Stage) -> None:
        """保存暂存区状态。"""
        self._validate(stage)
        self.path.write_bytes(json_bytes({"add": stage.additions,
                                          "remove": sorted(stage.removals)}))

    def clear(self) -> None:
        """清空暂存区。"""
        self.save(Stage())
