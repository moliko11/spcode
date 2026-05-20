from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path
from typing import Any


class RunTestsTool:
    """
    运行项目测试（默认 pytest），返回通过/失败统计与失败详情。
    
    参数：
      - path: 测试路径（可选，默认当前工作目录）
      - args: 附加 pytest 参数字符串（可选）
      - timeout: 超时秒数（可选，默认 120）
    """

    def __init__(self, workspace_root: str | Path = ".") -> None:
        self.workspace_root = Path(workspace_root).resolve()

    async def arun(self, arguments: dict[str, Any]) -> str:
        path = arguments.get("path", "")
        extra_args = arguments.get("args", "")
        timeout = int(arguments.get("timeout", 120))

        python = sys.executable
        cmd_parts = [python, "-m", "pytest", "--tb=short", "-q"]
        if path:
            cmd_parts.append(path)
        if extra_args:
            cmd_parts.extend(shlex.split(extra_args))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self.workspace_root),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            return f"[run_tests] timed out after {timeout}s"

        output = stdout.decode(errors="replace").strip()
        if len(output) > 8000:
            output = output[-8000:]
            output = "[...truncated...]\n" + output
        exit_code = proc.returncode
        status = "passed" if exit_code == 0 else f"failed (exit {exit_code})"
        return f"[run_tests] {status}\n\n{output}"
