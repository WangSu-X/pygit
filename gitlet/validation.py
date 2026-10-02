"""Validation for object IDs, working paths and reference names."""

import re
from .errors import GitletError


def validate_obj_id(obj_id: str) -> str:
    if not isinstance(obj_id, str) or not re.fullmatch(r"[0-9a-f]{40}", obj_id):
        raise ValueError("Invalid object ID")
    return obj_id


def validate_component(name: str) -> str:
    if (not isinstance(name, str) or not name or name in {".", ".."}
            or any(c in name for c in ("/", "\\", "\0"))):
        raise ValueError("Invalid tree entry name")
    return name


def validate_repo_path(path: str) -> str:
    if (not isinstance(path, str) or not path or "\\" in path or "\0" in path
            or path.split("/")[0] == ".gitlet"
            or re.match(r"^[A-Za-z]:", path)):
        raise GitletError("Incorrect operands.")
    try:
        for part in path.split("/"):
            validate_component(part)
    except ValueError:
        raise GitletError("Incorrect operands.") from None
    return path


def validate_branch_name(name: str) -> str:
    if (not isinstance(name, str) or not name or name == "@"
            or ".." in name or "@{" in name
            or any(ord(c) < 32 or ord(c) == 127 or c in " ~^:?*[\\" for c in name)):
        raise GitletError("Incorrect operands.")
    for part in name.split("/"):
        if not part or part.startswith(".") or part.endswith((".", ".lock")):
            raise GitletError("Incorrect operands.")
    return name
