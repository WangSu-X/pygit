"""Unified compressed, content-addressed object storage."""

from collections.abc import Iterator
from pathlib import Path
import re
import zlib

from .errors import GitletError
from .models import (Blob, Commit, GitObject, Tree, decode_payload,
                     encode_object, sha1)
from .validation import validate_obj_id


class ObjectStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def path_for(self, obj_id: str) -> Path:
        validate_obj_id(obj_id)
        return self.directory / obj_id[:2] / obj_id[2:]

    def contains(self, obj_id: str) -> bool:
        return self.path_for(obj_id).is_file()

    def write(self, obj: GitObject) -> str:
        raw = encode_object(obj)
        obj_id = sha1(raw)
        path = self.path_for(obj_id)
        if path.exists():
            self.read(obj_id)  # Never silently reuse a corrupted object.
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(zlib.compress(raw))
        return obj_id

    def read(self, obj_id: str) -> GitObject:
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

    def _read_typed(self, obj_id, expected):
        obj = self.read(obj_id)
        if not isinstance(obj, expected):
            raise GitletError(f"Unexpected object type: {obj_id}.")
        return obj

    def read_blob(self, obj_id: str) -> Blob:
        return self._read_typed(obj_id, Blob)

    def read_tree(self, obj_id: str) -> Tree:
        return self._read_typed(obj_id, Tree)

    def read_commit(self, obj_id: str) -> Commit:
        return self._read_typed(obj_id, Commit)

    def iter_commit_ids(self) -> Iterator[str]:
        for directory in sorted(self.directory.iterdir()):
            if not directory.is_dir() or not re.fullmatch(r"[0-9a-f]{2}", directory.name):
                continue
            for path in sorted(directory.iterdir()):
                if path.is_file() and re.fullmatch(r"[0-9a-f]{38}", path.name):
                    obj_id = directory.name + path.name
                    if isinstance(self.read(obj_id), Commit):
                        yield obj_id

    def resolve_commit_id(self, prefix: str) -> str:
        message = "No commit with that id exists."
        if not re.fullmatch(r"[0-9a-f]{1,40}", prefix):
            raise GitletError(message)
        if len(prefix) == 40:
            if self.contains(prefix) and isinstance(self.read(prefix), Commit):
                return prefix
        else:
            matches = [obj_id for obj_id in self.iter_commit_ids() if obj_id.startswith(prefix)]
            if len(matches) == 1:
                return matches[0]
        raise GitletError(message)
