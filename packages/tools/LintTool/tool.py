from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any


class LintTool:
    """
    对工作区代码运行静态分析（ruff，回退 pyflakes），返回问题列表。
    
    参数：
      - path: 检查路径（可选，默认 .）
      - fix: 是否自动修复（bool，默认 false）
      - timeout: 超时秒数（可选，默认 30）
    """

    def __init__(self, workspace_root: str | Path = ".") -> None:
        self.workspace_root = Path(workspace_root).resolve()

    async def arun(self, arguments: dict[str, Any]) -> str:
        path = arguments.get("path", ".")
        fix = bool(arguments.get("fix", False))
        timeout = int(arguments.get("timeout", 30))

        python = sys.executable
        cmd = [python, "-m", "ruff", "check"]
        if fix:
            cmd.append("--fix")
        cmd.append(str(path))

        output, exit_code = await self._run(cmd, timeout)
        if exit_code == 127 or "No module named ruff" in output:
            # ruff not installed, fallback to pyflakes
            cmd2 = [python, "-m", "pyflakes", str(path)]
            output, exit_code = await self._run(cmd2, timeout)
            tool_name = "pyflakes"
        else:
            tool_name = "ruff"

        if len(output) > 6000:
            output = output[-6000:]
            output = "[...truncated...]\n" + output

        status = "clean" if exit_code == 0 else f"issues found (exit {exit_code})"
        return f"[lint/{tool_name}] {status}\n\n{output}".rstrip()

    async def _run(self, cmd: list[str], timeout: int) -> tuple[str, int]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self.workspace_root),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return stdout.decode(errors="replace").strip(), proc.returncode or 0
        except asyncio.TimeoutError:
            return f"[lint] timed out after {timeout}s", 1
