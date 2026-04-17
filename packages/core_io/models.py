from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class IOResult:
    ok: bool
    tool_name: str
    changed_files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FileReadResult(IOResult):
    path: str = ""
    content: str = ""
    encoding: str = "utf-8"
    line_count: int = 0
    start_line: int = 1
    end_line: int = 0
    truncated: bool = False


@dataclass(slots=True)
class FileWriteResult(IOResult):
    path: str = ""
    mode: str = "overwrite"
    bytes_written: int = 0
    created: bool = False
    overwritten: bool = False
    appended: bool = False


@dataclass(slots=True)
class FileEditResult(IOResult):
    path: str = ""
    action: str = ""
    changed: bool = False
    occurrences_found: int = 0
    occurrences_changed: int = 0
    preview: str = ""


@dataclass(slots=True)
class GlobResult(IOResult):
    base_path: str = "."
    pattern: str = ""
    matches: list[str] = field(default_factory=list)
    truncated: bool = False


@dataclass(slots=True)
class GrepMatch:
    path: str
    line_no: int
    line_text: str


@dataclass(slots=True)
class GrepResult(IOResult):
    base_path: str = "."
    pattern: str = ""
    matches: list[dict[str, Any]] = field(default_factory=list)
    match_count: int = 0
    files_scanned: int = 0
    truncated: bool = False
