"""对象存储仓储：管理 blob、tree 和 commit 的持久化。"""

from collections.abc import Iterator
from pathlib import Path
import re
import zlib

from ..errors import GitletError
from ..models import (Blob, Commit, GitObject, Tree, decode_payload,
                              encode_object)
from ..utils import sha1, validate_obj_id


class ObjectStore:
    """
    对象存储仓储。
    
    管理 .gitlet/objects/ 目录下的内容寻址对象存储。
    """
    
    def __init__(self, directory: Path):
        self.directory = directory

    def path_for(self, obj_id: str) -> Path:
        """返回对象的文件系统路径。"""
        validate_obj_id(obj_id)
        return self.directory / obj_id[:2] / obj_id[2:]

    def exists(self, obj_id: str) -> bool:
        """检查对象是否存在。"""
        return self.path_for(obj_id).is_file()
    
    def contains(self, obj_id: str) -> bool:
        """检查对象是否存在（exists 的别名，用于兼容性）。"""
        return self.exists(obj_id)

    def save(self, obj: GitObject) -> str:
        """保存对象，返回其 ID。"""
        raw = encode_object(obj)
        obj_id = sha1(raw)
        path = self.path_for(obj_id)
        if path.exists():
            self.load(obj_id)  # 验证已存在的对象未损坏
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(zlib.compress(raw))
        return obj_id

    def load(self, obj_id: str) -> GitObject:
        """加载对象。"""
        try:
            path = self.path_for(obj_id)
            raw = zlib.decompress(path.read_bytes())
            header, payload = raw.split(b"\0", 1)
            kind, size = header.decode("ascii").split(" ")
            if (kind not in ("blob", "tree", "commit") or size != str(len(payload))
                    or sha1(raw) != obj_id):
                raise ValueError("Object header or hash mismatch")
            return decode_payload(kind, payload)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, zlib.error) as error:
            raise GitletError(f"Invalid or missing object: {obj_id}.") from error
    
    def read(self, obj_id: str) -> GitObject:
        """加载对象（load 的别名，用于兼容性）。"""
        return self.load(obj_id)

    def load_blob(self, obj_id: str) -> Blob:
        """加载 blob 对象。"""
        obj = self.load(obj_id)
        if not isinstance(obj, Blob):
            raise GitletError(f"Unexpected object type: {obj_id}.")
        return obj

    def load_tree(self, obj_id: str) -> Tree:
        """加载 tree 对象。"""
        obj = self.load(obj_id)
        if not isinstance(obj, Tree):
            raise GitletError(f"Unexpected object type: {obj_id}.")
        return obj

    def load_commit(self, obj_id: str) -> Commit:
        """加载 commit 对象。"""
        obj = self.load(obj_id)
        if not isinstance(obj, Commit):
            raise GitletError(f"Unexpected object type: {obj_id}.")
        return obj

    def iter_commit_ids(self) -> Iterator[str]:
        """迭代所有 commit 对象的 ID。"""
        for directory in sorted(self.directory.iterdir()):
            if not directory.is_dir() or not re.fullmatch(r"[0-9a-f]{2}", directory.name):
                continue
            for path in sorted(directory.iterdir()):
                if path.is_file() and re.fullmatch(r"[0-9a-f]{38}", path.name):
                    obj_id = directory.name + path.name
                    if isinstance(self.load(obj_id), Commit):
                        yield obj_id

    def resolve_commit_id(self, prefix: str) -> str:
        """根据前缀解析完整的 commit ID。"""
        message = "No commit with that id exists."
        if not re.fullmatch(r"[0-9a-f]{1,40}", prefix):
            raise GitletError(message)
        if len(prefix) == 40:
            if self.exists(prefix) and isinstance(self.load(prefix), Commit):
                return prefix
        else:
            matches = [obj_id for obj_id in self.iter_commit_ids() if obj_id.startswith(prefix)]
            if len(matches) == 1:
                return matches[0]
        raise GitletError(message)
