from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import FileEditResult, FileReadResult, FileWriteResult
from .pathing import is_binary_file, read_text_with_limit, relative_to_workspace, resolve_workspace_path


def _resolve_read_path(workspace_root: Path, raw_path: str, extra_roots: list[Path] | None = None) -> Path:
    """解析只读路径：允许 workspace_root 或 extra_roots（如 skill 目录）下的绝对/相对路径。"""
    if not raw_path:
        raise ValueError("path cannot be empty")
    candidate = Path(raw_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (workspace_root / candidate).resolve()
    allowed = [workspace_root] + (extra_roots or [])
    for base in allowed:
        try:
            resolved.relative_to(base.resolve())
            return resolved
        except ValueError:
            continue
    raise ValueError(f"path escapes workspace root: {raw_path}")


class _WorkspaceToolMixin:
    def __init__(self, workspace_root: str | Path = ".", default_encoding: str = "utf-8") -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.default_encoding = default_encoding

    def _resolve_path(self, arguments: dict[str, Any]) -> Path:
        for key in ("path", "file_path", "filepath", "filename"):
            value = arguments.get(key)
            if value:
                return resolve_workspace_path(self.workspace_root, str(value))
        raise ValueError("path argument is required")

    def _relative(self, path: Path) -> str:
        return relative_to_workspace(self.workspace_root, path)


class FileReadTool(_WorkspaceToolMixin):
    name = "file_read"
    description = "Read text from a file inside the workspace."
    require_approval = False

    def __init__(self, workspace_root: str | Path = ".", default_encoding: str = "utf-8", extra_roots: list[Path] | None = None) -> None:
        super().__init__(workspace_root=workspace_root, default_encoding=default_encoding)
        self.extra_roots = extra_roots or []

    def _resolve_path(self, arguments: dict[str, Any]) -> Path:
        for key in ("path", "file_path", "filepath", "filename"):
            value = arguments.get(key)
            if value:
                return _resolve_read_path(self.workspace_root, str(value), self.extra_roots)
        raise ValueError("path argument is required")

    def _relative(self, path: Path) -> str:
        """返回相对路径；skill 目录下的文件则返回可读的相对路径，无法计算时返回绝对路径。"""
        for base in [self.workspace_root] + self.extra_roots:
            try:
                return str(path.resolve().relative_to(base.resolve())).replace("\\", "/")
            except ValueError:
                continue
        return str(path.resolve()).replace("\\", "/")

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_path(arguments)
        if not path.exists():
            raise FileNotFoundError(f"file not found: {path.name}")
        if path.is_dir():
            raise IsADirectoryError(f"cannot read directory: {path.name}")
        if is_binary_file(path):
            raise ValueError(f"binary files are not supported: {path.name}")

        max_bytes = int(arguments.get("max_bytes", 64 * 1024))
        content, truncated = read_text_with_limit(path, max_bytes=max_bytes, encoding=self.default_encoding)
        lines = content.splitlines()

        start_line = arguments.get("start_line")
        end_line = arguments.get("end_line")
        if start_line is not None or end_line is not None:
            start = int(start_line or 1)
            end = int(end_line or start)
            if start < 1 or end < start:
                raise ValueError("invalid line range")
            selected = lines[start - 1 : end]
            content = "\n".join(selected)
            line_count = len(selected)
            start_out = start
            end_out = end if selected else start - 1
        else:
            line_count = len(lines)
            start_out = 1
            end_out = line_count

        result = FileReadResult(
            ok=True,
            tool_name=self.name,
            path=self._relative(path),
            content=content,
            encoding=self.default_encoding,
            line_count=line_count,
            start_line=start_out,
            end_line=end_out,
            truncated=truncated,
        )
        return result.to_dict()


class FileWriteTool(_WorkspaceToolMixin):
    name = "file_write"
    description = "Create, overwrite, or append text files inside the workspace."
    require_approval = True

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_path(arguments)
        content = str(arguments.get("content", ""))
        mode = str(arguments.get("mode", "overwrite"))
        if mode not in {"create", "overwrite", "append"}:
            raise ValueError("mode must be one of: create, overwrite, append")

        existed_before = path.exists()
        if mode == "create" and existed_before:
            raise FileExistsError(f"file already exists: {path.name}")

        path.parent.mkdir(parents=True, exist_ok=True)
        open_mode = {"create": "x", "overwrite": "w", "append": "a"}[mode]
        with path.open(open_mode, encoding=self.default_encoding) as fh:
            fh.write(content)

        result = FileWriteResult(
            ok=True,
            tool_name=self.name,
            path=self._relative(path),
            mode=mode,
            bytes_written=len(content.encode(self.default_encoding)),
            created=mode == "create" and not existed_before,
            overwritten=mode == "overwrite" and existed_before,
            appended=mode == "append",
            changed_files=[self._relative(path)],
        )
        return result.to_dict()


class FileEditTool(_WorkspaceToolMixin):
    name = "file_edit"
    description = "Perform exact text replacements or line inserts inside workspace files."
    require_approval = True

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_path(arguments)
        if not path.exists():
            raise FileNotFoundError(f"file not found: {path.name}")
        if path.is_dir():
            raise IsADirectoryError(f"cannot edit directory: {path.name}")
        if is_binary_file(path):
            raise ValueError(f"binary files are not supported: {path.name}")

        action = str(arguments.get("action", "replace_exact"))
        if action == "replace_exact":
            return self._replace_exact(path, arguments).to_dict()
        if action == "insert_at_line":
            return self._insert_at_line(path, arguments).to_dict()
        raise ValueError("action must be one of: replace_exact, insert_at_line")

    def _replace_exact(self, path: Path, arguments: dict[str, Any]) -> FileEditResult:
        old_str = arguments.get("old_str")
        new_str = arguments.get("new_str")
        expected_occurrences = int(arguments.get("expected_occurrences", 1))
        if not isinstance(old_str, str) or not old_str:
            raise ValueError("old_str must be a non-empty string")
        if not isinstance(new_str, str):
            raise ValueError("new_str must be a string")
        if expected_occurrences < 1:
            raise ValueError("expected_occurrences must be >= 1")

        original = path.read_text(encoding=self.default_encoding)
        occurrences_found = original.count(old_str)
        if occurrences_found == 0:
            raise ValueError("old_str was not found")
        if occurrences_found != expected_occurrences:
            raise ValueError(
                f"expected {expected_occurrences} occurrences but found {occurrences_found}"
            )

        updated = original.replace(old_str, new_str)
        path.write_text(updated, encoding=self.default_encoding)
        return FileEditResult(
            ok=True,
            tool_name=self.name,
            path=self._relative(path),
            action="replace_exact",
            changed=True,
            occurrences_found=occurrences_found,
            occurrences_changed=expected_occurrences,
            preview=f"- {old_str[:120]}\n+ {new_str[:120]}",
            changed_files=[self._relative(path)],
        )

    def _insert_at_line(self, path: Path, arguments: dict[str, Any]) -> FileEditResult:
        line_no = int(arguments.get("line_no", 0))
        text = arguments.get("text")
        if line_no < 1:
            raise ValueError("line_no must be >= 1")
        if not isinstance(text, str):
            raise ValueError("text must be a string")

        original = path.read_text(encoding=self.default_encoding)
        lines = original.splitlines(keepends=True)
        if line_no > len(lines) + 1:
            raise ValueError(f"line_no {line_no} is beyond end of file")

        insert_text = text if text.endswith("\n") else f"{text}\n"
        lines.insert(line_no - 1, insert_text)
        path.write_text("".join(lines), encoding=self.default_encoding)
        return FileEditResult(
            ok=True,
            tool_name=self.name,
            path=self._relative(path),
            action="insert_at_line",
            changed=True,
            occurrences_found=1,
            occurrences_changed=1,
            preview=f"inserted at line {line_no}: {text[:120]}",
            changed_files=[self._relative(path)],
        )
