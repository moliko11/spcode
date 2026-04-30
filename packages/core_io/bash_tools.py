from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .pathing import relative_to_workspace, resolve_workspace_path


@dataclass(slots=True)
class BashSession:
    session_id: str
    cwd: str = "."
    env: dict[str, str] = field(default_factory=dict)


class BashSessionManager:
    DEFAULT_BLOCKED_PATTERNS = [
        r"\brm\b",
        r"\bdel\b",
        r"\bRemove-Item\b",
        r"\bformat\b",
        r"git\s+reset\s+--hard",
        r"git\s+checkout\s+--",
    ]

    def __init__(
        self,
        workspace_root: str | Path = ".",
        shell_mode: str = "powershell",
        blocked_patterns: list[str] | None = None,
        output_limit: int = 20_000,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.shell_mode = shell_mode
        self.blocked_patterns = blocked_patterns or list(self.DEFAULT_BLOCKED_PATTERNS)
        self.output_limit = output_limit
        self._sessions: dict[str, BashSession] = {}

    def get_session(self, session_id: str, restart: bool = False) -> BashSession:
        if restart or session_id not in self._sessions:
            self._sessions[session_id] = BashSession(session_id=session_id)
        return self._sessions[session_id]

    async def run(
        self,
        command: str,
        session_id: str = "default",
        cwd: str | None = None,
        timeout_s: float = 20.0,
        restart: bool = False,
    ) -> dict[str, Any]:
        self._validate_command(command)
        session = self.get_session(session_id, restart=restart)
        if cwd is not None:
            session.cwd = cwd

        working_dir = resolve_workspace_path(self.workspace_root, session.cwd)
        env = self._build_env(session.env)

        process = await asyncio.create_subprocess_exec(
            *self._build_command(command),
            cwd=str(working_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise TimeoutError(f"bash command timed out after {timeout_s}s") from exc

        stdout = self._truncate(stdout_raw.decode("utf-8", errors="replace"))
        stderr = self._truncate(stderr_raw.decode("utf-8", errors="replace"))
        updated_cwd = self._extract_cwd_from_output(stdout, session.cwd)
        session.cwd = updated_cwd
        stdout = self._strip_cwd_marker(stdout)
        ok = process.returncode == 0
        error = None if ok else (stderr.strip() or stdout.strip() or f"command exited with code {process.returncode}")

        return {
            "ok": ok,
            "tool_name": "bash",
            "command": command,
            "session_id": session.session_id,
            "cwd": updated_cwd,
            "stdout": stdout,
            "stderr": stderr,
            "error": error,
            "exit_code": process.returncode,
            "changed_files": [],
            "metadata": {"restart": restart},
        }

    def _build_command(self, command: str) -> list[str]:
        if self.shell_mode == "powershell":
            command = re.sub(r"\s&&\s", "; ", command)
            wrapped = (
                "$ErrorActionPreference='Stop'; "
                f"{command}; "
                "Write-Output ('__CODEX_CWD__=' + (Get-Location).Path)"
            )
            return ["powershell", "-NoProfile", "-Command", wrapped]
        if self.shell_mode == "bash":
            wrapped = f"{command}; printf '\\n__CODEX_CWD__=%s\\n' \"$PWD\""
            return ["bash", "-lc", wrapped]
        raise ValueError(f"unsupported shell mode: {self.shell_mode}")

    def _validate_command(self, command: str) -> None:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        for pattern in self.blocked_patterns:
            if re.search(pattern, command, flags=re.IGNORECASE):
                raise ValueError(f"command blocked by pattern: {pattern}")

    def _build_env(self, session_env: dict[str, str]) -> dict[str, str]:
        allowed = ["PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "HOME", "USERPROFILE"]
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update(session_env)
        return env

    def _truncate(self, text: str) -> str:
        if len(text) <= self.output_limit:
            return text
        return text[: self.output_limit] + "\n...[truncated]"

    def _extract_cwd_from_output(self, stdout: str, fallback_cwd: str) -> str:
        marker = "__CODEX_CWD__="
        lines = stdout.splitlines()
        for line in reversed(lines):
            if line.startswith(marker):
                path = Path(line[len(marker) :].strip())
                return relative_to_workspace(self.workspace_root, path)
        return fallback_cwd

    def _strip_cwd_marker(self, stdout: str) -> str:
        marker = "__CODEX_CWD__="
        lines = [line for line in stdout.splitlines() if not line.startswith(marker)]
        if stdout.endswith(("\n", "\r")) and lines:
            return "\n".join(lines) + "\n"
        return "\n".join(lines)


class BashTool:
    name = "bash"
    description = "Run a shell command inside a persistent workspace session."
    require_approval = True

    def __init__(self, session_manager: BashSessionManager) -> None:
        self.session_manager = session_manager

    async def arun(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self.session_manager.run(
            command=str(arguments.get("command", "")),
            session_id=str(arguments.get("session_id", "default")),
            cwd=arguments.get("cwd"),
            timeout_s=float(arguments.get("timeout_s", 20.0)),
            restart=bool(arguments.get("restart", False)),
        )
