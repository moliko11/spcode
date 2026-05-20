from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


class GitLogTool:
    """
    返回 git 提交历史，帮助理解代码演进脉络。
    
    参数：
      - n: 最大条数（int，默认 20）
      - path: 限定到某个文件或目录（可选）
      - author: 按作者过滤（可选）
      - since: 起始时间（如 "2 weeks ago"，可选）
      - oneline: 每条只输出一行（bool，默认 true）
      - timeout: 超时秒数（可选，默认 15）
    """

    def __init__(self, workspace_root: str | Path = ".") -> None:
        self.workspace_root = Path(workspace_root).resolve()

    async def arun(self, arguments: dict[str, Any]) -> str:
        n = int(arguments.get("n", 20))
        path = arguments.get("path", "")
        author = arguments.get("author", "")
        since = arguments.get("since", "")
        oneline = bool(arguments.get("oneline", True))
        timeout = int(arguments.get("timeout", 15))

        cmd = ["git", "log", f"-{n}"]
        if oneline:
            cmd.append("--oneline")
        else:
            cmd.extend(["--format=%h %as %an | %s"])
        if author:
            cmd.append(f"--author={author}")
        if since:
            cmd.append(f"--since={since}")
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
            return f"[git_log] timed out after {timeout}s"

        err = stderr.decode(errors="replace").strip()
        out = stdout.decode(errors="replace").strip()

        if proc.returncode != 0 and err:
            return f"[git_log] error: {err}"
        if not out:
            return "[git_log] no commits found"
        return out
