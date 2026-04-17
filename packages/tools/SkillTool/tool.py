from __future__ import annotations

from pathlib import Path
from typing import Any


class SkillTool:
    name = "skill"
    description = "Discover, inspect, and read local skills defined by SKILL.md files."
    require_approval = False

    def __init__(self, workspace_root: str | Path = ".", skill_roots: list[str | Path] | None = None) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.skill_roots = [Path(root).resolve() for root in (skill_roots or self._default_skill_roots())]

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action", "list"))
        if action == "list":
            return self._list_skills(arguments)
        if action == "inspect":
            return self._inspect_skill(arguments)
        if action == "read":
            return self._read_skill(arguments)
        raise ValueError("skill.action must be list, inspect, or read")

    def _default_skill_roots(self) -> list[Path]:
        roots = [self.workspace_root / "skills", self.workspace_root / "packages" / "tools"]
        return [root for root in roots if root.exists()]

    def _list_skills(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip().lower()
        matches = []
        for root in self.skill_roots:
            for skill_file in root.rglob("SKILL.md"):
                metadata = self._parse_skill_file(skill_file)
                skill_name = metadata["name"]
                rel = self._relative(skill_file)
                haystack = " ".join(
                    [
                        skill_name.lower(),
                        rel.lower(),
                        metadata["title"].lower(),
                        metadata["summary"].lower(),
                    ]
                )
                if query and query not in haystack:
                    continue
                matches.append(
                    {
                        "name": skill_name,
                        "title": metadata["title"],
                        "summary": metadata["summary"],
                        "path": rel,
                        "root": self._relative(root),
                    }
                )
        matches.sort(key=lambda item: item["path"])
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "list",
            "skills": matches,
            "changed_files": [],
            "metadata": {"count": len(matches)},
        }

    def _inspect_skill(self, arguments: dict[str, Any]) -> dict[str, Any]:
        skill_name = str(arguments.get("skill_name", "")).strip()
        path_arg = str(arguments.get("path", "")).strip()
        target = self._resolve_skill(skill_name=skill_name, path_arg=path_arg)
        metadata = self._parse_skill_file(target)
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "inspect",
            "skill": {
                "name": metadata["name"],
                "title": metadata["title"],
                "summary": metadata["summary"],
                "path": self._relative(target),
                "directory": self._relative(target.parent),
                "root": self._infer_root(target),
            },
            "changed_files": [],
            "metadata": {},
        }

    def _read_skill(self, arguments: dict[str, Any]) -> dict[str, Any]:
        skill_name = str(arguments.get("skill_name", "")).strip()
        path_arg = str(arguments.get("path", "")).strip()
        if not skill_name and not path_arg:
            raise ValueError("skill.read requires skill_name or path")

        target = self._resolve_skill(skill_name=skill_name, path_arg=path_arg)
        content = target.read_text(encoding="utf-8")
        metadata = self._parse_skill_file(target)
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "read",
            "skill_name": metadata["name"],
            "path": self._relative(target),
            "content": content,
            "changed_files": [],
            "metadata": {
                "title": metadata["title"],
                "summary": metadata["summary"],
                "root": self._infer_root(target),
            },
        }

    def _resolve_skill(self, skill_name: str, path_arg: str) -> Path:
        if path_arg:
            path = Path(path_arg)
            if not path.is_absolute():
                path = (self.workspace_root / path).resolve()
            if not path.exists():
                raise FileNotFoundError(f"skill path not found: {path_arg}")
            if path.is_dir():
                path = path / "SKILL.md"
            if path.name != "SKILL.md":
                raise ValueError("skill path must point to SKILL.md or its parent directory")
            return path

        candidates = []
        for root in self.skill_roots:
            for skill_file in root.rglob("SKILL.md"):
                if skill_file.parent.name == skill_name:
                    candidates.append(skill_file)
        if not candidates:
            raise FileNotFoundError(f"skill not found: {skill_name}")
        if len(candidates) > 1:
            raise ValueError(f"multiple skills found for name '{skill_name}', use path instead")
        return candidates[0]

    def _parse_skill_file(self, path: Path) -> dict[str, str]:
        content = path.read_text(encoding="utf-8")
        lines = [line.rstrip() for line in content.splitlines()]
        title = path.parent.name
        summary = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip() or title
                break
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            summary = stripped[:240]
            break
        return {
            "name": path.parent.name,
            "title": title,
            "summary": summary,
        }

    def _infer_root(self, path: Path) -> str:
        for root in self.skill_roots:
            try:
                path.resolve().relative_to(root)
                return self._relative(root)
            except ValueError:
                continue
        return ""

    def _relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace_root)).replace("\\", "/")
        except ValueError:
            return str(path.resolve()).replace("\\", "/")
