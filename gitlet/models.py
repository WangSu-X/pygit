"""Immutable blob, tree and commit models with canonical object encoding."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal, TypeAlias

from .validation import validate_component, validate_obj_id

ObjectId: TypeAlias = str
RepoPath: TypeAlias = str
ObjectType: TypeAlias = Literal["blob", "tree", "commit"]


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class Blob:
    content: bytes

    def __post_init__(self):
        if not isinstance(self.content, bytes):
            raise ValueError("Blob content must be bytes")


@dataclass(frozen=True)
class TreeEntry:
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
    entries: tuple[TreeEntry, ...]

    def __post_init__(self):
        entries = tuple(sorted(self.entries, key=lambda e: e.name))
        if len({e.name for e in entries}) != len(entries):
            raise ValueError("Duplicate tree entry")
        object.__setattr__(self, "entries", entries)


@dataclass(frozen=True)
class Commit:
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
        return encode_payload(self)

    @property
    def id(self) -> ObjectId:
        return object_id(self)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Commit":
        return decode_payload("commit", data)

    def log_entry(self) -> str:
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
    if isinstance(obj, Blob):
        return "blob"
    if isinstance(obj, Tree):
        return "tree"
    if isinstance(obj, Commit):
        return "commit"
    raise TypeError("Unknown object type")


def encode_payload(obj: GitObject) -> bytes:
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
    if kind == "blob":
        return Blob(payload)
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
    payload = encode_payload(obj)
    return f"{object_type(obj)} {len(payload)}\0".encode("ascii") + payload


def object_id(obj: GitObject) -> ObjectId:
    return sha1(encode_object(obj))
