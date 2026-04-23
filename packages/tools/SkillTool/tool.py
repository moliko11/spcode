from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


@dataclass
class SkillMetadata:
    name: str                                        # frontmatter name or dir name
    title: str                                       # first # heading
    description: str                                 # frontmatter description or first paragraph
    when_to_use: str = ""
    disable_model_invocation: bool = False
    user_invocable: bool = True
    allowed_tools: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    arguments: list[str] = field(default_factory=list)
    argument_hint: str = ""
    version: str = ""
    openclaw: dict[str, Any] = field(default_factory=dict)  # metadata.openclaw
    body: str = ""                                   # markdown body after frontmatter


class SkillTool:
    name = "skill"
    description = (
        "Discover, inspect, read, invoke, and check dependencies of local skills "
        "defined by SKILL.md files. Supports YAML frontmatter, argument substitution, "
        "and dependency validation."
    )
    require_approval = False

    def __init__(
        self,
        workspace_root: str | Path = ".",
        skill_roots: list[str | Path] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.skill_roots = [
            Path(root).resolve()
            for root in (skill_roots or self._default_skill_roots())
        ]

    # ------------------------------------------------------------------
    # Public async entry point
    # ------------------------------------------------------------------

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action", "list"))
        if action == "list":
            return self._list_skills(arguments)
        if action == "inspect":
            return self._inspect_skill(arguments)
        if action == "read":
            return self._read_skill(arguments)
        if action == "invoke":
            return self._invoke_skill(arguments)
        if action == "list_files":
            return self._list_files(arguments)
        if action == "check_deps":
            return self._check_deps(arguments)
        raise ValueError(
            "skill.action must be list, inspect, read, invoke, list_files, or check_deps"
        )

    # ------------------------------------------------------------------
    # Skill listing for system prompt injection (Phase 2)
    # ------------------------------------------------------------------

    def build_skill_listing(self, max_chars_per_skill: int = 512) -> str:
        """Return a markdown section for injection into the system prompt."""
        entries: list[str] = []
        for root in self.skill_roots:
            for skill_file in sorted(root.rglob("SKILL.md")):
                try:
                    meta = self._parse_skill_file(skill_file)
                except Exception:
                    continue
                if meta.disable_model_invocation:
                    continue
                oc = meta.openclaw
                desc = (meta.description or meta.title)[:max_chars_per_skill]
                emoji = oc.get("emoji", "")
                prefix = f"{emoji} " if emoji else ""
                deps_parts: list[str] = []
                env_deps = oc.get("requires", {}).get("env", [])
                bin_deps = oc.get("requires", {}).get("bins", [])
                if env_deps:
                    deps_parts.append(f"env:{','.join(env_deps)}")
                if bin_deps:
                    deps_parts.append(f"bins:{','.join(bin_deps)}")
                deps_note = f" [{'; '.join(deps_parts)}]" if deps_parts else ""
                entries.append(f"- **{meta.name}**{deps_note}: {prefix}{desc}")

        if not entries:
            return ""
        lines = ["## Available Skills", ""] + entries + [
            "",
            "Use `skill` tool: `action=read` to load full instructions, "
            "`action=invoke` to render with arguments, "
            "`action=check_deps` to verify dependencies, "
            "`action=list_files` to see supporting scripts.",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _list_skills(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip().lower()
        matches = []
        for root in self.skill_roots:
            for skill_file in sorted(root.rglob("SKILL.md")):
                try:
                    meta = self._parse_skill_file(skill_file)
                except Exception:
                    continue
                rel = self._relative(skill_file)
                haystack = " ".join([
                    meta.name.lower(),
                    rel.lower(),
                    meta.title.lower(),
                    meta.description.lower(),
                ])
                if query and query not in haystack:
                    continue
                oc = meta.openclaw
                matches.append({
                    "name": meta.name,
                    "title": meta.title,
                    "description": meta.description[:240],
                    "path": rel,
                    "root": self._relative(root),
                    "disable_model_invocation": meta.disable_model_invocation,
                    "user_invocable": meta.user_invocable,
                    "allowed_tools": meta.allowed_tools,
                    "paths": meta.paths,
                    "has_scripts": (skill_file.parent / "scripts").is_dir(),
                    "requires_env": oc.get("requires", {}).get("env", []),
                    "requires_bins": oc.get("requires", {}).get("bins", []),
                    "emoji": oc.get("emoji", ""),
                    "version": meta.version,
                })
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
        meta = self._parse_skill_file(target)
        oc = meta.openclaw
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "inspect",
            "skill": {
                "name": meta.name,
                "title": meta.title,
                "description": meta.description,
                "version": meta.version,
                "path": self._relative(target),
                "directory": self._relative(target.parent),
                "skill_dir": str(target.parent.resolve()),
                "root": self._infer_root(target),
                "disable_model_invocation": meta.disable_model_invocation,
                "user_invocable": meta.user_invocable,
                "allowed_tools": meta.allowed_tools,
                "paths": meta.paths,
                "argument_hint": meta.argument_hint,
                "arguments": meta.arguments,
                "has_scripts": (target.parent / "scripts").is_dir(),
                "requires_env": oc.get("requires", {}).get("env", []),
                "requires_bins": oc.get("requires", {}).get("bins", []),
                "install": oc.get("install", []),
                "emoji": oc.get("emoji", ""),
                "homepage": oc.get("homepage", ""),
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
        meta = self._parse_skill_file(target)
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "read",
            "skill_name": meta.name,
            "path": self._relative(target),
            "content": content,
            "changed_files": [],
            "metadata": {
                "title": meta.title,
                "description": meta.description,
                "skill_dir": str(target.parent.resolve()),
                "root": self._infer_root(target),
                "version": meta.version,
            },
        }

    def _invoke_skill(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Read a skill and render all placeholders before returning to the LLM."""
        skill_name = str(arguments.get("skill_name", "")).strip()
        path_arg = str(arguments.get("path", "")).strip()
        if not skill_name and not path_arg:
            raise ValueError("skill.invoke requires skill_name or path")
        invocation_args = arguments.get("arguments", "")
        session_id = str(arguments.get("session_id", ""))
        target = self._resolve_skill(skill_name=skill_name, path_arg=path_arg)
        meta = self._parse_skill_file(target)
        skill_dir = str(target.parent.resolve())
        rendered = self._render_content(
            body=meta.body,
            invocation_args=invocation_args,
            named_args=meta.arguments,
            skill_dir=skill_dir,
            workspace_root=str(self.workspace_root),
            session_id=session_id,
        )
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "invoke",
            "skill_name": meta.name,
            "path": self._relative(target),
            "rendered_content": rendered,
            "changed_files": [],
            "metadata": {
                "title": meta.title,
                "skill_dir": skill_dir,
                "allowed_tools": meta.allowed_tools,
            },
        }

    def _list_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """List all files in a skill directory with role classification."""
        skill_name = str(arguments.get("skill_name", "")).strip()
        path_arg = str(arguments.get("path", "")).strip()
        if not skill_name and not path_arg:
            raise ValueError("skill.list_files requires skill_name or path")
        target = self._resolve_skill(skill_name=skill_name, path_arg=path_arg)
        skill_dir = target.parent
        files = []
        for p in sorted(skill_dir.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(skill_dir)).replace("\\", "/")
                files.append({
                    "path": rel,
                    "role": self._classify_file(rel),
                    "size_bytes": p.stat().st_size,
                })
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "list_files",
            "skill_name": skill_name or path_arg,
            "skill_dir": self._relative(skill_dir),
            "files": files,
            "changed_files": [],
            "metadata": {"count": len(files)},
        }

    def _check_deps(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Check whether a skill's env vars and required binaries are available."""
        skill_name = str(arguments.get("skill_name", "")).strip()
        path_arg = str(arguments.get("path", "")).strip()
        if not skill_name and not path_arg:
            raise ValueError("skill.check_deps requires skill_name or path")
        target = self._resolve_skill(skill_name=skill_name, path_arg=path_arg)
        meta = self._parse_skill_file(target)
        oc = meta.openclaw
        requires = oc.get("requires", {})

        missing_env: list[str] = []
        present_env: list[str] = []
        for env_var in requires.get("env", []):
            (present_env if os.environ.get(env_var) else missing_env).append(env_var)

        missing_bins: list[str] = []
        present_bins: list[str] = []
        for bin_name in requires.get("bins", []):
            (present_bins if shutil.which(bin_name) else missing_bins).append(bin_name)

        satisfied = not missing_env and not missing_bins
        return {
            "ok": True,
            "tool_name": self.name,
            "action": "check_deps",
            "skill_name": meta.name,
            "deps_satisfied": satisfied,
            "missing_env": missing_env,
            "present_env": present_env,
            "missing_bins": missing_bins,
            "present_bins": present_bins,
            "install_instructions": oc.get("install", []),
            "homepage": oc.get("homepage", ""),
            "primary_env": oc.get("primaryEnv", ""),
            "changed_files": [],
            "metadata": {},
        }

    # ------------------------------------------------------------------
    # Frontmatter parsing (Phase 1)
    # ------------------------------------------------------------------

    def _parse_skill_file(self, path: Path) -> SkillMetadata:
        content = path.read_text(encoding="utf-8")
        frontmatter_str, body = self._split_frontmatter(content)
        dir_name = path.parent.name

        fm: dict[str, Any] = {}
        if frontmatter_str:
            if _HAS_YAML:
                try:
                    parsed = yaml.safe_load(frontmatter_str)
                    if isinstance(parsed, dict):
                        fm = parsed
                except Exception:
                    pass
            if not fm:
                stripped = frontmatter_str.strip()
                if stripped.startswith("{"):
                    try:
                        fm = json.loads(stripped)
                    except Exception:
                        pass

        # metadata.openclaw — supports both YAML nested and inline JSON string
        raw_meta = fm.get("metadata", {})
        if isinstance(raw_meta, str):
            try:
                raw_meta = json.loads(raw_meta)
            except Exception:
                raw_meta = {}
        openclaw: dict[str, Any] = {}
        if isinstance(raw_meta, dict):
            openclaw = raw_meta.get("openclaw", {})

        # Title from first # heading in body
        title = dir_name
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip() or title
                break

        # Description: frontmatter > first non-empty, non-heading body line
        raw_desc = fm.get("description", "")
        if isinstance(raw_desc, list):
            description = " ".join(str(x) for x in raw_desc).strip()
        else:
            description = str(raw_desc).strip()
        if not description:
            for line in body.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                description = stripped[:512]
                break

        # allowed-tools
        raw_allowed = fm.get("allowed-tools", fm.get("allowed_tools", []))
        allowed_tools = raw_allowed.split() if isinstance(raw_allowed, str) else list(raw_allowed or [])

        # paths
        raw_paths = fm.get("paths", [])
        paths = ([p.strip() for p in raw_paths.split(",") if p.strip()]
                 if isinstance(raw_paths, str) else list(raw_paths or []))

        # arguments (named positional)
        raw_args = fm.get("arguments", [])
        arg_names = raw_args.split() if isinstance(raw_args, str) else list(raw_args or [])

        return SkillMetadata(
            name=str(fm.get("name", dir_name)).strip() or dir_name,
            title=title,
            description=description,
            when_to_use=str(fm.get("when_to_use", fm.get("when-to-use", ""))).strip(),
            disable_model_invocation=bool(
                fm.get("disable-model-invocation", fm.get("disable_model_invocation", False))
            ),
            user_invocable=bool(fm.get("user-invocable", fm.get("user_invocable", True))),
            allowed_tools=allowed_tools,
            paths=paths,
            arguments=arg_names,
            argument_hint=str(fm.get("argument-hint", fm.get("argument_hint", ""))).strip(),
            version=str(fm.get("version", "")).strip(),
            openclaw=openclaw,
            body=body,
        )

    @staticmethod
    def _split_frontmatter(content: str) -> tuple[str, str]:
        """Return (frontmatter_str, body_str) from SKILL.md content."""
        if not content.startswith("---"):
            return "", content
        rest = content[3:]
        end = rest.find("\n---")
        if end == -1:
            return "", content
        frontmatter = rest[:end].strip()
        body = rest[end + 4:].lstrip("\n")
        return frontmatter, body

    # ------------------------------------------------------------------
    # Placeholder rendering for invoke (Phase 3)
    # ------------------------------------------------------------------

    @staticmethod
    def _render_content(
        body: str,
        invocation_args: str | list[str],
        named_args: list[str],
        skill_dir: str,
        workspace_root: str,
        session_id: str,
    ) -> str:
        if isinstance(invocation_args, list):
            args_str = " ".join(str(a) for a in invocation_args)
            pos_args = [str(a) for a in invocation_args]
        else:
            args_str = str(invocation_args)
            pos_args = args_str.split() if args_str.strip() else []

        result = body

        # Skill dir placeholders (used by 12306, pdf-to-ppt, etc.)
        result = result.replace("{baseDir}", skill_dir)
        result = result.replace("{SKILL_DIR}", skill_dir)
        result = result.replace("${CLAUDE_SKILL_DIR}", skill_dir)

        # Workspace root
        result = result.replace("{WORKSPACE}", workspace_root)

        # Session
        result = result.replace("${CLAUDE_SESSION_ID}", session_id)

        # $ARGUMENTS → full args string (replace before $ARGUMENTS[N])
        result = result.replace("$ARGUMENTS", args_str)

        # $ARGUMENTS[N]
        def _by_index(m: re.Match) -> str:
            idx = int(m.group(1))
            return pos_args[idx] if idx < len(pos_args) else ""
        result = re.sub(r"\$ARGUMENTS\[(\d+)\]", _by_index, result)

        # $N shorthand
        def _positional(m: re.Match) -> str:
            idx = int(m.group(1))
            return pos_args[idx] if idx < len(pos_args) else ""
        result = re.sub(r"\$(\d+)\b", _positional, result)

        # Named args: $argname → positional by order
        for i, arg_name in enumerate(named_args):
            if i < len(pos_args):
                result = result.replace(f"${arg_name}", pos_args[i])

        return result

    # ------------------------------------------------------------------
    # File role classifier (Phase 4)
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_file(rel_path: str) -> str:
        name = rel_path.lower()
        if name == "skill.md":
            return "entrypoint"
        if name in ("readme.md", "readme"):
            return "readme"
        if name in ("package.json", "pyproject.toml", "setup.py", "requirements.txt"):
            return "manifest"
        if "config" in name and "example" in name:
            return "config_template"
        if "config" in name:
            return "config"
        if name.endswith((".js", ".mjs", ".ts", ".py", ".sh", ".bash")):
            return "script"
        if name.endswith(".md"):
            return "docs"
        return "other"

    # ------------------------------------------------------------------
    # Resolution & path helpers
    # ------------------------------------------------------------------

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

        # Match by directory name OR by frontmatter name field
        candidates: list[Path] = []
        for root in self.skill_roots:
            for skill_file in root.rglob("SKILL.md"):
                if skill_file.parent.name == skill_name:
                    candidates.append(skill_file)
                    continue
                try:
                    meta = self._parse_skill_file(skill_file)
                    if meta.name == skill_name:
                        candidates.append(skill_file)
                except Exception:
                    pass

        # Deduplicate preserving order
        seen: set[Path] = set()
        unique: list[Path] = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique.append(c)

        if not unique:
            raise FileNotFoundError(f"skill not found: {skill_name}")
        if len(unique) > 1:
            raise ValueError(
                f"multiple skills found for name '{skill_name}', use path instead"
            )
        return unique[0]

    def _default_skill_roots(self) -> list[Path]:
        roots = [
            self.workspace_root / "skills",
            self.workspace_root / "packages" / "tools",
        ]
        return [root for root in roots if root.exists()]

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
