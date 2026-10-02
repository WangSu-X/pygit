"""Content-addressed commits. File contents live separately in blob files."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def json_bytes(value: object) -> bytes:
    # Stable ordering makes IDs independent of dict insertion order and locale.
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class Commit:
    message: str
    timestamp: int  # Nanoseconds since the Unix epoch; initial commit uses zero.
    parents: tuple[str, ...]
    files: dict[str, str]

    def to_bytes(self) -> bytes:
        return json_bytes({"message": self.message, "timestamp": self.timestamp,
                           "parents": self.parents, "files": self.files})

    @property
    def id(self) -> str:
        return sha1(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> "Commit":
        value = json.loads(data)
        return cls(value["message"], value["timestamp"], tuple(value["parents"]),
                   value["files"])

    def log_entry(self) -> str:
        date = datetime.fromtimestamp(self.timestamp // 1_000_000_000,
                                      timezone.utc).astimezone()
        # Keep English day/month names even under a Chinese system locale.
        day = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[date.weekday()]
        month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                 "Sep", "Oct", "Nov", "Dec")[date.month - 1]
        formatted = f"{day} {month} {date.day} {date:%H:%M:%S %Y %z}"
        lines = ["===", f"commit {self.id}"]
        if len(self.parents) == 2:
            lines.append("Merge: " + " ".join(p[:7] for p in self.parents))
        lines.extend([f"Date: {formatted}", self.message, "", ""])
        return "\n".join(lines)
