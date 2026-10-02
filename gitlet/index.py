"""The mutable staging area records changes relative to HEAD."""

from dataclasses import dataclass, field
import json
from pathlib import Path

from .errors import GitletError
from .models import json_bytes
from .validation import validate_obj_id, validate_repo_path


@dataclass
class Index:
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


class IndexStore:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> Index:
        try:
            value = json.loads(self.path.read_bytes())
            if (set(value) != {"add", "remove"} or not isinstance(value["add"], dict)
                    or not isinstance(value["remove"], list)):
                raise ValueError("Invalid index structure")
            index = Index(value["add"], set(value["remove"]))
            self._validate(index)
            return index
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise GitletError("Invalid staging index.") from error

    @staticmethod
    def _validate(index: Index) -> None:
        if index.additions.keys() & index.removals:
            raise GitletError("Invalid staging index.")
        for path in index.additions.keys() | index.removals:
            validate_repo_path(path)
        for obj_id in index.additions.values():
            validate_obj_id(obj_id)

    def write(self, index: Index) -> None:
        self._validate(index)
        self.path.write_bytes(json_bytes({"add": index.additions,
                                          "remove": sorted(index.removals)}))

    def clear(self) -> None:
        self.write(Index())
