"""不可变的数据实体。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeAlias

from ..utils import validate_component, validate_obj_id, sha1, json_bytes

ObjectId: TypeAlias = str
RepoPath: TypeAlias = str
ObjectType: TypeAlias = Literal["blob", "tree", "commit"]


@dataclass(frozen=True)
class Blob:
    """文件内容的不可变表示。"""
    content: bytes

    def __post_init__(self):
        if not isinstance(self.content, bytes):
            raise ValueError("Blob content must be bytes")


@dataclass(frozen=True)
class TreeEntry:
    """树条目：指向 blob 或子树。"""
    name: str
    type: Literal["blob", "tree"]
    obj_id: ObjectId

    def __post_init__(self):
        validate_component(self.name)
        validate_obj_id(self.obj_id)
        if self.type not in ("blob", "tree"):
            raise ValueError("Invalid tree entry type")


@dataclass(frozen=True)
class Tree:
    """目录快照的不可变表示。"""
    entries: tuple[TreeEntry, ...]

    def __post_init__(self):
        entries = tuple(sorted(self.entries, key=lambda e: e.name))
        if len({e.name for e in entries}) != len(entries):
            raise ValueError("Duplicate tree entry")
        object.__setattr__(self, "entries", entries)


@dataclass(frozen=True)
class Commit:
    """提交的不可变表示。"""
    message: str
    timestamp: int
    parents: tuple[ObjectId, ...]
    tree: ObjectId

    def __post_init__(self):
        if not isinstance(self.message, str) or type(self.timestamp) is not int:
            raise ValueError("Invalid commit metadata")
        object.__setattr__(self, "parents", tuple(self.parents))
        if len(self.parents) > 2:
            raise ValueError("A commit can have at most two parents")
        validate_obj_id(self.tree)
        for parent in self.parents:
            validate_obj_id(parent)

    def to_bytes(self) -> bytes:
        """序列化为字节。"""
        return encode_payload(self)

    @property
    def id(self) -> ObjectId:
        """提交的唯一标识。"""
        return object_id(self)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Commit":
        """从字节反序列化。"""
        return decode_payload("commit", data)

    def log_entry(self) -> str:
        """格式化为日志条目。"""
        date = datetime.fromtimestamp(self.timestamp // 1_000_000_000,
                                      timezone.utc).astimezone()
        day = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[date.weekday()]
        month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                 "Sep", "Oct", "Nov", "Dec")[date.month - 1]
        formatted = f"{day} {month} {date.day} {date:%H:%M:%S %Y %z}"
        lines = ["===", f"commit {self.id}"]
        if len(self.parents) == 2:
            lines.append("Merge: " + " ".join(p[:7] for p in self.parents))
        lines.extend([f"Date: {formatted}", self.message, "", ""])
        return "\n".join(lines)


GitObject: TypeAlias = Blob | Tree | Commit


def object_type(obj: GitObject) -> ObjectType:
    """返回对象类型。"""
    if isinstance(obj, Blob):
        return "blob"
    if isinstance(obj, Tree):
        return "tree"
    if isinstance(obj, Commit):
        return "commit"
    raise TypeError("Unknown object type")


def encode_payload(obj: GitObject) -> bytes:
    """编码对象的有效载荷。"""
    if isinstance(obj, Blob):
        return obj.content
    if isinstance(obj, Tree):
        return json_bytes({"entries": {e.name: {"type": e.type, "id": e.obj_id}
                                       for e in obj.entries}})
    if isinstance(obj, Commit):
        return json_bytes({"message": obj.message, "timestamp": obj.timestamp,
                           "parents": obj.parents, "tree": obj.tree})
    raise TypeError("Unknown object type")


def decode_payload(kind: ObjectType, payload: bytes) -> GitObject:
    """从有效载荷解码对象。"""
    if kind == "blob":
        return Blob(payload)
    import json
    value = json.loads(payload)
    if kind == "tree":
        if set(value) != {"entries"} or not isinstance(value["entries"], dict):
            raise ValueError("Invalid tree payload")
        entries = []
        for name, entry in value["entries"].items():
            if set(entry) != {"type", "id"}:
                raise ValueError("Invalid tree entry")
            entries.append(TreeEntry(name, entry["type"], entry["id"]))
        obj = Tree(tuple(entries))
    elif kind == "commit":
        if (set(value) != {"message", "timestamp", "parents", "tree"}
                or not isinstance(value["parents"], list)):
            raise ValueError("Invalid commit payload")
        obj = Commit(value["message"], value["timestamp"],
                     tuple(value["parents"]), value["tree"])
    else:
        raise ValueError("Unknown object type")
    if encode_payload(obj) != payload:
        raise ValueError("Noncanonical object payload")
    return obj


def encode_object(obj: GitObject) -> bytes:
    """编码完整的 Git 对象（包含头部）。"""
    payload = encode_payload(obj)
    return f"{object_type(obj)} {len(payload)}\0".encode("ascii") + payload


def object_id(obj: GitObject) -> ObjectId:
    """计算对象的 ID（内容哈希）。"""
    return sha1(encode_object(obj))


def make_tree(entries: list[TreeEntry]) -> Tree:
    """从条目列表创建树（会自动排序和去重）。"""
    return Tree(tuple(entries))
