from __future__ import annotations

from pathlib import Path


DEFAULT_IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
}


def resolve_workspace_path(workspace_root: str | Path, raw_path: str) -> Path:
    if not raw_path:
        raise ValueError("path cannot be empty")

    root = Path(workspace_root).resolve()
    candidate = Path(raw_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace root: {raw_path}") from exc
    return resolved


def relative_to_workspace(workspace_root: str | Path, path: Path) -> str:
    return str(path.resolve().relative_to(Path(workspace_root).resolve())).replace("\\", "/")


def is_binary_file(path: Path, sample_size: int = 4096) -> bool:
    sample = path.read_bytes()[:sample_size]
    return b"\0" in sample


def read_text_with_limit(path: Path, max_bytes: int, encoding: str = "utf-8") -> tuple[str, bool]:
    data = path.read_bytes()
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    text = data.decode(encoding, errors="replace")
    return text.replace("\r\n", "\n"), truncated


def should_ignore_path(
    workspace_root: str | Path,
    path: Path,
    ignored_dir_names: set[str] | None = None,
) -> bool:
    ignored = ignored_dir_names or DEFAULT_IGNORED_DIR_NAMES
    relative = path.resolve().relative_to(Path(workspace_root).resolve())
    return any(part in ignored for part in relative.parts[:-1])
