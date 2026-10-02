"""The mutable staging area records changes relative to HEAD."""

from dataclasses import dataclass, field
import json
from pathlib import Path

from .errors import GitletError
from .models import json_bytes
from .validation import validate_obj_id, validate_repo_path


@dataclass
class Stage:
    additions: dict[str, str] = field(default_factory=dict)
    removals: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        return not self.additions and not self.removals

    def stage_blob(self, path: str, obj_id: str) -> None:
        validate_repo_path(path)
        validate_obj_id(obj_id)
        self.additions[path] = obj_id
        self.removals.discard(path)

    def stage_removal(self, path: str) -> None:
        validate_repo_path(path)
        self.additions.pop(path, None)
        self.removals.add(path)

    def unstage_addition(self, path: str) -> None:
        self.additions.pop(path, None)


class StageStore:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> Stage:
        try:
            value = json.loads(self.path.read_bytes())
            if (set(value) != {"add", "remove"} or not isinstance(value["add"], dict)
                    or not isinstance(value["remove"], list)):
                raise ValueError("Invalid stage structure")
            stage = Stage(value["add"], set(value["remove"]))
            self._validate(stage)
            return stage
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise GitletError("Invalid stage.") from error

    @staticmethod
    def _validate(stage: Stage) -> None:
        if stage.additions.keys() & stage.removals:
            raise GitletError("Invalid stage.")
        for path in stage.additions.keys() | stage.removals:
            validate_repo_path(path)
        for obj_id in stage.additions.values():
            validate_obj_id(obj_id)

    def write(self, stage: Stage) -> None:
        self._validate(stage)
        self.path.write_bytes(json_bytes({"add": stage.additions,
                                          "remove": sorted(stage.removals)}))

    def clear(self) -> None:
        self.write(Stage())
