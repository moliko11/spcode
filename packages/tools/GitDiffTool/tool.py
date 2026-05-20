from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


class GitDiffTool:
    """
    返回 git diff 结果，理解最近的工作区变更或提交间差异。
    
    参数：
      - staged: 是否只显示暂存区差异（bool，默认 false）
      - commit: 指定 commit 或范围（如 "HEAD~1"、"abc123..def456"，可选）
      - path: 限定到某个文件或目录（可选）
      - stat: 只输出统计摘要（bool，默认 false）
      - timeout: 超时秒数（可选，默认 30）
    """

    def __init__(self, workspace_root: str | Path = ".") -> None:
        self.workspace_root = Path(workspace_root).resolve()

    async def arun(self, arguments: dict[str, Any]) -> str:
        staged = bool(arguments.get("staged", False))
        commit = arguments.get("commit", "")
        path = arguments.get("path", "")
        stat = bool(arguments.get("stat", False))
        timeout = int(arguments.get("timeout", 30))

        cmd = ["git", "diff"]
        if staged:
            cmd.append("--staged")
        if stat:
            cmd.append("--stat")
        if commit:
            cmd.append(commit)
        if path:
            cmd.extend(["--", path])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace_root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            return f"[git_diff] timed out after {timeout}s"

        err = stderr.decode(errors="replace").strip()
        out = stdout.decode(errors="replace").strip()

        if proc.returncode != 0 and err:
            return f"[git_diff] error: {err}"
        if not out:
            return "[git_diff] no changes"
        if len(out) > 12000:
            out = out[:12000] + "\n...[truncated; use --stat or narrow --path]"
        return out
