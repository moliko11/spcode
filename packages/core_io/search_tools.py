from __future__ import annotations

import json
import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .models import GlobResult, GrepResult
from .pathing import DEFAULT_IGNORED_DIR_NAMES, relative_to_workspace, resolve_workspace_path, should_ignore_path


class _SearchToolMixin:
    def __init__(self, workspace_root: str | Path = ".", ignored_dir_names: set[str] | None = None) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.ignored_dir_names = ignored_dir_names or DEFAULT_IGNORED_DIR_NAMES

    def _resolve_base_path(self, arguments: dict[str, Any]) -> Path:
        return resolve_workspace_path(self.workspace_root, str(arguments.get("base_path", ".")))

    def _relative(self, path: Path) -> str:
        return relative_to_workspace(self.workspace_root, path)

    def _iter_files(self, base_path: Path):
        for path in base_path.rglob("*"):
            if not path.is_file():
                continue
            if should_ignore_path(self.workspace_root, path, self.ignored_dir_names):
                continue
            yield path


class GlobTool(_SearchToolMixin):
    name = "glob"
    description = "Find files inside the workspace using glob patterns."
    require_approval = False

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = str(arguments.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("pattern is required")

        base_path = self._resolve_base_path(arguments)
        include_hidden = bool(arguments.get("include_hidden", False))
        max_results = int(arguments.get("max_results", 200))

        matches: list[str] = []
        truncated = False
        for path in self._iter_files(base_path):
            rel = self._relative(path)
            if not include_hidden and any(part.startswith(".") for part in Path(rel).parts):
                continue
            if self._matches_pattern(rel, path.name, pattern):
                if len(matches) >= max_results:
                    truncated = True
                    break
                matches.append(rel)

        result = GlobResult(
            ok=True,
            tool_name=self.name,
            base_path=self._relative(base_path) if base_path != self.workspace_root else ".",
            pattern=pattern,
            matches=sorted(matches),
            truncated=truncated,
        )
        return result.to_dict()

    def _matches_pattern(self, rel: str, name: str, pattern: str) -> bool:
        if fnmatch(rel, pattern) or fnmatch(name, pattern):
            return True
        if pattern.startswith("**/") and fnmatch(rel, pattern[3:]):
            return True
        return False


class GrepTool(_SearchToolMixin):
    name = "grep"
    description = "Search file contents inside the workspace."
    require_approval = False

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = str(arguments.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("pattern is required")

        try:
            return self._run_with_rg(arguments)
        except Exception:
            return self._run_python(arguments)

    def _run_with_rg(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = str(arguments.get("pattern", "")).strip()
        base_path = self._resolve_base_path(arguments)
        file_glob = arguments.get("file_glob")
        case_sensitive = bool(arguments.get("case_sensitive", False))
        max_matches = int(arguments.get("max_matches", 200))

        command = ["rg", "--json", "--color", "never"]
        if not case_sensitive:
            command.append("-i")
        if file_glob:
            command.extend(["-g", str(file_glob)])
        command.extend([pattern, str(base_path)])

        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode not in {0, 1}:
            raise RuntimeError(completed.stderr or "rg failed")

        matches = self._parse_rg_json(completed.stdout, max_matches=max_matches)

        result = GrepResult(
            ok=True,
            tool_name=self.name,
            base_path=self._relative(base_path) if base_path != self.workspace_root else ".",
            pattern=pattern,
            matches=matches,
            match_count=len(matches),
            files_scanned=0,
            truncated=completed.returncode == 0 and len(matches) >= max_matches,
        )
        return result.to_dict()

    def _parse_rg_json(self, stdout: str, max_matches: int) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            if len(matches) >= max_matches:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            path_data = data.get("path") if isinstance(data.get("path"), dict) else {}
            lines_data = data.get("lines") if isinstance(data.get("lines"), dict) else {}
            raw_path = path_data.get("text")
            line_text = lines_data.get("text", "")
            line_no = data.get("line_number")
            if not raw_path or not isinstance(line_no, int):
                continue
            matches.append(
                {
                    "path": self._relative(Path(raw_path)),
                    "line_no": line_no,
                    "line_text": str(line_text).rstrip("\n\r"),
                }
            )
        return matches

    def _run_python(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = str(arguments.get("pattern", "")).strip()
        base_path = self._resolve_base_path(arguments)
        is_regex = bool(arguments.get("is_regex", True))
        case_sensitive = bool(arguments.get("case_sensitive", False))
        file_glob = arguments.get("file_glob")
        max_matches = int(arguments.get("max_matches", 200))

        flags = 0 if case_sensitive else re.IGNORECASE
        matcher = re.compile(pattern, flags) if is_regex else None

        matches: list[dict[str, Any]] = []
        files_scanned = 0
        truncated = False

        for path in self._iter_files(base_path):
            rel = self._relative(path)
            if file_glob and not fnmatch(rel, str(file_glob)):
                continue
            files_scanned += 1
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue

            for line_no, line_text in enumerate(lines, start=1):
                if is_regex:
                    matched = bool(matcher.search(line_text))
                else:
                    needle = pattern if case_sensitive else pattern.lower()
                    haystack = line_text if case_sensitive else line_text.lower()
                    matched = needle in haystack
                if not matched:
                    continue
                matches.append({"path": rel, "line_no": line_no, "line_text": line_text})
                if len(matches) >= max_matches:
                    truncated = True
                    break
            if truncated:
                break

        result = GrepResult(
            ok=True,
            tool_name=self.name,
            base_path=self._relative(base_path) if base_path != self.workspace_root else ".",
            pattern=pattern,
            matches=matches,
            match_count=len(matches),
            files_scanned=files_scanned,
            truncated=truncated,
        )
        return result.to_dict()
