"""纯函数工具，无副作用，可复用。"""

import hashlib
import json
import re
from .errors import GitletError


def sha1(data: bytes) -> str:
    """计算 SHA-1 哈希。"""
    return hashlib.sha1(data).hexdigest()


def json_bytes(obj: dict) -> bytes:
    """规范化 JSON 序列化。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


def validate_obj_id(obj_id: str) -> str:
    """验证对象 ID 格式。"""
    if not isinstance(obj_id, str) or not re.fullmatch(r"[0-9a-f]{40}", obj_id):
        raise ValueError("Invalid object ID")
    return obj_id


def validate_component(name: str) -> str:
    """验证路径组件（文件名或目录名）。"""
    if (not isinstance(name, str) or not name or name in {".", ".."}
            or any(c in name for c in ("/", "\\", "\0"))):
        raise ValueError("Invalid tree entry name")
    return name


def validate_repo_path(path: str) -> str:
    """验证仓库内的文件路径。"""
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
    """验证分支名格式。"""
    if (not isinstance(name, str) or not name or name == "@"
            or ".." in name or "@{" in name
            or any(ord(c) < 32 or ord(c) == 127 or c in " ~^:?*[\\" for c in name)):
        raise GitletError("Incorrect operands.")
    for part in name.split("/"):
        if not part or part.startswith(".") or part.endswith((".", ".lock")):
            raise GitletError("Incorrect operands.")
    return name


def name_order(name: str) -> bytes:
    """Java 风格的字符串排序（UTF-16 code units）。"""
    return name.encode("utf-16-be", errors="surrogatepass")
